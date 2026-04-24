-- Scenario 9: Daily OHLCV rollup from 5-minute bars.
-- "Give me daily OHLCV for AAPL during 2024 (regular trading hours only)"
-- Demonstrates: GROUP BY with array_agg-style FIRST/LAST in one pass over
-- the filtered set. Compare the plan / wall time against Q9b (matview).
WITH rth AS (
    SELECT (ts AT TIME ZONE 'America/New_York')::date AS day,
           ts, open, high, low, close, volume
    FROM bar_5m b
    JOIN ticker t USING (ticker_id)
    WHERE t.symbol = 'AAPL'
      AND ts >= TIMESTAMPTZ '2024-01-01' AND ts < TIMESTAMPTZ '2025-01-01'
      AND (ts AT TIME ZONE 'America/New_York')::time >= TIME '09:30'
      AND (ts AT TIME ZONE 'America/New_York')::time <  TIME '16:00'
)
SELECT day,
       (array_agg(open  ORDER BY ts))[1]      AS open,
       MAX(high)                              AS high,
       MIN(low)                               AS low,
       (array_agg(close ORDER BY ts DESC))[1] AS close,
       SUM(volume)                            AS volume
FROM rth
GROUP BY day
ORDER BY day;
