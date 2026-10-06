# Power BI Semantic Model & Executive Overview Dashboard

This document details the Power BI semantic model architecture, data model relationships, DAX measure catalog, KPI targets, and Executive Overview dashboard layout for **StockGuard BI**.

---

## 1. Data Model Architecture & Relationships

The model is built on a clean **Star Schema** with single-direction 1-to-Many relationships to eliminate ambiguity and prevent circular dependencies.

```text
               ┌───────────────┐
               │   DateTable   │
               └───────┬───────┘
                       │
       ┌───────────────┼───────────────┬───────────────┐
       │               │               │               │
┌──────▼───────┐┌──────▼───────┐┌──────▼───────┐┌──────▼───────┐
│   DimSKU     ││   DimStore   ││DimReorderParam││  KPITargets   │
└──────┬───────┘└──────┬───────┘└──────┬───────┘└───────────────┘
       │               │               │          (Disconnected)
       ├───────────────┼───────────────┤
┌──────▼───────┐┌──────▼───────┐┌──────▼───────┐┌───────────────┐
│FactInventory ││ FactSales     ││ FactReceipts  ││ FactTransfers │
│   Daily      ││   Daily       ││               ││(Origin/Dest)  │
└──────────────┘└───────────────┘└───────────────┘└───────────────┘
```

### Relationship Catalog

| From Table | From Key | To Table | To Key | Cardinality | Cross Filter | Active |
| --- | --- | --- | --- | --- | --- | --- |
| `FactInventoryDaily` | `date_key` | `DateTable` | `DateKey` | Many to 1 (*:1) | Single | Yes |
| `FactSalesDaily` | `date_key` | `DateTable` | `DateKey` | Many to 1 (*:1) | Single | Yes |
| `FactReceipts` | `date_key` | `DateTable` | `DateKey` | Many to 1 (*:1) | Single | Yes |
| `FactTransfers` | `date_key` | `DateTable` | `DateKey` | Many to 1 (*:1) | Single | Yes |
| `FactInventoryDaily` | `sku_key` | `DimSKU` | `sku_key` | Many to 1 (*:1) | Single | Yes |
| `FactSalesDaily` | `sku_key` | `DimSKU` | `sku_key` | Many to 1 (*:1) | Single | Yes |
| `FactReceipts` | `sku_key` | `DimSKU` | `sku_key` | Many to 1 (*:1) | Single | Yes |
| `FactInventoryDaily` | `store_key` | `DimStore` | `store_key` | Many to 1 (*:1) | Single | Yes |
| `FactSalesDaily` | `store_key` | `DimStore` | `store_key` | Many to 1 (*:1) | Single | Yes |
| `FactReceipts` | `store_key` | `DimStore` | `store_key` | Many to 1 (*:1) | Single | Yes |
| `FactTransfers` | `origin_store_key` | `DimStore` | `store_key` | Many to 1 (*:1) | Single | Yes |
| `FactTransfers` | `destination_store_key` | `DimStore` | `store_key` | Many to 1 (*:1) | Single | No (Role-playing) |
| `FactInventoryDaily` | `reorder_parameter_key` | `DimReorderParameter` | `reorder_parameter_key` | Many to 1 (*:1) | Single | Yes |

---

## 2. Dynamic Date Table DAX Expression

Calculated dynamically from `FactInventoryDaily` date bounds to prevent date gap errors:

```dax
DateTable = 
VAR MinDate = MIN(FactInventoryDaily[business_date])
VAR MaxDate = MAX(FactInventoryDaily[business_date])
VAR StartDate = IF(ISBLANK(MinDate), DATE(2025, 1, 1), MinDate)
VAR EndDate = IF(ISBLANK(MaxDate), DATE(2025, 12, 31), MaxDate)
RETURN
ADDCOLUMNS(
    CALENDAR(StartDate, EndDate),
    "DateKey", FORMAT([Date], "YYYYMMDD"),
    "Year", YEAR([Date]),
    "Quarter", "Q" & QUARTER([Date]),
    "Month", FORMAT([Date], "MMM"),
    "MonthNo", MONTH([Date]),
    "YearMonth", FORMAT([Date], "YYYY-MM"),
    "DayOfWeek", FORMAT([Date], "DDD"),
    "DayOfWeekNo", WEEKDAY([Date], 2),
    "IsWorkingDay", IF(WEEKDAY([Date], 2) <= 5, 1, 0)
)
```

