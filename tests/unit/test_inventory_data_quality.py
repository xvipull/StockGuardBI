import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.quality.inventory_data_quality import quality_check
from src.transforms.inventory_staging import stage


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "raw" / "representative"


class InventoryDataQualityTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def quality_report(self, source_dir: Path, run_id: str = "quality-test"):
        manifest_path = stage(source_dir, self.temp_path / "staging", run_id)
        return quality_check(manifest_path.parent, self.temp_path / "reports", "2025-01-01", 0, source_dir)

    def test_passing_staging_run_generates_reconciled_report(self):
        report_path = self.quality_report(FIXTURE_DIR)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("passed", report["status"])
        self.assertEqual(0, report["failure_count"])
        self.assertTrue(any(check["control"] == "row_count_reconciliation" for check in report["checks"]))

    def test_detects_duplicate_snapshot_and_missing_master(self):
        bad_input = self.temp_path / "bad-input"
        shutil.copytree(FIXTURE_DIR, bad_input)
        with (bad_input / "inventory_snapshots.csv").open("a", encoding="utf-8") as source:
            source.write("2025-01-01,SKU-999,STORE-001,4,0,0,0,5.00\n")
            source.write("2025-01-01,SKU-100,STORE-001,6,0,0,0,12.50\n")
        report_path = self.quality_report(bad_input, "quality-defects")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        failures = {(check["control"], check["entity"]) for check in report["checks"] if check["status"] == "failed"}
        self.assertIn(("duplicate_snapshot_keys", "inventory_snapshots"), failures)
        self.assertIn(("missing_product_master", "inventory_snapshots"), failures)


if __name__ == "__main__":
    unittest.main()
