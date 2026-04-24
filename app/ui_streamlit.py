"""EquityDB demo UI — Streamlit.

Run:
    streamlit run app/ui_streamlit.py

Each tab demonstrates one or more user scenarios from PROJECT_PLAN.md and
calls out the underlying database concept so the live demo can narrate it.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")   # US equities trade on NYSE/ET
DATA_MIN = date(2023, 1, 1)
DATA_MAX = date(2024, 12, 31)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Make ingest/ importable for the connection helper
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingest"))
from db import connect  # noqa: E402

import queries as Q  # noqa: E402  (in same dir)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="EquityDB Demo", layout="wide")


@st.cache_resource(show_spinner=False)
def get_conn():
    # autocommit so each Streamlit query runs in its own implicit txn
    return connect(autocommit=True)


@st.cache_data(ttl=600, show_spinner=False)
def cached_symbols() -> pd.DataFrame:
    return Q.list_symbols(get_conn())


@st.cache_data(ttl=3600, show_spinner=False)
def cached_bounds() -> tuple[datetime, datetime]:
    return Q.date_bounds(get_conn())


def _fmt_ms(elapsed_s: float) -> str:
    return f"{elapsed_s*1000:,.1f} ms"


def _timed(callable_):
    t0 = time.perf_counter()
    out = callable_()
    return out, time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Sidebar — global controls
# ---------------------------------------------------------------------------

st.sidebar.title("EquityDB")
st.sidebar.caption(
    "5-min OHLCV for ~500 S&P 500 tickers, 2023–2024.\n"
    "PostgreSQL 16, monthly RANGE partitions, materialized daily rollup."
)
mn, mx = cached_bounds()
mn_date = max(mn.astimezone(ET).date(), DATA_MIN)
mx_date = min(mx.astimezone(ET).date(), DATA_MAX)
st.sidebar.markdown(f"**Loaded range (ET)**: {mn_date:%Y-%m-%d} → {mx_date:%Y-%m-%d}")
syms_df = cached_symbols()
st.sidebar.markdown(f"**Tickers loaded**: {len(syms_df)}")

show_explain = st.sidebar.toggle(
    "Show EXPLAIN ANALYZE", value=False,
    help="Run each query a second time with EXPLAIN (ANALYZE, BUFFERS) and show the plan.",
)

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_chart, tab_movers, tab_snap, tab_vol, tab_admin = st.tabs([
    "📈 Ticker chart",
    "🚀 Top movers",
    "📊 Cross-section snapshot",
    "📐 Rolling volatility",
    "⚙️  DB internals",
])


# ---- Tab 1: single-ticker chart (Scenario 1 + Scenario 9b) ----

with tab_chart:
    st.subheader("Single-ticker price + daily rollup")
    st.caption("Scenarios 1 & 9b — composite-PK lookup + matview.")

    col1, col2, col3 = st.columns([2, 1, 1])
    sym = col1.selectbox("Ticker", syms_df["symbol"],
                         index=int(syms_df.index[syms_df["symbol"] == "AAPL"][0]))
    start = col2.date_input("From", value=date(2024, 3, 1),
                            min_value=mn_date, max_value=mx_date)
    end = col3.date_input("To",   value=date(2024, 4, 1),
                          min_value=mn_date, max_value=mx_date)
    if end <= start:
        st.error("'To' must be after 'From'"); st.stop()

    range_days = (end - start).days
    auto_daily = range_days > 5
    opt1, opt2 = st.columns([1, 1])
    use_daily = opt1.toggle(
        "Daily candles (matview)", value=auto_daily,
        help="Auto-on when range > 5 days. Off = 5-min bars from bar_5m.",
    )
    rth_only = opt2.toggle(
        "Regular trading hours only (09:30–16:00 ET)", value=True,
        help="Hides pre/after-market 5-min bars and overnight gaps in the chart.",
    )

    # Always run BOTH queries so we can show the matview-vs-raw timing story
    bars,  t_bars  = _timed(lambda: Q.bars_for_ticker(get_conn(), sym, start, end))
    daily, t_daily = _timed(lambda: Q.daily_ohlcv(get_conn(), sym, start, end))

    m1, m2, m3 = st.columns(3)
    m1.metric("5-min bars (Scenario 1)",            f"{len(bars):,}",  _fmt_ms(t_bars))
    m2.metric("Daily bars from matview (Scen. 9b)", f"{len(daily):,}", _fmt_ms(t_daily))
    if t_daily > 0:
        m3.metric("Matview speedup", f"{t_bars / t_daily:.1f}×",
                  help="Time(raw 5-min query) / Time(matview query)")

    # Build the chart frame
    if use_daily:
        chart_df = daily.rename(columns={"day": "ts"})
        x_is_category = False
    else:
        chart_df = bars.copy()
        # DB stores ts as timestamptz in UTC — convert to ET for display & RTH filter.
        if not chart_df.empty:
            chart_df["ts"] = chart_df["ts"].dt.tz_convert(ET)
        if rth_only and not chart_df.empty:
            chart_df = chart_df[(chart_df["ts"].dt.time >= dtime(9, 30)) &
                                (chart_df["ts"].dt.time <  dtime(16, 0))]
        # Intraday view: a category x-axis is the only reliable way to
        # hide every non-trading gap (overnights, weekends, halts). The
        # data is already RTH-filtered, so each position is a real bar.
        x_is_category = True

    if chart_df.empty:
        st.info("No bars in range.")
    else:
        if x_is_category:
            x_vals = chart_df["ts"].dt.strftime("%Y-%m-%d %H:%M")
        else:
            x_vals = chart_df["ts"]
        fig = go.Figure(data=[go.Candlestick(
            x=x_vals, open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"], close=chart_df["close"], name=sym,
        )])
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_rangeslider_visible=False)
        if x_is_category:
            n = len(chart_df)
            step = max(1, n // 10)
            tickvals = list(x_vals.iloc[::step])
            fig.update_xaxes(type="category", tickmode="array",
                             tickvals=tickvals, tickangle=-30)
        else:
            fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
        st.plotly_chart(fig, width="stretch")

    if show_explain and not bars.empty:
        st.code(Q.explain(get_conn(),
            "SELECT ts, open, high, low, close FROM bar_5m b JOIN ticker t USING (ticker_id) "
            "WHERE t.symbol = %(sym)s AND ts >= %(s)s AND ts < %(e)s ORDER BY ts",
            {"sym": sym, "s": start, "e": end}), language="text")


# ---- Tab 2: top movers (Scenario 3) ----

with tab_movers:
    st.subheader("Top movers in an intraday window")
    st.caption("Scenario 3 — window function over all 503 tickers in a time slice.")

    c1, c2, c3 = st.columns([1, 1, 1])
    day = c1.date_input("Date (ET)", value=date(2024, 9, 18),
                        min_value=mn_date, max_value=mx_date, key="movers_d")
    t_from = c2.time_input("From (ET)", value=dtime(9, 30), key="movers_a")
    t_to   = c3.time_input("To (ET)",   value=dtime(10, 0), key="movers_b")
    n = st.slider("Top N", 5, 50, 20)

    ts0 = datetime.combine(day, t_from, tzinfo=ET)
    ts1 = datetime.combine(day, t_to,   tzinfo=ET)
    if ts1 <= ts0:
        st.error("End time must be after start time"); st.stop()

    df, dt_ = _timed(lambda: Q.top_movers(get_conn(), ts0, ts1, n))
    st.metric(f"Window: {ts0:%Y-%m-%d %H:%M} → {ts1:%H:%M} ET",
              f"{len(df)} rows", _fmt_ms(dt_))
    st.dataframe(df, width="stretch", height=420)

    if show_explain:
        st.code(Q.explain(get_conn(),
            "WITH wb AS (SELECT ticker_id, FIRST_VALUE(open) OVER w fo, LAST_VALUE(close) OVER w lc "
            "FROM bar_5m WHERE ts >= %(a)s AND ts < %(b)s "
            "WINDOW w AS (PARTITION BY ticker_id ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)) "
            "SELECT DISTINCT ticker_id, fo, lc FROM wb",
            {"a": ts0, "b": ts1}), language="text")


# ---- Tab 3: cross-section snapshot (Scenario 2) ----

with tab_snap:
    st.subheader("Cross-section snapshot at a single timestamp")
    st.caption("Scenario 2 — runtime partition pruning across 503 ticker lookups.")

    c1, c2 = st.columns(2)
    snap_day = c1.date_input("Day (ET)", value=date(2024, 6, 28),
                             min_value=mn_date, max_value=mx_date, key="snap_d")
    snap_t   = c2.time_input("Time (ET)", value=dtime(15, 55), key="snap_t")
    ts = datetime.combine(snap_day, snap_t, tzinfo=ET)

    df, dt_ = _timed(lambda: Q.cross_section(get_conn(), ts))
    st.metric(f"Snapshot @ {ts:%Y-%m-%d %H:%M} ET", f"{len(df)} tickers", _fmt_ms(dt_))

    sec = st.selectbox("Filter by sector", ["(all)"] + sorted(df["sector"].dropna().unique().tolist()))
    view = df if sec == "(all)" else df[df["sector"] == sec]
    st.dataframe(view, width="stretch", height=480)


# ---- Tab 4: rolling volatility (Scenario 6) ----

with tab_vol:
    st.subheader("Rolling realized volatility (window function)")
    st.caption("Scenario 6 — LAG + windowed SUM in pure SQL.")

    c1, c2, c3 = st.columns([2, 1, 1])
    sym = c1.selectbox("Ticker", syms_df["symbol"],
                       index=int(syms_df.index[syms_df["symbol"] == "NVDA"][0]),
                       key="vol_sym")
    day = c2.date_input("Date (ET)", value=date(2024, 10, 1),
                        min_value=mn_date, max_value=mx_date, key="vol_d")
    win = c3.slider("Window (bars)", 5, 60, 20)

    t0 = datetime.combine(day, dtime(9, 30),  tzinfo=ET)
    t1 = datetime.combine(day, dtime(16, 0),  tzinfo=ET)
    df, dt_ = _timed(lambda: Q.rolling_vol(get_conn(), sym, t0, t1, win))
    st.metric(f"{sym} on {day:%Y-%m-%d}, {win}-bar realized vol",
              f"{len(df)} bars", _fmt_ms(dt_))
    if not df.empty:
        df = df.copy()
        df["ts"] = df["ts"].dt.tz_convert(ET)
        st.line_chart(df.set_index("ts")["rv"])


# ---- Tab 5: DB internals (the optimization story) ----

with tab_admin:
    st.subheader("Database internals")

    st.markdown("**Partition layout — `bar_5m` children**")
    parts = Q._df(get_conn(),
        """
        SELECT inhrelid::regclass::text AS partition,
               (SELECT pg_size_pretty(pg_relation_size(inhrelid))) AS size,
               (SELECT reltuples::bigint FROM pg_class c WHERE c.oid = inhrelid) AS approx_rows
        FROM pg_inherits
        WHERE inhparent = 'bar_5m'::regclass
        ORDER BY partition
        """,
    )
    st.dataframe(parts, width="stretch", height=320)

    st.markdown("**Indexes on `bar_5m`**")
    idx = Q._df(get_conn(),
        """
        SELECT indexrelid::regclass::text AS index_name,
               indrelid::regclass::text   AS on_table,
               pg_get_indexdef(indexrelid) AS definition
        FROM pg_index
        WHERE indrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = 'bar_5m'::regclass)
        ORDER BY index_name
        LIMIT 50
        """,
    )
    st.dataframe(idx, width="stretch", height=300)

    st.markdown("**Top queries by total time** (from `pg_stat_statements`)")
    try:
        ss = Q._df(get_conn(),
            """
            SELECT calls, ROUND(total_exec_time::numeric, 1) AS total_ms,
                   ROUND(mean_exec_time::numeric, 2) AS mean_ms,
                   LEFT(query, 140) AS query
            FROM pg_stat_statements
            ORDER BY total_exec_time DESC
            LIMIT 20
            """,
        )
        st.dataframe(ss, width="stretch", height=320)
    except Exception as exc:
        st.info(f"pg_stat_statements not available: {exc}")
