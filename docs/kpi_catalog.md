# Inventory KPI Catalog and Assumptions

## Contract conventions

- **Business date** is the source-system close date represented by a KPI record; it is not the pipeline execution timestamp.
- Unless explicitly noted, operational KPIs are calculated at **SKU × location × business date** and may be rolled up only from additive numerator and denominator measures.
- Quantity measures use the item’s base stocking unit. Monetary measures use the approved local-currency inventory valuation basis; currency conversion is out of scope until a Finance-approved rate source is supplied.
- Source-system close is the daily cutoff. Curated marts refresh once daily after close and must expose `business_date`, `pipeline_run_ts`, and data-quality status.
- Thresholds below are launch defaults and require approval by the named business owner before production use.

## KPI catalog

| KPI | Grain | Formula | Launch threshold / interpretation | Business owner | Refresh rule |
| --- | --- | --- | --- | --- | --- |
| Days of supply (DOS) | SKU × location × business date | `available_inventory_qty / average_daily_demand_qty`; null when demand is zero or unavailable | Red: `< lead_time_days + safety_stock_days`; Amber: `< lead_time_days + safety_stock_days + 7`; Green otherwise | Inventory Planner | Daily after demand and inventory close; use trailing 28 calendar days of eligible demand |
| Stockout rate | SKU × location × business date, rollable by demand-weighted denominator | `stockout_demand_days / eligible_demand_days × 100` | Red: `> 5%`; Amber: `> 2% to 5%`; Green: `≤ 2%` | Store Operations | Daily; trailing 28 calendar days at summary level |
| Inventory turns | SKU × location × trailing 365-day ending business date | `trailing_365d_cost_of_goods_sold / average_inventory_value` | Review when `< 4.0`; calculate only when average inventory value is positive | Finance | Daily; trailing window advances with each refresh |
| Sell-through | SKU × location × selling period | `units_sold / (opening_on_hand_qty + receipts_qty) × 100` | Review when `< 50%` by the agreed selling-period end | Inventory Planner | Daily during active selling period; default period is trailing 28 days until merchandise calendar is available |
| Aged stock | SKU × location × inventory age bucket × business date | `on_hand_qty` and `on_hand_value` where inventory age is in the specified bucket | Aged exposure: inventory aged `> 90 days`; critical: `> 180 days` | Finance | Daily; age calculated from receipt/lot date, or approved proxy date when documented |
| Excess value | SKU × location × business date | `max(available_inventory_qty - target_stock_qty, 0) × unit_cost` | Review any positive excess; priority: high when excess value `≥ 10,000` in local currency, subject to Finance confirmation | Inventory Planner | Daily; target stock is demand through lead time plus safety stock |
| Fill rate | SKU × location × business date, rollable by requested quantity | `fulfilled_requested_qty / requested_qty × 100` | Red: `< 95%`; Amber: `95% to < 98%`; Green: `≥ 98%` | Store Operations | Daily; trailing 28 calendar days at summary level |

## Source and quality rules

| Input / rule | Contract |
| --- | --- |
| Available inventory | `on_hand_qty - allocated_qty - unavailable_qty`; it cannot be negative for DOS or excess calculations. Negative source positions must be retained as a quality exception and reported separately. |
| Demand | Use fulfilled sales/consumption in the approved demand source. Zero-demand days remain in the calendar denominator only for stockout-rate eligibility, not for DOS demand averaging unless the product-location is active. |
| Cost of goods sold | Finance-approved cost-of-goods-sold amount, using the same valuation basis as average inventory value. |
| Inventory value | `on_hand_qty × approved_unit_cost`; missing or non-positive cost is a Finance data-quality exception and excludes the record from value aggregations until remediated. |
| Active SKU-location | Item and location are both active on the business date and are within the agreed sellable assortment. Discontinued or not-yet-active combinations are excluded from operational scorecards unless explicitly analyzed as aged stock. |
| Rollups | Recalculate rate and ratio KPIs from their underlying numerator and denominator. Do not average percentages, DOS, or turns across SKUs/locations. |
| Late data | A refresh that misses source close must publish a stale-data flag and the latest available business date rather than silently treating stale data as current. |

## Planning assumptions

### Lost sales and stockouts

- A stockout day occurs when a SKU-location is active, has eligible demand, and `available_inventory_qty ≤ 0` at the agreed availability snapshot.
- The initial release uses observed fulfilled demand; lost sales are **not imputed** into demand, COGS, sell-through, or turns.
- Where an approved demand signal exists on a stockout day, it is retained for opportunity analysis but remains clearly labeled as observed demand, forecast demand, or lost-sales estimate.
- Partial availability is not a stockout by default; it affects fill rate when requested and fulfilled quantities are available.

### Lead time

- Use the approved supplier × SKU lead time when present; otherwise use supplier default lead time, then the category default.
- Lead time is measured in calendar days until a working-day calendar is formally approved.
- Missing lead time after all fallback levels is a replenishment data-quality exception; no stockout-risk priority is calculated without an explicit default.

### Safety stock

- `safety_stock_days` is supplied by Inventory Planning where available.
- The launch default is **7 calendar days** only for active SKU-locations with a valid demand history and no approved setting.
- Safety stock is converted to quantity as `average_daily_demand_qty × safety_stock_days`.
- Items with zero or unavailable demand do not receive a calculated safety-stock quantity; they are surfaced for planner review instead.

### Unavailable inventory

- Unavailable inventory includes quality hold, damaged, recalled, blocked, reserved, or otherwise non-sellable quantity, as classified by the source system.
- It is excluded from available inventory, DOS, and excess calculations, but remains visible in inventory value and a separate unavailable-inventory exception measure.
- In-transit inventory is excluded from available inventory until receipted, unless the business approves a separate confirmed-inbound metric.

## Approval and change control

Changes to formula, threshold, source, or default require agreement from the named KPI owner and documentation in this catalog before the next production refresh. Finance must additionally approve valuation and currency changes. The data team records implementation date and test evidence with any approved change.
