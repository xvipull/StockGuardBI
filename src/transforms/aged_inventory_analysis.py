#!/usr/bin/env python3
"""Build aged inventory, movement, excess-value, and reconciliation outputs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


ZERO = Decimal("0")
DEFAULT_TOLERANCE = Decimal("0.01")


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


def amount(row: Dict[str, str], field: str, default: Optional[Decimal] = None) -> Optional[Decimal]:
    value = (row.get(field) or "").strip()
    if not value:
        return default
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Invalid {field} value: {value}") from error
    if not result.is_finite():
        raise ValueError(f"Non-finite {field} value: {value}")
    return result


def date_value(row: Dict[str, str], field: str) -> Optional[date]:
    value = (row.get(field) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid {field} date: {value}") from error


def age_bucket(age_days: Optional[int]) -> str:
    if age_days is None or age_days < 0:
        return "UNAGED"
    if age_days <= 30:
        return "00_30_DAYS"
    if age_days <= 60:
        return "31_60_DAYS"
    if age_days <= 90:
        return "61_90_DAYS"
    if age_days <= 180:
        return "91_180_DAYS"
    if age_days <= 365:
        return "181_365_DAYS"
    return "OVER_365_DAYS"


def analyze(curated_run_dir: Path, risk_run_dir: Path, sales_file: Path, output_dir: Path, run_id: str, tolerance: Decimal = DEFAULT_TOLERANCE) -> Path:
    curated_root = curated_run_dir.resolve()
    risk_root = risk_run_dir.resolve()
    inventory = read_csv(curated_root / "fact_inventory_daily.csv")
    risk_rows = read_csv(risk_root / "mart_stock_risk_daily.csv")
    sales = read_csv(sales_file.resolve())
    if not inventory or not risk_rows:
        raise ValueError("Curated inventory fact and stock-risk mart are both required and cannot be empty")
    if not sales:
        raise ValueError("A sales fact CSV is required to calculate movement indicators")
    if tolerance < ZERO:
        raise ValueError("Reconciliation tolerance cannot be negative")

    sales_dates: Dict[Tuple[str, str], List[date]] = {}
    for row in sales:
        quantity = amount(row, "units_sold_qty", ZERO) or ZERO
        sold_on = date_value(row, "sale_date")
        if quantity > ZERO and sold_on:
            key = (row.get("sku_id", ""), row.get("store_id", ""))
            sales_dates.setdefault(key, []).append(sold_on)
    for key in sales_dates:
        sales_dates[key].sort()

    risk_index = {
        (row.get("business_date", ""), row.get("sku_id", ""), row.get("store_id", "")): row
        for row in risk_rows
    }
    details: List[Dict[str, str]] = []
    for row in inventory:
        key = (row.get("business_date", ""), row.get("sku_id", ""), row.get("store_id", ""))
        risk = risk_index.get(key)
        if risk is None:
            raise ValueError(f"Stock-risk mart row missing for inventory key: {key}")
        business_date = date_value(row, "business_date")
        inventory_age = amount(row, "inventory_age_days")
        age_days = int(inventory_age) if inventory_age is not None else None
        prior_sales = [
            sold_on for sold_on in sales_dates.get((row.get("sku_id", ""), row.get("store_id", "")), [])
            if business_date and sold_on <= business_date
        ]
        sale_date = max(prior_sales) if prior_sales else None
        no_sale_days = (business_date - sale_date).days if business_date and sale_date else None
        inventory_value = amount(row, "inventory_value")
        if inventory_value is None:
            on_hand = amount(row, "on_hand_qty")
            unit_cost = amount(row, "unit_cost")
            inventory_value = on_hand * unit_cost if on_hand is not None and unit_cost is not None else None
        excess_value = amount(risk, "excess_inventory_value")
        excess_qty = amount(risk, "excess_inventory_qty")
        cost = amount(row, "unit_cost")
        if excess_value is None and excess_qty is not None and cost is not None:
            excess_value = excess_qty * cost

        if no_sale_days is None:
            movement_status = "NO_SALES_HISTORY"
        elif no_sale_days >= 180:
            movement_status = "DEAD_STOCK"
        elif no_sale_days >= 60:
            movement_status = "SLOW_MOVING"
        else:
            movement_status = "RECENT_MOVEMENT"
        aged_stock_flag = age_days is not None and age_days > 90
        critical_aged_stock_flag = age_days is not None and age_days > 180
        details.append({
            **row,
            "inventory_age_days": "" if age_days is None else str(age_days),
            "age_bucket": age_bucket(age_days),
            "last_sale_date": "" if sale_date is None else sale_date.isoformat(),
            "days_since_last_sale": "" if no_sale_days is None else str(no_sale_days),
            "movement_status": movement_status,
            "aged_stock_flag": str(aged_stock_flag).lower(),
            "critical_aged_stock_flag": str(critical_aged_stock_flag).lower(),
            "aged_inventory_value": "" if inventory_value is None else format(inventory_value, "f"),
            "excess_inventory_qty": "" if excess_qty is None else format(excess_qty, "f"),
            "excess_inventory_value": "" if excess_value is None else format(excess_value, "f"),
            "risk_setup_exception": risk.get("risk_setup_exception", ""),
        })

    source_inventory_value = sum((amount(row, "inventory_value") or ZERO) for row in inventory)
    aged_inventory_value = sum((amount(row, "aged_inventory_value") or ZERO) for row in details)
    source_excess_value = sum((amount(row, "excess_inventory_value") or ZERO) for row in risk_rows)
    aged_excess_value = sum((amount(row, "excess_inventory_value") or ZERO) for row in details)
    inventory_variance = aged_inventory_value - source_inventory_value
    excess_variance = aged_excess_value - source_excess_value
    reconciliation = [
        {"measure": "inventory_value", "source_total": format(source_inventory_value, "f"), "aged_mart_total": format(aged_inventory_value, "f"), "variance": format(inventory_variance, "f"), "tolerance": format(tolerance, "f"), "status": "PASS" if abs(inventory_variance) <= tolerance else "EXCEPTION"},
        {"measure": "excess_inventory_value", "source_total": format(source_excess_value, "f"), "aged_mart_total": format(aged_excess_value, "f"), "variance": format(excess_variance, "f"), "tolerance": format(tolerance, "f"), "status": "PASS" if abs(excess_variance) <= tolerance else "EXCEPTION"},
    ]

    root = output_dir.resolve() / f"run_id={run_id}"
    if root.exists():
        raise ValueError(f"Aged-inventory output exists and will not be overwritten: {root}")
    write_csv(root / "mart_aged_inventory_daily.csv", details)
    buckets: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
    for row in details:
        key = (row["business_date"], row["age_bucket"])
        aggregate = buckets.setdefault(key, {"on_hand_qty": ZERO, "aged_inventory_value": ZERO, "sku_store_count": ZERO})
        aggregate["on_hand_qty"] += amount(row, "on_hand_qty", ZERO) or ZERO
        aggregate["aged_inventory_value"] += amount(row, "aged_inventory_value", ZERO) or ZERO
        aggregate["sku_store_count"] += 1
    bucket_rows = [
        {"business_date": key[0], "age_bucket": key[1], **{field: format(value, "f") for field, value in measures.items()}}
        for key, measures in sorted(buckets.items())
    ]
    write_csv(root / "mart_aged_inventory_by_bucket.csv", bucket_rows)
    exception_rows = [row for row in details if row.get("risk_setup_exception")]
    write_csv(root / "aged_inventory_value_exceptions.csv", exception_rows)
    write_csv(root / "aged_inventory_value_reconciliation.csv", reconciliation)
    manifest = {
        "report_version": "1.0.0", "pipeline": "stockguard_aged_inventory_analysis", "run_id": run_id,
        "curated_run_dir": str(curated_root), "risk_run_dir": str(risk_root), "sales_file": str(sales_file.resolve()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "row_count": len(details),
        "aged_stock_count": sum(row["aged_stock_flag"] == "true" for row in details),
        "critical_aged_stock_count": sum(row["critical_aged_stock_flag"] == "true" for row in details),
        "slow_moving_count": sum(row["movement_status"] == "SLOW_MOVING" for row in details),
        "dead_stock_count": sum(row["movement_status"] == "DEAD_STOCK" for row in details),
        "value_reconciliation_status": "passed" if all(row["status"] == "PASS" for row in reconciliation) else "failed",
        "reconciliations": reconciliation,
    }
    manifest_path = root / "aged_inventory_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-run-dir", type=Path, required=True)
    parser.add_argument("--risk-run-dir", type=Path, required=True)
    parser.add_argument("--sales-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tolerance", default="0.01")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tolerance = Decimal(args.tolerance)
        manifest_path = analyze(args.curated_run_dir, args.risk_run_dir, args.sales_file, args.output_dir, args.run_id, tolerance)
    except (ValueError, InvalidOperation) as error:
        print(f"Aged inventory analysis failed: {error}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Aged inventory manifest: {manifest_path} ({manifest['value_reconciliation_status']})")
    return 0 if manifest["value_reconciliation_status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
