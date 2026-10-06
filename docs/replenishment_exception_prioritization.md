# Replenishment Exception Prioritization

`src/transforms/replenishment_exceptions.py` builds a governed, scored replenishment exception file for every active SKU-store combination. Each exception is ranked by a composite priority score that combines five configurable signals.

## Priority Score Formula

$$\text{priority\_score} = w_1 \cdot S_{\text{lost\_sales}} + w_2 \cdot S_{\text{dos}} + w_3 \cdot S_{\text{lead\_time}} + w_4 \cdot S_{\text{margin}} + w_5 \cdot S_{\text{condition}}$$

| Signal | Weight | Description |
|---|---|---|
| Lost-sales proxy | **0.30** | Normalised unfulfilled demand `(requested − fulfilled) / 500`, or `1.0` on active stockout |
| Days of supply | **0.25** | `1 − DOS / cap` where cap = max(30, lead_time × 2). Null DOS (stockout) → signal = 1 |
| Lead-time urgency | **0.20** | `(reorder_cover − DOS) / reorder_cover` when DOS < reorder_cover, else 0 |
| Margin / value | **0.15** | `on_hand_qty × unit_margin / 10,000`. Falls back to `unit_cost` when margin is absent |
| Stock condition | **0.10** | Fresh stock = 1. Aged/dead stock reduces signal. Excess stock penalty applied |

Each signal is clamped to `[0, 1]` before weighting. The final score is also clamped to `[0, 1]`.

> [!IMPORTANT]
> **Active stockouts with complete data always receive priority_score = 1 (CRITICAL)**, bypassing normal weighting. This prevents a zero DOS from being diluted by other signals.

## Priority Bands

| Band | Score range | Meaning |
|---|---|---|
| `CRITICAL` | ≥ 0.80 | Immediate replenishment action required |
| `HIGH` | 0.60 – 0.79 | Action within 24 hours |
| `MEDIUM` | 0.40 – 0.59 | Action within current replenishment cycle |
| `LOW` | 0.20 – 0.39 | Monitor; queue in next wave |
| `MONITOR` | < 0.20 | Triggered by secondary condition; low urgency |
| `UNSCORED` | — | Insufficient data to score; listed in input exception file |
| `EXCLUDED` | — | Inactive/discontinued products |

## Reason Codes

Each exception row carries a semicolon-delimited `reason_codes` column. Codes can co-occur.

| Code | Trigger condition |
|---|---|
| `ACTIVE_STOCKOUT` | `available_inventory_qty ≤ 0` and product is active |
| `COVER_BELOW_LEAD_TIME` | `stockout_risk_flag = true` from upstream risk pipeline |
| `COVER_WITHIN_7_DAY_BUFFER` | `low_cover_flag = true` from upstream risk pipeline |
| `LOST_SALES_PROXY` | Unfulfilled demand > 0 or active stockout |
| `HIGH_VALUE_AT_RISK` | Margin signal > 0.70 (at-risk value ≥ £7,000) |
| `AGED_STOCK_CONDITION` | `aged_stock_flag = true` or `movement_status = SLOW_MOVING` |
| `DEAD_STOCK_CONDITION` | `critical_aged_stock_flag = true` or `movement_status = DEAD_STOCK` |
| `EXCESS_STOCK_CONDITION` | `excess_inventory_flag = true` |
| `INACTIVE_PRODUCT` | Product status not `ACTIVE` or `LIVE` |

### Input Exception Codes (data quality)

| Code | Meaning |
|---|---|
| `MISSING_DEMAND_DATA` | `average_daily_demand_qty` is blank or `demand_status = INCOMPLETE_HISTORY` |
| `MISSING_LEAD_TIME` | `lead_time_days` is blank |
| `MISSING_UNIT_COST` | Both `unit_cost` and `unit_margin` are absent |

> [!NOTE]
> Records with input exceptions are still written to the exception mart when an active stockout is detected, ensuring zero-stock items are never silently excluded.

## Outputs

All outputs are written to `<output_dir>/run_id=<run_id>/`.

| File | Description |
|---|---|
| `mart_replenishment_exceptions.csv` | All triggered exceptions, sorted CRITICAL → MONITOR by priority score |
| `replenishment_input_exceptions.csv` | Rows with missing or invalid inputs (data quality audit trail) |
| `replenishment_manifest.json` | Run lineage, row counts, band counts, reason-code frequencies, scoring weights |

### Output Column Reference

| Column | Type | Description |
|---|---|---|
| `priority_score` | decimal(10,6) | Composite signal score 0–1 |
| `priority_band` | string | `CRITICAL` / `HIGH` / `MEDIUM` / `LOW` / `MONITOR` / `UNSCORED` / `EXCLUDED` |
| `reason_codes` | string | `;`-delimited exception reason codes |
| `input_exception_codes` | string | `;`-delimited data quality issue codes |
| `exception_triggered` | boolean | `true` when the row meets any exception threshold |

## SQL Mart Views

`marts/replenishment/replenishment_exception_priority.sql` provides five analytical views built over the CSV mart:

| View | Purpose |
|---|---|
| `mart_replenishment_exceptions_ranked` | Full exception detail with `exception_priority_rank` per date |
| `mart_replenishment_band_summary` | Daily exception count and at-risk value by priority band |
| `mart_replenishment_reason_summary` | Reason code frequency and average priority score |
| `mart_replenishment_store_urgency` | Per-store rollup of exception counts and at-risk value |
| `mart_replenishment_sku_heatmap` | Cross-store SKU exceptions (only SKUs with > 1 affected store) |

## Pipeline Integration

```
inventory_risk_flags  ──┐
                        ├──► replenishment_exceptions ──► mart_replenishment_exceptions.csv
aged_inventory_analysis ┘
```

### CLI

```bash
python -m src.transforms.replenishment_exceptions \
  --risk-run-dir  data/marts/risk/run_id=2025-01-28 \
  --aged-run-dir  data/marts/aged/run_id=2025-01-28 \
  --output-dir    data/marts/replenishment \
  --run-id        2025-01-28
```

## Configuration

All thresholds are module-level constants in `replenishment_exceptions.py`:

| Constant | Default | Effect |
|---|---|---|
| `W_LOST_SALES` | 0.30 | Lost-sales signal weight |
| `W_DOS` | 0.25 | Days-of-supply signal weight |
| `W_LEAD_TIME` | 0.20 | Lead-time urgency signal weight |
| `W_MARGIN` | 0.15 | Margin/value signal weight |
| `W_CONDITION` | 0.10 | Stock condition signal weight |
| `DOS_CRITICAL_CAP` | 30 days | DOS at which signal = 0 |
| `LOST_SALES_CAP` | 500 units | Normalisation ceiling for unfulfilled units |
| `MARGIN_CAP` | £10,000 | Normalisation ceiling for at-risk margin value |

> [!TIP]
> The weights must sum to 1.0. When the business has a strong service-level focus, increase `W_LOST_SALES` or `W_DOS`. For high-margin categories, increase `W_MARGIN`.
