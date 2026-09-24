#!/usr/bin/env python3
"""Standardize raw inventory CSV extracts into traceable staging tables.

The module intentionally keeps raw source files immutable. Every clean or
quarantined row has a source file, source row number, source record hash, and
staging run identifier so it can be traced to the raw landing manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DATE_COLUMNS = {
    "business_date", "sale_date", "receipt_date", "transfer_date",
    "expected_receipt_date", "effective_from_date", "effective_to_date",
    "calendar_date", "oldest_receipt_date",
}
SKU_COLUMNS = {"sku_id"}
STORE_COLUMNS = {"store_id", "origin_store_id", "destination_store_id"}
UNIT_COLUMNS = {"base_uom", "uom", "unit_of_measure"}
STATUS_COLUMNS = {"product_status", "store_status", "transfer_status"}
NUMERIC_SUFFIXES = ("_qty", "_amount", "_cost", "_days", "_point_qty")

UNIT_MAP = {
    "EA": "EA", "EACH": "EA", "EACHES": "EA", "UNIT": "EA", "UNITS": "EA",
    "KG": "KG", "KILOGRAM": "KG", "KILOGRAMS": "KG",
    "G": "G", "GRAM": "G", "GRAMS": "G",
    "L": "L", "LITRE": "L", "LITRES": "L", "LITER": "L", "LITERS": "L",
    "ML": "ML", "MILLILITRE": "ML", "MILLILITRES": "ML",
}
STATUS_MAP = {
    "product_status": {
        "ACTIVE": "ACTIVE", "LIVE": "ACTIVE", "DISCONTINUED": "DISCONTINUED",
        "INACTIVE": "DISCONTINUED", "DELISTED": "DISCONTINUED",
    },
    "store_status": {"ACTIVE": "ACTIVE", "OPEN": "ACTIVE", "CLOSED": "CLOSED", "INACTIVE": "CLOSED"},
    "transfer_status": {
        "SHIPPED": "SHIPPED", "RECEIVED": "RECEIVED", "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED", "IN TRANSIT": "IN_TRANSIT", "IN_TRANSIT": "IN_TRANSIT",
    },
}
KEY_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalize_key(value: str, field: str) -> str:
    normalized = re.sub(r"\s+", "-", value.strip().upper())
    if not normalized or not KEY_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an alphanumeric key with optional hyphens or underscores")
    return normalized


def normalize_date(value: str, field: str) -> str:
    if not value.strip():
        return ""
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"{field} is not an accepted date")


def normalize_decimal(value: str, field: str) -> str:
    if not value.strip():
        return ""
    try:
        number = Decimal(value.strip().replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"{field} is not numeric") from error
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return format(number.normalize(), "f")


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    """Return a standardized row or raise a reason that makes it quarantinable."""
    clean = {key.strip().lower(): (value or "").strip() for key, value in row.items() if key}
    for field, value in list(clean.items()):
        if field in DATE_COLUMNS:
            clean[field] = normalize_date(value, field)
        elif field in SKU_COLUMNS or field in STORE_COLUMNS:
            if not value:
                raise ValueError(f"{field} is required")
            clean[field] = normalize_key(value, field)
        elif field in UNIT_COLUMNS and value:
            standardized = UNIT_MAP.get(normalize_label(value))
            if not standardized:
                raise ValueError(f"{field} is not a supported base unit")
            clean[field] = standardized
        elif field == "currency_code" and value:
            clean[field] = normalize_label(value)
            if not CURRENCY_PATTERN.fullmatch(clean[field]):
                raise ValueError("currency_code must be ISO-4217 alpha-3")
        elif field in STATUS_COLUMNS and value:
            standardized = STATUS_MAP[field].get(normalize_label(value))
            if not standardized:
                raise ValueError(f"{field} is not an approved status")
            clean[field] = standardized
        elif field in {"category", "subcategory", "brand", "store_type", "region", "format"} and value:
            clean[field] = normalize_label(value).replace(" ", "_")
        elif field.endswith(NUMERIC_SUFFIXES):
            clean[field] = normalize_decimal(value, field)
        elif value:
            clean[field] = re.sub(r"\s+", " ", value)
    return clean


def record_hash(row: Dict[str, str]) -> str:
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def csv_files(input_dir: Path) -> Iterable[Path]:
    return (path for path in sorted(input_dir.rglob("*.csv")) if path.is_file())


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    if not fields:
        return
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def stage(input_dir: Path, output_dir: Path, run_id: str) -> Path:
    """Create clean staging tables plus quarantines and a transformation manifest."""
    input_root = input_dir.resolve()
    if not input_root.is_dir():
        raise ValueError(f"Input directory does not exist: {input_root}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id or ""):
        raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores")
    files = list(csv_files(input_root))
    if not files:
        raise ValueError(f"No CSV files found in: {input_root}")

    run_root = output_dir.resolve() / f"run_id={run_id}"
    if run_root.exists():
        raise ValueError(f"Run output already exists and will not be overwritten: {run_root}")
    staged_by_entity: Dict[str, List[Dict[str, str]]] = {}
    quarantined_by_entity: Dict[str, List[Dict[str, str]]] = {}
    started_at = datetime.now(timezone.utc).isoformat()

    for source_file in files:
        entity = source_file.stem.lower()
        with source_file.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames:
                quarantined_by_entity.setdefault(entity, []).append({
                    "source_file": str(source_file.relative_to(input_root)),
                    "source_row_number": "1",
                    "source_record_hash": "",
                    "quarantine_reason": "CSV has no header row",
                    "raw_record": "{}",
                    "staging_run_id": run_id,
                })
                continue
            for line_number, raw_row in enumerate(reader, start=2):
                raw_record = {key: value or "" for key, value in raw_row.items() if key}
                trace = {
                    "source_file": str(source_file.relative_to(input_root)),
                    "source_row_number": str(line_number),
                    "source_record_hash": record_hash(raw_record),
                    "staging_run_id": run_id,
                    "staged_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    clean = normalize_row(raw_record)
                    clean.update(trace)
                    staged_by_entity.setdefault(entity, []).append(clean)
                except ValueError as error:
                    quarantine = dict(trace)
                    quarantine["quarantine_reason"] = str(error)
                    quarantine["raw_record"] = json.dumps(raw_record, sort_keys=True)
                    quarantined_by_entity.setdefault(entity, []).append(quarantine)

    table_summary = []
    for entity in sorted(set(staged_by_entity) | set(quarantined_by_entity) | {path.stem.lower() for path in files}):
        clean_rows = staged_by_entity.get(entity, [])
        quarantine_rows = quarantined_by_entity.get(entity, [])
        if clean_rows:
            write_csv(run_root / "staging" / f"stg_{entity}.csv", clean_rows)
        if quarantine_rows:
            write_csv(run_root / "quarantine" / f"quarantine_{entity}.csv", quarantine_rows)
        table_summary.append({
            "entity": entity,
            "staged_row_count": len(clean_rows),
            "quarantined_row_count": len(quarantine_rows),
            "status": "completed_with_quarantine" if quarantine_rows else "succeeded",
        })

    manifest = {
        "manifest_version": "1.0.0",
        "pipeline": "stockguard_inventory_staging",
        "run_id": run_id,
        "input_dir": str(input_root),
        "run_started_at_utc": started_at,
        "run_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_staged_rows": sum(item["staged_row_count"] for item in table_summary),
        "total_quarantined_rows": sum(item["quarantined_row_count"] for item in table_summary),
        "tables": table_summary,
    }
    manifest_path = run_root / "staging_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Raw CSV landing directory or its files/ child.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Staging output root.")
    parser.add_argument("--run-id", required=True, help="Unique staging run identifier.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = stage(args.input_dir, args.output_dir, args.run_id)
    except ValueError as error:
        print(f"Staging failed: {error}", file=sys.stderr)
        return 1
    print(f"Staging manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
