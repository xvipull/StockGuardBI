# Inventory Source-to-Target Mapping and Data Dictionary

## Conformance standards

- Source extracts must carry an extract timestamp and source-system identifier.
- `business_date`, `sale_date`, `receipt_date`, `transfer_date`, and `order_date` use ISO-8601 dates (`YYYY-MM-DD`). Timestamps use UTC ISO-8601 timestamps.
- Business keys are trimmed, uppercased where applicable, and retained alongside their source values when conformed.
- Monetary values are decimal values in the source currency. Quantity values use the product base unit of measure.
- Each staged record includes `source_system`, `source_extract_ts`, `ingested_at_ts`, and `record_hash` for lineage and rerun traceability.

## Source-to-target mapping

| Source entity | Source grain | Target table | Target grain | Business key | Transformation / quality rule |
| --- | --- | --- | --- | --- | --- |
| Inventory snapshot | SKU × store × snapshot date | `stg_inventory_snapshot` → `fct_sku_store_day` | SKU × store × business date | `sku_id`, `store_id`, `business_date` | Select latest valid snapshot per key/date; separate allocated, unavailable, in-transit, and on-hand quantities |
| Sales | SKU × store × transaction or sales date | `stg_sales` → `fct_sales_day` | SKU × store × sales date | `sku_id`, `store_id`, `sale_date` | Aggregate transactions to daily quantity, requested quantity, fulfilled quantity, net sales, and COGS |
| Receipts | SKU × store × receipt event | `stg_receipts` → `fct_receipts_day` | SKU × store × receipt date | `receipt_id`, `receipt_line_id` | Deduplicate receipt line events; aggregate received quantity/value daily; retain receipt/lot date for aging |
| Transfers | SKU × origin store × destination store × transfer event | `stg_transfers` → `fct_transfers_day` | SKU × origin × destination × transfer date | `transfer_id`, `transfer_line_id` | Classify shipped, received, cancelled, and in-transit quantities; do not add in-transit quantity to available stock |
| Reorder parameters | SKU × replenishment location × effective date | `stg_reorder_parameters` → `dim_reorder_parameter_daily` | SKU × store × business date | `sku_id`, `store_id`, `effective_date` | Apply latest approved effective record as of business date; flag missing lead time or safety stock defaults |
| Products | SKU version / effective date | `stg_products` → `dim_product` | SKU × effective date range | `sku_id` | Standardize hierarchy and base unit; preserve active/discontinued status and standard cost |
| Stores | Store version / effective date | `stg_stores` → `dim_store` | store × effective date range | `store_id` | Standardize store, region, format, and active status; validate unique store key |
| Calendar | Calendar date | `dim_calendar` | calendar date | `calendar_date` | Generate or acquire a continuous date spine; enrich fiscal period and working-day flags when approved |

## Data dictionary

### `fct_sku_store_day` — inventory snapshot fact

Grain: one conformed SKU, store, and business date.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `business_date` | date | Yes | Date of source inventory close. |
| `sku_id` | string | Yes | Conformed sellable SKU key; must exist in `dim_product`. |
| `store_id` | string | Yes | Conformed store/warehouse key; must exist in `dim_store`. |
| `on_hand_qty` | decimal(18,3) | Yes | Physically recorded on-hand quantity; may be negative only when retained as an exception. |
| `allocated_qty` | decimal(18,3) | Yes | Quantity reserved for orders or commitments. |
| `unavailable_qty` | decimal(18,3) | Yes | Blocked, damaged, recalled, held, or otherwise non-sellable quantity. |
| `in_transit_qty` | decimal(18,3) | Yes | Confirmed shipped but unreceived transfer quantity; excluded from available inventory. |
| `available_inventory_qty` | decimal(18,3) | Derived | `max(on_hand_qty - allocated_qty - unavailable_qty, 0)`. |
| `unit_cost` | decimal(18,4) | Yes | Approved unit inventory cost in source currency. |
| `inventory_value` | decimal(18,2) | Derived | `on_hand_qty × unit_cost`; negatives are separately flagged. |
| `oldest_receipt_date` | date | No | Earliest associated receipt/lot date used for aging. |
| `inventory_age_days` | integer | Derived | `business_date - oldest_receipt_date`; null when no validated date exists. |

### `fct_sales_day` — sales and fulfillment fact

