#!/usr/bin/env python3
"""Run StockGuard's reproducible representative-data control walkthrough.

The demo deliberately uses only the checked-in, non-sensitive fixture. It
creates immutable run partitions for raw landing, staging, curated data, and
control outputs, and never overwrites an existing output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.ingestion.raw_inventory_ingestion import ingest
from src.quality.inventory_data_quality import quality_check
from src.quality.inventory_reconciliation import reconcile
from src.transforms.inventory_staging import stage
from src.transforms.inventory_star_schema import materialize


FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "raw" / "representative"


def run_demo(output_dir: Path, run_id: str) -> dict:
    """Create and verify a complete representative control flow."""
    output_root = output_dir.resolve()
    if output_root.exists():
        raise ValueError(f"Demo output already exists and will not be overwritten: {output_root}")

    raw_manifest_path = ingest(FIXTURE_DIR, output_root / "raw", run_id)
    staging_manifest_path = stage(FIXTURE_DIR, output_root / "staging", run_id)
    staging_run_dir = staging_manifest_path.parent
    quality_report_path = quality_check(staging_run_dir, output_root / "quality", "2025-01-01", 0, FIXTURE_DIR)
    curated_manifest_path = materialize(staging_run_dir, output_root / "curated", run_id)
    reconciliation_manifest_path = reconcile(curated_manifest_path.parent, output_root / "reconciliation")

    quality_report = json.loads(quality_report_path.read_text(encoding="utf-8"))
    reconciliation_report = json.loads(reconciliation_manifest_path.read_text(encoding="utf-8"))
    if quality_report["status"] != "passed":
        raise RuntimeError(f"Demo quality gate failed: {quality_report_path}")
    if reconciliation_report["status"] != "passed":
        raise RuntimeError(f"Demo reconciliation gate failed: {reconciliation_manifest_path}")

    summary = {
        "status": "passed", "run_id": run_id,
        "raw_manifest": str(raw_manifest_path), "staging_manifest": str(staging_manifest_path),
        "quality_report": str(quality_report_path), "curated_manifest": str(curated_manifest_path),
        "reconciliation_manifest": str(reconciliation_manifest_path),
        "quality_check_count": quality_report["check_count"],
        "reconciliation_records_evaluated": reconciliation_report["records_evaluated"],
        "reconciliation_not_evaluated": reconciliation_report["not_evaluated_count"],
    }
    (output_root / "demo_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for demo artifacts.")
    parser.add_argument("--run-id", default="portfolio-demo", help="Immutable run identifier.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = run_demo(args.output_dir, args.run_id)
    except (RuntimeError, ValueError) as error:
        print(f"Portfolio demo failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
