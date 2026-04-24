-- Scenario 10: Market-wide volume by 5-min bucket
-- "Total dollar-volume across ALL S&P 500 tickers for each 5-min bar
--  during the first trading week of 2024 (Jan 2 - Jan 5)."
-- Demonstrates: cross-ticker time-range scan with NO ticker filter.
--   - Without BRIN: full PK index scan or sequential heap scan of every partition.
--   - With BRIN(ts): planner can skip ranges of disk pages outside the window.
SELECT ts,
       SUM(volume * vwap)::double precision AS dollar_volume,
       SUM(volume)                          AS share_volume,
       COUNT(*)                             AS bars
FROM bar_5m
WHERE ts >= TIMESTAMPTZ '2024-01-02 14:30:00+00'
  AND ts <  TIMESTAMPTZ '2024-01-06 00:00:00+00'
GROUP BY ts
ORDER BY ts;
