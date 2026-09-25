#!/usr/bin/env python3
"""Materialize StockGuard's inventory analytics star schema from staging CSVs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def stable_key(namespace: str, *values: str) -> str:
    """Create a deterministic, reproducible surrogate key from a business key."""
    value = "|".join([namespace, *[value or "" for value in values]])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def date_key(value: str) -> str:
    return value.replace("-", "") if value else ""


def sum_decimal(rows: List[Dict[str, str]], field: str) -> str:
    """Sum a fact measure while preserving empty-as-unknown semantics."""
    values = [Decimal(row[field]) for row in rows if row.get(field)]
    return format(sum(values), "f") if values else ""


def dimension_dates(calendar: List[Dict[str, str]], table_rows: Dict[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
    by_date = {row["calendar_date"]: dict(row) for row in calendar if row.get("calendar_date")}
    for field, entities in {
        "business_date": ["inventory_snapshots"], "sale_date": ["sales"], "receipt_date": ["receipts"],
        "transfer_date": ["transfers"], "effective_from_date": ["reorder_parameters"],
    }.items():
        for entity in entities:
            for row in table_rows[entity]:
                if row.get(field) and row[field] not in by_date:
                    by_date[row[field]] = {"calendar_date": row[field]}
    return [
        {"date_key": date_key(value), **row}
        for value, row in sorted(by_date.items())
    ]


def latest_reorder_key(parameters: List[Dict[str, str]], sku_id: str, store_id: str, business_date: str) -> str:
    eligible = [
        row for row in parameters
        if row.get("sku_id") == sku_id and row.get("store_id") == store_id
        and row.get("effective_from_date", "") <= business_date
        and (not row.get("effective_to_date") or row["effective_to_date"] >= business_date)
    ]
    if not eligible:
        return ""
    chosen = max(eligible, key=lambda row: row.get("effective_from_date", ""))
    return chosen["reorder_parameter_key"]


def materialize(staging_run_dir: Path, output_dir: Path, run_id: str) -> Path:
    """Build dimensions and facts in a non-overwriting curated run partition."""
    staging_dir = staging_run_dir.resolve() / "staging"
    if not staging_dir.is_dir():
        raise ValueError(f"Staging directory not found: {staging_dir}")
    tables = {
        entity: read_csv(staging_dir / f"stg_{entity}.csv")
        for entity in ("inventory_snapshots", "sales", "receipts", "transfers", "reorder_parameters", "products", "stores", "calendar")
    }
    required = ("inventory_snapshots", "sales", "receipts", "transfers", "reorder_parameters", "products", "stores", "calendar")
    missing = [entity for entity in required if not tables[entity]]
    if missing:
        raise ValueError(f"Required staging tables are empty or missing: {', '.join(missing)}")
    if not run_id or not all(character.isalnum() or character in "-_" for character in run_id):
        raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores")

    run_root = output_dir.resolve() / f"run_id={run_id}"
    if run_root.exists():
        raise ValueError(f"Curated output exists and will not be overwritten: {run_root}")

    dim_sku = []
    for row in tables["products"]:
        dimension = dict(row)
        dimension["sku_key"] = stable_key("sku", row["sku_id"])
        dim_sku.append(dimension)
    dim_store = []
    for row in tables["stores"]:
        dimension = dict(row)
        dimension["store_key"] = stable_key("store", row["store_id"])
        dim_store.append(dimension)
    dim_reorder_parameter = []
    for row in tables["reorder_parameters"]:
        dimension = dict(row)
        dimension["reorder_parameter_key"] = stable_key("reorder", row["sku_id"], row["store_id"], row["effective_from_date"])
        dimension["sku_key"] = stable_key("sku", row["sku_id"])
        dimension["store_key"] = stable_key("store", row["store_id"])
        dim_reorder_parameter.append(dimension)
    dim_date = dimension_dates(tables["calendar"], tables)

    sku_keys = {row["sku_id"]: row["sku_key"] for row in dim_sku}
    store_keys = {row["store_id"]: row["store_key"] for row in dim_store}
    facts = {"fact_inventory_daily": [], "fact_sales_daily": [], "fact_receipts": [], "fact_transfers": []}
    for row in tables["inventory_snapshots"]:
        fact = dict(row)
        fact.update({
            "inventory_daily_key": stable_key("inventory", row["business_date"], row["sku_id"], row["store_id"]),
            "date_key": date_key(row["business_date"]), "sku_key": sku_keys[row["sku_id"]], "store_key": store_keys[row["store_id"]],
            "reorder_parameter_key": latest_reorder_key(dim_reorder_parameter, row["sku_id"], row["store_id"], row["business_date"]),
        })
        facts["fact_inventory_daily"].append(fact)
    sales_by_key: Dict[tuple, List[Dict[str, str]]] = {}
    for row in tables["sales"]:
        sales_by_key.setdefault((row["sale_date"], row["sku_id"], row["store_id"]), []).append(row)
    for (sale_date, sku_id, store_id), rows in sales_by_key.items():
        row = rows[0]
        fact = dict(row)
        for measure in ("requested_qty", "fulfilled_qty", "units_sold_qty", "return_qty", "net_sales_amount", "cogs_amount"):
            fact[measure] = sum_decimal(rows, measure)
        fact["source_record_hash"] = "|".join(sorted(source["source_record_hash"] for source in rows))
        fact.update({
            "sales_daily_key": stable_key("sales", sale_date, sku_id, store_id),
            "date_key": date_key(sale_date), "sku_key": sku_keys[sku_id], "store_key": store_keys[store_id],
        })
        facts["fact_sales_daily"].append(fact)
    for row in tables["receipts"]:
        fact = dict(row)
        fact.update({
            "receipt_fact_key": stable_key("receipt", row["receipt_id"], row["receipt_line_id"]),
            "date_key": date_key(row["receipt_date"]), "sku_key": sku_keys[row["sku_id"]], "store_key": store_keys[row["store_id"]],
        })
        facts["fact_receipts"].append(fact)
    for row in tables["transfers"]:
        fact = dict(row)
        fact.update({
            "transfer_fact_key": stable_key("transfer", row["transfer_id"], row["transfer_line_id"]),
            "date_key": date_key(row["transfer_date"]), "sku_key": sku_keys[row["sku_id"]],
            "origin_store_key": store_keys[row["origin_store_id"]], "destination_store_key": store_keys[row["destination_store_id"]],
        })
        facts["fact_transfers"].append(fact)

    outputs = {
        "dim_date": dim_date, "dim_sku": dim_sku, "dim_store": dim_store,
        "dim_reorder_parameter": dim_reorder_parameter, **facts,
    }
    for table_name, rows in outputs.items():
        write_csv(run_root / f"{table_name}.csv", rows)
    manifest = {
        "manifest_version": "1.0.0", "pipeline": "stockguard_inventory_star_schema", "run_id": run_id,
        "staging_run_dir": str(staging_run_dir.resolve()), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tables": [{"table": table, "row_count": len(rows)} for table, rows in outputs.items()],
    }
    manifest_path = run_root / "star_schema_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = materialize(args.staging_run_dir, args.output_dir, args.run_id)
    except ValueError as error:
        print(f"Star schema materialization failed: {error}", file=sys.stderr)
        return 1
    print(f"Star schema manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
