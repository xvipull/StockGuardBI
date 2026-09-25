-- Validation queries for marts/inventory/kpi_views.sql.
-- Run after view deployment and before publishing a semantic model.

-- 1. Latest-date on-hand totals must reconcile to the base inventory fact.
WITH latest AS (SELECT MAX(calendar_date) AS business_date FROM dim_date)
SELECT
  (SELECT COALESCE(SUM(i.on_hand_qty), 0) FROM fact_inventory_daily i JOIN dim_date d ON d.date_key = i.date_key JOIN latest l ON l.business_date = d.calendar_date) AS fact_on_hand_qty,
  (SELECT COALESCE(SUM(on_hand_qty), 0) FROM vw_inventory_on_hand_daily v JOIN latest l ON l.business_date = v.business_date) AS view_on_hand_qty;

-- 2. Fill and sell-through rates must be within logical bounds when populated.
SELECT 'fill_rate_out_of_bounds' AS check_name, COUNT(*) AS failing_rows
FROM vw_inventory_fill_rate_daily WHERE fill_rate < 0 OR fill_rate > 1;
SELECT 'sell_through_out_of_bounds' AS check_name, COUNT(*) AS failing_rows
FROM vw_inventory_sell_through_monthly WHERE sell_through_rate < 0 OR sell_through_rate > 1;

-- 3. Stockout numerator cannot exceed its eligible-day denominator.
SELECT 'stockout_denominator_breach' AS check_name, COUNT(*) AS failing_rows
FROM vw_inventory_stockout_rate_28d WHERE stockout_demand_days > eligible_demand_days OR stockout_rate_28d < 0 OR stockout_rate_28d > 1;

-- 4. Inventory turns cannot be negative where COGS and inventory value have passed data quality controls.
SELECT 'negative_inventory_turns' AS check_name, COUNT(*) AS failing_rows
FROM vw_inventory_turns_365d WHERE inventory_turns_365d < 0;

-- 5. Facts must retain resolvable dimension keys before KPIs are trusted.
SELECT 'orphan_inventory_dimensions' AS check_name, COUNT(*) AS failing_rows
FROM fact_inventory_daily f
LEFT JOIN dim_date d ON d.date_key = f.date_key
LEFT JOIN dim_sku p ON p.sku_key = f.sku_key
LEFT JOIN dim_store s ON s.store_key = f.store_key
WHERE d.date_key IS NULL OR p.sku_key IS NULL OR s.store_key IS NULL;

-- 6. Daily sales fact must remain unique at its documented grain.
SELECT date_key, sku_key, store_key, COUNT(*) AS duplicate_rows
FROM fact_sales_daily
GROUP BY date_key, sku_key, store_key
HAVING COUNT(*) > 1;
