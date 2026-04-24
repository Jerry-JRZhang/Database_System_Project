-- ============================================================
-- Indexes for the hypertable — kept symmetric with ../03_indexes.sql so
-- the A/B comparison isolates the storage engine, not index differences.
-- Timescale propagates these to every chunk automatically.
-- ============================================================

CREATE INDEX IF NOT EXISTS bar_5m_ts_brin_idx
    ON bar_5m USING brin (ts) WITH (pages_per_range = 32);

ANALYZE bar_5m;
ANALYZE ticker;
