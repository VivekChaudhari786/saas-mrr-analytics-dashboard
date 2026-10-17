"""
app.py
======
Streamlit interactive dashboard for SaaS MRR Decomposition & LTV/CAC Forecast.

Theme:  System (auto-detect OS preference) / Light / Dark toggle in sidebar.
        A tiny JS snippet reads window.matchMedia on first load and stores the
        result in ?theme= query-param so Streamlit can pick it up.

Sections:
  1.  KPI header row
  2.  MRR Waterfall stacked-bar chart
  3.  MRR by Plan (area chart)
  4.  Cumulative MRR + 12-month forecast
  5.  Monthly Churn Rate trend
  6.  LTV / CAC table & bar chart
  7.  CAC Payback bars
  8.  Cohort retention heatmap
  9.  Raw data explorer (collapsible)
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── local modules ─────────────────────────────────────────────────────────────
from data_generator import seed_database, DB_PATH
from mrr_engine import (
    load_customers,
    load_subscriptions,
    compute_mrr_waterfall,
    compute_monthly_churn_rates,
    compute_ltv_cac,
    compute_mrr_by_plan,
    forecast_mrr,
    compute_cohort_retention,
)

# ─────────────────────────────────────────────────────────────────────────────
# Page config  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SaaS MRR Decomposition & LTV Forecast",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — JS snippet: detect OS theme on FIRST load, write to query-param
#   ?theme=dark  or  ?theme=light
#   On subsequent loads the query-param is already there so JS is a no-op.
# ─────────────────────────────────────────────────────────────────────────────
THEME_DETECT_JS = """
<script>
(function() {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('theme')) {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        params.set('theme', prefersDark ? 'dark' : 'light');
        const newUrl = window.location.pathname + '?' + params.toString();
        window.history.replaceState({}, '', newUrl);
        // Trigger a reload so Streamlit sees the new query param
        window.location.reload();
    }
})();
</script>
"""
st.components.v1.html(THEME_DETECT_JS, height=0)

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Read query-param to know the OS preference
# ─────────────────────────────────────────────────────────────────────────────
qp = st.query_params
os_pref = qp.get("theme", "light")   # 'light' or 'dark' injected by JS above

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Sidebar theme selector
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/combo-chart.png", width=64)
    st.title("MRR Dashboard")
    st.caption("SaaS Analytics · SQLite + Python")
    st.divider()

    theme_choice = st.radio(
        "🎨 Theme",
        options=["System", "Light", "Dark"],
        index=0,
        horizontal=True,
    )

# Resolve effective theme
if theme_choice == "System":
    effective_theme = os_pref        # what the browser/OS prefers
elif theme_choice == "Light":
    effective_theme = "light"
else:
    effective_theme = "dark"

IS_DARK = (effective_theme == "dark")

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Theme tokens
# ─────────────────────────────────────────────────────────────────────────────
if IS_DARK:
    BG          = "#0d1117"
    CARD_BG     = "#161b22"
    CARD_BORDER = "#30363d"
    TEXT_PRI    = "#e6edf3"
    TEXT_SEC    = "#8b949e"
    METRIC_VAL  = "#e6edf3"
    DELTA_POS   = "#3fb950"
    DELTA_NEG   = "#f85149"
    SIDEBAR_BG  = "#010409"
    PLOTLY_TPL  = "plotly_dark"
    DIV_COLOR   = "#21262d"
else:
    BG          = "#f6f8fa"
    CARD_BG     = "#ffffff"
    CARD_BORDER = "#d0d7de"
    TEXT_PRI    = "#1f2328"
    TEXT_SEC    = "#57606a"
    METRIC_VAL  = "#1f2328"
    DELTA_POS   = "#1a7f37"
    DELTA_NEG   = "#cf222e"
    SIDEBAR_BG  = "#eaeef2"
    PLOTLY_TPL  = "plotly_white"
    DIV_COLOR   = "#d0d7de"

PALETTE = {
    "new":          "#2da44e",
    "expansion":    "#0969da",
    "contraction":  "#fb8f44",
    "churn":        "#cf222e",
    "reactivation": "#8250df",
    "forecast":     "#a78bfa",
    "Basic":        "#0969da",
    "Pro":          "#8250df",
    "Enterprise":   "#fb8f44",
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Inject CSS driven by theme tokens
# ─────────────────────────────────────────────────────────────────────────────
CSS = f"""
<style>
/* ── App background ─────────────────────────────────────── */
.stApp {{
    background-color: {BG};
}}

/* ── Sidebar ─────────────────────────────────────────────── */
section[data-testid="stSidebar"] {{
    background-color: {SIDEBAR_BG};
}}
section[data-testid="stSidebar"] * {{
    color: {TEXT_PRI} !important;
}}

