"""
data_generator.py
=================
Generates realistic dummy SaaS billing data and seeds a local SQLite database.

Tables created:
  - customers     (customer_id, acquisition_date, acquisition_cost_usd)
  - subscriptions (subscription_id, customer_id, billing_month, plan_name, monthly_amount_usd)
"""

import sqlite3
import random
import string
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Reproducibility ──────────────────────────────────────────────────────────
SEED = 42
rng = np.random.default_rng(SEED)
random.seed(SEED)

# ── Config ───────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "saas_data.db"

NUM_CUSTOMERS = 300
SIM_START = date(2023, 1, 1)
SIM_END = date(2025, 9, 30)          # ~33 months of history

PLAN_PRICES = {
    "Basic":      29.0,
    "Pro":        99.0,
    "Enterprise": 299.0,
}

# Churn probability per month (per plan)
MONTHLY_CHURN_RATE = {
    "Basic":      0.07,
    "Pro":        0.04,
    "Enterprise": 0.02,
}

# Probability of upgrading in any given month (if not churned)
UPGRADE_PROB = {
    "Basic":      0.06,   # Basic → Pro
    "Pro":        0.03,   # Pro  → Enterprise
    "Enterprise": 0.00,   # can't go higher
}

# Downgrade probability
DOWNGRADE_PROB = {
    "Basic":      0.00,
    "Pro":        0.02,   # Pro → Basic
    "Enterprise": 0.01,   # Enterprise → Pro
}

# CAC ranges per plan (USD)
CAC_RANGE = {
    "Basic":      (80,  250),
    "Pro":        (200, 600),
    "Enterprise": (500, 2000),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def random_id(prefix: str, n: int = 8) -> str:
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def months_between(start: date, end: date):
    """Yield (year, month) tuples from start to end (inclusive)."""
    cur = start.replace(day=1)
    end = end.replace(day=1)
    while cur <= end:
        yield cur
        # advance one month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def next_plan(current: str, action: str) -> str:
    order = ["Basic", "Pro", "Enterprise"]
    idx = order.index(current)
    if action == "upgrade":
        return order[min(idx + 1, 2)]
    if action == "downgrade":
        return order[max(idx - 1, 0)]
    return current


# ── Core simulation ──────────────────────────────────────────────────────────

def simulate():
    all_months = list(months_between(SIM_START, SIM_END))
    total_months = len(all_months)

    customers_rows = []
    subscriptions_rows = []

    sub_counter = 1

    for _ in range(NUM_CUSTOMERS):
        cust_id = random_id("CUST_")

        # Acquisition date: random month in sim window (bias toward earlier months)
        acq_idx = int(rng.integers(0, total_months - 1))
        acq_month = all_months[acq_idx]

        # Starting plan: weighted toward Basic
        starting_plan = rng.choice(
            ["Basic", "Pro", "Enterprise"], p=[0.55, 0.35, 0.10]
        )
        cac_lo, cac_hi = CAC_RANGE[starting_plan]
        cac = round(float(rng.uniform(cac_lo, cac_hi)), 2)

        customers_rows.append(
            {
                "customer_id": cust_id,
                "acquisition_date": acq_month.strftime("%Y-%m-%d"),
                "acquisition_cost_usd": cac,
            }
        )

        # Simulate month-by-month subscription
        current_plan = starting_plan
        churned = False

        for month in all_months[acq_idx:]:
            if churned:
                break

            monthly_amount = PLAN_PRICES[current_plan]

            subscriptions_rows.append(
                {
                    "subscription_id": f"SUB_{sub_counter:07d}",
                    "customer_id": cust_id,
                    "billing_month": month.strftime("%Y-%m"),
                    "plan_name": current_plan,
                    "monthly_amount_usd": monthly_amount,
                }
            )
            sub_counter += 1

            # Decide next-month state
            r = rng.random()
            churn_p = MONTHLY_CHURN_RATE[current_plan]
            upgrade_p = UPGRADE_PROB[current_plan]
            downgrade_p = DOWNGRADE_PROB[current_plan]

            if r < churn_p:
                churned = True
            elif r < churn_p + upgrade_p:
                current_plan = next_plan(current_plan, "upgrade")
            elif r < churn_p + upgrade_p + downgrade_p:
                current_plan = next_plan(current_plan, "downgrade")
            # else: stay on same plan

    df_customers = pd.DataFrame(customers_rows)
    df_subscriptions = pd.DataFrame(subscriptions_rows)
    return df_customers, df_subscriptions


# ── DB seeding ───────────────────────────────────────────────────────────────

def seed_database(db_path: Path = DB_PATH, force: bool = False):
    if db_path.exists() and not force:
        print(f"[data_generator] Database already exists at {db_path}. Skipping seed.")
        return

    print("[data_generator] Simulating SaaS billing data ...")
    df_cust, df_subs = simulate()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executescript(
        """
        DROP TABLE IF EXISTS subscriptions;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id          TEXT PRIMARY KEY,
            acquisition_date     TEXT NOT NULL,   -- ISO date YYYY-MM-DD
            acquisition_cost_usd REAL NOT NULL
        );

        CREATE TABLE subscriptions (
            subscription_id     TEXT PRIMARY KEY,
            customer_id         TEXT NOT NULL,
            billing_month       TEXT NOT NULL,    -- YYYY-MM
            plan_name           TEXT NOT NULL,
            monthly_amount_usd  REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE INDEX IF NOT EXISTS idx_subs_customer ON subscriptions(customer_id);
        CREATE INDEX IF NOT EXISTS idx_subs_month    ON subscriptions(billing_month);
        """
    )

    df_cust.to_sql("customers", conn, if_exists="append", index=False)
    df_subs.to_sql("subscriptions", conn, if_exists="append", index=False)

    conn.commit()
    conn.close()

    print(
        f"[data_generator] Seeded {len(df_cust):,} customers "
        f"and {len(df_subs):,} subscription rows -> {db_path}"
    )


if __name__ == "__main__":
    seed_database(force=True)
