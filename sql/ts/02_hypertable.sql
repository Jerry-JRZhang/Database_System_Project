-- ============================================================
-- Promote bar_5m to a TimescaleDB hypertable.
--
-- chunk_time_interval = 1 month so chunks line up 1:1 with the monthly
-- RANGE partitions in the vanilla DB. That way the only variable in the
-- benchmark is "manual partition vs. hypertable machinery", not chunk size.
--
-- migrate_data => TRUE would move any existing rows into chunks. We call
-- this BEFORE loading data, so the table is empty and the flag is moot.
-- ============================================================

SELECT create_hypertable(
    'bar_5m',
    'ts',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists       => TRUE
);
