#!/usr/bin/env python3
"""Build governed replenishment exception file with store-SKU priority scores.

Priority score combines five weighted signals:

  1. Lost-sales proxy   – unfulfilled demand or stockout indicator  (weight 0.30)
  2. Days of supply     – proximity to zero cover                   (weight 0.25)
  3. Lead time urgency  – cover deficit vs lead time                (weight 0.20)
  4. Margin / value     – unit margin × on-hand or at-risk volume   (weight 0.15)
  5. Stock condition    – aged stock, dead stock, excess penalty     (weight 0.10)

Each signal is normalised to [0, 1] before weighting. Higher score = higher
replenishment urgency. Rows with insufficient data receive reason codes but
are not scored (score remains null) unless they are active stockouts, which
receive the maximum score.

Output
------
mart_replenishment_exceptions.csv    – all triggered exceptions, ranked
replenishment_input_exceptions.csv   – rows that could not be scored
replenishment_manifest.json          – run lineage and summary counts
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


ZERO = Decimal("0")
ONE = Decimal("1")

# Signal weights (must sum to 1.0)
W_LOST_SALES: Decimal = Decimal("0.30")
W_DOS: Decimal = Decimal("0.25")
W_LEAD_TIME: Decimal = Decimal("0.20")
W_MARGIN: Decimal = Decimal("0.15")
W_CONDITION: Decimal = Decimal("0.10")

# Thresholds
DOS_CRITICAL_CAP: Decimal = Decimal("30")      # ≥ 30 DOS → signal = 0
LOST_SALES_CAP: Decimal = Decimal("500")       # cap normalisation at 500 units
MARGIN_CAP: Decimal = Decimal("10000")         # cap at £10 k at-risk margin value

# Reason codes (bit flags carried through as a semicolon list)
RC_ACTIVE_STOCKOUT = "ACTIVE_STOCKOUT"
RC_COVER_BELOW_LEAD_TIME = "COVER_BELOW_LEAD_TIME"
RC_COVER_WITHIN_BUFFER = "COVER_WITHIN_7_DAY_BUFFER"
RC_HIGH_VALUE_AT_RISK = "HIGH_VALUE_AT_RISK"
RC_LOST_SALES_PROXY = "LOST_SALES_PROXY"
RC_AGED_STOCK = "AGED_STOCK_CONDITION"
RC_DEAD_STOCK = "DEAD_STOCK_CONDITION"
RC_EXCESS_STOCK = "EXCESS_STOCK_CONDITION"
RC_MISSING_DEMAND = "MISSING_DEMAND_DATA"
RC_MISSING_LEAD_TIME = "MISSING_LEAD_TIME"
RC_MISSING_COST = "MISSING_UNIT_COST"
RC_INACTIVE = "INACTIVE_PRODUCT"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    all_fields: List[str] = list(rows[0].keys())
    for row in rows:
        for key in row:
            if key not in all_fields:
                all_fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _decimal(row: Dict[str, str], field: str) -> Optional[Decimal]:
    value = (row.get(field) or "").strip()
    if not value:
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _bool_field(row: Dict[str, str], field: str) -> Optional[bool]:
    value = (row.get(field) or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------

def _clamp(value: Decimal, lo: Decimal = ZERO, hi: Decimal = ONE) -> Decimal:
    return max(lo, min(hi, value))


def _lost_sales_signal(row: Dict[str, str]) -> Tuple[Decimal, bool]:
    """Normalised lost-sales proxy signal [0,1].

    Proxy = (requested_qty – fulfilled_qty) where positive, or stockout flag.
    Returns (signal, triggered_flag).
    """
    requested = _decimal(row, "requested_qty") or ZERO
    fulfilled = _decimal(row, "fulfilled_qty") or ZERO
    unfulfilled = max(requested - fulfilled, ZERO)
    active_stockout = _bool_field(row, "active_stockout_flag") or False
    if unfulfilled == ZERO and not active_stockout:
        return ZERO, False
    if active_stockout and unfulfilled == ZERO:
        return ONE, True
    return _clamp(unfulfilled / LOST_SALES_CAP), True


def _dos_signal(dos: Optional[Decimal], lead_time: Optional[Decimal]) -> Decimal:
    """Normalised days-of-supply urgency signal [0,1].

    Signal = 1 at DOS=0, falls linearly to 0 at DOS ≥ DOS_CRITICAL_CAP.
    When DOS is null (active stockout) signal = 1.
    """
    if dos is None:
        return ONE
    cap = max(DOS_CRITICAL_CAP, lead_time * 2) if lead_time else DOS_CRITICAL_CAP
    return _clamp(ONE - dos / cap)


def _lead_time_signal(dos: Optional[Decimal], lead_time: Optional[Decimal], safety_stock_days: Optional[Decimal]) -> Decimal:
    """Normalised cover-deficit-vs-lead-time signal [0,1].

    Signal = 1 when DOS is zero; scales by (lead_time - DOS) / lead_time
    where DOS < lead_time; 0 otherwise.
    """
    if lead_time is None or lead_time <= ZERO:
        return ZERO
    if dos is None:
        return ONE
    reorder_cover = lead_time + (safety_stock_days or ZERO)
    deficit = reorder_cover - dos
    if deficit <= ZERO:
        return ZERO
    return _clamp(deficit / reorder_cover)


def _margin_signal(row: Dict[str, str]) -> Decimal:
    """Normalised at-risk margin value signal [0,1].

    at_risk_margin = on_hand_qty × unit_margin (or unit_cost as proxy).
    """
    on_hand = _decimal(row, "on_hand_qty") or ZERO
    unit_margin = _decimal(row, "unit_margin")
    if unit_margin is None:
        unit_margin = _decimal(row, "unit_cost") or ZERO
    at_risk = on_hand * unit_margin
    return _clamp(at_risk / MARGIN_CAP)


def _condition_signal(row: Dict[str, str]) -> Tuple[Decimal, List[str]]:
    """Stock condition signal: excess reduces urgency, aged/dead increases it.

    Returns (signal [0,1], list of triggered condition reason codes).
    """
    codes: List[str] = []
    score = ZERO

    aged = _bool_field(row, "aged_stock_flag")
    critical_aged = _bool_field(row, "critical_aged_stock_flag")
    movement_status = (row.get("movement_status") or "").strip().upper()
    excess_flag = _bool_field(row, "excess_inventory_flag")

    if critical_aged or movement_status == "DEAD_STOCK":
        score = Decimal("0.25")   # aged/dead reduces replen urgency
        codes.append(RC_DEAD_STOCK if movement_status == "DEAD_STOCK" else RC_AGED_STOCK)
    elif aged or movement_status == "SLOW_MOVING":
        score = Decimal("0.5")
        codes.append(RC_AGED_STOCK)

    if excess_flag:
        score = max(ZERO, score - Decimal("0.3"))
        codes.append(RC_EXCESS_STOCK)

    # Invert: high condition urgency = low condition score penalty
    # We treat condition as a modifier; a fresh item at risk = max signal
    final_signal = ONE - score
    return _clamp(final_signal), codes


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def score_exception(row: Dict[str, str]) -> Dict[str, str]:
    """Score and classify one SKU-store record for replenishment exception.

    Returns an enriched dict with priority_score, priority_band, and reason_codes.
    """
    result: Dict[str, str] = dict(row)
    input_exceptions: List[str] = []
    reason_codes: List[str] = []

    product_status = (row.get("product_status") or "ACTIVE").strip().upper()
    active = product_status in {"ACTIVE", "LIVE"}
    if not active:
        reason_codes.append(RC_INACTIVE)
        result["priority_score"] = ""
        result["priority_band"] = "EXCLUDED"
        result["reason_codes"] = RC_INACTIVE
        result["exception_triggered"] = "false"
        return result

    dos = _decimal(row, "days_of_supply")
    lead_time = _decimal(row, "lead_time_days")
    safety_stock_days = _decimal(row, "safety_stock_days")
    avg_demand = _decimal(row, "average_daily_demand_qty")
    demand_status = (row.get("demand_status") or "").strip().upper()

    # --- Validate required inputs ---
    if avg_demand is None or demand_status in {"INCOMPLETE_HISTORY", ""}:
        input_exceptions.append(RC_MISSING_DEMAND)

    if lead_time is None:
        input_exceptions.append(RC_MISSING_LEAD_TIME)

    if _decimal(row, "unit_cost") is None and _decimal(row, "unit_margin") is None:
        input_exceptions.append(RC_MISSING_COST)

    active_stockout = _bool_field(row, "active_stockout_flag") or False

    # --- Compute signals ---
    lost_signal, lost_triggered = _lost_sales_signal(row)
    dos_signal = _dos_signal(dos, lead_time)
    lt_signal = _lead_time_signal(dos, lead_time, safety_stock_days)
    margin_signal = _margin_signal(row)
    condition_signal, condition_codes = _condition_signal(row)

    # --- Reason codes ---
    if active_stockout:
        reason_codes.append(RC_ACTIVE_STOCKOUT)
    if lost_triggered:
        reason_codes.append(RC_LOST_SALES_PROXY)

    stockout_risk = (_bool_field(row, "stockout_risk_flag") or False)
    low_cover = (_bool_field(row, "low_cover_flag") or False)
    if stockout_risk:
        reason_codes.append(RC_COVER_BELOW_LEAD_TIME)
    elif low_cover:
        reason_codes.append(RC_COVER_WITHIN_BUFFER)

    # High value at-risk threshold: margin signal > 0.7
    if margin_signal > Decimal("0.7"):
        reason_codes.append(RC_HIGH_VALUE_AT_RISK)

    reason_codes.extend(condition_codes)

    # --- Priority score ---
    if active_stockout and not input_exceptions:
        # Force maximum score for active stockouts with clean data
        priority_score: Optional[Decimal] = ONE
    elif input_exceptions and not active_stockout:
        priority_score = None
    else:
        priority_score = _clamp(
            W_LOST_SALES * lost_signal
            + W_DOS * dos_signal
            + W_LEAD_TIME * lt_signal
            + W_MARGIN * margin_signal
            + W_CONDITION * condition_signal
        )

    # --- Determine exception trigger ---
    # Zero-demand items (no positive rolling demand) are only triggered when an
    # explicit supply risk flag is raised, not solely by the composite score.
    # This prevents ZERO_DEMAND / NEWLY_LAUNCHED_ZERO_DEMAND items from
    # appearing in the replenishment queue just because their margin signal
    # or condition signal nudges the score above the threshold.
    zero_demand = demand_status in {"ZERO_DEMAND", "NEWLY_LAUNCHED_ZERO_DEMAND"}
    score_triggers = (
        not zero_demand
        and priority_score is not None
        and priority_score >= Decimal("0.4")
    )
    exception_triggered = (
        active_stockout
        or stockout_risk
        or low_cover
        or lost_triggered
        or score_triggers
    )

    # --- Priority band ---
    if priority_score is None:
        band = "UNSCORED"
    elif priority_score >= Decimal("0.80"):
        band = "CRITICAL"
    elif priority_score >= Decimal("0.60"):
        band = "HIGH"
    elif priority_score >= Decimal("0.40"):
        band = "MEDIUM"
    elif priority_score >= Decimal("0.20"):
        band = "LOW"
    else:
        band = "MONITOR"

    result["priority_score"] = "" if priority_score is None else format(priority_score, "f")
    result["priority_band"] = band
    result["reason_codes"] = ";".join(reason_codes) if reason_codes else ""
    result["input_exception_codes"] = ";".join(input_exceptions) if input_exceptions else ""
    result["exception_triggered"] = str(exception_triggered).lower()

    return result


# ---------------------------------------------------------------------------
# Mart builder
# ---------------------------------------------------------------------------

def build_replenishment_exception_mart(
    risk_run_dir: Path,
    aged_run_dir: Path,
    output_dir: Path,
    run_id: str,
) -> Path:
    """Build the governed replenishment exception mart.

    Parameters
    ----------
    risk_run_dir:
        Directory containing ``mart_stock_risk_daily.csv`` (from Day 13
        inventory_risk_flags pipeline, partition directory ``run_id=...``).
    aged_run_dir:
        Directory containing ``mart_aged_inventory_daily.csv`` (from
        aged_inventory_analysis pipeline).
    output_dir:
        Root directory to write output partition ``run_id=<run_id>/``.
    run_id:
        Alphanumeric run identifier used for partition naming.
    """
    if not run_id or not all(c.isalnum() or c in "-_" for c in run_id):
        raise ValueError("run_id may contain only letters, numbers, hyphens, and underscores")

    risk_rows = read_csv(risk_run_dir / "mart_stock_risk_daily.csv")
    aged_rows = read_csv(aged_run_dir / "mart_aged_inventory_daily.csv")

    if not risk_rows:
        raise ValueError("mart_stock_risk_daily.csv is required and cannot be empty")

    # Index aged inventory by (business_date, sku_id, store_id)
    aged_index: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for row in aged_rows:
        key = (
            row.get("business_date", ""),
            row.get("sku_id", ""),
            row.get("store_id", ""),
        )
        aged_index[key] = row

    scored_rows: List[Dict[str, str]] = []
    input_exception_rows: List[Dict[str, str]] = []

    for risk_row in risk_rows:
        key = (
            risk_row.get("business_date", ""),
            risk_row.get("sku_id", ""),
            risk_row.get("store_id", ""),
        )
        aged_row = aged_index.get(key, {})

        # Merge aged inventory enrichment into risk row.
        # Risk row is authoritative for all risk signals (flags, DOS, demand).
        # Aged row supplements condition metadata (movement_status, age flags)
        # only when the risk row does not already carry that field.
        merged: Dict[str, str] = dict(risk_row)
        for field, value in aged_row.items():
            if field not in merged or not merged[field]:
                merged[field] = value

        try:
            scored = score_exception(merged)
        except Exception as exc:
            exception_row: Dict[str, str] = dict(merged)
            exception_row["input_exception_codes"] = f"SCORING_ERROR:{exc}"
            exception_row["priority_score"] = ""
            exception_row["priority_band"] = "UNSCORED"
            exception_row["reason_codes"] = ""
            exception_row["exception_triggered"] = "false"
            scored_rows.append(exception_row)
            input_exception_rows.append(exception_row)
            continue

        scored_rows.append(scored)
        if scored.get("input_exception_codes"):
            input_exception_rows.append(scored)

    # Filter to exception-triggered rows only for the main mart
    exception_mart = [r for r in scored_rows if r.get("exception_triggered") == "true"]

    # Sort by priority score descending (CRITICAL first, unscored last)
    def _sort_key(r: Dict[str, str]) -> Tuple[int, Decimal]:
        band_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "MONITOR": 4, "UNSCORED": 5, "EXCLUDED": 6}
        band = r.get("priority_band", "UNSCORED")
        score = _decimal(r, "priority_score") or ZERO
        return (band_order.get(band, 99), -score)

    exception_mart.sort(key=_sort_key)

    root = output_dir.resolve() / f"run_id={run_id}"
    if root.exists():
        raise ValueError(f"Replenishment exception mart output exists and will not be overwritten: {root}")

    write_csv(root / "mart_replenishment_exceptions.csv", exception_mart)
    write_csv(root / "replenishment_input_exceptions.csv", input_exception_rows)

    band_counts: Dict[str, int] = {}
    for row in exception_mart:
        band = row.get("priority_band", "UNSCORED")
        band_counts[band] = band_counts.get(band, 0) + 1

    reason_counts: Dict[str, int] = {}
    for row in exception_mart:
        for code in (row.get("reason_codes") or "").split(";"):
            code = code.strip()
            if code:
                reason_counts[code] = reason_counts.get(code, 0) + 1

    manifest = {
        "manifest_version": "1.0.0",
        "pipeline": "stockguard_replenishment_exceptions",
        "run_id": run_id,
        "risk_run_dir": str(risk_run_dir.resolve()),
        "aged_run_dir": str(aged_run_dir.resolve()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_risk_rows_processed": len(risk_rows),
        "exception_rows_generated": len(exception_mart),
        "input_exception_count": len(input_exception_rows),
        "priority_band_counts": band_counts,
        "reason_code_counts": reason_counts,
        "scoring_weights": {
            "lost_sales_proxy": str(W_LOST_SALES),
            "days_of_supply": str(W_DOS),
            "lead_time_urgency": str(W_LEAD_TIME),
            "margin_value": str(W_MARGIN),
            "stock_condition": str(W_CONDITION),
        },
    }
    manifest_path = root / "replenishment_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risk-run-dir", type=Path, required=True,
                        help="Directory containing mart_stock_risk_daily.csv")
    parser.add_argument("--aged-run-dir", type=Path, required=True,
                        help="Directory containing mart_aged_inventory_daily.csv")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Root directory for replenishment exception outputs")
    parser.add_argument("--run-id", required=True,
                        help="Alphanumeric run identifier")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = build_replenishment_exception_mart(
            args.risk_run_dir,
            args.aged_run_dir,
            args.output_dir,
            args.run_id,
        )
    except ValueError as error:
        print(f"Replenishment exception mart build failed: {error}", file=sys.stderr)
        return 1
    data = json.loads(manifest.read_text(encoding="utf-8"))
    print(
        f"Replenishment exception mart: {manifest}\n"
        f"  Exceptions generated : {data['exception_rows_generated']}\n"
        f"  Input exceptions     : {data['input_exception_count']}\n"
        f"  Priority bands       : {data['priority_band_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
