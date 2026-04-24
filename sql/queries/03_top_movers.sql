-- Scenario 3: Top movers in an arbitrary intraday window
-- "Top 20 tickers by return between 2024-09-18 13:30 and 14:00 UTC"
-- Demonstrates: window function over all tickers, hash agg, ranking
WITH window_bars AS (
    SELECT ticker_id,
           FIRST_VALUE(open)  OVER w AS first_open,
           LAST_VALUE(close)  OVER w AS last_close
    FROM bar_5m
    WHERE ts >= TIMESTAMPTZ '2024-09-18 13:30:00+00'
      AND ts <  TIMESTAMPTZ '2024-09-18 14:00:00+00'
    WINDOW w AS (
        PARTITION BY ticker_id
        ORDER BY ts
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
),
agg AS (
    SELECT DISTINCT ticker_id, first_open, last_close,
           (last_close / first_open - 1.0) AS ret
    FROM window_bars
)
SELECT t.symbol, ROUND(ret::numeric * 100, 3) AS ret_pct
FROM agg JOIN ticker t USING (ticker_id)
ORDER BY ret DESC
LIMIT 20;
