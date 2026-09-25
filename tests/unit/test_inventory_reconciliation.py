import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.quality.inventory_reconciliation import reconcile


def write_csv(path, rows):
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class InventoryReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temporary_directory.name)
        self.curated = self.temp_path / "curated"
        self.curated.mkdir()
        write_csv(self.curated / "fact_inventory_daily.csv", [
            {"business_date": "2025-01-01", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "100"},
            {"business_date": "2025-01-02", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "92"},
            {"business_date": "2025-01-02", "sku_id": "SKU-200", "store_id": "STORE-001", "on_hand_qty": "50"},
        ])
        write_csv(self.curated / "fact_sales_daily.csv", [
            {"sale_date": "2025-01-02", "sku_id": "SKU-100", "store_id": "STORE-001", "units_sold_qty": "15"},
        ])
        write_csv(self.curated / "fact_receipts.csv", [
            {"receipt_date": "2025-01-02", "sku_id": "SKU-100", "store_id": "STORE-001", "received_qty": "10"},
        ])
        write_csv(self.curated / "fact_transfers.csv", [
            {"transfer_date": "2025-01-02", "sku_id": "SKU-100", "origin_store_id": "STORE-001", "destination_store_id": "STORE-002", "transfer_qty": "3", "transfer_status": "SHIPPED"},
        ])

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_reconciles_balance_and_marks_initial_snapshot_not_evaluated(self):
        manifest_path = reconcile(self.curated, self.temp_path / "output")
        report = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("passed", report["status"])
        self.assertEqual(1, report["records_evaluated"])
        self.assertEqual(2, report["not_evaluated_count"])
        with (manifest_path.parent / "inventory_balance_reconciliation.csv").open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        evaluated = next(row for row in rows if row["business_date"] == "2025-01-02" and row["sku_id"] == "SKU-100")
        self.assertEqual("92", evaluated["expected_closing_stock_qty"])
        self.assertEqual("PASS", evaluated["reconciliation_status"])

    def test_outputs_exception_when_balance_variance_exceeds_tolerance(self):
        inventory_path = self.curated / "fact_inventory_daily.csv"
        with inventory_path.open(newline="", encoding="utf-8") as source:
            inventory_rows = list(csv.DictReader(source))
        inventory_rows.append({"business_date": "2025-01-03", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "99"})
        write_csv(inventory_path, inventory_rows)
        manifest_path = reconcile(self.curated, self.temp_path / "output")
        report = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", report["status"])
        self.assertEqual(1, report["exception_count"])
        with (manifest_path.parent / "inventory_balance_exceptions.csv").open(newline="", encoding="utf-8") as source:
            exception = next(csv.DictReader(source))
        self.assertEqual("EXCEPTION", exception["reconciliation_status"])
        self.assertEqual("7", exception["variance_qty"])


if __name__ == "__main__":
    unittest.main()
