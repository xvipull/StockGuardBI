# PySpark Pipeline Performance and Optimization

This document details the benchmarking, performance optimization, and pipeline hardening of StockGuard BI's PySpark rolling demand pipeline (`src/transforms/rolling_demand.py`).

---

## 1. Pipeline Overview

The PySpark rolling demand pipeline calculates SKU-store level rolling demand metrics and Days of Supply (DOS) over a 28-day window:

1. **Daily Sales Aggregation**: Sums granular daily sales facts into `daily_demand_qty` per `(business_date, sku_id, store_id)`.
2. **Spine Left Join**: Joins daily inventory facts with sales daily aggregates.
3. **Launch Date & Age Evaluation**: Computes the first observed business date (`sku_store_launch_date`) and SKU-store age in days.
4. **Rolling Demand Window**: Evaluates 28-day rolling demand sum (`rolling_demand_qty`) and observed vs expected window days (`is_complete_history`).
5. **Demand Status & Days of Supply**: Categorizes status into `ESTABLISHED`, `NEWLY_LAUNCHED`, `ZERO_DEMAND`, `NEWLY_LAUNCHED_ZERO_DEMAND`, or `INCOMPLETE_HISTORY`, computing `average_daily_demand_qty` and `days_of_supply`.

---

## 2. Identified Performance Bottlenecks

1. **Unbounded Window Frame State**:
   * *Baseline Implementation*:
     ```python
     history = Window.partitionBy("sku_id", "store_id").orderBy("business_date").rowsBetween(Window.unboundedPreceding, Window.currentRow)
     _first_observed_date = F.min("business_date").over(history)
     ```
   * *Bottleneck*: Forced Spark to maintain an ordered streaming window frame state (`rowsBetween`) across every business date per SKU-store partition, increasing JVM heap memory pressure and shuffle serialization overhead.

2. **Shuffle Join Overhead**:
   * *Baseline Implementation*: Standard Spark left join between `daily_inventory` and `sales_by_day` on `(business_date, sku_id, store_id)` triggered a full SortMergeJoin with two-sided data shuffle.

3. **Partition Skew & Default Partitioning**:
   * Unpartitioned input matrices relied on default Spark partition counts (200 partitions or single partition in local mode), causing task overhead on smaller datasets or partition skew on high-volume stores.

---

## 3. Applied Optimizations & Hardening

### Optimization 1: Static Partition Min (Un-ordered Window Frame)
Replacing the ordered unbounded window with an un-ordered partition-level minimum expression:
```python
# Optimized static partition min (single-pass aggregate without row window frame)
first_observed_col = F.min("business_date").over(Window.partitionBy("sku_id", "store_id"))
```
* **Impact**: Eliminates row ordering overhead across history frames while producing 100% mathematically identical launch date results.

### Optimization 2: Broadcast Join Strategy
When `daily_sales` is smaller than the full daily inventory matrix, enabling `--use-broadcast` wraps sales in a broadcast hint:
```python
sales_by_day = F.broadcast(sales_by_day)
```
* **Impact**: Replaces shuffle-heavy `SortMergeJoin` with a fast local `BroadcastHashJoin`, reducing network I/O and stage count.

### Optimization 3: Partition Alignment & Adaptive Query Execution (AQE)
Added partition tuning (`num_partitions` and `repartition_keys`) alongside AQE configuration in `get_spark_session()`:
```python
builder = (
    builder
    .config("spark.sql.shuffle.partitions", str(num_partitions))
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
)
```

### Optimization 4: SparkSession Hardening & Local IP Binding
Hardened session initialization with environment auto-detection and local driver IP binding (`SPARK_LOCAL_IP=127.0.0.1` and `spark.driver.host=127.0.0.1`) to ensure zero-configuration execution across macOS and Linux developer environments.

---

## 4. Empirical Benchmark Results

Benchmarking executed via `scripts/benchmark_spark_pipeline.py` on synthetic representative retail volumes:

| Scale (SKUs × Stores × Days) | Total Inventory Rows | Baseline Runtime (s) | Optimized Runtime (s) | Speedup Factor | Optimized Throughput (rows/sec) |
| :---------------------------- | :------------------- | :------------------- | :-------------------- | :------------- | :------------------------------ |
| **50 × 10 × 28** | 14,000 | 0.529 s | 0.311 s | **1.70×** | 45,024 rows/s |
| **200 × 20 × 28** | 112,000 | 0.502 s | 0.376 s | **1.34×** | 297,635 rows/s |
| **500 × 50 × 28** | 700,000 | 0.388 s | 0.315 s | **1.23×** | **2,224,655 rows/s** |

> Benchmark JSON metrics are exported to `data/benchmarks/spark_pipeline_benchmark.json`.

---

## 5. Scalability Limitations & Recommendations

1. **Driver Memory Limit**:
   * For datasets > 10 million rows, default driver memory (`1g`) must be scaled to `--driver-memory 4g` or higher when collecting output metrics.
2. **Broadcast Threshold**:
   * Broadcast joins should only be enabled when `daily_sales` fits comfortably within `spark.sql.autoBroadcastJoinThreshold` (default 10 MB). For massive multi-region sales tables, prefer hash repartitioning on `(sku_id, store_id)`.
3. **Cluster Sizing & Sizing Formula**:
   * **Target partition size**: 100 MB to 200 MB per partition.
   * **Recommended Partition Count**: `total_input_size_mb / 128MB` or `2 * total_cpu_cores`.

---

## 6. Execution & Usage Reference

### Running Benchmarks
```bash
# Run quick benchmark suite (~1.4k to 56k rows)
python3 scripts/benchmark_spark_pipeline.py --quick

# Run full benchmark suite (14k to 700k rows)
python3 scripts/benchmark_spark_pipeline.py
```

### Executing Rolling Demand Transform
```bash
python3 src/transforms/rolling_demand.py \
  --inventory-input /path/to/fact_inventory_daily.parquet \
  --sales-input /path/to/fact_sales_daily.parquet \
  --output /path/to/inventory_demand_daily.parquet \
  --use-broadcast \
  --repartition-keys \
  --num-partitions 16 \
  --explain
```
