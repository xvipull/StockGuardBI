#!/usr/bin/env python3
"""Run governed data-quality controls over StockGuard staging outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "inventory_snapshots": ("business_date", "sku_id", "store_id", "on_hand_qty", "allocated_qty", "unavailable_qty", "in_transit_qty", "unit_cost"),
    "sales": ("sale_date", "sku_id", "store_id", "fulfilled_qty", "units_sold_qty", "return_qty"),
    "receipts": ("receipt_id", "receipt_line_id", "receipt_date", "sku_id", "store_id", "received_qty"),
    "transfers": ("transfer_id", "transfer_line_id", "transfer_date", "sku_id", "origin_store_id", "destination_store_id", "transfer_qty", "transfer_status"),
    "reorder_parameters": ("sku_id", "store_id", "effective_from_date"),
    "products": ("sku_id", "sku_name", "category", "base_uom", "product_status"),
    "stores": ("store_id", "store_name", "store_type", "store_status"),
    "calendar": ("calendar_date", "day_of_week", "week_start_date", "month_start_date", "is_working_day"),
}
DATE_FIELD_BY_ENTITY = {
    "inventory_snapshots": "business_date", "sales": "sale_date", "receipts": "receipt_date",
    "transfers": "transfer_date", "reorder_parameters": "effective_from_date", "calendar": "calendar_date",
}
QUANTITY_SUFFIXES = ("_qty", "_point_qty")
VALUE_SUFFIXES = ("_amount", "_cost")


def read_table(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames or [], list(reader)


def result(control: str, entity: str, status: str, observed: object, threshold: object, detail: str) -> Dict[str, object]:
    return {"control": control, "entity": entity, "status": status, "observed": observed, "threshold": threshold, "detail": detail}


def is_decimal(value: str) -> bool:
    try:
        return Decimal(value).is_finite()
    except (InvalidOperation, ValueError):
        return False


def check_entity(entity: str, fields: List[str], rows: List[Dict[str, str]], as_of: date, max_lag_days: int) -> List[Dict[str, object]]:
    checks: List[Dict[str, object]] = []
    required = REQUIRED_COLUMNS[entity]
    missing_fields = [field for field in required if field not in fields]
    checks.append(result("required_columns", entity, "passed" if not missing_fields else "failed", missing_fields, [], "All documented required columns must be present."))

    null_counts = {field: sum(not (row.get(field) or "").strip() for row in rows) for field in required if field in fields}
    null_violations = {field: count for field, count in null_counts.items() if count > 0}
    checks.append(result("null_limits", entity, "passed" if not null_violations else "failed", null_violations, 0, "Required fields allow zero null records."))

    invalid_quantities: List[str] = []
    negative_quantities: List[str] = []
    negative_values: List[str] = []
    numeric_fields = [field for field in fields if field.endswith(QUANTITY_SUFFIXES + VALUE_SUFFIXES)]
    for row_number, row in enumerate(rows, start=2):
        for field in numeric_fields:
            value = (row.get(field) or "").strip()
            if not value:
                continue
            if not is_decimal(value):
                if field.endswith(QUANTITY_SUFFIXES):
                    invalid_quantities.append(f"row {row_number}:{field}")
                else:
                    negative_values.append(f"row {row_number}:{field}=non-numeric")
                continue
            if Decimal(value) < 0:
                (negative_quantities if field.endswith(QUANTITY_SUFFIXES) else negative_values).append(f"row {row_number}:{field}")
    checks.append(result("invalid_quantities", entity, "passed" if not invalid_quantities else "failed", invalid_quantities, [], "Quantity fields must be finite decimals."))
    checks.append(result("negative_quantities", entity, "passed" if not negative_quantities else "failed", negative_quantities, [], "Quantity fields must be non-negative in staging."))
    checks.append(result("negative_values", entity, "passed" if not negative_values else "failed", negative_values, [], "Cost and amount fields must be non-negative in staging."))

    if entity == "inventory_snapshots" and not missing_fields:
        keys = [(row.get("business_date", ""), row.get("sku_id", ""), row.get("store_id", "")) for row in rows]
        duplicates = ["|".join(key) for key, count in Counter(keys).items() if count > 1]
        checks.append(result("duplicate_snapshot_keys", entity, "passed" if not duplicates else "failed", duplicates, [], "Snapshot key is business_date + sku_id + store_id."))

    date_field = DATE_FIELD_BY_ENTITY.get(entity)
    if date_field and date_field in fields and rows:
        valid_dates = []
        for row in rows:
            try:
                valid_dates.append(date.fromisoformat(row[date_field]))
            except (ValueError, TypeError):
                pass
        latest = max(valid_dates) if valid_dates else None
        lag_days = (as_of - latest).days if latest else None
        checks.append(result("freshness", entity, "passed" if lag_days is not None and lag_days <= max_lag_days else "failed", {"latest_date": latest.isoformat() if latest else None, "lag_days": lag_days}, max_lag_days, "Latest business date must be within the permitted lag."))
    return checks


def source_row_count(source_file: Path) -> int:
    with source_file.open("r", encoding="utf-8-sig", newline="") as source:
        return sum(1 for _ in csv.DictReader(source))


def quality_check(staging_run_dir: Path, report_dir: Path, as_of_date: str, max_lag_days: int, source_dir: Optional[Path] = None) -> Path:
    """Generate a report from a staging run, optionally reconciling to raw CSV input."""
    run_dir = staging_run_dir.resolve()
    staging_dir = run_dir / "staging"
    if not staging_dir.is_dir():
        raise ValueError(f"Staging directory not found: {staging_dir}")
    try:
        as_of = date.fromisoformat(as_of_date)
    except ValueError as error:
        raise ValueError("as_of_date must use YYYY-MM-DD") from error
    if max_lag_days < 0:
        raise ValueError("max_lag_days must be zero or greater")

    checks: List[Dict[str, object]] = []
    tables: Dict[str, Tuple[List[str], List[Dict[str, str]]]] = {}
    for entity in REQUIRED_COLUMNS:
        table_path = staging_dir / f"stg_{entity}.csv"
        if not table_path.is_file():
            checks.append(result("required_staging_table", entity, "failed", "missing", "present", "Required staging table is absent."))
            continue
        fields, rows = read_table(table_path)
        tables[entity] = (fields, rows)
        checks.extend(check_entity(entity, fields, rows, as_of, max_lag_days))

    products = {row.get("sku_id") for row in tables.get("products", ([], []))[1]}
    stores = {row.get("store_id") for row in tables.get("stores", ([], []))[1]}
    for entity, (_, rows) in tables.items():
        if entity in {"products", "stores", "calendar"}:
            continue
        missing_product = [f"row {index}" for index, row in enumerate(rows, start=2) if row.get("sku_id") not in products]
        store_columns = [field for field in ("store_id", "origin_store_id", "destination_store_id") if rows and field in rows[0]]
        missing_store = [f"row {index}:{field}" for index, row in enumerate(rows, start=2) for field in store_columns if row.get(field) not in stores]
        checks.append(result("missing_product_master", entity, "passed" if not missing_product else "failed", missing_product, [], "All fact SKU keys must resolve to dim_product."))
        checks.append(result("missing_store_master", entity, "passed" if not missing_store else "failed", missing_store, [], "All fact location keys must resolve to dim_store."))

    if source_dir:
        raw_root = source_dir.resolve()
        for entity, (_, rows) in tables.items():
            source_file = raw_root / f"{entity}.csv"
            quarantine_file = run_dir / "quarantine" / f"quarantine_{entity}.csv"
            source_count = source_row_count(source_file) if source_file.is_file() else None
            quarantine_count = len(read_table(quarantine_file)[1]) if quarantine_file.is_file() else 0
            observed = {"source_rows": source_count, "staged_rows": len(rows), "quarantined_rows": quarantine_count}
            passed = source_count is not None and source_count == len(rows) + quarantine_count
            checks.append(result("row_count_reconciliation", entity, "passed" if passed else "failed", observed, "source = staged + quarantined", "Raw source rows must be fully accounted for."))

    failure_count = sum(check["status"] == "failed" for check in checks)
    report = {
        "report_version": "1.0.0",
        "pipeline": "stockguard_inventory_data_quality",
        "staging_run_dir": str(run_dir),
        "as_of_date": as_of.isoformat(),
        "max_lag_days": max_lag_days,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if failure_count == 0 else "failed",
        "check_count": len(checks),
        "failure_count": failure_count,
        "checks": checks,
    }
    report_path = report_dir.resolve() / f"data_quality_report_{as_of.isoformat()}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-run-dir", type=Path, required=True, help="A staging run_id=<id> directory.")
    parser.add_argument("--report-dir", type=Path, required=True, help="Directory for the data-quality JSON report.")
    parser.add_argument("--as-of-date", required=True, help="Expected latest business date, YYYY-MM-DD.")
    parser.add_argument("--max-lag-days", type=int, default=1, help="Maximum acceptable freshness lag.")
    parser.add_argument("--source-dir", type=Path, help="Optional raw input directory for row-count reconciliation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report_path = quality_check(args.staging_run_dir, args.report_dir, args.as_of_date, args.max_lag_days, args.source_dir)
    except ValueError as error:
        print(f"Data quality failed to run: {error}", file=sys.stderr)
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"Data quality report: {report_path} ({report['status']})")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
