"""
run_pipeline.py
===============
CLI entry point to:
  1. Seed the SQLite database with simulated data
  2. Run the full analytics pipeline
  3. Print a summary report to stdout
  4. Export key tables to CSV for external analysis
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from data_generator import seed_database, DB_PATH
from mrr_engine import run_pipeline

EXPORT_DIR = ROOT / "exports"


def print_section(title: str, df: pd.DataFrame):
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)
    print(df.to_string(index=False))


def main():
    print("[pipeline] Step 1/3 — Seeding database...")
    seed_database(DB_PATH, force="--force" in sys.argv)

    print("[pipeline] Step 2/3 — Running analytics pipeline...")
    data = run_pipeline(DB_PATH)

    print("[pipeline] Step 3/3 — Generating report...")

    # KPIs
    wf = data["waterfall"]
    latest = wf.iloc[-1]
    avg_churn = data["churn_rates"]["churn_rate"].mean() * 100

    print("\n" + "╔" + "═" * 70 + "╗")
    print("║{:^70}║".format(" SAAS MRR ANALYTICS REPORT "))
    print("╠" + "═" * 70 + "╣")
    print("║{:<35}{:>35}║".format("  Total Customers", f"{len(data['customers']):,}  "))
    print("║{:<35}{:>35}║".format("  Total Subscription Rows", f"{len(data['subscriptions']):,}  "))
    print("║{:<35}{:>35}║".format("  Latest Total MRR", f"${latest['total_mrr']:>,.2f}  "))
    print("║{:<35}{:>35}║".format("  Latest Net New MRR", f"${latest['net_new_mrr']:>+,.2f}  "))
    print("║{:<35}{:>35}║".format("  Avg Monthly Churn Rate", f"{avg_churn:.2f}%  "))
    print("╚" + "═" * 70 + "╝")

    print_section(
        "MRR WATERFALL — Last 8 months",
        wf[["month", "new_mrr", "expansion_mrr", "contraction_mrr",
             "churn_mrr", "net_new_mrr", "total_mrr"]].tail(8).assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m")
        ),
    )

    print_section("LTV / CAC SUMMARY", data["ltv_cac"])

    print_section(
        "CHURN RATE — Last 6 months",
        data["churn_rates"].tail(6).assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m"),
            churn_rate=lambda d: (d["churn_rate"] * 100).round(2).astype(str) + "%",
        ),
    )

    print_section(
        "MRR FORECAST — Next 12 months",
        data["forecast"][data["forecast"]["is_forecast"]].assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m")
        )[["month", "total_mrr"]],
    )

    # Export to CSV
    EXPORT_DIR.mkdir(exist_ok=True)
    exports = {
        "customers.csv":     data["customers"],
        "subscriptions.csv": data["subscriptions"].assign(
            billing_month=lambda d: d["billing_month"].dt.strftime("%Y-%m")
        ),
        "mrr_waterfall.csv": wf.assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m")
        ),
        "ltv_cac.csv":       data["ltv_cac"],
        "churn_rates.csv":   data["churn_rates"].assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m")
        ),
        "mrr_forecast.csv":  data["forecast"].assign(
            month=lambda d: d["month"].dt.strftime("%Y-%m")
        ),
    }
    for fname, df in exports.items():
        df.to_csv(EXPORT_DIR / fname, index=False)
        print(f"[pipeline] Exported -> exports/{fname}")

    print("\n[pipeline] Done! Launch the dashboard with:\n")
    print("    streamlit run app.py\n")


if __name__ == "__main__":
    main()

