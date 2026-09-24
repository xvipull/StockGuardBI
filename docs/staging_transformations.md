# Inventory Standardization and Staging

The staging pipeline in `src/transforms/inventory_staging.py` turns raw CSV input into clean, entity-level staging tables while retaining row-level lineage. Raw extracts are never modified or deleted.

## Execute

```bash
python3 -m src.transforms.inventory_staging \
  --input-dir tests/fixtures/raw/representative \
  --output-dir /tmp/stockguard-staging \
  --run-id local-20260924
```

The output uses a non-overwriting run partition:

```text
<output-dir>/run_id=<run-id>/
├── staging/stg_<entity>.csv
├── quarantine/quarantine_<entity>.csv   # only when needed
└── staging_manifest.json
```

## Standardization rules

| Data area | Standardization | Quarantine condition |
| --- | --- | --- |
| Dates | Accept `YYYY-MM-DD`, `YYYY/MM/DD`, `DD-MM-YYYY`, and `DD/MM/YYYY`; write ISO date | Non-empty unparseable date |
| SKU and store keys | Trim, uppercase, change internal whitespace to hyphens; permit letters, digits, `_`, and `-` | Missing key or invalid character |
| Units | Map common synonyms to `EA`, `KG`, `G`, `L`, or `ML` | Non-empty unsupported unit |
| Currency | Uppercase optional ISO-4217 `currency_code` | Present code is not three letters |
| Categories and labels | Trim, uppercase, collapse whitespace, replace label spaces with `_` | N/A; empty optional labels remain empty |
| Status | Map approved product, store, and transfer synonyms to canonical values | Non-empty unapproved status |
| Numeric fields | Parse finite decimal values and write canonical decimal text | Non-empty malformed or non-finite numeric value |

## Traceability and quarantine

Every staged row has `source_file`, `source_row_number`, `source_record_hash`, `staging_run_id`, and `staged_at_utc`. Every quarantined row preserves the same source identifiers, a specific `quarantine_reason`, and the unmodified logical `raw_record` JSON. The manifest totals clean and quarantined records per entity; a run with quarantine is completed, not hidden or dropped.

## Staging table contract

The pipeline writes `stg_inventory_snapshots`, `stg_sales`, `stg_receipts`, `stg_transfers`, `stg_reorder_parameters`, `stg_products`, `stg_stores`, and `stg_calendar` as CSV staging tables. Their business columns follow [the source data dictionary](source_to_target_mapping.md); staging adds the traceability columns above. Curated marts may consume only staged rows, while data-quality reporting consumes the quarantine tables and manifest.
