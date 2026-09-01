import altair as alt
import pandas as pd
import streamlit as st

from budget_app.db.dashboard import get_dashboard_transactions
from budget_app.transactions.categories import CATEGORIES

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
df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
df["month_label"] = df["month"].dt.strftime("%b %Y")

theme_base = st.context.theme.type or "light"
bar_color = "#3987e5" if theme_base == "dark" else "#2a78d6"
TEXT_INK = "#ffffff" if theme_base == "dark" else "#0b0b0b"  # dataviz skill's primary ink tokens
LIMIT_COLOR = "#d03b3b"  # status "critical" — mode-invariant, reserved for threshold/limit cues

total_actual = df["amount"].sum()
total_adjusted = df["adjusted_amount"].sum()
total_lent = df["lent_total"].sum()

col1, col2, col3 = st.columns(3)
col1.metric("Adjusted spend", f"${total_adjusted:,.2f}")
col2.metric("Actual spend", f"${total_actual:,.2f}")
col3.metric("Lent out", f"${total_lent:,.2f}")

st.subheader("Spend over time")

# Part-to-whole job (category composition per month): color carries real
# identity here. Fixed per-category color mapping, not ranked by spend
# ("color follows the entity, never its rank" — a filter/month change must
# not repaint the survivors). All 10 app categories get their own slot from
# a 10-hue order extended past the skill's 8-hue reference and validated with
# scripts/validate_palette.js (adjacent-pairs form, matching stacked bars) —
# ALL CHECKS PASS in both light and dark, so nothing needs to fold away here.
CATEGORY_COLOR_ORDER = CATEGORIES
CATEGORICAL_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948", "#0079a0", "#767a0f"]
CATEGORICAL_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767", "#1a96b5", "#8b8f1c"]

# "Uncategorized" (a blank category — an edge case outside the fixed 10)
# shares the "Other" slot rather than needing an 11th unvalidated hue.
df["category_grouped"] = df["category"].replace("Uncategorized", "Other")

monthly_cat = (
    df.groupby(["month", "month_label", "category_grouped"])["adjusted_amount"]
    .sum()
    .reset_index()
    .sort_values("month")
)

month_order = list(dict.fromkeys(df.sort_values("month")["month_label"]))
category_rank = df.groupby("category")["adjusted_amount"].sum().sort_values(ascending=False)
present_categories = set(monthly_cat["category_grouped"])
category_order = [c for c in CATEGORY_COLOR_ORDER if c in present_categories]
palette = CATEGORICAL_DARK if theme_base == "dark" else CATEGORICAL_LIGHT
category_color_scale = alt.Scale(domain=CATEGORY_COLOR_ORDER, range=palette)

bar_col, pie_col = st.columns([3, 1])

with bar_col:
    # Click a bar to drill the pie chart into that month; click again (or an
    # empty click) clears it back to the whole selected range.
    month_click = alt.selection_point(fields=["month_label"], on="click", empty=True, name="month_click")

    month_totals = (
        df.groupby(["month", "month_label"])["adjusted_amount"].sum().reset_index().sort_values("month")
    )

    # Binding the click directly to the colored stacked segments scoped the
    # selection to the clicked segment's full identity (month + category),
    # not just month_label, even with fields=["month_label"] set — a Vega-Lite
    # quirk with point selections on stacked/colored marks. This invisible,
    # category-free layer is the only thing the click binds to, so the
    # selection can only ever be scoped to month. It has to sit on top to
    # actually receive the click, which means it also owns hover/tooltip now.
    click_catcher = (
        alt.Chart(month_totals)
        .mark_bar(opacity=0.001)
        .encode(
            x=alt.X("month_label:N", sort=month_order),
            y=alt.Y("adjusted_amount:Q"),
            tooltip=[
                alt.Tooltip("month_label:N", title="Month"),
                alt.Tooltip("adjusted_amount:Q", title="Total adjusted spend", format="$,.2f"),
            ],
        )
        .add_params(month_click)
    )

    visible_bars = (
        alt.Chart(monthly_cat)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            # Nominal (band scale), not temporal: a continuous time scale places
            # ticks at each interval's boundary rather than centered under the
            # bar, so the label already comes pre-formatted as a plain string.
            x=alt.X("month_label:N", sort=month_order, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("adjusted_amount:Q", title="Adjusted spend ($)"),
            color=alt.Color(
                "category_grouped:N",
                title="Category",
                scale=category_color_scale,
                legend=alt.Legend(symbolSize=260, labelFontSize=14, titleFontSize=15),
            ),
            opacity=alt.condition(month_click, alt.value(1), alt.value(0.45)),
        )
    )

    stacked_chart = (visible_bars + click_catcher).properties(height=320)
    bar_event = st.altair_chart(
        stacked_chart, width="stretch", on_select="rerun", key="stacked_bar_chart"
    )

    with st.expander("View as table"):
        pivot = monthly_cat.pivot_table(
            index="month_label", columns="category_grouped", values="adjusted_amount", fill_value=0
        ).reindex(month_order)[category_order]
        pivot.index.name = "Month"
        st.dataframe(pivot, width="stretch")

