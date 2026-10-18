# Inventory stakeholder UAT cases

## Scope and acceptance

This pack is for Inventory Planning, Store Operations, and Finance to validate the decision outputs and control behaviour before release. Run the cases against an immutable run ID; capture the run ID, tester, test date, actual result, and approver in the sign-off column. A failed data-quality or balance-reconciliation control blocks publication unless it is formally accepted through governance.

| ID | Stakeholder / scenario | Test data and action | Expected result | Evidence / sign-off |
| --- | --- | --- | --- | --- |
| UAT-01 | Inventory Planner — established demand and healthy stock | Load 28 consecutive inventory days for a SKU-store with 10 units sold each day and 100 units available on day 28. | Demand status is `ESTABLISHED`; average daily demand is 10; days of supply is 10. | `tests/unit/test_rolling_demand.py::RollingDemandTest.test_established_zero_and_newly_launched_demand_states`; Planner: ____; result: ____ |
| UAT-02 | Inventory Planner — no sales | Load 28 consecutive inventory days but no sales fact rows for the SKU-store. | Missing sales facts are treated as zero demand; status is `ZERO_DEMAND`; average demand and days of supply are blank/null, not infinity. | Same executable test as UAT-01; Planner: ____; result: ____ |
| UAT-03 | Store Operations — zero stock | Load 28 consecutive days with sales of 10 units/day and 0 available inventory on the last day. | Demand remains `ESTABLISHED`; average demand is 10; days of supply is exactly 0. | Same executable test as UAT-01; Store Operations: ____; result: ____ |
| UAT-04 | Finance — late receipt | Start at 100 units; record 10 units sold on day 2 (closing 90); book a 5-unit receipt on day 3 (closing 95). | Day 2 has zero receipts and expected close of 90. The receipt affects day 3 only, where expected close is 95. Both evaluated days pass. | `tests/integration/test_inventory_uat_controls.py::InventoryUatControlTest.test_late_receipt_is_applied_on_actual_receipt_date_only`; Finance: ____; result: ____ |
| UAT-05 | Data Owner — duplicate snapshot | Add two snapshot rows with the same business date, SKU, and store. | `duplicate_snapshot_keys` fails and identifies the duplicated business key; the run cannot be published. | `tests/unit/test_inventory_data_quality.py::InventoryDataQualityTest.test_detects_duplicate_snapshot_and_missing_master`; Data Owner: ____; result: ____ |
| UAT-06 | Data Owner — unknown SKU | Add a fact row for a SKU absent from the product master. | `missing_product_master` fails with the offending fact row; the run cannot be published. | Same executable test as UAT-05; Data Owner: ____; result: ____ |
| UAT-07 | Finance — daily inventory balance | Reconcile a curated run with a prior-day closing snapshot, sales, receipts, and transfers. | Expected closing stock follows the documented balance equation; variance within tolerance is `PASS`; initial snapshots are `NOT_EVALUATED`. | `tests/unit/test_inventory_reconciliation.py::InventoryReconciliationTest.test_reconciles_balance_and_marks_initial_snapshot_not_evaluated`; Finance: ____; result: ____ |
| UAT-08 | Finance — reconciliation breach | Change a closing snapshot so the variance exceeds tolerance. | The row is `EXCEPTION`, appears in the exception extract, and the run manifest status is `failed`. | `tests/unit/test_inventory_reconciliation.py::InventoryReconciliationTest.test_outputs_exception_when_balance_variance_exceeds_tolerance`; Finance: ____; result: ____ |

## Execution commands

```bash
python3 -m unittest \
  tests.unit.test_rolling_demand \
  tests.unit.test_inventory_data_quality \
  tests.unit.test_inventory_reconciliation \
  tests.integration.test_inventory_uat_controls
```

Record evidence in the related control outputs and in [reconciliation evidence](evidence/reconciliation_evidence.md) and [performance evidence](evidence/performance_evidence.md). The automated results demonstrate expected control behavior; business owners still complete the sign-off fields after reviewing the report surfaces.
