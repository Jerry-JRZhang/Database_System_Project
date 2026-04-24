-- ============================================================
-- Sanity checks to run AFTER load_bars.py finishes.
-- Use:  docker exec -i equitydb-pg psql -U equity -d equitydb -f /sql/04_validate_load.sql
-- ============================================================

\echo '== Row counts'
SELECT (SELECT COUNT(*) FROM bar_5m)             AS total_bars,
       (SELECT COUNT(DISTINCT ticker_id) FROM bar_5m) AS distinct_tickers,
       (SELECT MIN(ts) FROM bar_5m)              AS first_ts,
       (SELECT MAX(ts) FROM bar_5m)              AS last_ts;

\echo '== Per-partition row counts'
SELECT inhrelid::regclass::text AS part,
       (SELECT reltuples::bigint FROM pg_class c WHERE c.oid = inhrelid) AS approx_rows
FROM pg_inherits
WHERE inhparent = 'bar_5m'::regclass
ORDER BY part;

\echo '== Top 5 most-active tickers'
SELECT t.symbol, COUNT(*) AS bars
FROM bar_5m b JOIN ticker t USING (ticker_id)
GROUP BY t.symbol
ORDER BY bars DESC
LIMIT 5;

\echo '== Symbols missing from bar_5m (in ticker table but no bars)'
SELECT t.symbol
FROM ticker t
LEFT JOIN bar_5m b ON b.ticker_id = t.ticker_id
WHERE b.ticker_id IS NULL
ORDER BY t.symbol;

\echo '== Sample: AAPL first day of 2023 regular session'
SELECT ts, open, high, low, close, volume
FROM bar_5m b JOIN ticker t USING (ticker_id)
WHERE t.symbol = 'AAPL'
  AND ts >= TIMESTAMPTZ '2023-01-03 14:30:00+00'
  AND ts <  TIMESTAMPTZ '2023-01-03 21:00:00+00'
ORDER BY ts
LIMIT 10;