if bar_event is not None:
    selection = bar_event["selection"] if isinstance(bar_event, dict) else bar_event.selection
else:
    selection = {}
selected_points = selection.get("month_click", [])
selected_months = [pt["month_label"] for pt in selected_points]

with pie_col:
    if selected_months:
        pie_source = df[df["month_label"].isin(selected_months)]
        pie_caption = f"Category breakdown — {selected_months[0]}"
    else:
        pie_source = df
        pie_caption = f"Category breakdown — {range_label}"

    st.caption(pie_caption)
    pie_data = (
        pie_source.groupby("category_grouped")["adjusted_amount"]
        .sum()
        .reset_index()
    )
    pie_data = pie_data[pie_data["adjusted_amount"] > 0]

    # Explicit, identical stack order for every layer. The arc layer's color
    # scale has a fixed domain (CATEGORY_COLOR_ORDER), which Vega-Lite uses
    # to decide stacking order — but a layer with no color encoding (the
    # text layer) has nothing to infer order from and falls back to
    # groupby's alphabetical row order instead, silently stacking in a
    # different sequence than the arcs and landing labels on the wrong
    # slice. An explicit numeric order field, present on both layers, removes
    # the ambiguity instead of relying on inferred sort behavior.
    category_rank_map = {cat: i for i, cat in enumerate(CATEGORY_COLOR_ORDER)}
    pie_data["order_rank"] = pie_data["category_grouped"].map(category_rank_map)

    pie_arcs = (
        alt.Chart(pie_data)
        .mark_arc(outerRadius=110)
        .encode(
            theta=alt.Theta("adjusted_amount:Q", stack=True),
            order=alt.Order("order_rank:Q"),
            # Legend is skipped here — the bar chart's legend to the left
            # already names every color in this shared category scale.
            color=alt.Color("category_grouped:N", scale=category_color_scale, legend=None),
            tooltip=[
                alt.Tooltip("category_grouped:N", title="Category"),
                alt.Tooltip("adjusted_amount:Q", title="Adjusted spend", format="$,.2f"),
            ],
        )
    )
    # Direct dollar labels, placed just outside the slice rather than on top
    # of it — avoids picking a text color that has to stay readable against
    # every slice's fill. Only labeled above a small-share threshold so tiny
    # slices don't turn into overlapping label soup (still visible via color
    # and the hover tooltip either way). Both layers must stack over the
    # exact same rows in the same order — theta with stack=True computes
    # each layer's angular position independently, so filtering only the
    # label layer's data desyncs its running total from the arcs' and the
    # labels land on the wrong slice.
    pie_data["share"] = pie_data["adjusted_amount"] / pie_data["adjusted_amount"].sum()
    pie_data["label"] = pie_data["adjusted_amount"].map(lambda v: f"${v:,.0f}")
    pie_data.loc[pie_data["share"] <= 0.03, "label"] = ""

    pie_labels = (
        alt.Chart(pie_data)
        .mark_text(radius=130, size=12, color=TEXT_INK)
        .encode(
            theta=alt.Theta("adjusted_amount:Q", stack=True),
            order=alt.Order("order_rank:Q"),
            text=alt.Text("label:N"),
        )
    )
    st.altair_chart((pie_arcs + pie_labels).properties(height=320), width="stretch")

st.subheader("Spend by category")

category_options = list(category_rank.index)

cat_col, monthly_col, yearly_col = st.columns(3)
with cat_col:
    selected_category = st.selectbox("Category", category_options)

category_monthly = (
    df[df["category"] == selected_category]
    .groupby(["month", "month_label"])["adjusted_amount"]
    .sum()
    .reset_index()
    .sort_values("month")
)
cat_month_order = list(category_monthly["month_label"])

default_monthly_limit = float(round(category_monthly["adjusted_amount"].mean(), -1)) or 50.0
default_yearly_limit = default_monthly_limit * 12
monthly_limit_key = f"limit_input_{selected_category}"
yearly_limit_key = f"yearly_limit_input_{selected_category}"

