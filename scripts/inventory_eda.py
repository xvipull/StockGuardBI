#!/usr/bin/env python3
"""Create reproducible, decision-useful inventory EDA figures from raw CSV extracts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


REQUIRED_FILES = ("inventory_snapshots", "sales", "products", "stores")
FIGURE_STYLE = {"dpi": 160, "bbox_inches": "tight"}
REQUIRED_FIELDS = {
    "inventory_snapshots": ("business_date", "sku_id", "store_id", "on_hand_qty", "allocated_qty", "unavailable_qty", "in_transit_qty", "unit_cost"),
    "sales": ("sale_date", "sku_id", "store_id", "fulfilled_qty", "units_sold_qty", "return_qty"),
    "receipts": ("receipt_id", "receipt_line_id", "receipt_date", "sku_id", "store_id", "received_qty"),
    "transfers": ("transfer_id", "transfer_line_id", "transfer_date", "sku_id", "origin_store_id", "destination_store_id", "transfer_qty", "transfer_status"),
    "reorder_parameters": ("sku_id", "store_id", "effective_from_date"),
    "products": ("sku_id", "sku_name", "category", "base_uom", "product_status"),
    "stores": ("store_id", "store_name", "store_type", "store_status"),
    "calendar": ("calendar_date", "day_of_week", "week_start_date", "month_start_date", "is_working_day"),
}


def load_sources(input_dir: Path) -> Dict[str, pd.DataFrame]:
    frames = {}
    for path in input_dir.glob("*.csv"):
        frames[path.stem] = pd.read_csv(path)
    missing = [name for name in REQUIRED_FILES if name not in frames]
    if missing:
        raise ValueError(f"Missing required EDA inputs: {', '.join(missing)}")
    return frames


def save_missingness(frames: Dict[str, pd.DataFrame], figure_dir: Path) -> pd.DataFrame:
    records = []
    for name, frame in sorted(frames.items()):
        for column in REQUIRED_FIELDS.get(name, frame.columns):
            if column not in frame.columns:
                records.append({"source": name, "column": column, "missing_pct": 100.0})
                continue
            records.append({"source": name, "column": column, "missing_pct": frame[column].isna().mean() * 100})
    missingness = pd.DataFrame(records)
    summary = missingness.groupby("source", as_index=False)["missing_pct"].max()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(summary["source"], summary["missing_pct"], color="#3b82f6")
    ax.bar_label(bars, labels=[f"{value:.1f}%" for value in summary["missing_pct"]], padding=3)
    ax.set_ylim(0, max(5, summary["missing_pct"].max() + 5))
    ax.set_ylabel("Maximum required-field missingness (%)")
    ax.set_title("Source completeness: highest required-field missingness")
    ax.tick_params(axis="x", rotation=35)
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(figure_dir / "source-missingness.png", **FIGURE_STYLE)
    plt.close(fig)
    return missingness


def enrich(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    inventory = frames["inventory_snapshots"].copy()
    inventory["on_hand_qty"] = pd.to_numeric(inventory["on_hand_qty"], errors="coerce")
    inventory["allocated_qty"] = pd.to_numeric(inventory["allocated_qty"], errors="coerce")
    inventory["unavailable_qty"] = pd.to_numeric(inventory["unavailable_qty"], errors="coerce")
    inventory["unit_cost"] = pd.to_numeric(inventory["unit_cost"], errors="coerce")
    inventory["available_qty"] = (inventory["on_hand_qty"] - inventory["allocated_qty"] - inventory["unavailable_qty"]).clip(lower=0)
    inventory["inventory_value"] = inventory["on_hand_qty"] * inventory["unit_cost"]
    products = frames["products"][["sku_id", "sku_name", "category"]]
    stores = frames["stores"][["store_id", "store_name", "region"]]
    return inventory.merge(products, on="sku_id", how="left").merge(stores, on="store_id", how="left")


def save_demand_distribution(sales: pd.DataFrame, figure_dir: Path) -> pd.DataFrame:
    demand = sales.copy()
    demand["units_sold_qty"] = pd.to_numeric(demand["units_sold_qty"], errors="coerce")
    demand = demand.groupby("sku_id", as_index=False)["units_sold_qty"].sum().sort_values("units_sold_qty", ascending=False)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(demand["sku_id"], demand["units_sold_qty"], color="#0f766e")
    ax.bar_label(bars, fmt="%.0f", padding=3)
    ax.set_ylabel("Units sold")
    ax.set_title("Demand distribution by SKU")
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(figure_dir / "demand-distribution.png", **FIGURE_STYLE)
    plt.close(fig)
    return demand


def save_stock_patterns(inventory: pd.DataFrame, figure_dir: Path) -> pd.DataFrame:
    ordered = inventory.sort_values("on_hand_qty", ascending=False).copy()
    labels = ordered["sku_id"] + " · " + ordered["store_id"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, ordered["on_hand_qty"], label="On hand", color="#2563eb")
    ax.bar(labels, ordered["available_qty"], label="Available", color="#22c55e", alpha=0.8)
    ax.set_ylabel("Units")
    ax.set_title("Stock pattern: on-hand versus available inventory")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(figure_dir / "stock-patterns.png", **FIGURE_STYLE)
    plt.close(fig)
    return ordered


def save_high_value_and_segments(inventory: pd.DataFrame, figure_dir: Path) -> pd.DataFrame:
    ranked = inventory.sort_values("inventory_value", ascending=False).copy()
    total_value = ranked["inventory_value"].sum()
    ranked["cumulative_value_share"] = ranked["inventory_value"].cumsum() / total_value if total_value else 0
    ranked["value_segment"] = pd.cut(
        ranked["cumulative_value_share"], bins=[-0.01, 0.8, 0.95, 1.0], labels=["A (top 80%)", "B (next 15%)", "C (tail 5%)"]
    )
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = {"A (top 80%)": "#dc2626", "B (next 15%)": "#d97706", "C (tail 5%)": "#2563eb"}
    labels = ranked["sku_id"] + " · " + ranked["store_id"]
    bars = ax.bar(labels, ranked["inventory_value"], color=[colors[str(segment)] for segment in ranked["value_segment"]])
    ax.bar_label(bars, labels=[f"{value:,.0f}" for value in ranked["inventory_value"]], padding=3)
    ax.set_ylabel("Inventory value")
    ax.set_title("High-value inventory by SKU-store segment")
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(figure_dir / "high-value-inventory-segments.png", **FIGURE_STYLE)
    plt.close(fig)
    return ranked


def outlier_summary(inventory: pd.DataFrame) -> pd.DataFrame:
    values = inventory["inventory_value"].dropna()
    if len(values) < 4:
        return inventory.assign(is_value_outlier=False)
    first_quartile, third_quartile = values.quantile([0.25, 0.75])
    iqr = third_quartile - first_quartile
    return inventory.assign(is_value_outlier=(inventory["inventory_value"] < first_quartile - 1.5 * iqr) | (inventory["inventory_value"] > third_quartile + 1.5 * iqr))


def write_summary(path: Path, missingness: pd.DataFrame, demand: pd.DataFrame, inventory: pd.DataFrame) -> None:
    outliers = outlier_summary(inventory)
    top = inventory.sort_values("inventory_value", ascending=False).iloc[0]
    lines = [
        "# Inventory EDA diagnostic summary",
        "",
        "This output is generated from the selected raw fixture/source extract. It is diagnostic only; KPI and reconciliation outputs remain the governed decision source.",
        "",
        "## Decision cues",
        "",
        f"- Highest observed source-field missingness: **{missingness['missing_pct'].max():.1f}%**.",
        f"- Highest demand SKU: **{demand.iloc[0]['sku_id']}** with **{demand.iloc[0]['units_sold_qty']:.0f}** units sold in the supplied period.",
        f"- Highest inventory-value position: **{top['sku_id']} at {top['store_id']}**, valued at **{top['inventory_value']:,.2f}**.",
        f"- IQR high/low inventory-value outliers: **{int(outliers['is_value_outlier'].sum())}** (small samples may not support meaningful outlier detection).",
        "",
        "## Figures",
        "",
        "- `source-missingness.png` — completeness check by input source.",
        "- `demand-distribution.png` — demand concentration by SKU.",
        "- `stock-patterns.png` — on-hand compared with sellable inventory by SKU-store.",
        "- `high-value-inventory-segments.png` — value concentration and ABC-style segment assignment.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = load_sources(input_dir)
    missingness = save_missingness(frames, output_dir)
    demand = save_demand_distribution(frames["sales"], output_dir)
    inventory = enrich(frames)
    save_stock_patterns(inventory, output_dir)
    save_high_value_and_segments(inventory, output_dir)
    write_summary(output_dir.parent / "eda_summary.md", missingness, demand, inventory)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/figures"))
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run(arguments.input_dir, arguments.output_dir)
