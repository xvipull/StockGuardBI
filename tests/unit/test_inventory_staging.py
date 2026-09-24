import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.transforms.inventory_staging import stage


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "raw" / "representative"


class InventoryStagingTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name) / "staging"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_writes_standardized_staging_tables_with_lineage(self):
        manifest_path = stage(FIXTURE_DIR, self.output_dir, "test-stage-001")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(10, manifest["total_staged_rows"])
        self.assertEqual(0, manifest["total_quarantined_rows"])

        with (manifest_path.parent / "staging" / "stg_products.csv").open(newline="", encoding="utf-8") as source:
            product = next(csv.DictReader(source))
        self.assertEqual("WIDGETS", product["category"])
        self.assertEqual("EA", product["base_uom"])
        self.assertEqual("ACTIVE", product["product_status"])
        self.assertEqual("SKU-100", product["sku_id"])
        self.assertTrue(product["source_record_hash"])

    def test_quarantines_malformed_records_without_losing_source_trace(self):
        bad_input = Path(self.temporary_directory.name) / "bad-input"
        shutil.copytree(FIXTURE_DIR, bad_input)
        with (bad_input / "products.csv").open("a", encoding="utf-8") as source:
            source.write("SKU bad,Invalid Product,Widgets,,StockGuard,EACH,UNKNOWN,12.50\n")

        manifest_path = stage(bad_input, self.output_dir, "test-stage-002")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["total_quarantined_rows"])
        with (manifest_path.parent / "quarantine" / "quarantine_products.csv").open(newline="", encoding="utf-8") as source:
            quarantine = next(csv.DictReader(source))
        self.assertIn("product_status", quarantine["quarantine_reason"])
        self.assertEqual("products.csv", quarantine["source_file"])
        self.assertTrue(quarantine["source_record_hash"])
        self.assertIn("SKU bad", quarantine["raw_record"])


if __name__ == "__main__":
    unittest.main()
