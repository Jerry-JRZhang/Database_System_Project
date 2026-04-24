-- Extensions used by EquityDB.
-- pg_stat_statements needs a matching entry in shared_preload_libraries
-- (see docker-compose.yml) — CREATE EXTENSION only registers the SQL API.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
