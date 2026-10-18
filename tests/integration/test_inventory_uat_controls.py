"""Executable control evidence for the stakeholder inventory UAT pack."""

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


class InventoryUatControlTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.curated = self.root / "curated"
        self.curated.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_late_receipt_is_applied_on_actual_receipt_date_only(self):
        write_csv(self.curated / "fact_inventory_daily.csv", [
            {"business_date": "2025-01-01", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "100"},
            {"business_date": "2025-01-02", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "90"},
            {"business_date": "2025-01-03", "sku_id": "SKU-100", "store_id": "STORE-001", "on_hand_qty": "95"},
        ])
        write_csv(self.curated / "fact_sales_daily.csv", [
            {"sale_date": "2025-01-02", "sku_id": "SKU-100", "store_id": "STORE-001", "units_sold_qty": "10"},
        ])
        write_csv(self.curated / "fact_receipts.csv", [
            # This receipt was expected earlier but is booked on its actual arrival date.
            {"receipt_date": "2025-01-03", "sku_id": "SKU-100", "store_id": "STORE-001", "received_qty": "5"},
        ])

        manifest_path = reconcile(self.curated, self.root / "reconciliation")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("passed", manifest["status"])
        with (manifest_path.parent / "inventory_balance_reconciliation.csv").open(encoding="utf-8", newline="") as source:
            rows = {row["business_date"]: row for row in csv.DictReader(source)}
        self.assertEqual("0", rows["2025-01-02"]["receipts_qty"])
        self.assertEqual("90", rows["2025-01-02"]["expected_closing_stock_qty"])
        self.assertEqual("5", rows["2025-01-03"]["receipts_qty"])
        self.assertEqual("95", rows["2025-01-03"]["expected_closing_stock_qty"])


if __name__ == "__main__":
    unittest.main()
