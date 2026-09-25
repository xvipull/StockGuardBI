#!/usr/bin/env python3
"""Reconcile daily inventory balances from the curated StockGuard star schema."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence


ZERO = Decimal("0")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: Sequence[Dict[str, str]], fields: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fields or sorted({field for row in rows for field in row}))
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def decimal_value(row: Dict[str, str], field: str) -> Decimal:
    value = (row.get(field) or "").strip()
    if not value:
        return ZERO
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Invalid decimal in {field}: {value}") from error


def render(value: Decimal) -> str:
    return format(value, "f")


def reconcile(curated_run_dir: Path, output_dir: Path, absolute_tolerance: str = "0.001", percent_tolerance: str = "0.001") -> Path:
    """Reconcile inventory positions and write result and exception CSV outputs.

    A balance passes when absolute variance is no larger than the greater of the
    absolute tolerance and the percentage tolerance applied to closing stock.
    """
    run_dir = curated_run_dir.resolve()
    inventory = read_csv(run_dir / "fact_inventory_daily.csv")
    if not inventory:
        raise ValueError("fact_inventory_daily.csv is required and cannot be empty")
    try:
        absolute = Decimal(absolute_tolerance)
        percentage = Decimal(percent_tolerance)
    except InvalidOperation as error:
        raise ValueError("Tolerances must be decimal values") from error
    if absolute < ZERO or percentage < ZERO:
        raise ValueError("Tolerances must be non-negative")

    sales = read_csv(run_dir / "fact_sales_daily.csv")
    receipts = read_csv(run_dir / "fact_receipts.csv")
    transfers = read_csv(run_dir / "fact_transfers.csv")
    sales_by_key = defaultdict(Decimal)
    receipt_by_key = defaultdict(Decimal)
    inbound_by_key = defaultdict(Decimal)
    outbound_by_key = defaultdict(Decimal)
    for row in sales:
        sales_by_key[(row["sale_date"], row["sku_id"], row["store_id"])] += decimal_value(row, "units_sold_qty")
    for row in receipts:
        receipt_by_key[(row["receipt_date"], row["sku_id"], row["store_id"])] += decimal_value(row, "received_qty")
    for row in transfers:
        quantity = decimal_value(row, "transfer_qty")
        if row.get("transfer_status") != "CANCELLED":
            outbound_by_key[(row["transfer_date"], row["sku_id"], row["origin_store_id"])] += quantity
            if row.get("transfer_status") == "RECEIVED":
                inbound_by_key[(row["transfer_date"], row["sku_id"], row["destination_store_id"])] += quantity

    inventory_by_key = {(row["business_date"], row["sku_id"], row["store_id"]): row for row in inventory}
    results: List[Dict[str, str]] = []
    for business_date, sku_id, store_id in sorted(inventory_by_key):
        row = inventory_by_key[(business_date, sku_id, store_id)]
        prior_date = (date.fromisoformat(business_date) - timedelta(days=1)).isoformat()
        opening_row = inventory_by_key.get((prior_date, sku_id, store_id))
        closing = decimal_value(row, "on_hand_qty")
        key = (business_date, sku_id, store_id)
        receipts_qty = receipt_by_key[key]
        inbound_qty = inbound_by_key[key]
        outbound_qty = outbound_by_key[key]
        sales_qty = sales_by_key[key]
        adjustments_qty = decimal_value(row, "adjustment_qty")
        base = {
            "business_date": business_date, "sku_id": sku_id, "store_id": store_id,
            "closing_stock_qty": render(closing), "receipts_qty": render(receipts_qty),
            "inbound_transfer_qty": render(inbound_qty), "outbound_transfer_qty": render(outbound_qty),
            "sales_qty": render(sales_qty), "adjustments_qty": render(adjustments_qty),
            "absolute_tolerance_qty": render(absolute), "percent_tolerance": render(percentage),
        }
        if not opening_row:
            base.update({
                "opening_stock_qty": "", "expected_closing_stock_qty": "", "variance_qty": "",
                "allowed_variance_qty": "", "reconciliation_status": "NOT_EVALUATED",
                "exception_reason": "No prior-calendar-day closing inventory snapshot",
            })
        else:
            opening = decimal_value(opening_row, "on_hand_qty")
            expected = opening + receipts_qty + inbound_qty - outbound_qty - sales_qty + adjustments_qty
            variance = closing - expected
            allowed = max(absolute, abs(closing) * percentage)
            status = "PASS" if abs(variance) <= allowed else "EXCEPTION"
            base.update({
                "opening_stock_qty": render(opening), "expected_closing_stock_qty": render(expected),
                "variance_qty": render(variance), "allowed_variance_qty": render(allowed),
                "reconciliation_status": status,
                "exception_reason": "" if status == "PASS" else "Closing stock differs from expected balance beyond tolerance",
            })
        results.append(base)

    exceptions = [row for row in results if row["reconciliation_status"] == "EXCEPTION"]
    output_root = output_dir.resolve()
    result_fields = sorted({field for row in results for field in row})
    write_csv(output_root / "inventory_balance_reconciliation.csv", results, result_fields)
    write_csv(output_root / "inventory_balance_exceptions.csv", exceptions, result_fields)
    manifest = {
        "report_version": "1.0.0", "pipeline": "stockguard_inventory_balance_reconciliation",
        "curated_run_dir": str(run_dir), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "absolute_tolerance_qty": render(absolute), "percent_tolerance": render(percentage),
        "records_evaluated": sum(row["reconciliation_status"] != "NOT_EVALUATED" for row in results),
        "not_evaluated_count": sum(row["reconciliation_status"] == "NOT_EVALUATED" for row in results),
        "exception_count": len(exceptions), "status": "passed" if not exceptions else "failed",
    }
    manifest_path = output_root / "inventory_balance_reconciliation_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--absolute-tolerance", default="0.001")
    parser.add_argument("--percent-tolerance", default="0.001")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = reconcile(args.curated_run_dir, args.output_dir, args.absolute_tolerance, args.percent_tolerance)
    except ValueError as error:
        print(f"Reconciliation failed to run: {error}", file=sys.stderr)
        return 1
    report = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Reconciliation manifest: {manifest_path} ({report['status']})")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
