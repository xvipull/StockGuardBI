# Inventory EDA diagnostic summary

This output is generated from the selected raw fixture/source extract. It is diagnostic only; KPI and reconciliation outputs remain the governed decision source.

## Decision cues

- Highest observed source-field missingness: **0.0%**.
- Highest demand SKU: **SKU-100** with **12** units sold in the supplied period.
- Highest inventory-value position: **SKU-100 at STORE-001**, valued at **562.50**.
- IQR high/low inventory-value outliers: **0** (small samples may not support meaningful outlier detection).

## Figures

- `source-missingness.png` — completeness check by input source.
- `demand-distribution.png` — demand concentration by SKU.
- `stock-patterns.png` — on-hand compared with sellable inventory by SKU-store.
- `high-value-inventory-segments.png` — value concentration and ABC-style segment assignment.
