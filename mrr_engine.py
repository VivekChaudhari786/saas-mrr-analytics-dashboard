"""
mrr_engine.py
=============
Core analytics pipeline:
  1. Loads raw subscription data from SQLite
  2. Computes MRR waterfall (New / Expansion / Contraction / Churn / Reactivation)
  3. Models Customer LTV using Average Revenue Per User × Gross Margin / Churn Rate
  4. Computes CAC Payback Period (months)

All heavy SQL is done via pandas + SQLite for portability.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = Path(__file__).parent / "saas_data.db"

GROSS_MARGIN = 0.75          # assumed 75% SaaS gross margin for LTV calc
DISCOUNT_RATE = 0.10         # annual discount rate for present-value LTV


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Raw data loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_customers(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT customer_id, acquisition_date, acquisition_cost_usd FROM customers",
        conn,
        parse_dates=["acquisition_date"],
    )
    return df


def load_subscriptions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT s.subscription_id,
               s.customer_id,
               s.billing_month,
               s.plan_name,
               s.monthly_amount_usd
        FROM   subscriptions s
        ORDER  BY s.customer_id, s.billing_month
        """,
        conn,
    )
    df["billing_month"] = pd.to_datetime(df["billing_month"], format="%Y-%m")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2.  MRR Waterfall (cohort-based decomposition)
# ─────────────────────────────────────────────────────────────────────────────

def compute_mrr_waterfall(df_subs: pd.DataFrame) -> pd.DataFrame:
    """
    For each billing month returns:
      month, new_mrr, expansion_mrr, contraction_mrr, churn_mrr,
      reactivation_mrr, net_new_mrr, total_mrr
    """
    # pivot: rows = customer, cols = billing_month, values = monthly_amount_usd
    pivot = (
        df_subs.pivot_table(
            index="customer_id",
            columns="billing_month",
            values="monthly_amount_usd",
            aggfunc="sum",
        )
        .fillna(0)
    )

    months = sorted(pivot.columns)
    records = []

    for i, month in enumerate(months):
        if i == 0:
            # First month: everything is "new"
            records.append(
                {
                    "month": month,
                    "new_mrr": pivot[month].sum(),
                    "expansion_mrr": 0.0,
                    "contraction_mrr": 0.0,
                    "churn_mrr": 0.0,
                    "reactivation_mrr": 0.0,
                }
            )
            continue

        prev_month = months[i - 1]
        curr = pivot[month]
        prev = pivot[prev_month]

        # Completely new (no revenue last month, revenue this month)
        new_mask = (prev == 0) & (curr > 0)
        # Reactivated (had revenue before prev month but not last month)
        if i >= 2:
            before_prev = pivot[months[i - 2]]
            react_mask = (prev == 0) & (curr > 0) & (before_prev > 0)
            new_mask = new_mask & ~react_mask
        else:
            react_mask = pd.Series(False, index=pivot.index)

        # Expansion (revenue increased)
        expansion_mask = (prev > 0) & (curr > prev)
        # Contraction (revenue decreased but still paying)
        contraction_mask = (prev > 0) & (curr < prev) & (curr > 0)
        # Churn (had revenue last month, zero this month)
        churn_mask = (prev > 0) & (curr == 0)

        new_mrr = curr[new_mask].sum()
        expansion_mrr = (curr[expansion_mask] - prev[expansion_mask]).sum()
        contraction_mrr = (curr[contraction_mask] - prev[contraction_mask]).sum()   # negative
        churn_mrr = -prev[churn_mask].sum()                                          # negative
        reactivation_mrr = curr[react_mask].sum()

        records.append(
            {
                "month": month,
                "new_mrr": round(new_mrr, 2),
                "expansion_mrr": round(expansion_mrr, 2),
                "contraction_mrr": round(contraction_mrr, 2),
                "churn_mrr": round(churn_mrr, 2),
                "reactivation_mrr": round(reactivation_mrr, 2),
            }
        )

    df_wf = pd.DataFrame(records)
    df_wf["net_new_mrr"] = (
        df_wf["new_mrr"]
        + df_wf["expansion_mrr"]
        + df_wf["contraction_mrr"]
        + df_wf["churn_mrr"]
        + df_wf["reactivation_mrr"]
    ).round(2)
    df_wf["total_mrr"] = df_wf["net_new_mrr"].cumsum().round(2)
    return df_wf


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Cohort retention & churn rate
# ─────────────────────────────────────────────────────────────────────────────

