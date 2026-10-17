"""
sql_queries.py
==============
Collection of reusable SQL queries that operate against the SQLite database.
These can also be executed directly via any SQLite client.
"""

# ── 1. Total MRR per billing month ───────────────────────────────────────────
TOTAL_MRR_BY_MONTH = """
SELECT   billing_month,
         SUM(monthly_amount_usd) AS total_mrr
FROM     subscriptions
GROUP BY billing_month
ORDER BY billing_month;
"""

# ── 2. MRR breakdown by plan per month ───────────────────────────────────────
MRR_BY_PLAN_MONTH = """
SELECT   billing_month,
         plan_name,
         COUNT(DISTINCT customer_id) AS active_customers,
         SUM(monthly_amount_usd)     AS plan_mrr
FROM     subscriptions
GROUP BY billing_month, plan_name
ORDER BY billing_month, plan_name;
"""

# ── 3. Customer-level MRR delta (for waterfall decomposition) ─────────────────
MRR_DELTA_CTE = """
WITH monthly AS (
    SELECT customer_id,
           billing_month,
           SUM(monthly_amount_usd) AS mrr
    FROM   subscriptions
    GROUP  BY customer_id, billing_month
),
lagged AS (
    SELECT customer_id,
           billing_month,
           mrr,
           LAG(mrr) OVER (PARTITION BY customer_id ORDER BY billing_month) AS prev_mrr,
           LAG(billing_month) OVER (PARTITION BY customer_id ORDER BY billing_month) AS prev_month
    FROM   monthly
)
SELECT
    billing_month,
    customer_id,
    COALESCE(prev_mrr, 0) AS prev_mrr,
    mrr,
    mrr - COALESCE(prev_mrr, 0) AS mrr_delta,
    CASE
        WHEN prev_mrr IS NULL OR prev_mrr = 0 THEN 'new'
        WHEN mrr      = 0                      THEN 'churned'
        WHEN mrr      > prev_mrr               THEN 'expansion'
        WHEN mrr      < prev_mrr               THEN 'contraction'
        ELSE 'flat'
    END AS movement_type
FROM lagged
ORDER BY billing_month, movement_type;
"""

# ── 4. New MRR per month ──────────────────────────────────────────────────────
NEW_MRR_QUERY = f"""
WITH base AS ({MRR_DELTA_CTE.strip()})
SELECT billing_month,
       SUM(mrr) AS new_mrr
FROM   base
WHERE  movement_type = 'new'
GROUP  BY billing_month
ORDER  BY billing_month;
"""

# ── 5. Churn MRR per month ────────────────────────────────────────────────────
CHURN_MRR_QUERY = f"""
WITH base AS ({MRR_DELTA_CTE.strip()})
SELECT billing_month,
       -SUM(prev_mrr) AS churn_mrr
FROM   base
WHERE  movement_type = 'churned'
GROUP  BY billing_month
ORDER  BY billing_month;
"""

# ── 6. LTV proxy via average tenure ──────────────────────────────────────────
LTV_TENURE_QUERY = """
SELECT c.customer_id,
       c.acquisition_cost_usd                                       AS cac,
       MIN(s.billing_month)                                         AS first_month,
       MAX(s.billing_month)                                         AS last_month,
       COUNT(DISTINCT s.billing_month)                              AS active_months,
       AVG(s.monthly_amount_usd)                                    AS avg_monthly_revenue,
       SUM(s.monthly_amount_usd)                                    AS lifetime_revenue,
       SUM(s.monthly_amount_usd) * 0.75                            AS gross_profit,
       SUM(s.monthly_amount_usd) * 0.75 - c.acquisition_cost_usd  AS net_ltv
FROM   customers  c
JOIN   subscriptions s ON s.customer_id = c.customer_id
GROUP  BY c.customer_id, c.acquisition_cost_usd
ORDER  BY net_ltv DESC;
"""

# ── 7. Plan upgrade / downgrade events ───────────────────────────────────────
PLAN_TRANSITIONS_QUERY = """
WITH ordered AS (
    SELECT customer_id,
           billing_month,
           plan_name,
           LAG(plan_name) OVER (PARTITION BY customer_id ORDER BY billing_month) AS prev_plan
    FROM   subscriptions
)
SELECT billing_month,
       prev_plan,
       plan_name AS current_plan,
       CASE
           WHEN prev_plan = 'Basic'        AND plan_name = 'Pro'         THEN 'upgrade'
           WHEN prev_plan = 'Pro'          AND plan_name = 'Enterprise'  THEN 'upgrade'
           WHEN prev_plan = 'Pro'          AND plan_name = 'Basic'       THEN 'downgrade'
           WHEN prev_plan = 'Enterprise'   AND plan_name = 'Pro'         THEN 'downgrade'
           ELSE 'lateral'
       END AS transition_type,
       COUNT(*) AS event_count
FROM   ordered
WHERE  prev_plan IS NOT NULL
  AND  prev_plan <> plan_name
GROUP  BY billing_month, prev_plan, plan_name
ORDER  BY billing_month;
"""

# ── 8. CAC payback period per customer ───────────────────────────────────────
CAC_PAYBACK_QUERY = """
WITH cumulative AS (
    SELECT s.customer_id,
           s.billing_month,
           c.acquisition_cost_usd AS cac,
           SUM(s.monthly_amount_usd * 0.75)
               OVER (PARTITION BY s.customer_id ORDER BY s.billing_month) AS cum_gross_profit,
           ROW_NUMBER()
               OVER (PARTITION BY s.customer_id ORDER BY s.billing_month) AS month_num
    FROM   subscriptions s
    JOIN   customers     c ON c.customer_id = s.customer_id
)
SELECT customer_id,
       MIN(month_num) AS payback_months,
       MIN(cac)       AS cac
FROM   cumulative
WHERE  cum_gross_profit >= cac
GROUP  BY customer_id
ORDER  BY payback_months;
"""

# ── 9. Active customer count per month ───────────────────────────────────────
ACTIVE_CUSTOMERS_QUERY = """
SELECT   billing_month,
         COUNT(DISTINCT customer_id) AS active_customers
FROM     subscriptions
GROUP BY billing_month
ORDER BY billing_month;
"""


if __name__ == "__main__":
    import sqlite3
    from pathlib import Path

    db = Path(__file__).parent / "saas_data.db"
    conn = sqlite3.connect(db)

    print("=== Total MRR (last 5 months) ===")
    import pandas as pd
    df = pd.read_sql_query(TOTAL_MRR_BY_MONTH, conn)
    print(df.tail(5).to_string(index=False))

    print("\n=== Customer LTV (top 5) ===")
    df2 = pd.read_sql_query(LTV_TENURE_QUERY, conn)
    print(df2.head(5).to_string(index=False))

    conn.close()
