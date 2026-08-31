import altair as alt
import pandas as pd
import streamlit as st

from budget_app.db.dashboard import get_dashboard_transactions

st.title("📊 Dashboard")

owner_email = st.session_state.get("user_email")
if not owner_email:
    st.info("Sign in to see your dashboard.")
    st.stop()

range_label = st.selectbox(
    "Time range",
    ["Last 3 months", "Last 6 months", "Last 12 months", "Year to date", "All time"],
    index=2,
)

today = pd.Timestamp.today().normalize()
if range_label == "All time":
    start_date = None
elif range_label == "Year to date":
    start_date = pd.Timestamp(today.year, 1, 1).date()
else:
    months = int(range_label.split()[1])
    start_date = (today - pd.DateOffset(months=months)).date()

df = get_dashboard_transactions(owner_email, start_date=start_date)

if df.empty:
    st.info("No transactions yet. Head to **Upload & Categorize** to add some.")
    st.stop()

df["date"] = pd.to_datetime(df["date"])
df["category"] = df["category"].fillna("").replace("", "Uncategorized")

# Single sequential hue: every chart here is one measure (adjusted spend), so
# color encodes magnitude/emphasis, not series identity — no legend needed.
theme_base = st.get_option("theme.base") or "light"
bar_color = "#3987e5" if theme_base == "dark" else "#2a78d6"

total_actual = df["amount"].sum()
total_adjusted = df["adjusted_amount"].sum()
total_lent = df["lent_total"].sum()

col1, col2, col3 = st.columns(3)
col1.metric("Adjusted spend", f"${total_adjusted:,.2f}")
col2.metric("Actual spend", f"${total_actual:,.2f}")
col3.metric("Lent out", f"${total_lent:,.2f}")

st.subheader("Category breakdown")
category_totals = (
    df.groupby("category", dropna=False)["adjusted_amount"]
    .sum()
    .reset_index()
)
category_totals = category_totals[category_totals["adjusted_amount"] > 0]
category_totals = category_totals.sort_values("adjusted_amount", ascending=False)

if category_totals.empty:
    st.caption("Nothing to show for this time range.")
else:
    category_chart = (
        alt.Chart(category_totals)
        .mark_bar(color=bar_color, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("adjusted_amount:Q", title="Adjusted spend ($)"),
            y=alt.Y("category:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("adjusted_amount:Q", title="Adjusted spend", format="$,.2f"),
            ],
        )
        .properties(height=max(200, 28 * len(category_totals)))
    )
    st.altair_chart(category_chart, width="stretch")

    with st.expander("View as table"):
        st.dataframe(
            category_totals.rename(columns={"adjusted_amount": "Adjusted spend"}),
            hide_index=True,
        )

st.subheader("Spend over time")
monthly = (
    df.assign(month=df["date"].dt.to_period("M").dt.to_timestamp())
    .groupby("month")["adjusted_amount"]
    .sum()
    .reset_index()
    .sort_values("month")
)
monthly["month_label"] = monthly["month"].dt.strftime("%b %Y")

time_chart = (
    alt.Chart(monthly)
    .mark_bar(color=bar_color, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
    .encode(
        # Nominal (band scale), not temporal: a continuous time scale places
        # ticks at each interval's boundary rather than centered under the
        # bar, so the label already comes pre-formatted as a plain string.
        x=alt.X(
            "month_label:N",
            sort=list(monthly["month_label"]),
            title=None,
            axis=alt.Axis(labelAngle=0),
        ),
        y=alt.Y("adjusted_amount:Q", title="Adjusted spend ($)"),
        tooltip=[
            alt.Tooltip("month_label:N", title="Month"),
            alt.Tooltip("adjusted_amount:Q", title="Adjusted spend", format="$,.2f"),
        ],
    )
    .properties(height=300)
)
st.altair_chart(time_chart, width="stretch")

with st.expander("View as table"):
    st.dataframe(
        monthly[["month_label", "adjusted_amount"]].rename(
            columns={"month_label": "Month", "adjusted_amount": "Adjusted spend"}
        ),
        hide_index=True,
    )
