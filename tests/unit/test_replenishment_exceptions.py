import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.transforms.replenishment_exceptions import (
    _clamp,
    _condition_signal,
    _dos_signal,
    _lead_time_signal,
    _lost_sales_signal,
    _margin_signal,
    build_replenishment_exception_mart,
    score_exception,
    RC_ACTIVE_STOCKOUT,
    RC_COVER_BELOW_LEAD_TIME,
    RC_COVER_WITHIN_BUFFER,
    RC_DEAD_STOCK,
    RC_EXCESS_STOCK,
    RC_HIGH_VALUE_AT_RISK,
    RC_INACTIVE,
    RC_LOST_SALES_PROXY,
    RC_MISSING_DEMAND,
    RC_MISSING_LEAD_TIME,
)


ZERO = Decimal("0")
ONE = Decimal("1")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _base_risk_row(**overrides) -> dict:
    """Minimal valid risk row from the stock risk mart (no aged-inventory fields)."""
    row = {
        "business_date": "2025-01-28",
        "sku_id": "SKU-100",
        "store_id": "STORE-001",
        "product_status": "ACTIVE",
        "on_hand_qty": "45",
        "available_inventory_qty": "45",
        "days_of_supply": "10",
        "average_daily_demand_qty": "4.5",
        "demand_status": "ESTABLISHED",
        "lead_time_days": "7",
        "safety_stock_days": "3",
        "reorder_cover_days": "10",
        "unit_cost": "12.50",
        "unit_margin": "5.00",
        "requested_qty": "12",
        "fulfilled_qty": "12",
        "active_stockout_flag": "false",
        "stockout_risk_flag": "false",
        "low_cover_flag": "false",
        "excess_inventory_flag": "false",
        "excess_inventory_qty": "0",
        "excess_inventory_value": "0",
        # Note: movement_status, aged_stock_flag, critical_aged_stock_flag
        # originate from the aged_inventory_analysis pipeline, not risk_flags.
        # They are absent from the base risk row to reflect real data flow.
        "risk_reason": "COVER_ABOVE_REPLENISHMENT_BUFFER",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Signal unit tests
# ---------------------------------------------------------------------------

class TestClamp(unittest.TestCase):
    def test_clamps_below_zero(self):
        self.assertEqual(ZERO, _clamp(Decimal("-0.5")))

    def test_clamps_above_one(self):
        self.assertEqual(ONE, _clamp(Decimal("1.5")))

    def test_passthrough_mid(self):
        val = Decimal("0.6")
        self.assertEqual(val, _clamp(val))


class TestLostSalesSignal(unittest.TestCase):
    def test_no_unfulfilled_no_stockout_returns_zero(self):
        row = {"requested_qty": "10", "fulfilled_qty": "10", "active_stockout_flag": "false"}
        signal, triggered = _lost_sales_signal(row)
        self.assertEqual(ZERO, signal)
        self.assertFalse(triggered)

    def test_unfulfilled_qty_normalises(self):
        row = {"requested_qty": "250", "fulfilled_qty": "0", "active_stockout_flag": "false"}
        signal, triggered = _lost_sales_signal(row)
        # 250/500 = 0.5
        self.assertAlmostEqual(float(signal), 0.5, places=4)
        self.assertTrue(triggered)

    def test_active_stockout_with_no_unfulfilled_returns_max(self):
        row = {"requested_qty": "0", "fulfilled_qty": "0", "active_stockout_flag": "true"}
        signal, triggered = _lost_sales_signal(row)
        self.assertEqual(ONE, signal)
        self.assertTrue(triggered)

    def test_caps_at_one(self):
        row = {"requested_qty": "9999", "fulfilled_qty": "0", "active_stockout_flag": "false"}
        signal, _ = _lost_sales_signal(row)
        self.assertEqual(ONE, signal)


class TestDosSignal(unittest.TestCase):
    def test_null_dos_returns_one(self):
        self.assertEqual(ONE, _dos_signal(None, None))

    def test_zero_dos_returns_one(self):
        self.assertEqual(ONE, _dos_signal(ZERO, None))

    def test_dos_above_cap_returns_zero(self):
        self.assertEqual(ZERO, _dos_signal(Decimal("60"), Decimal("7")))

    def test_midpoint(self):
        signal = _dos_signal(Decimal("15"), None)
        # 1 - 15/30 = 0.5
        self.assertAlmostEqual(float(signal), 0.5, places=4)


class TestLeadTimeSignal(unittest.TestCase):
    def test_no_lead_time_returns_zero(self):
        self.assertEqual(ZERO, _lead_time_signal(Decimal("5"), None, None))

    def test_dos_above_reorder_cover_returns_zero(self):
        self.assertEqual(ZERO, _lead_time_signal(Decimal("20"), Decimal("7"), Decimal("3")))

    def test_active_stockout_null_dos_returns_one(self):
        self.assertEqual(ONE, _lead_time_signal(None, Decimal("7"), Decimal("3")))

    def test_partial_deficit(self):
        # DOS=5, lead=7, safety=3  → reorder_cover=10, deficit=5, signal=0.5
        signal = _lead_time_signal(Decimal("5"), Decimal("7"), Decimal("3"))
        self.assertAlmostEqual(float(signal), 0.5, places=4)


class TestMarginSignal(unittest.TestCase):
    def test_no_cost_or_margin_returns_zero(self):
        signal = _margin_signal({"on_hand_qty": "100"})
        self.assertEqual(ZERO, signal)

    def test_uses_unit_margin_when_available(self):
        row = {"on_hand_qty": "100", "unit_margin": "50.00", "unit_cost": "75.00"}
        signal = _margin_signal(row)
        # 100 * 50 / 10000 = 0.5
        self.assertAlmostEqual(float(signal), 0.5, places=4)

    def test_falls_back_to_unit_cost(self):
        row = {"on_hand_qty": "100", "unit_cost": "50.00"}
        signal = _margin_signal(row)
        self.assertAlmostEqual(float(signal), 0.5, places=4)

    def test_caps_at_one(self):
        row = {"on_hand_qty": "10000", "unit_margin": "9999.00"}
        self.assertEqual(ONE, _margin_signal(row))


class TestConditionSignal(unittest.TestCase):
    def test_fresh_stock_returns_one(self):
        row = {
            "aged_stock_flag": "false",
            "critical_aged_stock_flag": "false",
            "movement_status": "RECENT_MOVEMENT",
            "excess_inventory_flag": "false",
        }
        signal, codes = _condition_signal(row)
        self.assertEqual(ONE, signal)
        self.assertEqual([], codes)

    def test_dead_stock_reduces_signal(self):
        row = {
            "aged_stock_flag": "true",
            "critical_aged_stock_flag": "false",
            "movement_status": "DEAD_STOCK",
            "excess_inventory_flag": "false",
        }
        signal, codes = _condition_signal(row)
        self.assertLess(float(signal), 1.0)
        self.assertIn(RC_DEAD_STOCK, codes)

    def test_excess_flag_appends_code(self):
        row = {
            "aged_stock_flag": "false",
            "critical_aged_stock_flag": "false",
            "movement_status": "RECENT_MOVEMENT",
            "excess_inventory_flag": "true",
        }
        _, codes = _condition_signal(row)
        self.assertIn(RC_EXCESS_STOCK, codes)


# ---------------------------------------------------------------------------
# score_exception integration tests
# ---------------------------------------------------------------------------

class TestScoreException(unittest.TestCase):
    def test_inactive_product_excluded(self):
        row = _base_risk_row(product_status="DISCONTINUED")
        result = score_exception(row)
        self.assertEqual("EXCLUDED", result["priority_band"])
        self.assertEqual("", result["priority_score"])
        self.assertIn(RC_INACTIVE, result["reason_codes"])
        self.assertEqual("false", result["exception_triggered"])

    def test_active_stockout_scores_maximum(self):
        row = _base_risk_row(
            active_stockout_flag="true",
            available_inventory_qty="0",
            days_of_supply="",
            stockout_risk_flag="true",
        )
        result = score_exception(row)
        self.assertEqual("1", result["priority_score"])
        self.assertEqual("CRITICAL", result["priority_band"])
        self.assertIn(RC_ACTIVE_STOCKOUT, result["reason_codes"])
        self.assertEqual("true", result["exception_triggered"])

    def test_missing_demand_marks_unscored(self):
        row = _base_risk_row(
            average_daily_demand_qty="",
            demand_status="INCOMPLETE_HISTORY",
        )
        result = score_exception(row)
        self.assertIn(RC_MISSING_DEMAND, result["input_exception_codes"])
        self.assertEqual("UNSCORED", result["priority_band"])

    def test_missing_lead_time_recorded_but_still_scored(self):
        row = _base_risk_row(lead_time_days="")
        result = score_exception(row)
        self.assertIn(RC_MISSING_LEAD_TIME, result["input_exception_codes"])

    def test_cover_below_lead_time_reason_code(self):
        row = _base_risk_row(
            stockout_risk_flag="true",
            days_of_supply="5",
        )
        result = score_exception(row)
        self.assertIn(RC_COVER_BELOW_LEAD_TIME, result["reason_codes"])
        self.assertEqual("true", result["exception_triggered"])

    def test_cover_within_7_day_buffer_reason_code(self):
        row = _base_risk_row(
            low_cover_flag="true",
            days_of_supply="9",
        )
        result = score_exception(row)
        self.assertIn(RC_COVER_WITHIN_BUFFER, result["reason_codes"])
        self.assertEqual("true", result["exception_triggered"])

    def test_high_value_at_risk_reason_code(self):
        # on_hand=1000, margin=80 → at_risk=80,000 → signal > 0.7
        row = _base_risk_row(on_hand_qty="1000", unit_margin="80.00", unit_cost="100.00")
        result = score_exception(row)
        self.assertIn(RC_HIGH_VALUE_AT_RISK, result["reason_codes"])

    def test_lost_sales_reason_code_with_unfulfilled_qty(self):
        row = _base_risk_row(requested_qty="50", fulfilled_qty="20")
        result = score_exception(row)
        self.assertIn(RC_LOST_SALES_PROXY, result["reason_codes"])

    def test_priority_score_in_range(self):
        row = _base_risk_row(
            stockout_risk_flag="true",
            days_of_supply="5",
            requested_qty="20",
            fulfilled_qty="10",
        )
        result = score_exception(row)
        score = Decimal(result["priority_score"])
        self.assertGreaterEqual(score, ZERO)
        self.assertLessEqual(score, ONE)

    def test_zero_demand_item_not_exception_triggered_without_other_flags(self):
        row = _base_risk_row(
            average_daily_demand_qty="0",
            demand_status="ZERO_DEMAND",
            days_of_supply="",
            stockout_risk_flag="false",
            low_cover_flag="false",
            active_stockout_flag="false",
            requested_qty="0",
            fulfilled_qty="0",
        )
        result = score_exception(row)
        # No exception flags → should NOT be triggered as a replenishment exception
        self.assertEqual("false", result["exception_triggered"])

    def test_priority_band_critical_threshold(self):
        # Force a nearly perfect score by using active stockout
        row = _base_risk_row(
            active_stockout_flag="true",
            available_inventory_qty="0",
            days_of_supply="",
            stockout_risk_flag="true",
        )
        result = score_exception(row)
        self.assertEqual("CRITICAL", result["priority_band"])

    def test_priority_band_monitor_for_low_score(self):
        # Healthy stock, some margin but no risk flags
        row = _base_risk_row(
            days_of_supply="25",
            stockout_risk_flag="false",
            low_cover_flag="false",
            requested_qty="10",
            fulfilled_qty="10",
            unit_margin="0.10",
        )
        result = score_exception(row)
        band = result["priority_band"]
        self.assertIn(band, {"LOW", "MONITOR", "MEDIUM"})


# ---------------------------------------------------------------------------
# Full mart integration test
# ---------------------------------------------------------------------------

class TestBuildReplenishmentExceptionMart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.risk_dir = self.root / "risk"
        self.aged_dir = self.root / "aged"
        self.output_dir = self.root / "output"
        self.risk_dir.mkdir(parents=True)
        self.aged_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_risk(self, rows):
        write_csv(self.risk_dir / "mart_stock_risk_daily.csv", rows)

    def _write_aged(self, rows):
        write_csv(self.aged_dir / "mart_aged_inventory_daily.csv", rows)

    def test_empty_risk_raises(self):
        write_csv(self.risk_dir / "mart_stock_risk_daily.csv", [])
        write_csv(self.aged_dir / "mart_aged_inventory_daily.csv", [])
        with self.assertRaises(ValueError):
            build_replenishment_exception_mart(self.risk_dir, self.aged_dir, self.output_dir, "run001")

    def test_invalid_run_id_raises(self):
        self._write_risk([_base_risk_row()])
        self._write_aged([])
        with self.assertRaises(ValueError):
            build_replenishment_exception_mart(self.risk_dir, self.aged_dir, self.output_dir, "bad run!")

    def test_existing_output_raises(self):
        self._write_risk([_base_risk_row()])
        self._write_aged([])
        existing = self.output_dir / "run_id=run001"
        existing.mkdir(parents=True)
        with self.assertRaises(ValueError):
            build_replenishment_exception_mart(self.risk_dir, self.aged_dir, self.output_dir, "run001")

    def test_active_stockout_appears_in_exception_mart(self):
        risk_row = _base_risk_row(
            active_stockout_flag="true",
            available_inventory_qty="0",
            days_of_supply="",
            stockout_risk_flag="true",
        )
        aged_row = {
            "business_date": risk_row["business_date"],
            "sku_id": risk_row["sku_id"],
            "store_id": risk_row["store_id"],
            "aged_stock_flag": "false",
            "critical_aged_stock_flag": "false",
            "movement_status": "RECENT_MOVEMENT",
        }
        self._write_risk([risk_row])
        self._write_aged([aged_row])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run001"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertGreater(manifest["exception_rows_generated"], 0)
        output_csv = manifest_path.parent / "mart_replenishment_exceptions.csv"
        with output_csv.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(1, len(rows))
        self.assertEqual("CRITICAL", rows[0]["priority_band"])
        self.assertIn(RC_ACTIVE_STOCKOUT, rows[0]["reason_codes"])

    def test_manifest_contains_required_keys(self):
        self._write_risk([_base_risk_row(stockout_risk_flag="true", days_of_supply="5")])
        self._write_aged([])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run-abc"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in (
            "manifest_version",
            "pipeline",
            "run_id",
            "generated_at_utc",
            "total_risk_rows_processed",
            "exception_rows_generated",
            "input_exception_count",
            "priority_band_counts",
            "reason_code_counts",
            "scoring_weights",
        ):
            self.assertIn(key, manifest, f"Missing manifest key: {key}")
        self.assertEqual("run-abc", manifest["run_id"])

    def test_inactive_product_not_in_exception_mart(self):
        self._write_risk([_base_risk_row(product_status="DISCONTINUED")])
        self._write_aged([])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run002"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(0, manifest["exception_rows_generated"])

    def test_input_exception_file_written(self):
        row = _base_risk_row(average_daily_demand_qty="", demand_status="INCOMPLETE_HISTORY")
        self._write_risk([row])
        self._write_aged([])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run003"
        )
        exc_csv = manifest_path.parent / "replenishment_input_exceptions.csv"
        self.assertTrue(exc_csv.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertGreater(manifest["input_exception_count"], 0)

    def test_exceptions_sorted_critical_first(self):
        rows = [
            _base_risk_row(sku_id="SKU-LOW",  days_of_supply="25", low_cover_flag="true"),
            _base_risk_row(sku_id="SKU-HIGH", active_stockout_flag="true", available_inventory_qty="0",
                           days_of_supply="", stockout_risk_flag="true"),
            _base_risk_row(sku_id="SKU-MED",  stockout_risk_flag="true", days_of_supply="5"),
        ]
        self._write_risk(rows)
        self._write_aged([])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run004"
        )
        output_csv = manifest_path.parent / "mart_replenishment_exceptions.csv"
        with output_csv.open(newline="", encoding="utf-8") as f:
            result_rows = list(csv.DictReader(f))
        self.assertTrue(len(result_rows) >= 2)
        # CRITICAL row must appear before non-CRITICAL rows
        critical_indices = [i for i, r in enumerate(result_rows) if r["priority_band"] == "CRITICAL"]
        non_critical_indices = [i for i, r in enumerate(result_rows) if r["priority_band"] != "CRITICAL"]
        if critical_indices and non_critical_indices:
            self.assertLess(max(critical_indices), min(non_critical_indices))

    def test_aged_inventory_merged_correctly(self):
        risk_row = _base_risk_row(stockout_risk_flag="true", days_of_supply="5")
        aged_row = {
            "business_date": risk_row["business_date"],
            "sku_id": risk_row["sku_id"],
            "store_id": risk_row["store_id"],
            "aged_stock_flag": "true",
            "critical_aged_stock_flag": "false",
            "movement_status": "SLOW_MOVING",
            "days_since_last_sale": "75",
        }
        self._write_risk([risk_row])
        self._write_aged([aged_row])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run005"
        )
        output_csv = manifest_path.parent / "mart_replenishment_exceptions.csv"
        with output_csv.open(newline="", encoding="utf-8") as f:
            result_rows = list(csv.DictReader(f))
        self.assertTrue(len(result_rows) > 0)
        first = result_rows[0]
        self.assertEqual("SLOW_MOVING", first.get("movement_status"))

    def test_weights_sum_reported_in_manifest(self):
        self._write_risk([_base_risk_row(stockout_risk_flag="true", days_of_supply="4")])
        self._write_aged([])
        manifest_path = build_replenishment_exception_mart(
            self.risk_dir, self.aged_dir, self.output_dir, "run006"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        weights = manifest["scoring_weights"]
        total = sum(Decimal(v) for v in weights.values())
        self.assertAlmostEqual(float(total), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
