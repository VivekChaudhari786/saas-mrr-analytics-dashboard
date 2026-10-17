# SaaS MRR Decomposition & LTV Forecast

A complete analytics pipeline and interactive Streamlit dashboard for SaaS
Monthly Recurring Revenue decomposition and Customer Lifetime Value modeling.

## Project Structure

```
mrr_dashboard/
├── app.py              ← Streamlit dashboard (9 interactive sections)
├── mrr_engine.py       ← Core analytics: waterfall, LTV/CAC, forecast, cohort
├── data_generator.py   ← Synthetic data simulation → SQLite
├── sql_queries.py      ← Reusable SQL query library
├── run_pipeline.py     ← CLI runner: seed DB + print report + export CSVs
├── saas_data.db        ← Auto-generated SQLite database (after first run)
├── exports/            ← Auto-generated CSV exports
└── README.md
```

## Quick Start

### 1. Run the pipeline (seeds DB + prints report)

```bash
cd mrr_dashboard
python run_pipeline.py
```

Add `--force` to regenerate the database from scratch:

```bash
python run_pipeline.py --force
```

### 2. Launch the Streamlit dashboard

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## What's Inside

### Data Model

#### `customers` table
| Column | Type | Description |
|---|---|---|
| `customer_id` | TEXT PK | Unique customer identifier |
| `acquisition_date` | TEXT | ISO date of first subscription |
| `acquisition_cost_usd` | REAL | CAC — marketing spend to acquire |

#### `subscriptions` table
| Column | Type | Description |
|---|---|---|
| `subscription_id` | TEXT PK | Unique subscription row |
| `customer_id` | TEXT FK | Links to customers |
| `billing_month` | TEXT | YYYY-MM format |
| `plan_name` | TEXT | Basic / Pro / Enterprise |
| `monthly_amount_usd` | REAL | Revenue for that month |

### Plan Pricing

| Plan | Monthly Price | Starting Churn | CAC Range |
|---|---|---|---|
| Basic | \$29 | 7%/mo | \$80–\$250 |
| Pro | \$99 | 4%/mo | \$200–\$600 |
| Enterprise | \$299 | 2%/mo | \$500–\$2,000 |

---

## MRR Waterfall Definitions

| Component | Definition |
|---|---|
| **New MRR** | Revenue from brand-new customers (no prior history) |
| **Expansion MRR** | Revenue increase from existing customers (upgrades) |
| **Contraction MRR** | Revenue decrease from existing customers (downgrades) — negative |
| **Churn MRR** | Revenue lost from customers who cancelled — negative |
| **Reactivation MRR** | Revenue from previously churned customers who returned |
| **Net New MRR** | New + Expansion + Contraction + Churn + Reactivation |
| **Total MRR** | Cumulative sum of Net New MRR |

---

## LTV / CAC Formulas

```
LTV          = (ARPU × Gross Margin) / Monthly Churn Rate
LTV:CAC      = LTV / Avg CAC
Payback (mo) = Avg CAC / (ARPU × Gross Margin)
```

Assumptions:
- Gross Margin = 75%
- Annual Discount Rate = 10% (for PV LTV variant)

---

## Dashboard Sections

1. **KPI Header** — Total MRR, Net New MRR, Avg Churn %, Max LTV, Min CAC, Min Payback
2. **MRR Waterfall** — Stacked bar chart decomposing each component
3. **MRR by Plan** — Area chart showing plan mix over time
4. **Cumulative MRR + Forecast** — Historical + linear-trend projection
5. **Monthly Churn Rate** — Trend line with average benchmark
6. **LTV / CAC Table & Bar Chart** — Per-plan comparison
7. **CAC Payback Period** — Bar chart with 12-month benchmark line
8. **Cohort Retention Heatmap** — % of each cohort still active by month
9. **Raw Data Explorer** — Searchable tables for Customers, Subscriptions, Waterfall

### Sidebar Controls
- **Date Range** slider
- **Plan** multiselect filter
- **Forecast Horizon** (3–24 months)
- **Refresh** button to clear cache

---

## SQL Queries Available (`sql_queries.py`)

| Query | Description |
|---|---|
| `TOTAL_MRR_BY_MONTH` | Aggregate MRR per month |
| `MRR_BY_PLAN_MONTH` | MRR + customer count by plan/month |
| `MRR_DELTA_CTE` | Customer-level movement type (new/churn/expansion/contraction) |
| `NEW_MRR_QUERY` | New MRR per month |
| `CHURN_MRR_QUERY` | Churn MRR per month |
| `LTV_TENURE_QUERY` | Lifetime revenue, gross profit, net LTV per customer |
| `PLAN_TRANSITIONS_QUERY` | Upgrade/downgrade events |
| `CAC_PAYBACK_QUERY` | Months to recover CAC per customer |
| `ACTIVE_CUSTOMERS_QUERY` | Active subscriber count per month |
