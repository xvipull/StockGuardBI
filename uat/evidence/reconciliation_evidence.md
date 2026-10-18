# Reconciliation control evidence

## Control design

The daily reconciliation is implemented in `src/quality/inventory_reconciliation.py`. For every SKU × store × business date with a prior calendar-day snapshot, it validates:

```text
closing stock = opening stock + receipts + received transfers
                - outbound transfers - units sold + adjustments
```

The launch tolerance is the larger of 0.001 units and 0.1% of closing stock. First-observed or gapped snapshots are explicitly `NOT_EVALUATED`; they are not silently treated as balance passes.

## Reproducible evidence

| Evidence item | Expected observed result | Automated source |
| --- | --- | --- |
| Balanced movement | 100 opening + 10 receipts − 3 outbound transfer − 15 sales = 92 closing; manifest is `passed`. | `tests/unit/test_inventory_reconciliation.py::InventoryReconciliationTest.test_reconciles_balance_and_marks_initial_snapshot_not_evaluated` |
| Material variance | A closing value of 99 versus expected 92 produces variance 7, an `EXCEPTION`, and a `failed` manifest. | `tests/unit/test_inventory_reconciliation.py::InventoryReconciliationTest.test_outputs_exception_when_balance_variance_exceeds_tolerance` |
| Late receipt timing | A 5-unit receipt dated day 3 does not restate day 2. Day 2 expected close is 90 and day 3 expected close is 95. | `tests/integration/test_inventory_uat_controls.py::InventoryUatControlTest.test_late_receipt_is_applied_on_actual_receipt_date_only` |
| Input completeness | Raw rows reconcile to staged plus quarantined rows; duplicate snapshot keys and unresolved SKU facts fail the quality report. | `tests/unit/test_inventory_data_quality.py` |

## Run and retain

```bash
python3 -m unittest tests.unit.test_inventory_reconciliation tests.integration.test_inventory_uat_controls
```

For a release candidate, retain `inventory_balance_reconciliation.csv`, `inventory_balance_exceptions.csv`, and `inventory_balance_reconciliation_manifest.json` with the curated run ID. Finance reviews exceptions before publication; a manifest status of `failed` blocks release.
