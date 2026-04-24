-- ============================================================
-- Monthly RANGE partitions on bar_5m for 2023-01 .. 2024-12
-- (24 child partitions; partition pruning will be visible in EXPLAIN)
-- ============================================================

DO $$
DECLARE
    start_month DATE := DATE '2023-01-01';
    end_month   DATE := DATE '2025-01-01';   -- exclusive upper bound
    cur DATE := start_month;
    nxt DATE;
    part_name TEXT;
BEGIN
    WHILE cur < end_month LOOP
        nxt := (cur + INTERVAL '1 month')::date;
        part_name := format('bar_5m_%s', to_char(cur, 'YYYY_MM'));
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF bar_5m
             FOR VALUES FROM (%L) TO (%L);',
            part_name, cur, nxt
        );
        cur := nxt;
    END LOOP;
END $$;

-- A default partition catches any out-of-range rows during loading mistakes
CREATE TABLE IF NOT EXISTS bar_5m_default PARTITION OF bar_5m DEFAULT;
