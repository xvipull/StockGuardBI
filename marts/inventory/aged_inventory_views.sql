-- Aged inventory views over the persisted mart_aged_inventory_daily table.
-- Inventory age comes from receipt/lot age (or the documented approved proxy).

CREATE OR REPLACE VIEW vw_aged_inventory_daily AS
SELECT
  business_date, sku_id, store_id, inventory_age_days, age_bucket,
  on_hand_qty, unit_cost, aged_inventory_value,
  aged_stock_flag, critical_aged_stock_flag,
  last_sale_date, days_since_last_sale, movement_status,
  excess_inventory_qty, excess_inventory_value, risk_setup_exception
FROM mart_aged_inventory_daily;

CREATE OR REPLACE VIEW vw_aged_inventory_exposure AS
SELECT
  business_date,
  age_bucket,
  COUNT(*) AS sku_store_positions,
  SUM(on_hand_qty) AS on_hand_qty,
  SUM(aged_inventory_value) AS inventory_value,
  SUM(CASE WHEN aged_stock_flag = 'true' THEN aged_inventory_value ELSE 0 END) AS aged_over_90d_value,
  SUM(CASE WHEN critical_aged_stock_flag = 'true' THEN aged_inventory_value ELSE 0 END) AS aged_over_180d_value,
  SUM(excess_inventory_value) AS excess_inventory_value
FROM mart_aged_inventory_daily
GROUP BY business_date, age_bucket;

CREATE OR REPLACE VIEW vw_slow_and_dead_stock AS
SELECT *
FROM mart_aged_inventory_daily
WHERE movement_status IN ('SLOW_MOVING', 'DEAD_STOCK', 'NO_SALES_HISTORY');
