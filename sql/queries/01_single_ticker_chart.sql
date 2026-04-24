-- Scenario 1: Single-ticker chart load
-- "Give me all 5-min bars for AAPL during March 2024"
-- Demonstrates: composite PK lookup + range scan + partition pruning
SELECT ts, open, high, low, close, volume
FROM bar_5m b
JOIN ticker t USING (ticker_id)
WHERE t.symbol = 'AAPL'
  AND ts >= TIMESTAMPTZ '2024-03-01 00:00:00+00'
  AND ts <  TIMESTAMPTZ '2024-04-01 00:00:00+00'
ORDER BY ts;
