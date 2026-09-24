# Raw Inventory Ingestion Framework

The raw ingestion framework preserves source extracts before any transformation. It is implemented in Python for portability and produces a raw landing layout and JSON manifest that a subsequent PySpark staging job can consume.

## Run the command

```bash
python3 -m src.ingestion.raw_inventory_ingestion \
  --input-dir tests/fixtures/raw/representative \
  --output-dir /tmp/stockguard-raw-landing \
  --run-id local-20260924
```

The command writes:

```text
<output-dir>/run_id=<run-id>/
├── files/                 # byte-preserved copies, retaining relative paths
└── run_manifest.json      # run status, metadata, checks, and checksums
```

It exits nonzero on a registered-schema failure only after preserving files and writing the manifest. `--allow-invalid` supports controlled exploratory landing while retaining explicit failure metadata.

## Validation coverage

CSV sources with contract names from [the source-to-target mapping](source_to_target_mapping.md) are checked for required headers and readable CSV structure. The manifest records file size, modified time, SHA-256, source and landed paths, entity, row count, and schema outcome. Unknown or non-CSV files are preserved and marked as warnings, never silently treated as validated.

The output directory is intentionally external to `data/raw`: raw acquisition inputs and landed execution artifacts have different retention and access controls.