/* ── Headings ────────────────────────────────────────────── */
h1, h2, h3, h4, .stMarkdown p {{
    color: {TEXT_PRI} !important;
}}

/* ── Caption / small text ────────────────────────────────── */
.stCaption, small, .stCaption span {{
    color: {TEXT_SEC} !important;
}}

/* ── Metric card ─────────────────────────────────────────── */
.metric-card {{
    background-color: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,.12);
}}
.metric-label {{
    color: {TEXT_SEC};
    font-size: 0.75rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 4px;
}}
.metric-value {{
    color: {METRIC_VAL};
    font-size: 1.75rem;
    font-weight: 700;
    margin: 4px 0;
}}
.metric-delta-pos {{ color: {DELTA_POS}; font-size: 0.82rem; }}
.metric-delta-neg {{ color: {DELTA_NEG}; font-size: 0.82rem; }}

/* ── Divider ─────────────────────────────────────────────── */
hr {{
    border-color: {DIV_COLOR} !important;
}}

/* ── DataFrames ──────────────────────────────────────────── */
.stDataFrame {{
    background-color: {CARD_BG} !important;
    border: 1px solid {CARD_BORDER} !important;
    border-radius: 8px;
}}

/* ── Tab strip ───────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {{
    background-color: {CARD_BG};
    border-bottom: 2px solid {CARD_BORDER};
}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Data loading (cached)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="Seeding database ...")
def get_db_conn():
    seed_database(DB_PATH, force=False)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data(ttl=300, show_spinner="Running analytics pipeline ...")
def load_all_data():
    conn = get_db_conn()
    df_cust = load_customers(conn)
    df_subs = load_subscriptions(conn)

    df_wf       = compute_mrr_waterfall(df_subs)
    df_churn    = compute_monthly_churn_rates(df_subs)
    df_ltv      = compute_ltv_cac(df_subs, df_cust)
    df_plan_mrr = compute_mrr_by_plan(df_subs)
    df_forecast = forecast_mrr(df_wf, horizon_months=12)
    df_cohort   = compute_cohort_retention(df_subs)

    return df_cust, df_subs, df_wf, df_churn, df_ltv, df_plan_mrr, df_forecast, df_cohort


df_cust, df_subs, df_wf, df_churn, df_ltv, df_plan_mrr, df_forecast, df_cohort = load_all_data()


# ─────────────────────────────────────────────────────────────────────────────
# Rest of sidebar  (filters below theme toggle)
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.divider()

    all_months = sorted(df_wf["month"].dt.to_period("M").astype(str).unique())

    selected_range = st.select_slider(
        "📅 Date Range",
        options=all_months,
        value=(all_months[0], all_months[-1]),
    )

    selected_plans = st.multiselect(
        "📦 Plans",
        options=["Basic", "Pro", "Enterprise"],
        default=["Basic", "Pro", "Enterprise"],
    )

    forecast_horizon = st.slider(
        "🔮 Forecast Horizon (months)", min_value=3, max_value=24, value=12
    )

    st.divider()
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    # Show resolved theme info
    st.caption(
        f"Theme: **{theme_choice}** "
        f"{'🌙' if IS_DARK else '☀️'} "
        f"(OS pref: {os_pref})"
    )


# ── apply date filter ──────────────────────────────────────────────────────────
def filter_by_date(df: pd.DataFrame, col: str = "month"):
    s = pd.Period(selected_range[0], "M")
    e = pd.Period(selected_range[1], "M")
    mask = df[col].dt.to_period("M").between(s, e)
    return df[mask]


df_wf_f       = filter_by_date(df_wf)
df_churn_f    = filter_by_date(df_churn)
df_plan_mrr_f = filter_by_date(df_plan_mrr, col="billing_month")
if selected_plans:
    df_plan_mrr_f = df_plan_mrr_f[df_plan_mrr_f["plan_name"].isin(selected_plans)]

# Re-compute forecast with sidebar horizon
df_forecast_dyn = forecast_mrr(df_wf, horizon_months=forecast_horizon)


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("# 📈 SaaS MRR Decomposition & LTV Forecast")
st.caption(
    f"Data: **{len(df_cust):,} customers** · "
    f"**{len(df_subs):,} subscription records** · "
    f"Period **{all_months[0]} → {all_months[-1]}**"
)
st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 · KPI Tiles
# ─────────────────────────────────────────────────────────────────────────────
latest = df_wf_f.iloc[-1] if len(df_wf_f) else df_wf.iloc[-1]

total_mrr     = latest["total_mrr"]
mrr_mom_delta = latest["net_new_mrr"]
avg_churn     = df_churn_f["churn_rate"].mean() * 100 if len(df_churn_f) else 0
ltv_best      = df_ltv["ltv"].max()
cac_best      = df_ltv["avg_cac"].min()
payback_best  = df_ltv["payback_months"].min()


def delta_color(val):
    return "metric-delta-pos" if val >= 0 else "metric-delta-neg"


def delta_arrow(val):
    return "▲" if val >= 0 else "▼"


tiles = [
    ("Total MRR",           f"${total_mrr:,.0f}",         mrr_mom_delta,  "mom"),
    ("Net New MRR (MoM)",   f"${mrr_mom_delta:+,.0f}",    mrr_mom_delta,  ""),
    ("Avg Monthly Churn",   f"{avg_churn:.2f}%",          -avg_churn,     ""),
    ("Max LTV",             f"${ltv_best:,.0f}",           ltv_best,       ""),
    ("Min CAC",             f"${cac_best:,.0f}",          -cac_best,       ""),
    ("Min Payback",         f"{payback_best:.1f} mo",     -payback_best,   ""),
]

cols = st.columns(6)
for col, (label, value, delta, suffix) in zip(cols, tiles):
    with col:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{value}</div>
                <div class="{delta_color(delta)}">{delta_arrow(delta)} {abs(delta):,.1f}{suffix}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: themed Plotly layout defaults
# ─────────────────────────────────────────────────────────────────────────────
def themed_layout(**kwargs):
    base = dict(
        template=PLOTLY_TPL,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=30, b=10),
        font_color=TEXT_PRI,
    )
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 · MRR Waterfall
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("💧 MRR Waterfall — New · Expansion · Contraction · Churn")

wf_melt = df_wf_f.melt(
    id_vars="month",
    value_vars=["new_mrr", "expansion_mrr", "contraction_mrr", "churn_mrr", "reactivation_mrr"],
    var_name="component",
    value_name="mrr",
)
wf_melt["component"] = wf_melt["component"].str.replace("_mrr", "").str.capitalize()
color_map = {k.capitalize(): v for k, v in PALETTE.items() if k in
             ["new", "expansion", "contraction", "churn", "reactivation"]}

fig_wf = px.bar(
    wf_melt,
    x="month", y="mrr",
    color="component",
    color_discrete_map=color_map,
    barmode="relative",
    labels={"mrr": "MRR (USD)", "month": "Month", "component": "Component"},
    height=420,
)
fig_wf.update_layout(
    **themed_layout(),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_wf, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 & 4 · Plan MRR + Forecast
# ─────────────────────────────────────────────────────────────────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("📦 MRR by Plan")
    fig_plan = px.area(
        df_plan_mrr_f,
        x="billing_month", y="mrr",
        color="plan_name",
        color_discrete_map={k: PALETTE[k] for k in ["Basic", "Pro", "Enterprise"]},
        labels={"mrr": "MRR (USD)", "billing_month": "Month", "plan_name": "Plan"},
        height=360,
    )
    fig_plan.update_layout(**themed_layout())
    st.plotly_chart(fig_plan, use_container_width=True)

with col_b:
    st.subheader(f"🔮 Cumulative MRR + {forecast_horizon}-Month Forecast")
    hist_fc = df_forecast_dyn[~df_forecast_dyn["is_forecast"]]
    fore_fc = df_forecast_dyn[df_forecast_dyn["is_forecast"]]

    fig_fc = go.Figure()
    fig_fc.add_trace(go.Scatter(
        x=hist_fc["month"], y=hist_fc["total_mrr"],
        mode="lines", name="Historical MRR",
        line=dict(color=PALETTE["expansion"], width=2),
    ))
    fig_fc.add_trace(go.Scatter(
        x=fore_fc["month"], y=fore_fc["total_mrr"],
        mode="lines+markers", name="Forecast",
        line=dict(color=PALETTE["forecast"], width=2, dash="dash"),
        marker=dict(size=5),
    ))
    fig_fc.update_layout(
        **themed_layout(height=360),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis_title="Cumulative MRR (USD)",
        xaxis_title="Month",
    )
    st.plotly_chart(fig_fc, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 · Monthly Churn Rate
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("📉 Monthly Gross Churn Rate")
fig_churn = px.line(
    df_churn_f, x="month", y="churn_rate",
    labels={"churn_rate": "Churn Rate", "month": "Month"},
    height=280, markers=True,
    color_discrete_sequence=[PALETTE["churn"]],
)
fig_churn.add_hline(
    y=df_churn_f["churn_rate"].mean(),
    line_dash="dot", line_color="orange",
    annotation_text=f"Avg {df_churn_f['churn_rate'].mean()*100:.2f}%",
    annotation_font_color=TEXT_PRI,
)
fig_churn.update_yaxes(tickformat=".1%")
fig_churn.update_layout(**themed_layout())
st.plotly_chart(fig_churn, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 · LTV / CAC
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("💰 LTV · CAC · Payback Period by Plan")
col_ltv1, col_ltv2 = st.columns([2, 3])

with col_ltv1:
    display_ltv = df_ltv.rename(columns={
        "plan_name":          "Plan",
        "arpu":               "ARPU ($)",
        "monthly_churn_rate": "Monthly Churn",
        "avg_cac":            "Avg CAC ($)",
        "ltv":                "LTV ($)",
        "ltv_cac_ratio":      "LTV:CAC",
        "payback_months":     "Payback (mo)",
    })
    display_ltv["Monthly Churn"] = (
        display_ltv["Monthly Churn"] * 100
    ).round(2).astype(str) + "%"
    display_ltv["ARPU ($)"] = display_ltv["ARPU ($)"].round(2)
    st.dataframe(display_ltv.set_index("Plan"), use_container_width=True, height=180)

with col_ltv2:
    fig_ltv = go.Figure()
    for _, row in df_ltv.iterrows():
        plan = row["plan_name"]
        fig_ltv.add_trace(go.Bar(
            name=plan,
            x=["LTV", "Avg CAC"],
            y=[row["ltv"], row["avg_cac"]],
            marker_color=PALETTE.get(plan, "#888"),
            text=[f"${row['ltv']:,.0f}", f"${row['avg_cac']:,.0f}"],
            textposition="outside",
            textfont=dict(color=TEXT_PRI),
        ))
    fig_ltv.update_layout(
        **themed_layout(height=280),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis_title="USD",
    )
    st.plotly_chart(fig_ltv, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 · CAC Payback
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("⏱ CAC Payback Period")
fig_pb = px.bar(
    df_ltv, x="plan_name", y="payback_months",
    color="plan_name", color_discrete_map=PALETTE,
    text="payback_months",
    labels={"payback_months": "Payback (months)", "plan_name": "Plan"},
    height=280,
)
fig_pb.add_hline(
    y=12, line_dash="dash", line_color="red",
    annotation_text="12-month benchmark",
    annotation_font_color=TEXT_PRI,
)
fig_pb.update_traces(texttemplate="%{text:.1f} mo", textposition="outside",
                     textfont=dict(color=TEXT_PRI))
fig_pb.update_layout(**themed_layout(), showlegend=False)
st.plotly_chart(fig_pb, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 · Cohort Retention Heatmap
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("🗓 Cohort Retention Heatmap")

cohort_pivot = (
    df_cohort[df_cohort["month_number"] <= 12]
    .pivot_table(index="cohort", columns="month_number", values="retention_rate")
)
cohort_pivot.index = cohort_pivot.index.astype(str)
cohort_pivot = cohort_pivot.tail(18)

color_scale = "Blues" if not IS_DARK else "teal"
fig_heat = px.imshow(
    cohort_pivot,
    color_continuous_scale=color_scale,
    aspect="auto",
    labels=dict(x="Month Since Acquisition", y="Acquisition Cohort", color="Retention"),
    text_auto=".0%",
    height=450,
)
fig_heat.update_layout(**themed_layout())
st.plotly_chart(fig_heat, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 · Raw Data Explorer
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("🗄 Raw Data Explorer", expanded=False):
    tab1, tab2, tab3 = st.tabs(["Customers", "Subscriptions", "MRR Waterfall"])

    with tab1:
        st.dataframe(df_cust, use_container_width=True, height=300)

    with tab2:
        search = st.text_input("Filter by Customer ID or Plan")
        df_sub_display = df_subs.copy()
        df_sub_display["billing_month"] = df_sub_display["billing_month"].dt.strftime("%Y-%m")
        if search:
            mask = (
                df_sub_display["customer_id"].str.contains(search, case=False)
                | df_sub_display["plan_name"].str.contains(search, case=False)
            )
            df_sub_display = df_sub_display[mask]
        st.dataframe(df_sub_display, use_container_width=True, height=300)

    with tab3:
        wf_display = df_wf.copy()
        wf_display["month"] = wf_display["month"].dt.strftime("%Y-%m")
        st.dataframe(wf_display, use_container_width=True, height=300)


# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    f"Theme: {'Dark 🌙' if IS_DARK else 'Light ☀️'} (choice: {theme_choice}) · "
    "Built with Python · Pandas · SQLite · Plotly · Streamlit  |  "
    "LTV = (ARPU × GM) / Churn Rate"
)
