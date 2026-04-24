-- ============================================================
-- Baseline indexes for bar_5m (the PK already covers (ticker_id, ts))
-- These run AFTER bulk load completes for speed.
-- ============================================================

-- BRIN on ts: cheap, tiny, great for cross-section / time-range scans
CREATE INDEX IF NOT EXISTS bar_5m_ts_brin_idx
    ON bar_5m USING brin (ts) WITH (pages_per_range = 32);

-- Optional secondary B-tree on ts for cases where BRIN is too coarse.
-- Left commented; enable during the optimization story to compare plans.
-- CREATE INDEX IF NOT EXISTS bar_5m_ts_btree_idx ON bar_5m (ts);

-- Refresh planner stats after bulk load
ANALYZE bar_5m;
ANALYZE ticker;
