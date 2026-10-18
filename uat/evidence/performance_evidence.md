# Performance evidence

The rolling-demand optimization was benchmarked using synthetic SKU-store-day inventory volumes. Results are retained in [`data/benchmarks/spark_pipeline_benchmark.json`](../../data/benchmarks/spark_pipeline_benchmark.json) and documented in [`docs/pyspark_pipeline_performance.md`](../../docs/pyspark_pipeline_performance.md).

| Inventory rows | Baseline | Optimized | Speedup | Optimized throughput |
| ---: | ---: | ---: | ---: | ---: |
| 14,000 | 0.5293 s | 0.3109 s | 1.70× | 45,024 rows/s |
| 112,000 | 0.5025 s | 0.3763 s | 1.34× | 297,635 rows/s |
| 700,000 | 0.3877 s | 0.3147 s | 1.23× | 2,224,655 rows/s |

The optimized path uses a static partition minimum for launch-date derivation, broadcasts the smaller daily-sales aggregation, enables adaptive query execution, and aligns partitions by SKU-store. `tests/unit/test_rolling_demand.py::RollingDemandTest.test_optimization_equivalence` verifies that optimized and baseline calculations produce equivalent demand status, average demand, and days-of-supply results.

Re-run the evidence on the intended release environment before setting an operational SLA:

```bash
python3 scripts/benchmark_spark_pipeline.py
```

The retained figures are comparative engineering evidence, not a production refresh SLA; actual capacity depends on source volume, cluster sizing, and sales-table broadcast eligibility.
