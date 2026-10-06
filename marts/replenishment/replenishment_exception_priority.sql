-- =============================================================================
-- Replenishment Exception Priority Mart
-- mart: marts/replenishment/replenishment_exception_priority.sql
-- =============================================================================
-- Aggregates the scored exception file into analytical views for planning.
-- Assumes mart_replenishment_exceptions is registered as a table/view or
-- loaded via the warehouse ingest step.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Full exception mart with computed rank within business_date
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart_replenishment_exceptions_ranked AS
SELECT
    business_date,
    sku_id,
    store_id,
    product_status,
    demand_status,
    -- Inventory position
    CAST(on_hand_qty          AS DECIMAL(18, 4)) AS on_hand_qty,
    CAST(available_inventory_qty AS DECIMAL(18, 4)) AS available_inventory_qty,
    CAST(days_of_supply       AS DECIMAL(18, 4)) AS days_of_supply,
    CAST(lead_time_days       AS DECIMAL(18, 4)) AS lead_time_days,
    CAST(safety_stock_days    AS DECIMAL(18, 4)) AS safety_stock_days,
    CAST(reorder_cover_days   AS DECIMAL(18, 4)) AS reorder_cover_days,
    -- Demand signals
    CAST(average_daily_demand_qty AS DECIMAL(18, 4)) AS average_daily_demand_qty,
    CAST(requested_qty        AS DECIMAL(18, 4)) AS requested_qty,
    CAST(fulfilled_qty        AS DECIMAL(18, 4)) AS fulfilled_qty,
    CAST(requested_qty - fulfilled_qty AS DECIMAL(18, 4)) AS unfulfilled_qty,
    -- Value signals
    CAST(unit_cost            AS DECIMAL(18, 4)) AS unit_cost,
    CAST(unit_margin          AS DECIMAL(18, 4)) AS unit_margin,
    CAST(on_hand_qty * COALESCE(unit_margin, unit_cost) AS DECIMAL(18, 4)) AS at_risk_value,
    -- Stock condition
    aged_stock_flag,
    critical_aged_stock_flag,
    movement_status,
    excess_inventory_flag,
    CAST(excess_inventory_qty  AS DECIMAL(18, 4)) AS excess_inventory_qty,
    CAST(excess_inventory_value AS DECIMAL(18, 4)) AS excess_inventory_value,
    -- Risk flags (from upstream risk pipeline)
    active_stockout_flag,
    stockout_risk_flag,
    low_cover_flag,
    risk_reason,
    -- Exception priority output
    CAST(priority_score AS DECIMAL(10, 6)) AS priority_score,
    priority_band,
    reason_codes,
    input_exception_codes,
    exception_triggered,
    -- Rank within date: 1 = most urgent
    RANK() OVER (
        PARTITION BY business_date
        ORDER BY
            CASE priority_band
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH'     THEN 2
                WHEN 'MEDIUM'   THEN 3
                WHEN 'LOW'      THEN 4
                WHEN 'MONITOR'  THEN 5
                ELSE 6
            END,
            CAST(priority_score AS DECIMAL(10, 6)) DESC
    ) AS exception_priority_rank
FROM mart_replenishment_exceptions;


-- ---------------------------------------------------------------------------
-- 2. Daily priority band summary
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart_replenishment_band_summary AS
SELECT
    business_date,
    priority_band,
    COUNT(*) AS exception_count,
    SUM(CAST(on_hand_qty * COALESCE(unit_margin, unit_cost) AS DECIMAL(18, 4))) AS total_at_risk_value,
    AVG(CAST(days_of_supply AS DECIMAL(18, 4))) AS avg_days_of_supply,
    SUM(CAST(requested_qty - fulfilled_qty AS DECIMAL(18, 4))) AS total_unfulfilled_qty
FROM mart_replenishment_exceptions
WHERE exception_triggered = 'true'
GROUP BY business_date, priority_band;


-- ---------------------------------------------------------------------------
-- 3. Reason code frequency summary
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart_replenishment_reason_summary AS
WITH reason_exploded AS (
    SELECT
        business_date,
        sku_id,
        store_id,
        TRIM(reason_code_value) AS reason_code,
        CAST(priority_score AS DECIMAL(10, 6)) AS priority_score
    FROM mart_replenishment_exceptions
    -- Cross-join with a reason-code split (compatible with most SQL dialects)
    -- For Spark SQL / Hive use LATERAL VIEW explode(split(reason_codes, ';'))
    CROSS JOIN UNNEST(STRING_TO_ARRAY(reason_codes, ';')) AS t(reason_code_value)
    WHERE exception_triggered = 'true'
      AND reason_codes IS NOT NULL
      AND reason_codes <> ''
)
SELECT
    business_date,
    reason_code,
    COUNT(*) AS occurrence_count,
    AVG(priority_score) AS avg_priority_score
FROM reason_exploded
GROUP BY business_date, reason_code
ORDER BY business_date, occurrence_count DESC;


-- ---------------------------------------------------------------------------
-- 4. Store-level replenishment urgency rollup
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart_replenishment_store_urgency AS
SELECT
    business_date,
    store_id,
    COUNT(*) AS total_exceptions,
    SUM(CASE WHEN priority_band = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_count,
    SUM(CASE WHEN priority_band = 'HIGH'     THEN 1 ELSE 0 END) AS high_count,
    SUM(CASE WHEN priority_band = 'MEDIUM'   THEN 1 ELSE 0 END) AS medium_count,
    SUM(CASE WHEN active_stockout_flag = 'true' THEN 1 ELSE 0 END) AS active_stockout_count,
    SUM(CAST(requested_qty - fulfilled_qty AS DECIMAL(18, 4))) AS total_unfulfilled_qty,
    SUM(CAST(on_hand_qty * COALESCE(unit_margin, unit_cost) AS DECIMAL(18, 4))) AS total_at_risk_value,
    AVG(CAST(days_of_supply AS DECIMAL(18, 4))) AS avg_days_of_supply
FROM mart_replenishment_exceptions
WHERE exception_triggered = 'true'
GROUP BY business_date, store_id
ORDER BY business_date, total_at_risk_value DESC;


-- ---------------------------------------------------------------------------
-- 5. SKU-level cross-store exception heat map
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart_replenishment_sku_heatmap AS
SELECT
    business_date,
    sku_id,
    COUNT(DISTINCT store_id)                                        AS stores_with_exception,
    SUM(CASE WHEN priority_band = 'CRITICAL' THEN 1 ELSE 0 END)    AS critical_store_count,
    MIN(CAST(days_of_supply AS DECIMAL(18, 4)))                     AS min_dos_across_stores,
    MAX(CAST(priority_score AS DECIMAL(10, 6)))                     AS max_priority_score,
    -- First-seen reason codes (representative sample)
    MAX(reason_codes)                                               AS sample_reason_codes
FROM mart_replenishment_exceptions
WHERE exception_triggered = 'true'
GROUP BY business_date, sku_id
HAVING COUNT(DISTINCT store_id) > 1
ORDER BY business_date, critical_store_count DESC, max_priority_score DESC;
