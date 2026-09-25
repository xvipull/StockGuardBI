-- StockGuard BI core KPI views
-- Dialect: PostgreSQL-compatible SQL. Adapt DATE_TRUNC / INTERVAL syntax for the target warehouse.
-- All rates are calculated from additive numerators and denominators; never average a rate across entities.

CREATE OR REPLACE VIEW vw_inventory_on_hand_daily AS
SELECT
  d.calendar_date AS business_date,
  p.sku_id, p.sku_name, p.category, p.subcategory,
  s.store_id, s.store_name, s.region, s.store_type,
  i.on_hand_qty, i.allocated_qty, i.unavailable_qty, i.in_transit_qty,
  i.available_inventory_qty, i.unit_cost, i.inventory_value, i.inventory_age_days,
  i.reorder_parameter_key
FROM fact_inventory_daily i
JOIN dim_date d ON d.date_key = i.date_key
JOIN dim_sku p ON p.sku_key = i.sku_key
JOIN dim_store s ON s.store_key = i.store_key;

-- Monthly sell-through = units sold / (opening inventory + receipts).
-- If an item/location has no prior observed day, its first observed closing balance is used as the opening proxy.
CREATE OR REPLACE VIEW vw_inventory_sell_through_monthly AS
WITH daily_inventory AS (
  SELECT
    i.date_key, i.sku_key, i.store_key, d.calendar_date,
    i.on_hand_qty,
    COALESCE(LAG(i.on_hand_qty) OVER (PARTITION BY i.sku_key, i.store_key ORDER BY d.calendar_date), i.on_hand_qty) AS opening_stock_qty
  FROM fact_inventory_daily i
  JOIN dim_date d ON d.date_key = i.date_key
), period_inventory AS (
  SELECT
    DATE_TRUNC('month', calendar_date)::date AS period_start,
    sku_key, store_key,
    MAX(opening_stock_qty) FILTER (WHERE sequence_in_period = 1) AS opening_stock_qty
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY sku_key, store_key, DATE_TRUNC('month', calendar_date) ORDER BY calendar_date) AS sequence_in_period
    FROM daily_inventory
  ) ranked
  GROUP BY 1, 2, 3
), period_sales AS (
  SELECT DATE_TRUNC('month', d.calendar_date)::date AS period_start, sku_key, store_key, SUM(units_sold_qty) AS units_sold_qty
  FROM fact_sales_daily f JOIN dim_date d ON d.date_key = f.date_key
  GROUP BY 1, 2, 3
), period_receipts AS (
  SELECT DATE_TRUNC('month', d.calendar_date)::date AS period_start, sku_key, store_key, SUM(received_qty) AS receipts_qty
  FROM fact_receipts f JOIN dim_date d ON d.date_key = f.date_key
  GROUP BY 1, 2, 3
)
SELECT
  pi.period_start, p.sku_id, s.store_id,
  pi.opening_stock_qty, COALESCE(pr.receipts_qty, 0) AS receipts_qty,
  COALESCE(ps.units_sold_qty, 0) AS units_sold_qty,
  pi.opening_stock_qty + COALESCE(pr.receipts_qty, 0) AS sell_through_denominator_qty,
  CASE WHEN pi.opening_stock_qty + COALESCE(pr.receipts_qty, 0) > 0
    THEN COALESCE(ps.units_sold_qty, 0) / (pi.opening_stock_qty + COALESCE(pr.receipts_qty, 0))
  END AS sell_through_rate
FROM period_inventory pi
JOIN dim_sku p ON p.sku_key = pi.sku_key
JOIN dim_store s ON s.store_key = pi.store_key
LEFT JOIN period_sales ps ON (ps.period_start, ps.sku_key, ps.store_key) = (pi.period_start, pi.sku_key, pi.store_key)
LEFT JOIN period_receipts pr ON (pr.period_start, pr.sku_key, pr.store_key) = (pi.period_start, pi.sku_key, pi.store_key);

-- Daily fill rate; aggregate requested and fulfilled quantities for period-level reporting.
CREATE OR REPLACE VIEW vw_inventory_fill_rate_daily AS
SELECT
  d.calendar_date AS business_date, p.sku_id, s.store_id,
  f.requested_qty, f.fulfilled_qty,
  CASE WHEN f.requested_qty > 0 THEN f.fulfilled_qty / f.requested_qty END AS fill_rate
FROM fact_sales_daily f
JOIN dim_date d ON d.date_key = f.date_key
JOIN dim_sku p ON p.sku_key = f.sku_key
JOIN dim_store s ON s.store_key = f.store_key;

-- Trailing 365-day inventory turns = trailing COGS / average daily inventory value.
-- Rows with no positive average inventory value return NULL rather than an invalid ratio.
CREATE OR REPLACE VIEW vw_inventory_turns_365d AS
SELECT
  ending.calendar_date AS business_date, p.sku_id, s.store_id,
  COALESCE(SUM(sales.cogs_amount), 0) AS trailing_365d_cogs_amount,
  AVG(history.inventory_value) AS trailing_365d_average_inventory_value,
  CASE WHEN AVG(history.inventory_value) > 0
    THEN COALESCE(SUM(sales.cogs_amount), 0) / AVG(history.inventory_value)
  END AS inventory_turns_365d