def compute_cohort_retention(df_subs: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a cohort × month_number retention table (% of cohort still active).
    Cohorts are defined by acquisition_month.
    """
    df = df_subs.copy()
    df["cohort"] = df.groupby("customer_id")["billing_month"].transform("min")
    df["cohort"] = df["cohort"].dt.to_period("M")
    df["billing_period"] = df["billing_month"].dt.to_period("M")
    df["month_number"] = (df["billing_period"] - df["cohort"]).apply(lambda x: x.n)

    cohort_size = df.groupby("cohort")["customer_id"].nunique().rename("cohort_size")

    retention = (
        df.groupby(["cohort", "month_number"])["customer_id"]
        .nunique()
        .reset_index()
        .rename(columns={"customer_id": "active_customers"})
    )
    retention = retention.merge(cohort_size, on="cohort")
    retention["retention_rate"] = retention["active_customers"] / retention["cohort_size"]
    return retention


def compute_monthly_churn_rates(df_subs: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate monthly gross churn rate = churned customers / customers at start of month.
    """
    pivot = (
        df_subs.pivot_table(
            index="customer_id",
            columns="billing_month",
            values="monthly_amount_usd",
            aggfunc="sum",
        )
        .fillna(0)
    )
    months = sorted(pivot.columns)
    rows = []
    for i in range(1, len(months)):
        prev = pivot[months[i - 1]]
        curr = pivot[months[i]]
        active_prev = (prev > 0).sum()
        churned = ((prev > 0) & (curr == 0)).sum()
        churn_rate = churned / active_prev if active_prev > 0 else 0
        rows.append(
            {
                "month": months[i],
                "active_start": int(active_prev),
                "churned": int(churned),
                "churn_rate": round(churn_rate, 4),
            }
        )
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  LTV & CAC metrics
# ─────────────────────────────────────────────────────────────────────────────

def compute_ltv_cac(
    df_subs: pd.DataFrame,
    df_customers: pd.DataFrame,
) -> pd.DataFrame:
    """
    Per-plan LTV and CAC summary.

    LTV  = (ARPU × Gross Margin) / Monthly Churn Rate
    CAC  = average acquisition cost for customers on that starting plan
    LTV:CAC ratio
    Payback = CAC / (ARPU × Gross Margin)  [months]
    """
    # ARPU per plan
    arpu = (
        df_subs.groupby("plan_name")["monthly_amount_usd"]
        .mean()
        .rename("arpu")
    )

    # Monthly churn rate per plan
    pivot = (
        df_subs.pivot_table(
            index="customer_id",
            columns="billing_month",
            values="monthly_amount_usd",
            aggfunc="sum",
        )
        .fillna(0)
    )
    months = sorted(pivot.columns)
    churn_data = []
    for i in range(1, len(months)):
        prev = pivot[months[i - 1]]
        curr = pivot[months[i]]
        for cid in pivot.index:
            if prev[cid] > 0:
                plan = df_subs[df_subs["customer_id"] == cid]["plan_name"].iloc[-1]
                churned = 1 if curr[cid] == 0 else 0
                churn_data.append({"plan_name": plan, "churned": churned})

    df_churn_data = pd.DataFrame(churn_data)
    plan_churn_rate = (
        df_churn_data.groupby("plan_name")["churned"]
        .mean()
        .rename("monthly_churn_rate")
    )

    # CAC: map starting plan from first subscription record
    first_plan = (
        df_subs.sort_values("billing_month")
        .groupby("customer_id")["plan_name"]
        .first()
        .rename("starting_plan")
    )
    df_cac = df_customers.merge(first_plan, on="customer_id")
    avg_cac = (
        df_cac.groupby("starting_plan")["acquisition_cost_usd"]
        .mean()
        .rename("avg_cac")
    )

    # Assemble — use join instead of DataFrame constructor to avoid misaligned index
    summary = (
        pd.DataFrame({"arpu": arpu})
        .join(plan_churn_rate, how="left")
        .join(avg_cac, how="left")
        .dropna()
    )

    summary["ltv"] = (
        summary["arpu"] * GROSS_MARGIN / summary["monthly_churn_rate"]
    ).round(2)
    summary["ltv_cac_ratio"] = (summary["ltv"] / summary["avg_cac"]).round(2)
    summary["payback_months"] = (
        summary["avg_cac"] / (summary["arpu"] * GROSS_MARGIN)
    ).round(1)

    return summary.reset_index().rename(columns={"index": "plan_name"})


# ─────────────────────────────────────────────────────────────────────────────
# 5.  MRR by plan
# ─────────────────────────────────────────────────────────────────────────────

def compute_mrr_by_plan(df_subs: pd.DataFrame) -> pd.DataFrame:
    return (
        df_subs.groupby(["billing_month", "plan_name"])["monthly_amount_usd"]
        .sum()
        .reset_index()
        .rename(columns={"monthly_amount_usd": "mrr"})
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Revenue forecast (simple exponential smoothing + linear trend)
# ─────────────────────────────────────────────────────────────────────────────

def forecast_mrr(df_wf: pd.DataFrame, horizon_months: int = 12) -> pd.DataFrame:
    """
    Naive but interpretable forecast:
      fit linear trend on last 12 months of total_mrr, extrapolate forward.
    """
    history = df_wf[["month", "total_mrr"]].dropna().copy()
    history = history.sort_values("month").reset_index(drop=True)

    # Use last 12 months for trend
    lookback = history.tail(12)
    x = np.arange(len(lookback))
    y = lookback["total_mrr"].values

    # Linear fit
    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs

    last_month = history["month"].iloc[-1]
    future_months = pd.date_range(
        start=last_month + pd.DateOffset(months=1),
        periods=horizon_months,
        freq="MS",
    )
    future_x = np.arange(len(lookback), len(lookback) + horizon_months)
    future_mrr = np.polyval(coeffs, future_x).clip(0)

    df_forecast = pd.DataFrame(
        {
            "month": future_months,
            "total_mrr": np.round(future_mrr, 2),
            "is_forecast": True,
        }
    )
    history["is_forecast"] = False
    return pd.concat([history, df_forecast], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Main entry point (run standalone to verify)
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    df_customers = load_customers(conn)
    df_subs = load_subscriptions(conn)
    conn.close()

    df_wf = compute_mrr_waterfall(df_subs)
    df_churn = compute_monthly_churn_rates(df_subs)
    df_ltv = compute_ltv_cac(df_subs, df_customers)
    df_plan_mrr = compute_mrr_by_plan(df_subs)
    df_forecast = forecast_mrr(df_wf)
    df_cohort = compute_cohort_retention(df_subs)

    return {
        "customers": df_customers,
        "subscriptions": df_subs,
        "waterfall": df_wf,
        "churn_rates": df_churn,
        "ltv_cac": df_ltv,
        "plan_mrr": df_plan_mrr,
        "forecast": df_forecast,
        "cohort_retention": df_cohort,
    }


if __name__ == "__main__":
    results = run_pipeline()
    print("\n=== MRR Waterfall (last 5 months) ===")
    print(results["waterfall"].tail(5).to_string(index=False))
    print("\n=== LTV / CAC Summary ===")
    print(results["ltv_cac"].to_string(index=False))
