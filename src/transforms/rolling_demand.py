#!/usr/bin/env python3
"""PySpark rolling demand and days-of-supply calculations at SKU-store-day grain."""

from __future__ import annotations

import argparse
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F


WINDOW_DAYS = 28


def calculate_rolling_demand_and_dos(daily_inventory: DataFrame, daily_sales: DataFrame, window_days: int = WINDOW_DAYS) -> DataFrame:
    """Calculate rolling demand and DOS from daily inventory and sales facts.

    ``daily_inventory`` must be one row per ``business_date``, ``sku_id``, and
    ``store_id`` and include ``available_inventory_qty``. ``daily_sales`` may
    contain multiple sales rows per key but must include ``sale_date`` and
    ``units_sold_qty``. An optional ``launch_date`` on inventory overrides the
    first observed date as the SKU-store launch date.

    Zero demand yields a null DOS rather than an artificial infinite value.
    Newly launched SKU-store combinations calculate an average over the
    observed launch-to-date window. Missing calendar days are marked
    ``INCOMPLETE_HISTORY`` and do not produce a DOS.
    """
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    inventory_required = {"business_date", "sku_id", "store_id", "available_inventory_qty"}
    sales_required = {"sale_date", "sku_id", "store_id", "units_sold_qty"}
    if inventory_required - set(daily_inventory.columns):
        raise ValueError(f"daily_inventory missing columns: {sorted(inventory_required - set(daily_inventory.columns))}")
    if sales_required - set(daily_sales.columns):
        raise ValueError(f"daily_sales missing columns: {sorted(sales_required - set(daily_sales.columns))}")

    sales_by_day = (
        daily_sales
        .select(F.to_date("sale_date").alias("business_date"), "sku_id", "store_id", F.col("units_sold_qty").cast("double").alias("units_sold_qty"))
        .groupBy("business_date", "sku_id", "store_id")
        .agg(F.sum("units_sold_qty").alias("daily_demand_qty"))
    )
    base = (
        daily_inventory
        .withColumn("business_date", F.to_date("business_date"))
        .join(sales_by_day, ["business_date", "sku_id", "store_id"], "left")
        .withColumn("daily_demand_qty", F.coalesce(F.col("daily_demand_qty"), F.lit(0.0)))
        .withColumn("available_inventory_qty", F.col("available_inventory_qty").cast("double"))
        .withColumn("_day_index", F.unix_date("business_date"))
    )
    partition = Window.partitionBy("sku_id", "store_id")
    rolling = partition.orderBy(F.col("_day_index")).rangeBetween(-(window_days - 1), 0)
    history = partition.orderBy("business_date").rowsBetween(Window.unboundedPreceding, Window.currentRow)

    launch_date = F.coalesce(F.to_date("launch_date"), F.col("_first_observed_date")) if "launch_date" in base.columns else F.col("_first_observed_date")
    calculated = (
        base
        .withColumn("_first_observed_date", F.min("business_date").over(history))
        .withColumn("sku_store_launch_date", launch_date)
        .withColumn("sku_store_age_days", F.datediff("business_date", "sku_store_launch_date"))
        .withColumn("rolling_demand_qty", F.sum("daily_demand_qty").over(rolling))
        .withColumn("observed_days_in_window", F.count("business_date").over(rolling))
        .withColumn("expected_days_in_window", F.least(F.lit(window_days), F.col("sku_store_age_days") + F.lit(1)))
        .withColumn("is_complete_history", F.col("observed_days_in_window") == F.col("expected_days_in_window"))
        .withColumn(
            "demand_status",
            F.when(~F.col("is_complete_history"), F.lit("INCOMPLETE_HISTORY"))
            .when((F.col("rolling_demand_qty") == 0) & (F.col("sku_store_age_days") < window_days - 1), F.lit("NEWLY_LAUNCHED_ZERO_DEMAND"))
            .when(F.col("rolling_demand_qty") == 0, F.lit("ZERO_DEMAND"))
            .when(F.col("sku_store_age_days") < window_days - 1, F.lit("NEWLY_LAUNCHED"))
            .otherwise(F.lit("ESTABLISHED")),
        )
        .withColumn(
            "average_daily_demand_qty",
            F.when(F.col("is_complete_history") & (F.col("rolling_demand_qty") > 0), F.col("rolling_demand_qty") / F.col("observed_days_in_window")),
        )
        .withColumn(
            "days_of_supply",
            F.when(F.col("average_daily_demand_qty") > 0, F.col("available_inventory_qty") / F.col("average_daily_demand_qty")),
        )
    )
    return calculated.drop("_day_index", "_first_observed_date")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-input", required=True, help="Parquet path for daily inventory records.")
    parser.add_argument("--sales-input", required=True, help="Parquet path for daily sales records.")
    parser.add_argument("--output", required=True, help="Parquet output path for rolling-demand records.")
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.appName("stockguard-rolling-demand").getOrCreate()
    try:
        result = calculate_rolling_demand_and_dos(
            spark.read.parquet(args.inventory_input), spark.read.parquet(args.sales_input), args.window_days
        )
        result.write.mode("errorifexists").parquet(args.output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
