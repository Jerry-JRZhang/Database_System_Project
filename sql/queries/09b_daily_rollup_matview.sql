-- Scenario 9b: Same daily-OHLCV question as Q9, but rewritten to use the
-- bar_1d materialized view. Compare against 09_daily_rollup__brin.txt.
-- This is the "matview rewrite" half of the optimization story.
SELECT b.session_date AS day,
       b.open, b.high, b.low, b.close, b.volume
FROM bar_1d b
JOIN ticker t USING (ticker_id)
WHERE t.symbol = 'AAPL'
  AND b.session_date >= DATE '2024-01-01'
  AND b.session_date <  DATE '2025-01-01'
ORDER BY b.session_date;
