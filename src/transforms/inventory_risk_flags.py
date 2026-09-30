#!/usr/bin/env python3
"""Build governed stockout and excess inventory risk flags."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Sequence


ZERO = Decimal("0")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    if rows:
        fields = sorted(set(fields) | set(rows[0]))
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def decimal_field(row: Dict[str, str], name: str) -> Decimal:
    value = (row.get(name) or "").strip()
    if not value:
        raise ValueError(f"Required risk input is missing: {name}")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Risk input {name} is not numeric: {value}") from error


def compute_flag(row: Dict[str, str]) -> Dict[str, str]:
    """Classify one SKU-store-day record with explicit missing-setup behavior."""
    available = decimal_field(row, "available_inventory_qty")
    on_hand = decimal_field(row, "on_hand_qty")
    average_daily_demand = decimal_field(row, "average_daily_demand_qty")
    lead_time = Decimal(row["lead_time_days"]) if row.get("lead_time_days") else None
    safety_stock_days = Decimal(row["safety_stock_days"]) if row.get("safety_stock_days") else None
    target_stock = Decimal(row["target_stock_qty"]) if row.get("target_stock_qty") else None
    unit_cost = Decimal(row["unit_cost"]) if row.get("unit_cost") else None

    supplied = [available, on_hand, average_daily_demand, lead_time, safety_stock_days, target_stock, unit_cost]
    if any(value is not None and value < ZERO for value in supplied):
        raise ValueError("Risk inputs cannot be negative")
    demand_status = (row.get("demand_status") or "").strip().upper()
    active_status = (row.get("product_status") or "ACTIVE").strip().upper()
    active = active_status in {"ACTIVE", "LIVE"}
    has_demand = average_daily_demand > ZERO and demand_status not in {"ZERO_DEMAND", "NEWLY_LAUNCHED_ZERO_DEMAND", "INCOMPLETE_HISTORY"}
    dos = available / average_daily_demand if has_demand else None
    reorder_cover = lead_time + safety_stock_days if lead_time is not None and safety_stock_days is not None else None
    computed_target = average_daily_demand * reorder_cover if reorder_cover is not None else None
    target = target_stock if target_stock is not None and target_stock > ZERO else computed_target
    active_stockout = active and available <= ZERO
    stockout_risk = active and has_demand and dos is not None and reorder_cover is not None and dos < reorder_cover
    low_cover = active and has_demand and dos is not None and reorder_cover is not None and dos < reorder_cover + Decimal("7")
    excess_qty = max(available - target, ZERO) if target is not None else None
    excess_value = excess_qty * unit_cost if excess_qty is not None and unit_cost is not None else None
    excess_flag = active and excess_qty is not None and excess_qty > ZERO

    if not active:
        risk_reason = "INACTIVE_PRODUCT"
    elif active_stockout:
        risk_reason = "ACTIVE_STOCKOUT"
    elif not has_demand:
        risk_reason = "DEMAND_UNAVAILABLE_OR_ZERO"
    elif reorder_cover is None:
        risk_reason = "MISSING_REORDER_PARAMETERS"
    elif stockout_risk:
        risk_reason = "COVER_BELOW_LEAD_TIME_PLUS_SAFETY_STOCK"
    elif low_cover:
        risk_reason = "COVER_WITHIN_SEVEN_DAY_BUFFER"
    else:
        risk_reason = "COVER_ABOVE_REPLENISHMENT_BUFFER"

    result = dict(row)
    result.update({
        "days_of_supply": "" if dos is None else format(dos, "f"),
        "reorder_cover_days": "" if reorder_cover is None else format(reorder_cover, "f"),
        "effective_target_stock_qty": "" if target is None else format(target, "f"),
        "stockout_risk_flag": str(bool(stockout_risk)).lower(),
        "active_stockout_flag": str(bool(active_stockout)).lower(),
        "low_cover_flag": str(bool(low_cover)).lower(),
        "excess_inventory_flag": str(bool(excess_flag)).lower(),
        "excess_inventory_qty": "" if excess_qty is None else format(excess_qty, "f"),
        "excess_inventory_value": "" if excess_value is None else format(excess_value, "f"),
        "risk_reason": risk_reason,
        "risk_setup_exception": ";".join(
            reason for condition, reason in (
                (reorder_cover is None, "MISSING_REORDER_PARAMETERS"),
                (target is None, "TARGET_STOCK_UNAVAILABLE"),
                (unit_cost is None, "MISSING_UNIT_COST"),
            ) if condition
        ),
    })
    return result


def build_risk_mart(curated_run_dir: Path, output_dir: Path, run_id: str) -> Path:
    run_dir = curated_run_dir.resolve()
    inventory = read_csv(run_dir / "fact_inventory_daily.csv")
    if not inventory:
        raise ValueError("fact_inventory_daily.csv is required and cannot be empty")
    if not run_id or not all(character.isalnum() or character in "-_" for character in run_id):
        raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores")
    if "average_daily_demand_qty" not in inventory[0]:
        raise ValueError("Inventory fact must be enriched with average_daily_demand_qty from the rolling demand step before risk scoring")

    reorder = {row["reorder_parameter_key"]: row for row in read_csv(run_dir / "dim_reorder_parameter.csv") if row.get("reorder_parameter_key")}
    products = {row["sku_key"]: row for row in read_csv(run_dir / "dim_sku.csv") if row.get("sku_key")}
    result_rows = []
    for row in inventory:
        parameters = reorder.get(row.get("reorder_parameter_key", ""), {})
        product = products.get(row.get("sku_key", ""), {})
        merged = dict(row)
        merged["lead_time_days"] = parameters.get("lead_time_days", "")
        merged["safety_stock_days"] = parameters.get("safety_stock_days", "")
        merged["target_stock_qty"] = parameters.get("target_stock_qty", "")
        merged["product_status"] = product.get("product_status", "ACTIVE")
        try:
            result_rows.append(compute_flag(merged))
        except ValueError as error:
            exception = dict(merged)
            exception["risk_exception_reason"] = str(error)
            result_rows.append({**exception, "stockout_risk_flag": "", "active_stockout_flag": "", "low_cover_flag": "", "excess_inventory_flag": "", "risk_reason": "INPUT_EXCEPTION", "risk_setup_exception": "INPUT_EXCEPTION"})

    root = output_dir.resolve() / f"run_id={run_id}"
    if root.exists():
        raise ValueError(f"Risk mart output exists and will not be overwritten: {root}")
    write_csv(root / "mart_stock_risk_daily.csv", result_rows)
    flagged = [row for row in result_rows if row.get("risk_reason") == "INPUT_EXCEPTION"]
    write_csv(root / "stock_risk_input_exceptions.csv", flagged)
    summary = {
        "manifest_version": "1.0.0", "pipeline": "stockguard_inventory_risk_flags", "run_id": run_id,
        "curated_run_dir": str(run_dir), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": len(result_rows), "input_exception_count": len(flagged),
        "stockout_risk_count": sum(row.get("stockout_risk_flag") == "true" for row in result_rows),
        "active_stockout_count": sum(row.get("active_stockout_flag") == "true" for row in result_rows),
        "low_cover_count": sum(row.get("low_cover_flag") == "true" for row in result_rows),
        "excess_inventory_count": sum(row.get("excess_inventory_flag") == "true" for row in result_rows),
    }
    manifest = root / "stock_risk_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = build_risk_mart(args.curated_run_dir, args.output_dir, args.run_id)
    except ValueError as error:
        print(f"Risk mart build failed: {error}", file=sys.stderr)
        return 1
    print(f"Stock risk mart manifest: {manifest}")
    return 0


if __name__ == "__main__":
    main()