# Seed session_state once per category, before either widget is created,
# rather than passing value= alongside an already-set key (which Streamlit
# flags as a policy violation once a sync callback has touched it).
if monthly_limit_key not in st.session_state:
    st.session_state[monthly_limit_key] = default_monthly_limit
if yearly_limit_key not in st.session_state:
    st.session_state[yearly_limit_key] = default_yearly_limit

def _sync_yearly_from_monthly():
    st.session_state[yearly_limit_key] = round(st.session_state[monthly_limit_key] * 12, 2)

def _sync_monthly_from_yearly():
    st.session_state[monthly_limit_key] = round(st.session_state[yearly_limit_key] / 12, 2)

with monthly_col:
    limit_value = st.number_input(
        f"Monthly limit for {selected_category} ($)",
        min_value=0.0,
        step=10.0,
        key=monthly_limit_key,
        on_change=_sync_yearly_from_monthly,
    )
with yearly_col:
    st.number_input(
        f"Yearly limit for {selected_category} ($)",
        min_value=0.0,
        step=100.0,
        key=yearly_limit_key,
        on_change=_sync_monthly_from_yearly,
    )

# "This year" always means the actual current calendar year, regardless of
# the Time range filter at the top of the page (which could be "Last 3
# months" etc.) — so it needs its own year-scoped query, not a slice of df.
year_start = pd.Timestamp(today.year, 1, 1).date()
year_df = get_dashboard_transactions(owner_email, start_date=year_start)
year_df["category"] = year_df["category"].fillna("").replace("", "Uncategorized")
total_this_year = year_df.loc[year_df["category"] == selected_category, "adjusted_amount"].sum()

effective_yearly_limit = st.session_state[yearly_limit_key]
total_left_this_year = effective_yearly_limit - total_this_year

chart_col, stats_col = st.columns([3, 1])

# Single category selected: sequential hue for the spend line (no legend
# needed on its own), but the limit line is a distinct semantic role (a
# threshold, not a series), so it uses the reserved status "critical" red —
# both get a name via the shared "series" field so one small legend
# distinguishes them.
spend_line = (
    alt.Chart(category_monthly.assign(series="Spend"))
    .mark_line(point=alt.OverlayMarkDef(size=80), strokeWidth=2)
    .encode(
        x=alt.X("month_label:N", sort=cat_month_order, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("adjusted_amount:Q", title="Adjusted spend ($)"),
        color=alt.Color(
            "series:N",
            title=None,
            scale=alt.Scale(domain=["Spend", "Monthly limit"], range=[bar_color, LIMIT_COLOR]),
        ),
        tooltip=[
            alt.Tooltip("month_label:N", title="Month"),
            alt.Tooltip("adjusted_amount:Q", title="Adjusted spend", format="$,.2f"),
        ],
    )
)
limit_rule = (
    alt.Chart(pd.DataFrame({"limit": [limit_value], "series": ["Monthly limit"]}))
    .mark_rule(strokeDash=[6, 4], size=2)
    .encode(
        y="limit:Q",
        color=alt.Color("series:N", scale=alt.Scale(domain=["Spend", "Monthly limit"], range=[bar_color, LIMIT_COLOR])),
        tooltip=[alt.Tooltip("limit:Q", title="Monthly limit", format="$,.2f")],
    )
)
with chart_col:
    st.altair_chart((spend_line + limit_rule).properties(height=320), width="stretch")

    with st.expander("View as table"):
        st.dataframe(
            category_monthly[["month_label", "adjusted_amount"]].rename(
                columns={"month_label": "Month", "adjusted_amount": "Adjusted spend"}
            ),
            hide_index=True,
        )

with stats_col:
    # One explicit flexbox, fully specified here, rather than relying on
    # st.columns' vertical_alignment plus container gap plus per-element
    # justify-content — that combination fought itself and left the two
    # metrics pinned to opposite corners instead of centered as a group.
    # min-height approximates the chart's rendered height (320px chart +
    # collapsed expander) so justify-content: center has room to center
    # against, both metrics + divider treated as one group.
    with st.container(key="category-stats"):
        st.html(
            """
            <style>
            .st-key-category-stats {
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                min-height: 360px;
            }
            .st-key-category-stats [data-testid="stMetric"] {
                display: flex;
                flex-direction: column;
                align-items: center;
                text-align: center;
                width: 100%;
            }
            .st-key-category-stats [data-testid="stMetricLabel"] p {
                font-size: 1.15rem;
            }
            .st-key-category-stats hr {
                margin: 2rem 0;
            }
            </style>
            """
        )
        st.metric("Spent this year", f"${total_this_year:,.2f}")
        st.divider()
        st.metric("Left for this year", f"${total_left_this_year:,.2f}")
