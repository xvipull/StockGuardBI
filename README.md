# StockGuard BI

StockGuard BI is a governed inventory analytics project that turns operational inventory data into actionable stockout, excess, aged-stock, and replenishment decisions.

## Project charter

The project delivers a PySpark pipeline, governed inventory marts, and Power BI and Excel decision surfaces for Inventory Planning, Store Operations, and Finance. The complete charter and acceptance criteria are in [docs/requirements.md](docs/requirements.md).

## Architecture

```text
Source owners / operational systems
              |
              v
data/raw -> PySpark ingestion & quality -> data/staging
                                              |
                                              v
                                    governed inventory marts
                                    (stockout, excess, aged stock,
                                     replenishment exceptions)
                                              |
                              +---------------+---------------+
                              v                               v
                       Power BI semantic model          Excel outputs
```

## Repository layout

- `data/` — raw, staging, and curated data boundaries.
- `src/` — PySpark ingestion, transformation, and quality code.
- `marts/` — governed inventory and replenishment mart definitions.
- `powerbi/` — semantic model, report assets, and screenshots.
- `excel/` — planning templates and exported outputs.
- `tests/` — unit and integration tests.
- `uat/` — user acceptance testing evidence and sign-off.
- `docs/` — project governance and documentation.

## Screenshot placeholders

| Planned view | Placeholder |
| --- | --- |
| Inventory health overview | `powerbi/screenshots/inventory-health-overview.png` |
| Stockout and replenishment exceptions | `powerbi/screenshots/replenishment-exceptions.png` |
| Excess and aged-stock exposure | `powerbi/screenshots/excess-aged-stock.png` |

## Delivery cadence

The intended production refresh is daily, after source-system close. Initial delivery focuses on the documented decision scope before connecting production sources.

## Getting started

Implementation, data contracts, test instructions, and report build guidance will be added to the relevant folders as each delivery increment lands.