Grain: one conformed SKU, store, and sale date.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `sale_date` | date | Yes | Trading date from the approved sales source. |
| `sku_id` | string | Yes | Conformed SKU key. |
| `store_id` | string | Yes | Conformed selling location key. |
| `requested_qty` | decimal(18,3) | No | Demand requested by customers/orders; required for fill rate when available. |
| `fulfilled_qty` | decimal(18,3) | Yes | Quantity actually fulfilled or sold. |
| `units_sold_qty` | decimal(18,3) | Yes | Net units sold after approved returns treatment. |
| `net_sales_amount` | decimal(18,2) | No | Net sales in source currency. |
| `cogs_amount` | decimal(18,2) | No | Finance-approved cost of goods sold. |
| `return_qty` | decimal(18,3) | Yes | Returned units; zero if not supplied. |

### `fct_receipts_day` — receipt fact

Grain: one receipt line; daily mart aggregates by SKU, store, and receipt date.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `receipt_id` | string | Yes | Source receipt document identifier. |
| `receipt_line_id` | string | Yes | Unique receipt-line identifier within receipt. |
| `receipt_date` | date | Yes | Date received into inventory. |
| `sku_id` | string | Yes | Conformed SKU key. |
| `store_id` | string | Yes | Receiving location key. |
| `received_qty` | decimal(18,3) | Yes | Accepted received quantity. |
| `received_unit_cost` | decimal(18,4) | No | Unit cost recorded for receipt. |
| `lot_id` | string | No | Lot/batch identifier when tracked. |

### `fct_transfers_day` — transfer fact

Grain: one transfer line; daily mart aggregates by SKU, origin, destination, and transfer date.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `transfer_id` | string | Yes | Source transfer document identifier. |
| `transfer_line_id` | string | Yes | Unique transfer-line identifier. |
| `transfer_date` | date | Yes | Date the transfer was created or shipped; event type identifies its state. |
| `sku_id` | string | Yes | Conformed SKU key. |
| `origin_store_id` | string | Yes | Shipping location key. |
| `destination_store_id` | string | Yes | Receiving location key. |
| `transfer_qty` | decimal(18,3) | Yes | Quantity for this event/line. |
| `transfer_status` | string | Yes | One of `SHIPPED`, `RECEIVED`, `CANCELLED`, `IN_TRANSIT`. |
| `expected_receipt_date` | date | No | Expected destination receipt date. |

### `dim_reorder_parameter_daily` — effective reorder parameters

Grain: one SKU, replenishment location, and effective date range.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `sku_id` | string | Yes | Conformed SKU key. |
| `store_id` | string | Yes | Replenishment location key. |
| `effective_from_date` | date | Yes | First business date on which parameters apply. |
| `effective_to_date` | date | No | Last applicable date; null means current. |
| `lead_time_days` | integer | No | Approved calendar-day replenishment lead time. |
| `safety_stock_days` | decimal(8,2) | No | Approved safety stock cover in days. |
| `reorder_point_qty` | decimal(18,3) | No | Source-approved reorder point, retained for comparison. |
| `target_stock_qty` | decimal(18,3) | Derived | Demand through lead time plus safety stock, unless approved source target is selected. |

### `dim_product` — product master

Grain: one SKU and effective date range.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `sku_id` | string | Yes | Stable conformed product key. |
| `sku_name` | string | Yes | Product description. |
| `category` | string | Yes | Approved product category. |
| `subcategory` | string | No | Approved product subcategory. |
| `brand` | string | No | Product brand. |
| `base_uom` | string | Yes | Base stocking unit of measure. |
| `product_status` | string | Yes | `ACTIVE`, `DISCONTINUED`, or approved status value. |
| `standard_unit_cost` | decimal(18,4) | No | Finance-approved reference unit cost. |

### `dim_store` — store and location master

Grain: one store and effective date range.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `store_id` | string | Yes | Stable conformed location key. |
| `store_name` | string | Yes | Business-readable location name. |
| `store_type` | string | Yes | Store, warehouse, distribution center, or approved type. |
| `region` | string | No | Operating region. |
| `format` | string | No | Store format or channel. |
| `store_status` | string | Yes | `ACTIVE`, `CLOSED`, or approved status. |

### `dim_calendar` — reporting calendar

Grain: one calendar date.

| Field | Type | Required | Definition / rule |
| --- | --- | --- | --- |
| `calendar_date` | date | Yes | Continuous Gregorian date. |
| `day_of_week` | integer | Yes | ISO day number, 1 (Monday) through 7 (Sunday). |
| `week_start_date` | date | Yes | Monday of the ISO week. |
| `month_start_date` | date | Yes | First date of Gregorian month. |
| `fiscal_period` | string | No | Finance-approved fiscal period when supplied. |
| `is_working_day` | boolean | Yes | Default Monday–Friday; replace with approved holiday calendar when available. |
