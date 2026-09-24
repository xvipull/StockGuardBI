#!/usr/bin/env python3
"""Land raw inventory extracts unchanged and produce an auditable run manifest.

The command is deliberately file-based so it can run locally, in a scheduler, or
before a PySpark transformation job. It preserves each file byte-for-byte via
``copy2`` and validates CSV headers against the documented source contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "inventory_snapshots": (
        "business_date", "sku_id", "store_id", "on_hand_qty", "allocated_qty",
        "unavailable_qty", "in_transit_qty", "unit_cost",
    ),
    "sales": (
        "sale_date", "sku_id", "store_id", "fulfilled_qty", "units_sold_qty",
        "return_qty",
    ),
    "receipts": (
        "receipt_id", "receipt_line_id", "receipt_date", "sku_id", "store_id",
        "received_qty",
    ),
    "transfers": (
        "transfer_id", "transfer_line_id", "transfer_date", "sku_id",
        "origin_store_id", "destination_store_id", "transfer_qty", "transfer_status",
    ),
    "reorder_parameters": (
        "sku_id", "store_id", "effective_from_date",
    ),
    "products": (
        "sku_id", "sku_name", "category", "base_uom", "product_status",
    ),
    "stores": (
        "store_id", "store_name", "store_type", "store_status",
    ),
    "calendar": (
        "calendar_date", "day_of_week", "week_start_date", "month_start_date",
        "is_working_day",
    ),
}


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest without loading a source file in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entity_for(path: Path) -> str:
    """Map a contract file name to its source entity."""
    return path.stem.lower()


def csv_schema_check(path: Path, entity: str) -> Dict[str, object]:
    """Validate a CSV header and return an explicit, serializable result."""
    required = REQUIRED_COLUMNS.get(entity)
    if required is None:
        return {
            "status": "warning",
            "message": "No registered schema for file; preserved without CSV schema validation.",
            "required_columns": [],
            "missing_columns": [],
            "row_count": None,
        }

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            header = reader.fieldnames or []
            normalized_header = [name.strip() for name in header if name]
            missing = [column for column in required if column not in normalized_header]
            row_count = sum(1 for _ in reader)
    except (UnicodeDecodeError, csv.Error, OSError) as error:
        return {
            "status": "failed",
            "message": f"Unable to read CSV: {error}",
            "required_columns": list(required),
            "missing_columns": list(required),
            "row_count": None,
        }

    return {
        "status": "passed" if not missing else "failed",
        "message": "Required columns present." if not missing else "Missing required columns.",
        "required_columns": list(required),
        "missing_columns": missing,
        "row_count": row_count,
    }


def file_metadata(path: Path, input_root: Path) -> Dict[str, object]:
    """Collect immutable source-file attributes required for raw lineage."""
    stats = path.stat()
    entity = entity_for(path)
    schema = (
        csv_schema_check(path, entity)
        if path.suffix.lower() == ".csv"
        else {
            "status": "warning",
            "message": "Non-CSV file preserved; schema validation is not implemented for this format.",
            "required_columns": [],
            "missing_columns": [],
            "row_count": None,
        }
    )
    return {
        "entity": entity,
        "source_path": str(path.relative_to(input_root)),
        "file_name": path.name,
        "file_extension": path.suffix.lower(),
        "size_bytes": stats.st_size,
        "modified_at_utc": datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": sha256_file(path),
        "schema_check": schema,
    }


def source_files(input_root: Path) -> Iterable[Path]:
    """Yield regular source files in a stable order, excluding generated manifests."""
    return (
        path for path in sorted(input_root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "source_manifest.yml"}
    )


def ingest(input_dir: Path, output_dir: Path, run_id: str, allow_invalid: bool = False) -> Path:
    """Copy source files unchanged to a raw landing run and write ``run_manifest.json``.

    Raises ``ValueError`` only for invalid parameters or empty input. Schema
    failures remain visible in the manifest and make the run status failed.
    """
    input_root = input_dir.resolve()
    if not input_root.is_dir():
        raise ValueError(f"Input directory does not exist: {input_root}")
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in run_id):
        raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores")

    files = list(source_files(input_root))
    if not files:
        raise ValueError(f"No source files found in: {input_root}")

    run_root = output_dir.resolve() / f"run_id={run_id}"
    if run_root.exists():
        raise ValueError(f"Run output already exists and will not be overwritten: {run_root}")
    landing_dir = run_root / "files"
    landed_files: List[Dict[str, object]] = []
    run_started = datetime.now(timezone.utc)

    for file_path in files:
        metadata = file_metadata(file_path, input_root)
        destination = landing_dir / file_path.relative_to(input_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
        metadata["landed_path"] = str(destination.relative_to(run_root))
        landed_files.append(metadata)

    failed_checks = [
        file["source_path"] for file in landed_files
        if file["schema_check"]["status"] == "failed"
    ]
    status = "succeeded" if not failed_checks else "completed_with_schema_failures"
    manifest = {
        "manifest_version": "1.0.0",
        "pipeline": "stockguard_raw_inventory_ingestion",
        "run_id": run_id,
        "status": status,
        "run_started_at_utc": run_started.isoformat(),
        "run_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_root),
        "landing_dir": str(landing_dir),
        "file_count": len(landed_files),
        "schema_failure_count": len(failed_checks),
        "schema_failures": failed_checks,
        "files": landed_files,
    }
    manifest_path = run_root / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if failed_checks and not allow_invalid:
        raise RuntimeError(
            f"Schema checks failed for {', '.join(failed_checks)}. "
            f"Raw files and manifest were preserved at {run_root}."
        )
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing raw source extracts.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Raw landing root; a run_id partition is created.")
    parser.add_argument("--run-id", required=True, help="Unique run identifier: letters, numbers, hyphens, underscores.")
    parser.add_argument("--allow-invalid", action="store_true", help="Return success despite schema failures after manifest creation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = ingest(args.input_dir, args.output_dir, args.run_id, args.allow_invalid)
    except (ValueError, RuntimeError) as error:
        print(f"Ingestion failed: {error}", file=sys.stderr)
        return 1
    print(f"Raw ingestion manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
