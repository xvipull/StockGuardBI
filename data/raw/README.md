# Raw Data Acquisition

`data/raw` is a landing area for reproducible, non-sensitive public or synthetic source extracts. Do not commit ERP exports, access tokens, customer data, supplier-confidential terms, or other restricted information.

## Default acquisition method: deterministic synthetic data

The project will use a deterministic generator for development and testing. It must accept a seed, date range, and output directory; the same inputs must create byte-stable logical records. Generated files will be ignored by Git, while the generator code, configuration, and manifest remain version controlled.

Expected generated files:

```text
inventory_snapshots.csv
sales.csv
receipts.csv
transfers.csv
reorder_parameters.csv
products.csv
stores.csv
calendar.csv
manifest.json
```

When the generator is delivered, run it from the repository root with the documented command. Record the seed, date range, schema version, row counts, checksum, and generation timestamp in `manifest.json`.

## Optional public-data substitute

If public data is used, it must be downloaded by a versioned script or documented immutable URL, with license, retrieval date, source URL, checksum, and transformation notes added to `source_manifest.yml`. Public data is only accepted when it can be mapped to the source contract in [source_to_target_mapping.md](../../docs/source_to_target_mapping.md); missing entities may be deterministically synthesized and clearly labeled.

## Landing conventions

- UTF-8 CSV files use lowercase snake_case column names and one header row.
- Files contain no PII and no credentials.
- Data dates cover the manifest `business_date_start` through `business_date_end`.
- Each reload replaces only the generated files listed above; versioned configuration and documentation stay intact.
