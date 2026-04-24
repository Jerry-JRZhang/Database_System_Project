"""Parametrized SQL queries used by the Streamlit demo.

Each function returns a pandas.DataFrame. Connections are handled by the
caller (we use a streamlit cached connection)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd


def _df(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame.

    Avoids `pandas.read_sql` because it warns when given a raw psycopg
    connection (it wants SQLAlchemy). Going through the cursor directly is
    faster and warning-free.
    """
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def list_symbols(conn) -> pd.DataFrame:
    return _df(conn,
        """
        SELECT t.symbol, t.name, s.name AS sector, i.name AS industry
        FROM ticker t
        JOIN industry i USING (industry_id)
        JOIN sector   s USING (sector_id)
        ORDER BY t.symbol
        """,
    )


def date_bounds(conn) -> tuple[datetime, datetime]:
    df = _df(conn, "SELECT MIN(ts) AS mn, MAX(ts) AS mx FROM bar_5m")
    return df["mn"][0], df["mx"][0]


# ---------------------------------------------------------------------------
# Scenario 1: single-ticker chart
# ---------------------------------------------------------------------------

def bars_for_ticker(conn, symbol: str, start: date, end: date) -> pd.DataFrame:
    return _df(conn,
        """
        SELECT ts, open, high, low, close, volume, vwap
        FROM bar_5m b
        JOIN ticker t USING (ticker_id)
        WHERE t.symbol = %(sym)s
          AND ts >= %(start)s AND ts < %(end)s
        ORDER BY ts
        """,
        {"sym": symbol, "start": start, "end": end},
    )


# ---------------------------------------------------------------------------
# Scenario 3: top movers in an arbitrary window
# ---------------------------------------------------------------------------

def top_movers(conn, ts_from: datetime, ts_to: datetime, n: int = 20) -> pd.DataFrame:
    return _df(conn,
        """
        WITH window_bars AS (
            SELECT ticker_id,
                   FIRST_VALUE(open) OVER w AS first_open,
                   LAST_VALUE(close) OVER w AS last_close,
                   SUM(volume)       OVER w AS vol_sum
            FROM bar_5m
            WHERE ts >= %(t0)s AND ts < %(t1)s
            WINDOW w AS (
                PARTITION BY ticker_id
                ORDER BY ts
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )
        ),
        agg AS (
            SELECT DISTINCT ticker_id, first_open, last_close, vol_sum,
                   (last_close / first_open - 1.0) AS ret
            FROM window_bars
        )
        SELECT t.symbol, ROUND((ret*100)::numeric, 3) AS ret_pct,
               ROUND(first_open::numeric, 2)  AS open,
               ROUND(last_close::numeric, 2)  AS close,
               vol_sum                         AS volume
        FROM agg JOIN ticker t USING (ticker_id)
        ORDER BY ret DESC
        LIMIT %(n)s
        """,
        {"t0": ts_from, "t1": ts_to, "n": n},
    )


# ---------------------------------------------------------------------------
# Scenario 9b: daily OHLCV via the matview
# ---------------------------------------------------------------------------

def daily_ohlcv(conn, symbol: str, start: date, end: date) -> pd.DataFrame:
    return _df(conn,
        """
        SELECT b.session_date AS day,
               b.open, b.high, b.low, b.close, b.volume, b.vwap
        FROM bar_1d b
        JOIN ticker t USING (ticker_id)
        WHERE t.symbol = %(sym)s
          AND b.session_date >= %(start)s AND b.session_date < %(end)s
        ORDER BY b.session_date
        """,
        {"sym": symbol, "start": start, "end": end},
    )


# ---------------------------------------------------------------------------
# Scenario 2: cross-section snapshot
# ---------------------------------------------------------------------------

def cross_section(conn, ts: datetime) -> pd.DataFrame:
    return _df(conn,
        """
        WITH asof AS (SELECT %(ts)s::timestamptz AS ts),
        last_bar AS (
            SELECT b.ticker_id, b.close FROM bar_5m b, asof
            WHERE b.ts = asof.ts
        ),
        prev AS (
            SELECT b.ticker_id, b.close AS prev_close FROM bar_5m b, asof
            WHERE b.ts = asof.ts - INTERVAL '1 day'
        )
        SELECT t.symbol, s.name AS sector,
               ROUND(l.close::numeric, 2)         AS close,
               ROUND(p.prev_close::numeric, 2)    AS prev_close,
               ROUND(((l.close / p.prev_close - 1) * 100)::numeric, 3)
                 AS ret_1d_pct
        FROM last_bar l
        JOIN ticker t USING (ticker_id)
        JOIN industry i USING (industry_id)
        JOIN sector   s USING (sector_id)
        LEFT JOIN prev p USING (ticker_id)
        ORDER BY ret_1d_pct DESC NULLS LAST
        """,
        {"ts": ts},
    )


# ---------------------------------------------------------------------------
# Scenario 6: rolling realized volatility
# ---------------------------------------------------------------------------

def rolling_vol(conn, symbol: str, start: datetime, end: datetime,
                window: int = 20) -> pd.DataFrame:
    return _df(conn,
        """
        WITH r AS (
            SELECT ts,
                   LN(close / LAG(close) OVER (ORDER BY ts)) AS logret
            FROM bar_5m b JOIN ticker t USING (ticker_id)
            WHERE t.symbol = %(sym)s
              AND ts >= %(t0)s AND ts < %(t1)s
        )
        SELECT ts,
               SQRT(SUM(logret*logret) OVER (
                   ORDER BY ts ROWS BETWEEN %(w)s PRECEDING AND CURRENT ROW
               )) AS rv
        FROM r
        WHERE logret IS NOT NULL
        ORDER BY ts
        """,
        {"sym": symbol, "t0": start, "t1": end, "w": window - 1},
    )


# ---------------------------------------------------------------------------
# Plan inspection — used by the "DB Internals" tab
# ---------------------------------------------------------------------------

def explain(conn, sql: str, params: dict | None = None) -> str:
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql,
                    params or {})
        return "\n".join(r[0] for r in cur.fetchall())
