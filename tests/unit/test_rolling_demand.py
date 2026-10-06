import os
import shutil
import subprocess
import unittest
from datetime import date, timedelta

from pyspark.sql import SparkSession

from src.transforms.rolling_demand import (
    calculate_rolling_demand_and_dos,
    ensure_java_env,
    get_spark_session,
)


class RollingDemandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_java_env()
        java = subprocess.run(["java", "-version"], capture_output=True, text=True)
        if java.returncode != 0:
            raise unittest.SkipTest("A Java runtime is required to execute PySpark tests")
        cls.spark = SparkSession.builder.master("local[1]").appName("stockguard-rolling-demand-test").getOrCreate()
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_established_zero_and_newly_launched_demand_states(self):
        inventory_rows, sales_rows = [], []
        start = date(2025, 1, 1)
        for offset in range(28):
            day = start + timedelta(days=offset)
            for sku_id, available_qty, demand in (("SKU-EST", 100.0, 10.0), ("SKU-ZERO", 50.0, 0.0)):
                inventory_rows.append((day.isoformat(), sku_id, "STORE-001", available_qty))
                sales_rows.append((day.isoformat(), sku_id, "STORE-001", demand))
        for offset in range(4):
            day = start + timedelta(days=24 + offset)
            inventory_rows.append((day.isoformat(), "SKU-NEW", "STORE-001", 20.0))
            sales_rows.append((day.isoformat(), "SKU-NEW", "STORE-001", 2.0))

        inventory = self.spark.createDataFrame(inventory_rows, "business_date string, sku_id string, store_id string, available_inventory_qty double")
        sales = self.spark.createDataFrame(sales_rows, "sale_date string, sku_id string, store_id string, units_sold_qty double")
        result = calculate_rolling_demand_and_dos(inventory, sales).select("sku_id", "business_date", "demand_status", "average_daily_demand_qty", "days_of_supply")
        rows = {(row["sku_id"], str(row["business_date"])): row.asDict() for row in result.collect()}

        established = rows[("SKU-EST", "2025-01-28")]
        self.assertEqual("ESTABLISHED", established["demand_status"])
        self.assertAlmostEqual(10.0, established["average_daily_demand_qty"])
        self.assertAlmostEqual(10.0, established["days_of_supply"])

        zero_demand = rows[("SKU-ZERO", "2025-01-28")]
        self.assertEqual("ZERO_DEMAND", zero_demand["demand_status"])
        self.assertIsNone(zero_demand["average_daily_demand_qty"])
        self.assertIsNone(zero_demand["days_of_supply"])

        launched = rows[("SKU-NEW", "2025-01-28")]
        self.assertEqual("NEWLY_LAUNCHED", launched["demand_status"])
        self.assertAlmostEqual(2.0, launched["average_daily_demand_qty"])
        self.assertAlmostEqual(10.0, launched["days_of_supply"])

    def test_missing_calendar_day_blocks_days_of_supply(self):
        inventory = self.spark.createDataFrame([
            ("2025-01-01", "SKU-GAP", "STORE-001", 20.0),
            ("2025-01-03", "SKU-GAP", "STORE-001", 20.0),
        ], "business_date string, sku_id string, store_id string, available_inventory_qty double")
        sales = self.spark.createDataFrame([
            ("2025-01-01", "SKU-GAP", "STORE-001", 2.0),
            ("2025-01-03", "SKU-GAP", "STORE-001", 2.0),
        ], "sale_date string, sku_id string, store_id string, units_sold_qty double")
        result = calculate_rolling_demand_and_dos(inventory, sales).filter("business_date = DATE '2025-01-03'").first()
        self.assertEqual("INCOMPLETE_HISTORY", result["demand_status"])
        self.assertIsNone(result["average_daily_demand_qty"])
        self.assertIsNone(result["days_of_supply"])

    def test_optimization_equivalence(self):
        inventory_rows, sales_rows = [], []
        start = date(2025, 1, 1)
        for offset in range(30):
            day = start + timedelta(days=offset)
            inventory_rows.append((day.isoformat(), "SKU-OPT", "STORE-001", 100.0))
            sales_rows.append((day.isoformat(), "SKU-OPT", "STORE-001", 5.0))

        inventory = self.spark.createDataFrame(inventory_rows, "business_date string, sku_id string, store_id string, available_inventory_qty double")
        sales = self.spark.createDataFrame(sales_rows, "sale_date string, sku_id string, store_id string, units_sold_qty double")

        baseline = calculate_rolling_demand_and_dos(inventory, sales, optimize=False).collect()
        optimized = calculate_rolling_demand_and_dos(inventory, sales, optimize=True).collect()

        baseline_sorted = sorted([r.asDict() for r in baseline], key=lambda x: str(x["business_date"]))
        optimized_sorted = sorted([r.asDict() for r in optimized], key=lambda x: str(x["business_date"]))

        self.assertEqual(len(baseline_sorted), len(optimized_sorted))
        for b, o in zip(baseline_sorted, optimized_sorted):
            self.assertEqual(b["business_date"], o["business_date"])
            self.assertEqual(b["demand_status"], o["demand_status"])
            self.assertEqual(b["average_daily_demand_qty"], o["average_daily_demand_qty"])
            self.assertEqual(b["days_of_supply"], o["days_of_supply"])

    def test_broadcast_and_partitioning_flags(self):
        inventory = self.spark.createDataFrame([
            ("2025-01-01", "SKU-BCAST", "STORE-001", 50.0),
        ], "business_date string, sku_id string, store_id string, available_inventory_qty double")
        sales = self.spark.createDataFrame([
            ("2025-01-01", "SKU-BCAST", "STORE-001", 5.0),
        ], "sale_date string, sku_id string, store_id string, units_sold_qty double")

        result = calculate_rolling_demand_and_dos(
            inventory, sales, use_broadcast=True, repartition_keys=True, num_partitions=4
        )
        row = result.first()
        self.assertIsNotNone(row)
        self.assertEqual("SKU-BCAST", row["sku_id"])


if __name__ == "__main__":
    unittest.main()
