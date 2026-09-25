# Inventory Balance Reconciliation

The reconciliation layer validates daily on-hand inventory in the curated star schema. It is implemented in `src/quality/inventory_reconciliation.py` and writes a full balance output, an exception-only output, and a run manifest.

## Balance equation

For each SKU × store × business date with a prior-calendar-day inventory snapshot:

```text
expected closing stock
  = opening stock
  + receipts
  + received inbound transfers
  - non-cancelled outbound transfers
  - units sold
  + adjustments
```

Opening stock is the prior calendar day’s `on_hand_qty`; closing stock is the current day’s `on_hand_qty`. `adjustment_qty` is zero unless the curated inventory fact contains an approved adjustment measure. Transfer shipments reduce origin stock; only `RECEIVED` transfers increase destination stock. Cancelled transfers have no balance impact.

## Tolerances and status

Launch defaults are an absolute quantity tolerance of `0.001` and a relative tolerance of `0.1%` (`0.001`). The permitted variance is the greater of those values and `abs(closing stock) × relative tolerance`. A row is an `EXCEPTION` only when its absolute variance exceeds the permitted variance.

Rows without a prior-calendar-day snapshot are `NOT_EVALUATED`, not balance exceptions. This makes initial loads and snapshot gaps transparent without inventing an opening balance. Inventory Planning and Finance must approve any production tolerance change.

## Execute

```bash
python3 -m src.quality.inventory_reconciliation \
  --curated-run-dir /tmp/stockguard-curated/run_id=local-20260925 \
  --output-dir /tmp/stockguard-reconciliation
```

Outputs:

- `inventory_balance_reconciliation.csv` — all daily reconciliation results.
- `inventory_balance_exceptions.csv` — only variance breaches, for investigation.
- `inventory_balance_reconciliation_manifest.json` — control totals, tolerances, and overall status.