---

## 3. Comprehensive DAX Measure Catalog

*Note: All rate/ratio calculations are derived dynamically using DAX measures to avoid unnecessary calculated columns.*

### Category 1: Inventory Position & Valuation

| Measure Name | DAX Expression | Format String | Description |
| --- | --- | --- | --- |
| `[Total On-Hand Qty]` | `SUM(FactInventoryDaily[on_hand_qty])` | `#,##0` | Total physical stock on hand |
| `[Total Available Qty]` | `SUM(FactInventoryDaily[available_inventory_qty])` | `#,##0` | Sellable stock (On Hand - Allocated - Unavailable) |
| `[Total Allocated Qty]` | `SUM(FactInventoryDaily[allocated_qty])` | `#,##0` | Committed stock allocated to pending orders |
| `[Total Unavailable Qty]` | `SUM(FactInventoryDaily[unavailable_qty])` | `#,##0` | Stock on quality hold, damaged, or blocked |
| `[Total In-Transit Qty]` | `SUM(FactInventoryDaily[in_transit_qty])` | `#,##0` | Stock shipped from DC/supplier, not yet received |
| `[Ending Inventory Value]` | `SUM(FactInventoryDaily[inventory_value])` | `$#,##0.00` | Closing inventory monetary value |
| `[Average Inventory Value]`| `AVERAGEX(VALUES(DateTable[DateKey]), SUM(FactInventoryDaily[inventory_value]))` | `$#,##0.00` | Daily average inventory value over time |

### Category 2: Demand, Sales & Days of Supply (DOS)

| Measure Name | DAX Expression | Format String | Governance Target |
| --- | --- | --- | --- |
| `[Total Units Sold]` | `SUM(FactSalesDaily[units_sold_qty])` | `#,##0` | N/A |
| `[Total Net Sales]` | `SUM(FactSalesDaily[net_sales_amount])` | `$#,##0.00` | N/A |
| `[Total COGS]` | `SUM(FactSalesDaily[cogs_amount])` | `$#,##0.00` | N/A |
| `[Average Daily Demand Qty]` | `DIVIDE([Total Units Sold], MAX(COUNTROWS(DateTable), 1), 0)` | `#,##0.00` | N/A |
| `[Days of Supply (DOS)]` | `DIVIDE([Total Available Qty], [Average Daily Demand Qty], BLANK())` | `0.0` | **14.0 days** |
| `[DOS Target]` | `LOOKUPVALUE(KPITargets[target_value], KPITargets[kpi_code], "DOS")` | `0.0` | 14.0 days |
| `[DOS Variance]` | `IF(ISBLANK([Days of Supply (DOS)]), BLANK(), [Days of Supply (DOS)] - [DOS Target])` | `+0.0;-0.0;0.0` | 0.0 |
| `[DOS Status]` | `IF(ISBLANK([Days of Supply (DOS)]), "NO DEMAND", IF([Days of Supply (DOS)] < 7, "RED", IF([Days of Supply (DOS)] <= 14, "AMBER", "GREEN")))` | Text | GREEN |

### Category 3: Performance & Operational Ratios

| Measure Name | DAX Expression | Format String | Governance Target |
| --- | --- | --- | --- |
| `[Fill Rate %]` | `DIVIDE(SUM(FactSalesDaily[fulfilled_qty]), SUM(FactSalesDaily[requested_qty]), BLANK())` | `0.0%` | **≥ 98.0%** (Red < 95%) |
| `[Stockout Rate %]` | `DIVIDE(COUNTROWS(FILTER(FactInventoryDaily, FactInventoryDaily[available_inventory_qty] <= 0)), COUNTROWS(FactInventoryDaily), BLANK())` | `0.0%` | **≤ 2.0%** (Red > 5%) |
| `[Inventory Turns (Annualized)]` | `DIVIDE([Total COGS] * (365 / MAX(COUNTROWS(DateTable), 1)), [Average Inventory Value], BLANK())` | `0.00` | **≥ 4.0** |
| `[Sell-Through %]` | `DIVIDE([Total Units Sold], [Total On-Hand Qty] + SUM(FactReceipts[received_qty]), BLANK())` | `0.0%` | **≥ 50.0%** |

