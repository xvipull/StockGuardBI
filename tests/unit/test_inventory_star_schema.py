import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.transforms.inventory_staging import stage
from src.transforms.inventory_star_schema import materialize


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "raw" / "representative"


class InventoryStarSchemaTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temporary_directory.name)
        staging_manifest = stage(FIXTURE_DIR, self.temp_path / "staging", "star-stage")
        self.staging_run_dir = staging_manifest.parent

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_materializes_dimensions_facts_and_business_keys(self):
        manifest_path = materialize(self.staging_run_dir, self.temp_path / "curated", "star-model")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        counts = {table["table"]: table["row_count"] for table in manifest["tables"]}
        self.assertEqual(2, counts["fact_inventory_daily"])
        self.assertEqual(2, counts["fact_sales_daily"])
        self.assertEqual(1, counts["fact_receipts"])
        self.assertEqual(1, counts["fact_transfers"])
        self.assertEqual(2, counts["dim_sku"])
        self.assertEqual(2, counts["dim_store"])

        with (manifest_path.parent / "fact_inventory_daily.csv").open(newline="", encoding="utf-8") as source:
            inventory = next(csv.DictReader(source))
        self.assertTrue(inventory["inventory_daily_key"])
        self.assertEqual("20250101", inventory["date_key"])
        self.assertTrue(inventory["sku_key"])
        self.assertTrue(inventory["store_key"])
        self.assertTrue(inventory["reorder_parameter_key"])


if __name__ == "__main__":
    unittest.main()
