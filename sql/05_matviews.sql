-- ============================================================
-- Materialized views — used by the optimization story.
-- Compare Scenario 9 (daily rollup) before/after the matview.
-- ============================================================

-- Regular trading session is 09:30–16:00 America/New_York. We convert once per
-- row via `AT TIME ZONE` so the filter is DST-correct year-round: a naive
-- 13:30–20:00 UTC filter silently drops the last hour of every EST session
-- (Nov–Mar) and includes an hour of pre-market.
CREATE MATERIALIZED VIEW IF NOT EXISTS bar_1d AS
WITH rth AS (
    SELECT ts, ticker_id, open, high, low, close, volume, vwap, trade_count,
           (ts AT TIME ZONE 'America/New_York') AS ts_et
    FROM bar_5m
)
SELECT
    ticker_id,
    ts_et::date AS session_date,
    (array_agg(open  ORDER BY ts))[1]                          AS open,
    MAX(high)                                                  AS high,
    MIN(low)                                                   AS low,
    (array_agg(close ORDER BY ts DESC))[1]                     AS close,
    SUM(volume)                                                AS volume,
    SUM(volume * vwap) / NULLIF(SUM(volume), 0)                AS vwap,
    SUM(trade_count)                                           AS trade_count
FROM rth
WHERE ts_et::time >= TIME '09:30' AND ts_et::time < TIME '16:00'
GROUP BY ticker_id, ts_et::date;

CREATE UNIQUE INDEX IF NOT EXISTS bar_1d_pk ON bar_1d (ticker_id, session_date);
CREATE INDEX IF NOT EXISTS bar_1d_date_idx ON bar_1d (session_date);

ANALYZE bar_1d;
