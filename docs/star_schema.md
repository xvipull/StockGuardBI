# Inventory Analytics Star Schema

The curated analytics model is a star schema materialized by `src/transforms/inventory_star_schema.py`. It consumes only validated staging tables and writes a non-overwriting curated run directory with CSV tables and `star_schema_manifest.json`.

```text
dim_date ─────────────┐
dim_sku ──────────────┼── fact_inventory_daily
dim_store ────────────┤
dim_reorder_parameter ┘

dim_date + dim_sku + dim_store ── fact_sales_daily
dim_date + dim_sku + dim_store ── fact_receipts
dim_date + dim_sku + origin/destination dim_store ── fact_transfers
```

## Run

```bash
python3 -m src.transforms.inventory_star_schema \
  --staging-run-dir /tmp/stockguard-staging/run_id=local-20260925 \
  --output-dir /tmp/stockguard-curated \
  --run-id local-20260925
```

## Tables, grain, and business keys

| Table | Type | Grain | Business key | Purpose |
| --- | --- | --- | --- | --- |
| `dim_date` | Dimension | One calendar date | `calendar_date` | Calendar, fiscal, and working-day attributes; `date_key` is `YYYYMMDD`. |
| `dim_sku` | Dimension | One conformed SKU version in the current source extract | `sku_id` | Product hierarchy, base UOM, status, and standard cost. |
| `dim_store` | Dimension | One conformed store/location version in the current source extract | `store_id` | Location hierarchy, format, and operational status. |
| `dim_reorder_parameter` | Dimension | One SKU × store × effective-from date parameter record | `sku_id`, `store_id`, `effective_from_date` | Lead time, safety stock, and reorder settings. |
| `fact_inventory_daily` | Fact | One SKU × store × business date inventory position | `business_date`, `sku_id`, `store_id` | On-hand, allocated, unavailable, in-transit, available, cost, value, and age inputs. |
| `fact_sales_daily` | Fact | One SKU × store × sale date | `sale_date`, `sku_id`, `store_id` | Requested, fulfilled, sold, returned, sales, and COGS measures. |
| `fact_receipts` | Fact | One receipt line | `receipt_id`, `receipt_line_id` | Receipt quantity, unit cost, lot, and receipt date. |
| `fact_transfers` | Fact | One transfer line | `transfer_id`, `transfer_line_id` | Origin, destination, quantity, status, and expected receipt date. |

## Key and relationship rules

- Each table retains its natural business key and includes a deterministic surrogate key. The key is an SHA-256-derived value from the stable business key, so reruns are reproducible.
- Facts reference `date_key`, `sku_key`, and `store_key`; transfers have both `origin_store_key` and `destination_store_key` role-playing references.
- `fact_inventory_daily` resolves its `reorder_parameter_key` to the latest parameter whose effective range covers the inventory business date. It is blank when a valid parameter is unavailable, so missing setup is visible rather than inferred.
- Dimensions are current-extract Type 1 at this stage. Effective-date columns are retained for future historical Type 2 implementation; facts retain original business dates and trace columns.
- Curated publication must be gated by the data-quality report from Day 6.
