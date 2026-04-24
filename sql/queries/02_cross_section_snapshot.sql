-- Scenario 2: Cross-section snapshot
-- "For 2024-06-28 16:00 UTC, give me the latest close + 1-day return for every ticker"
-- Demonstrates: lateral join, time-range scan across all tickers, BRIN index value
WITH asof AS (SELECT TIMESTAMPTZ '2024-06-28 19:55:00+00' AS ts),
last_bar AS (
    SELECT b.ticker_id, b.ts, b.close
    FROM bar_5m b, asof
    WHERE b.ts = asof.ts
),
prev_day AS (
    SELECT b.ticker_id, b.close AS prev_close
    FROM bar_5m b, asof
    WHERE b.ts = asof.ts - INTERVAL '1 day'
)
SELECT t.symbol, l.close, p.prev_close,
       (l.close / p.prev_close - 1.0) AS ret_1d
FROM last_bar l
JOIN ticker t USING (ticker_id)
LEFT JOIN prev_day p USING (ticker_id)
ORDER BY ret_1d DESC NULLS LAST;
