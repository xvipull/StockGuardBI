#!/usr/bin/env python3
"""Benchmark and profile PySpark rolling demand pipeline under representative inventory volume."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Auto-detect OpenJDK if JAVA_HOME is not set
if not shutil.which("java") and "JAVA_HOME" not in os.environ:
    for candidate in ["/opt/homebrew/opt/openjdk@17", "/opt/homebrew/opt/openjdk", "/usr/local/opt/openjdk@17", "/usr/local/opt/openjdk"]:
        if os.path.exists(candidate):
            os.environ["JAVA_HOME"] = candidate
            os.environ["PATH"] = os.path.join(candidate, "bin") + ":" + os.environ["PATH"]
            break

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.transforms.rolling_demand import calculate_rolling_demand_and_dos, get_spark_session


def generate_synthetic_data(
    spark: SparkSession,
    num_skus: int,
    num_stores: int,
    num_days: int,
    start_date: date = date(2025, 1, 1),
    sales_probability: float = 0.6,
) -> Tuple[DataFrame, DataFrame]:
    """Generate representative synthetic daily inventory and sales DataFrames in Spark."""
    sku_list = [f"SKU-{i:05d}" for i in range(1, num_skus + 1)]
    store_list = [f"STORE-{j:03d}" for j in range(1, num_stores + 1)]
    date_list = [(start_date + timedelta(days=d)).isoformat() for d in range(num_days)]

    # Generate daily inventory matrix
    inventory_rows = []
    for day in date_list:
        for store in store_list:
            for sku in sku_list:
                inventory_rows.append((day, sku, store, 100.0))

    inventory_df = spark.createDataFrame(
        inventory_rows,
        ["business_date", "sku_id", "store_id", "available_inventory_qty"]
    )

    # Generate sales records (subset of days/skus/stores)
    sales_rows = []
    step = int(1.0 / max(sales_probability, 0.01))
    for idx, (day, sku, store, _) in enumerate(inventory_rows):
        if idx % step == 0:
            sales_rows.append((day, sku, store, 5.0))

    sales_df = spark.createDataFrame(
        sales_rows,
        ["sale_date", "sku_id", "store_id", "units_sold_qty"]
    )

    return inventory_df, sales_df


def benchmark_run(
    spark: SparkSession,
    inventory_df: DataFrame,
    sales_df: DataFrame,
    optimize: bool,
    use_broadcast: bool,
    repartition_keys: bool,
    num_partitions: int,
) -> Dict[str, Any]:
    """Execute a single benchmark pass and collect performance metrics."""
    # Warmup / force materialization into memory or spark cache if needed
    inventory_count = inventory_df.count()
    sales_count = sales_df.count()

    start_time = time.perf_counter()

    result = calculate_rolling_demand_and_dos(
        inventory_df,
        sales_df,
        window_days=28,
        optimize=optimize,
        use_broadcast=use_broadcast,
        repartition_keys=repartition_keys,
        num_partitions=num_partitions,
    )

    # Trigger action to force complete evaluation across all stages
    result_count = result.count()

    elapsed_sec = time.perf_counter() - start_time
    rows_per_sec = result_count / elapsed_sec if elapsed_sec > 0 else 0.0

    return {
        "optimize": optimize,
        "use_broadcast": use_broadcast,
        "repartition_keys": repartition_keys,
        "num_partitions": num_partitions,
        "elapsed_seconds": round(elapsed_sec, 4),
        "result_row_count": result_count,
        "throughput_rows_per_sec": round(rows_per_sec, 2),
    }


def run_benchmark_suite(
    scales: List[Tuple[int, int, int]], output_dir: Path
) -> List[Dict[str, Any]]:
    """Run full benchmark suite across multiple volume scale configurations."""
    results = []
    output_dir.mkdir(parents=True, exist_ok=True)

    spark = get_spark_session(app_name="stockguard-spark-benchmark", num_partitions=8, enable_aqe=True)
    try:
        for num_skus, num_stores, num_days in scales:
            total_rows = num_skus * num_stores * num_days
            print(f"\n=======================================================")
            print(f" Benchmarking Scale: {num_skus} SKUs x {num_stores} Stores x {num_days} Days ({total_rows:,} rows)")
            print(f"=======================================================")

            inventory_df, sales_df = generate_synthetic_data(
                spark, num_skus=num_skus, num_stores=num_stores, num_days=num_days
            )
            # Cache dataframes to benchmark processing, not generation
            inventory_df.cache()
            sales_df.cache()

            # 1. Baseline (Unoptimized)
            print("Running Baseline (Unoptimized: ordered unbounded window)...", end="", flush=True)
            baseline = benchmark_run(
                spark, inventory_df, sales_df,
                optimize=False, use_broadcast=False, repartition_keys=False, num_partitions=8
            )
            print(f" Done ({baseline['elapsed_seconds']} s)")

            # 2. Optimized (Window frame opt + Broadcast + Partition tuning + AQE)
            print("Running Optimized (Static partition min + Broadcast + Partition tuning)...", end="", flush=True)
            optimized = benchmark_run(
                spark, inventory_df, sales_df,
                optimize=True, use_broadcast=True, repartition_keys=True, num_partitions=8
            )
            print(f" Done ({optimized['elapsed_seconds']} s)")

            speedup = baseline["elapsed_seconds"] / optimized["elapsed_seconds"] if optimized["elapsed_seconds"] > 0 else 1.0
            time_saved_pct = ((baseline["elapsed_seconds"] - optimized["elapsed_seconds"]) / baseline["elapsed_seconds"]) * 100.0 if baseline["elapsed_seconds"] > 0 else 0.0

            scale_metrics = {
                "scale_config": {
                    "num_skus": num_skus,
                    "num_stores": num_stores,
                    "num_days": num_days,
                    "total_inventory_rows": total_rows,
                },
                "baseline": baseline,
                "optimized": optimized,
                "performance_summary": {
                    "speedup_factor": round(speedup, 2),
                    "time_saved_percent": round(time_saved_pct, 2),
                    "baseline_throughput_rows_sec": baseline["throughput_rows_per_sec"],
                    "optimized_throughput_rows_sec": optimized["throughput_rows_per_sec"],
                }
            }
            results.append(scale_metrics)

            inventory_df.unpersist()
            sales_df.unpersist()

    finally:
        spark.stop()

    # Save benchmark report to JSON
    json_path = output_dir / "spark_pipeline_benchmark.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"benchmark_results": results}, f, indent=2)
    print(f"\nBenchmark report exported to: {json_path}")

    return results


def print_summary_table(results: List[Dict[str, Any]]) -> None:
    """Print formatted markdown table of benchmark results."""
    print("\n### PySpark Rolling Demand Benchmark Results\n")
    print("| Scale (SKUs x Stores x Days) | Total Rows | Baseline (s) | Optimized (s) | Speedup | Optimized Throughput (rows/s) |")
    print("| ----------------------------- | ---------- | ------------ | ------------- | ------- | ----------------------------- |")
    for r in results:
        cfg = r["scale_config"]
        b = r["baseline"]
        o = r["optimized"]
        p = r["performance_summary"]
        scale_label = f"{cfg['num_skus']} x {cfg['num_stores']} x {cfg['num_days']}"
        print(f"| {scale_label:<29} | {cfg['total_inventory_rows']:<10,} | {b['elapsed_seconds']:<12.3f} | {o['elapsed_seconds']:<13.3f} | {p['speedup_factor']:<7.2f}x | {o['throughput_rows_per_sec']:<29,.0f} |")
    print("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/benchmarks"), help="Output directory for benchmark reports.")
    parser.add_argument("--quick", action="store_true", help="Run a quick benchmark with smaller volumes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.quick:
        scales = [(10, 5, 28), (50, 10, 28), (100, 20, 28)]  # ~1.4k, ~14k, ~56k rows
    else:
        scales = [(50, 10, 28), (200, 20, 28), (500, 50, 28)]  # 14k, 112k, 700k rows

    results = run_benchmark_suite(scales, args.output_dir)
    print_summary_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
