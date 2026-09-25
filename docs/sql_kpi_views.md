# Core Inventory SQL KPI Views

The reusable PostgreSQL-compatible views in [kpi_views.sql](../marts/inventory/kpi_views.sql) publish curated metrics from the inventory star schema. They are intended for Power BI, Excel extracts, and governed ad hoc analysis; raw or staging tables should not be queried directly by end-user tools.

| View | KPI / grain | Notes |
| --- | --- | --- |
| `vw_inventory_on_hand_daily` | On-hand and available stock, SKU × store × day | Exposes balance, value, age, and product/location context. |
| `vw_inventory_sell_through_monthly` | Sell-through, SKU × store × month | Units sold divided by opening stock plus receipts. |
| `vw_inventory_fill_rate_daily` | Fill rate, SKU × store × day | Fulfilled quantity divided by requested quantity; null when request is zero. |
| `vw_inventory_turns_365d` | Turns, SKU × store × day | Trailing 365-day COGS divided by average daily inventory value. |
| `vw_inventory_stockout_rate_28d` | Stockout rate, SKU × store × day | Trailing 28-day stockout demand days divided by eligible demand days. |
| `vw_inventory_period_trends_monthly` | Period-over-period trends, SKU × store × month | Closing inventory, sales, and month-over-month deltas. |

## Deployment and validation

Deploy `inventory_star_schema.sql` before `kpi_views.sql`. Execute [kpi_validation_queries.sql](../marts/inventory/kpi_validation_queries.sql) after deployment. The on-hand reconciliation must agree, and all failing-row checks must return zero rows or zero counts before report publication.

The views honor the KPI definitions in [kpi_catalog.md](kpi_catalog.md). Threshold classification remains in the semantic/reporting layer so owners can adjust approved thresholds without rewriting base calculations.
