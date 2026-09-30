# Inventory EDA reports

`reports/figures/` contains only figures that support a decision: required-field completeness, demand concentration, stock availability, and high-value inventory focus. Regenerate them with:

```bash
MPLCONFIGDIR=/tmp/matplotlib python3 scripts/inventory_eda.py \
  --input-dir tests/fixtures/raw/representative \
  --output-dir reports/figures
```

The committed figures are generated from synthetic representative data and are not production findings.
