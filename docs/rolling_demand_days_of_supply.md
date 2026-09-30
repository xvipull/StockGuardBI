# Rolling Demand and Days of Supply

`src/transforms/rolling_demand.py` calculates demand and days of supply (DOS) at **SKU × store × business date** using PySpark date-range windows.

## Logic

1. Aggregate sales to daily `units_sold_qty` and left join them to the daily inventory spine. Missing sales become zero daily demand.
2. Within each SKU-store, sum demand for the current business date and the preceding 27 calendar days (`rolling_demand_qty`).
3. Calculate `average_daily_demand_qty` using the actual expected launch-to-date window for items younger than 28 days, otherwise 28 days.
4. Calculate `days_of_supply = available_inventory_qty / average_daily_demand_qty` only where valid demand exists.

This requires one inventory row per SKU-store-calendar day. The logic uses the date as the window range, so duplicate or skipped days cannot silently be treated as a valid 28-row window.

## Explicit demand states

| `demand_status` | Meaning | Average demand / DOS |
| --- | --- | --- |
| `ESTABLISHED` | At least 28 days old, complete date history, positive rolling demand | Calculated over 28 days. |
| `NEWLY_LAUNCHED` | Less than 28 days old, complete history, positive demand | Calculated over observed launch-to-date days. |
| `ZERO_DEMAND` | Established item with complete history and zero rolling demand | Both values are null; no infinite DOS is emitted. |
| `NEWLY_LAUNCHED_ZERO_DEMAND` | New item with complete history and zero demand | Both values are null; planner review is required. |
| `INCOMPLETE_HISTORY` | A required calendar day is missing | Both values are null until the spine is complete. |

An optional `launch_date` inventory column can supply the authoritative launch date. If omitted, the first observed SKU-store inventory date is used.

## Run

```bash
spark-submit src/transforms/rolling_demand.py \
  --inventory-input /data/curated/fact_inventory_daily \
  --sales-input /data/curated/fact_sales_daily \
  --output /data/curated/inventory_demand_daily
```

The output retains source inventory columns and adds `daily_demand_qty`, rolling/window counts, launch age, demand status, average daily demand, and DOS.
