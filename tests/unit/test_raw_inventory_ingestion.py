import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.ingestion.raw_inventory_ingestion import ingest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "raw" / "representative"


class RawInventoryIngestionTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name) / "landing"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_lands_files_with_complete_manifest_and_schema_checks(self):
        manifest_path = ingest(FIXTURE_DIR, self.output_dir, "test-run-001")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("succeeded", manifest["status"])
        self.assertEqual(8, manifest["file_count"])
        self.assertEqual(0, manifest["schema_failure_count"])
        self.assertTrue((manifest_path.parent / "files" / "inventory_snapshots.csv").is_file())
        self.assertEqual(
            (FIXTURE_DIR / "inventory_snapshots.csv").read_bytes(),
            (manifest_path.parent / "files" / "inventory_snapshots.csv").read_bytes(),
        )
        self.assertTrue(all(entry["sha256"] for entry in manifest["files"]))
        self.assertTrue(all(entry["schema_check"]["status"] == "passed" for entry in manifest["files"]))

    def test_schema_failure_preserves_raw_file_and_manifest(self):
        bad_input = Path(self.temporary_directory.name) / "bad-input"
        shutil.copytree(FIXTURE_DIR, bad_input)
        (bad_input / "sales.csv").write_text("sale_date,sku_id\n2025-01-01,SKU-100\n", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            ingest(bad_input, self.output_dir, "test-run-002")

        manifest_path = self.output_dir / "run_id=test-run-002" / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("completed_with_schema_failures", manifest["status"])
        self.assertIn("sales.csv", manifest["schema_failures"])
        self.assertTrue((manifest_path.parent / "files" / "sales.csv").is_file())


if __name__ == "__main__":
    unittest.main()