### Category 4: Working Capital & Exception Prioritization

| Measure Name | DAX Expression | Format String | Governance Target |
| --- | --- | --- | --- |
| `[Aged Stock Value (>90d)]` | `CALCULATE([Ending Inventory Value], FactInventoryDaily[inventory_age_days] > 90)` | `$#,##0.00` | N/A |
| `[Critical Aged Value (>180d)]` | `CALCULATE([Ending Inventory Value], FactInventoryDaily[inventory_age_days] > 180)` | `$#,##0.00` | N/A |
| `[Aged Stock %]` | `DIVIDE([Aged Stock Value (>90d)], [Ending Inventory Value], 0)` | `0.0%` | **≤ 5.0%** |
| `[Replenishment Exception Count]` | `COUNTROWS(FILTER(FactInventoryDaily, FactInventoryDaily[available_inventory_qty] <= 0 \|\| [Days of Supply (DOS)] < 7))` | `#,##0` | **0 exceptions** |

---

## 4. Governance KPI Targets Reference Table

Managed in the disconnected `KPITargets` model table:

| KPI Code | KPI Name | Target Value | Red Threshold | Amber Threshold |
| --- | --- | --- | --- | --- |
| `DOS` | Days of Supply | 14.0 days | < 7.0 days | 7.0 - 14.0 days |
| `FILL_RATE` | Order Fill Rate | 98.0% | < 95.0% | 95.0% - 97.9% |
| `STOCKOUT_RATE` | Stockout Rate | 2.0% | > 5.0% | 2.1% - 5.0% |
| `TURNS` | Inventory Turns | 4.0 | < 2.0 | 2.0 - 3.9 |
| `AGED_PCT` | Aged Stock % | 5.0% | > 10.0% | 5.1% - 10.0% |

---

## 5. Executive Overview Page Layout Design

The **Executive Overview** dashboard (`powerbi/StockGuardBI.Report`) provides a 1920×1080 canvas designed for supply chain executive leadership:

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ HEADER: StockGuard BI — Executive Inventory Overview                                                       │
├─────────────────┬─────────────────┬─────────────────┬─────────────────┬────────────────────────────────────┤
│ KPI CARD 1      │ KPI CARD 2      │ KPI CARD 3      │ KPI CARD 4      │ KPI CARD 5                         │
│ Inventory Value │ Days of Supply  │ Fill Rate %     │ Stockout Rate % │ Replenishment Exceptions           │
│ $1,245,800      │ 12.4 Days (AMB) │ 98.4% (GREEN)   │ 1.8% (GREEN)    │ 14 Priority Action Items           │
├─────────────────┴─────────────────┴─────────────────┼─────────────────┴────────────────────────────────────┤
│ LINE CHART: Daily Inventory Value vs COGS Trend      │ BAR CHART: Aged Stock Value (>90 Days) by Category   │
│ (Tracks working capital trajectory over dates)      │ (Identifies category-level working capital ties)     │
├──────────────────────────────────────────────────────┴──────────────────────────────────────────────────────┤
│ STORE PERFORMANCE MATRIX                                                                                   │
│ Region → Store Name | Inv Value | Days of Supply | Fill Rate % | Stockout Rate % | Exception Count             │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Project Artifact Paths

- **PBIP Project Root**: [`powerbi/StockGuardBI.pbip`](file:///Users/vipulmahesh/Desktop/StockGuardBI/powerbi/StockGuardBI.pbip)
- **TMDL Model Folder**: [`powerbi/StockGuardBI.Dataset/definition/`](file:///Users/vipulmahesh/Desktop/StockGuardBI/powerbi/StockGuardBI.Dataset/definition/)
- **DAX Measure Library**: [`powerbi/model/measures.dax`](file:///Users/vipulmahesh/Desktop/StockGuardBI/powerbi/model/measures.dax)
- **Report Page Config**: [`powerbi/StockGuardBI.Report/definition/report.json`](file:///Users/vipulmahesh/Desktop/StockGuardBI/powerbi/StockGuardBI.Report/definition/report.json)
