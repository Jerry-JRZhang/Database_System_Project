-- Scenario 6: Rolling 20-bar realized volatility
-- "For NVDA on 2024-10-01, compute rolling 20-bar realized vol of log-returns"
-- Demonstrates: window functions (LAG), aggregate over moving window, math in SQL
WITH r AS (
    SELECT ts,
           LN(close / LAG(close) OVER (ORDER BY ts)) AS logret
    FROM bar_5m b
    JOIN ticker t USING (ticker_id)
    WHERE t.symbol = 'NVDA'
      AND ts >= TIMESTAMPTZ '2024-10-01 13:30:00+00'
      AND ts <  TIMESTAMPTZ '2024-10-01 20:00:00+00'
)
SELECT ts,
       SQRT(SUM(logret*logret) OVER (
           ORDER BY ts ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
       )) AS rv_20bar
FROM r
ORDER BY ts;
