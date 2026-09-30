-- Governed SKU-store-day stock risk views over the curated inventory star.
-- Days of supply is null for zero/missing demand; setup exceptions are surfaced explicitly.

CREATE OR REPLACE VIEW vw_stock_risk_daily AS
WITH base AS (
  SELECT
    d.calendar_date AS business_date,
    p.sku_id, p.sku_name, p.category, p.product_status,
    s.store_id, s.store_name, s.region,
    i.on_hand_qty, i.available_inventory_qty, i.unit_cost, i.inventory_value,
    rd.average_daily_demand_qty, rd.demand_status,
    rp.lead_time_days, rp.safety_stock_days,
    COALESCE(NULLIF(rp.target_stock_qty, 0), rd.average_daily_demand_qty * (rp.lead_time_days + rp.safety_stock_days)) AS effective_target_stock_qty,
    i.reorder_parameter_key,
    CASE WHEN rd.average_daily_demand_qty > 0
      THEN i.available_inventory_qty / rd.average_daily_demand_qty
    END AS days_of_supply
  FROM fact_inventory_daily i
  JOIN dim_date d ON d.date_key = i.date_key
  JOIN dim_sku p ON p.sku_key = i.sku_key
  JOIN dim_store s ON s.store_key = i.store_key
  LEFT JOIN fact_inventory_demand_daily rd
    ON rd.date_key = i.date_key AND rd.sku_key = i.sku_key AND rd.store_key = i.store_key
  LEFT JOIN dim_reorder_parameter rp ON rp.reorder_parameter_key = i.reorder_parameter_key
)
SELECT
  *,
  lead_time_days + safety_stock_days AS reorder_cover_days,
  CASE WHEN UPPER(COALESCE(product_status, 'ACTIVE')) IN ('ACTIVE', 'LIVE')
        AND days_of_supply IS NOT NULL
        AND lead_time_days IS NOT NULL AND safety_stock_days IS NOT NULL
        AND days_of_supply < lead_time_days + safety_stock_days
    THEN TRUE ELSE FALSE END AS stockout_risk_flag,
  CASE WHEN UPPER(COALESCE(product_status, 'ACTIVE')) IN ('ACTIVE', 'LIVE')
        AND COALESCE(available_inventory_qty, 0) <= 0
    THEN TRUE ELSE FALSE END AS active_stockout_flag,
  CASE WHEN UPPER(COALESCE(product_status, 'ACTIVE')) IN ('ACTIVE', 'LIVE')
        AND days_of_supply IS NOT NULL
        AND lead_time_days IS NOT NULL AND safety_stock_days IS NOT NULL
        AND days_of_supply < lead_time_days + safety_stock_days + 7
    THEN TRUE ELSE FALSE END AS low_cover_flag,
  CASE WHEN UPPER(COALESCE(product_status, 'ACTIVE')) IN ('ACTIVE', 'LIVE')
        AND effective_target_stock_qty IS NOT NULL
        AND available_inventory_qty > effective_target_stock_qty
    THEN TRUE ELSE FALSE END AS excess_inventory_flag,
  GREATEST(COALESCE(available_inventory_qty, 0) - COALESCE(effective_target_stock_qty, 0), 0) AS excess_inventory_qty,
  CASE WHEN unit_cost IS NOT NULL THEN GREATEST(COALESCE(available_inventory_qty, 0) - COALESCE(effective_target_stock_qty, 0), 0) * unit_cost END AS excess_inventory_value,
  CASE
    WHEN UPPER(COALESCE(product_status, 'ACTIVE')) NOT IN ('ACTIVE', 'LIVE') THEN 'INACTIVE_PRODUCT'
    WHEN COALESCE(available_inventory_qty, 0) <= 0 THEN 'ACTIVE_STOCKOUT'
    WHEN days_of_supply IS NULL THEN 'DEMAND_UNAVAILABLE_OR_ZERO'
    WHEN lead_time_days IS NULL OR safety_stock_days IS NULL THEN 'MISSING_REORDER_PARAMETERS'
    WHEN days_of_supply < lead_time_days + safety_stock_days THEN 'COVER_BELOW_LEAD_TIME_PLUS_SAFETY_STOCK'
    WHEN days_of_supply < lead_time_days + safety_stock_days + 7 THEN 'COVER_WITHIN_SEVEN_DAY_BUFFER'
    ELSE 'COVER_ABOVE_REPLENISHMENT_BUFFER'
  END AS risk_reason,
  CASE
    WHEN lead_time_days IS NULL OR safety_stock_days IS NULL THEN 'MISSING_REORDER_PARAMETERS'
    WHEN unit_cost IS NULL THEN 'MISSING_UNIT_COST'
    ELSE NULL
  END AS risk_setup_exception
FROM base;

CREATE OR REPLACE VIEW vw_stock_risk_exceptions AS
SELECT *
FROM vw_stock_risk_daily
WHERE risk_setup_exception IS NOT NULL;
