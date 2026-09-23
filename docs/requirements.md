# StockGuard BI — Project Charter and Requirements

## Purpose

Provide a trusted, daily inventory decision layer that lets business teams identify stockout risk, excess inventory, aged stock, and replenishment exceptions early enough to act.

## Personas and decisions

| Persona | Primary decisions | Required insight |
| --- | --- | --- |
| Inventory Planner | What to replenish, defer, transfer, or escalate | Item-location stock position, demand, lead time, safety stock, and replenishment exceptions |
| Store Operations | Which stores and SKUs need immediate action | Stockout risk, on-hand imbalance, blocked/late replenishment, and store-level priorities |
| Finance | Where working capital and write-off exposure require intervention | Excess value, aged inventory value, turns, and exposure by product, location, and supplier |

## Scope

1. Ingest and standardize approved inventory, product, location, demand, purchase-order, and supplier data using PySpark.
2. Build governed inventory marts with documented grain, lineage, refresh status, and data-quality controls.
3. Calculate stockout risk, excess stock, aged-stock exposure, and replenishment exceptions.
4. Deliver Power BI reporting and Excel-ready operational outputs.
5. Provide automated tests, UAT evidence, and portfolio-ready project documentation.

## Exclusions

- Creating purchase orders, stock transfers, or other write-back transactions in operational systems.
- Replacing source inventory or ERP systems.
- Price optimization, demand forecasting model development, and autonomous ordering in the initial release.
- Handling customer-level personal data.

## Refresh cadence and availability

The target refresh cadence is once daily after the source-system close. The pipeline must report its source extract date, pipeline run timestamp, row counts, and quality result with each refresh. Intraday use is explicitly out of scope for the first release.

## Data source owners

| Source domain | Accountable owner | Intended use |
| --- | --- | --- |
| Inventory positions and movements | Supply Chain / ERP Inventory owner | On-hand, allocated, in-transit, and inventory age inputs |
| Product and hierarchy master | Merchandising master-data owner | SKU, category, status, unit, and cost attributes |
| Store / warehouse master | Store Operations master-data owner | Location hierarchy and operational status |
| Sales and demand history | Commercial Analytics owner | Consumption and demand-rate calculations |
| Purchase orders and supplier lead times | Procurement owner | Replenishment status and exception detection |

## Privacy and security notes

The first release is designed for product, location, supplier, and aggregate operational measures; no customer personal data is required. Access to extracts and reports must follow least-privilege business roles. Source extracts, credentials, and confidential supplier terms must not be committed to the repository. Any personal data discovered during onboarding must be removed or handled under the organization’s approved privacy process before use.

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Missing or late source extracts | Stale decisions | Record freshness; fail or flag refreshes; agree source cutoffs with owners |
| Inconsistent SKU or location keys | Incorrect joins and totals | Apply conformance checks and publish rejected-record counts |
| Unreliable inventory age fields | Misstated aged-stock exposure | Reconcile aging logic with Finance and Inventory Planning during UAT |
| Lead-time gaps | Incorrect replenishment priority | Default only with documented rules; surface missing lead times as exceptions |
| Metric disagreement | Low adoption | Version metric definitions and obtain named business sign-off |

## Measurable acceptance criteria

1. PySpark pipeline runs successfully against the agreed sample/source extract and writes staged and curated outputs with an auditable run timestamp.
2. Every inventory mart documents its grain, business keys, measures, source lineage, and refresh status.
3. Reconciliation shows 100% of accepted inventory records mapped to a valid product and location, or records exceptions with reason and count.
4. Stockout-risk, excess-stock, aged-stock, and replenishment-exception outputs each include SKU, location, calculation date, classification, and an actionable priority or value measure.
5. Power BI totals for on-hand quantity and inventory value reconcile to the approved curated mart for the same refresh date.
6. Excel output contains the current prioritized replenishment exception list and can be filtered by location, product hierarchy, and priority.
7. Automated unit tests cover metric thresholds and key data-quality rules; integration testing verifies the end-to-end sample pipeline.
8. Inventory Planner, Store Operations, and Finance each execute agreed UAT scenarios and record acceptance or defects in `uat/`.
9. Documentation includes the approved scope, exclusions, refresh cadence, source owners, privacy treatment, and known risks.

## Governance decisions to confirm during implementation

- Business definitions and thresholds for stockout risk, excess stock, and aged stock.
- Inventory valuation basis and currency treatment for Finance.
- Authoritative source and cutoff time for each domain.
- Report workspace roles and UAT approvers.
