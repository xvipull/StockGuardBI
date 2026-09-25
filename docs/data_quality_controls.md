# Inventory Data-Quality Controls

`src/quality/inventory_data_quality.py` evaluates a staging run and writes a versioned JSON report. It is designed to run after standardization and before any curated mart is published.

## Run

```bash
python3 -m src.quality.inventory_data_quality \
  --staging-run-dir /tmp/stockguard-staging/run_id=local-20260925 \
  --source-dir tests/fixtures/raw/representative \
  --report-dir /tmp/stockguard-quality \
  --as-of-date 2025-01-01 \
  --max-lag-days 0
```

An all-pass execution exits with code 0. A completed report with failed controls exits with code 2; the report is still emitted for investigation.

## Controls

| Control | Scope | Pass criterion |
| --- | --- | --- |
| Required columns | All eight staging tables | Every documented required field exists. |
| Null limits | Required fields | Zero null or blank values. |
| Duplicate snapshot keys | `stg_inventory_snapshots` | Exactly one row per `business_date`, `sku_id`, `store_id`. |
| Invalid quantities | Numeric quantity fields | All supplied quantities are finite decimals. |
| Negative quantities | Numeric quantity fields | Quantity fields are non-negative after staging. |
| Negative values | Cost and amount fields | Cost and amount fields are non-negative after staging. |
| Missing master data | Facts referencing product/location | Every SKU resolves to `stg_products`; each store/origin/destination resolves to `stg_stores`. |
| Freshness | Dated tables | Latest source date is within `max_lag_days` of the given as-of date. |
| Row-count reconciliation | Optional when `--source-dir` is supplied | `source rows = staged rows + quarantined rows` for every staged entity. |

## Report contract

The report records execution context, an overall status, check and failure counts, and the observed value, threshold, and detail for each control. A report contains no raw row payloads; source-level investigation remains in the staging quarantine output. Failed controls block curated publication until accepted through the project’s governance process.
