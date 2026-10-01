# Aged Inventory and Working Capital Analysis

`src/transforms/aged_inventory_analysis.py` writes a SKU × store × business-date aged inventory mart from the curated inventory fact, stock-risk mart, and sales history. All values remain in the inventory mart's currency basis.

## Definitions

| Measure | Rule |
| --- | --- |
| Age bucket | `00_30_DAYS`, `31_60_DAYS`, `61_90_DAYS`, `91_180_DAYS`, `181_365_DAYS`, `OVER_365_DAYS`; missing or negative age is `UNAGED`. |
| Aged stock | Inventory age `> 90` calendar days. |
| Critical aged stock | Inventory age `> 180` calendar days. |
| Slow moving | At least 60 and fewer than 180 days since last positive sale at the SKU-store. |
| Dead stock | At least 180 days since last positive sale. |
| No sales history | No positive sale exists through the analysis date; surfaced separately from a measured dead-stock interval. |
| Aged inventory value | `on_hand_qty × unit_cost`, using the curated `inventory_value` when present. |
| Excess inventory value | Governed Day 12 excess value from the stock-risk mart; quantity is excess over target stock times unit cost. |

## Value reconciliation

The process writes `aged_inventory_value_reconciliation.csv` and records both checks in the manifest. Total aged-mart inventory value must equal the sum of `fact_inventory_daily.inventory_value`, and aged-mart excess value must equal the Day 12 risk-mart excess value. The default variance tolerance is **0.01** in the mart currency. A failed reconciliation exits with status 2 and remains visible in the output manifest.

Rows with missing inventory value or unresolved risk setup appear in `aged_inventory_value_exceptions.csv`; do not treat incomplete value coverage as a Finance-approved total.

## Execute

```bash
python3 -m src.transforms.aged_inventory_analysis \
  --curated-run-dir /data/curated/star/run_id=daily \
  --risk-run-dir /data/curated/risk/run_id=daily \
  --sales-file /data/staging/stg_sales.csv \
  --output-dir /data/curated/aged_inventory \
  --run-id daily
```

Outputs include `mart_aged_inventory_daily.csv`, bucket-level summaries, value exceptions, a reconciliation table, and a JSON manifest. SQL consumers can deploy [aged_inventory_views.sql](../marts/inventory/aged_inventory_views.sql) to expose daily aged-stock, slow/dead-stock, and working-capital views.
