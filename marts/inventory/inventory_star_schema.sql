-- StockGuard BI curated inventory analytics star schema.
-- Physical storage and partition syntax should be adapted to the target warehouse.

CREATE TABLE dim_date (
  date_key INTEGER PRIMARY KEY,
  calendar_date DATE NOT NULL UNIQUE,
  day_of_week INTEGER,
  week_start_date DATE,
  month_start_date DATE,
  fiscal_period VARCHAR(32),
  is_working_day BOOLEAN
);

CREATE TABLE dim_sku (
  sku_key VARCHAR(20) PRIMARY KEY,
  sku_id VARCHAR(100) NOT NULL UNIQUE,
  sku_name VARCHAR(500), category VARCHAR(200), subcategory VARCHAR(200), brand VARCHAR(200),
  base_uom VARCHAR(20), product_status VARCHAR(30), standard_unit_cost DECIMAL(18,4)
);

CREATE TABLE dim_store (
  store_key VARCHAR(20) PRIMARY KEY,
  store_id VARCHAR(100) NOT NULL UNIQUE,
  store_name VARCHAR(500), store_type VARCHAR(50), region VARCHAR(100), format VARCHAR(100), store_status VARCHAR(30)
);

CREATE TABLE dim_reorder_parameter (
  reorder_parameter_key VARCHAR(20) PRIMARY KEY,
  sku_key VARCHAR(20) NOT NULL REFERENCES dim_sku(sku_key),
  store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key),
  effective_from_date DATE NOT NULL, effective_to_date DATE,
  lead_time_days DECIMAL(8,2), safety_stock_days DECIMAL(8,2), reorder_point_qty DECIMAL(18,3), target_stock_qty DECIMAL(18,3),
  UNIQUE (sku_key, store_key, effective_from_date)
);

CREATE TABLE fact_inventory_daily (
  inventory_daily_key VARCHAR(20) PRIMARY KEY,
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key), sku_key VARCHAR(20) NOT NULL REFERENCES dim_sku(sku_key),
  store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key), reorder_parameter_key VARCHAR(20) REFERENCES dim_reorder_parameter(reorder_parameter_key),
  on_hand_qty DECIMAL(18,3), allocated_qty DECIMAL(18,3), unavailable_qty DECIMAL(18,3), in_transit_qty DECIMAL(18,3),
  available_inventory_qty DECIMAL(18,3), unit_cost DECIMAL(18,4), inventory_value DECIMAL(18,2), inventory_age_days INTEGER,
  UNIQUE (date_key, sku_key, store_key)
);

CREATE TABLE fact_sales_daily (
  sales_daily_key VARCHAR(20) PRIMARY KEY,
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key), sku_key VARCHAR(20) NOT NULL REFERENCES dim_sku(sku_key), store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key),
  requested_qty DECIMAL(18,3), fulfilled_qty DECIMAL(18,3), units_sold_qty DECIMAL(18,3), return_qty DECIMAL(18,3), net_sales_amount DECIMAL(18,2), cogs_amount DECIMAL(18,2),
  UNIQUE (date_key, sku_key, store_key)
);

CREATE TABLE fact_receipts (
  receipt_fact_key VARCHAR(20) PRIMARY KEY,
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key), sku_key VARCHAR(20) NOT NULL REFERENCES dim_sku(sku_key), store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key),
  receipt_id VARCHAR(100) NOT NULL, receipt_line_id VARCHAR(100) NOT NULL, received_qty DECIMAL(18,3), received_unit_cost DECIMAL(18,4), lot_id VARCHAR(100),
  UNIQUE (receipt_id, receipt_line_id)
);

CREATE TABLE fact_transfers (
  transfer_fact_key VARCHAR(20) PRIMARY KEY,
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key), sku_key VARCHAR(20) NOT NULL REFERENCES dim_sku(sku_key),
  origin_store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key), destination_store_key VARCHAR(20) NOT NULL REFERENCES dim_store(store_key),
  transfer_id VARCHAR(100) NOT NULL, transfer_line_id VARCHAR(100) NOT NULL, transfer_qty DECIMAL(18,3), transfer_status VARCHAR(30), expected_receipt_date DATE,
  UNIQUE (transfer_id, transfer_line_id)
);