FROM fact_inventory_daily ending
JOIN dim_date ending_date ON ending_date.date_key = ending.date_key
JOIN dim_sku p ON p.sku_key = ending.sku_key
JOIN dim_store s ON s.store_key = ending.store_key
LEFT JOIN fact_inventory_daily history ON history.sku_key = ending.sku_key AND history.store_key = ending.store_key
LEFT JOIN dim_date history_date ON history_date.date_key = history.date_key
LEFT JOIN fact_sales_daily sales ON sales.sku_key = history.sku_key AND sales.store_key = history.store_key AND sales.date_key = history.date_key
WHERE history_date.calendar_date BETWEEN ending_date.calendar_date - INTERVAL '364 days' AND ending_date.calendar_date
GROUP BY ending.calendar_date, p.sku_id, s.store_id;

-- Trailing 28-day stockout rate, eligible only on days with requested demand.
CREATE OR REPLACE VIEW vw_inventory_stockout_rate_28d AS
SELECT
  ending.calendar_date AS business_date, p.sku_id, s.store_id,
  COUNT(*) FILTER (WHERE COALESCE(sales.requested_qty, 0) > 0) AS eligible_demand_days,
  COUNT(*) FILTER (WHERE COALESCE(sales.requested_qty, 0) > 0 AND history.available_inventory_qty <= 0) AS stockout_demand_days,
  CASE WHEN COUNT(*) FILTER (WHERE COALESCE(sales.requested_qty, 0) > 0) > 0
    THEN COUNT(*) FILTER (WHERE COALESCE(sales.requested_qty, 0) > 0 AND history.available_inventory_qty <= 0)::decimal
      / COUNT(*) FILTER (WHERE COALESCE(sales.requested_qty, 0) > 0)
  END AS stockout_rate_28d
FROM fact_inventory_daily ending
JOIN dim_date ending_date ON ending_date.date_key = ending.date_key
JOIN dim_sku p ON p.sku_key = ending.sku_key
JOIN dim_store s ON s.store_key = ending.store_key
LEFT JOIN fact_inventory_daily history ON history.sku_key = ending.sku_key AND history.store_key = ending.store_key
LEFT JOIN dim_date history_date ON history_date.date_key = history.date_key
LEFT JOIN fact_sales_daily sales ON sales.sku_key = history.sku_key AND sales.store_key = history.store_key AND sales.date_key = history.date_key
WHERE history_date.calendar_date BETWEEN ending_date.calendar_date - INTERVAL '27 days' AND ending_date.calendar_date
GROUP BY ending.calendar_date, p.sku_id, s.store_id;

-- Monthly period-over-period trends. Closing inventory is the final snapshot in each month.
CREATE OR REPLACE VIEW vw_inventory_period_trends_monthly AS
WITH inventory_month AS (
  SELECT
    DATE_TRUNC('month', d.calendar_date)::date AS period_start, i.sku_key, i.store_key,
    i.on_hand_qty, i.inventory_value,
    ROW_NUMBER() OVER (PARTITION BY i.sku_key, i.store_key, DATE_TRUNC('month', d.calendar_date) ORDER BY d.calendar_date DESC) AS reverse_sequence
  FROM fact_inventory_daily i JOIN dim_date d ON d.date_key = i.date_key
), sales_month AS (
  SELECT DATE_TRUNC('month', d.calendar_date)::date AS period_start, sku_key, store_key,
         SUM(units_sold_qty) AS units_sold_qty, SUM(net_sales_amount) AS net_sales_amount
  FROM fact_sales_daily f JOIN dim_date d ON d.date_key = f.date_key
  GROUP BY 1, 2, 3
), base AS (
  SELECT im.period_start, im.sku_key, im.store_key, im.on_hand_qty AS closing_on_hand_qty, im.inventory_value AS closing_inventory_value,
         COALESCE(sm.units_sold_qty, 0) AS units_sold_qty, COALESCE(sm.net_sales_amount, 0) AS net_sales_amount
  FROM inventory_month im LEFT JOIN sales_month sm ON (sm.period_start, sm.sku_key, sm.store_key) = (im.period_start, im.sku_key, im.store_key)
  WHERE im.reverse_sequence = 1
)
SELECT
  base.period_start, p.sku_id, s.store_id, base.closing_on_hand_qty, base.closing_inventory_value, base.units_sold_qty, base.net_sales_amount,
  base.closing_on_hand_qty - LAG(base.closing_on_hand_qty) OVER (PARTITION BY base.sku_key, base.store_key ORDER BY base.period_start) AS on_hand_period_change_qty,
  base.units_sold_qty - LAG(base.units_sold_qty) OVER (PARTITION BY base.sku_key, base.store_key ORDER BY base.period_start) AS units_sold_period_change_qty,
  base.net_sales_amount - LAG(base.net_sales_amount) OVER (PARTITION BY base.sku_key, base.store_key ORDER BY base.period_start) AS net_sales_period_change_amount
FROM base
JOIN dim_sku p ON p.sku_key = base.sku_key
JOIN dim_store s ON s.store_key = base.store_key;
