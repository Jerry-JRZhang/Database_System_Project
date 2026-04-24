# EquityDB — 5-Minute OHLCV Analytics on PostgreSQL

A database-systems final project: PostgreSQL 16 over US S&P 500 5-minute
bars (2023–2024, ~22 M rows) with a Python/Streamlit demo. An optional
TimescaleDB container provides a head-to-head comparison.

Documentation:
- [docs/design_notes.md](docs/design_notes.md) — normalization & physical design
- [docs/schema.dbml](docs/schema.dbml) — ER-diagram source
- [docs/optimization_notes.md](docs/optimization_notes.md) — benchmark results & lessons

## Prerequisites

- Docker Desktop
- Python 3.13 (`py install 3.13` via the Python Install Manager)
- ~5 GB free disk

## Inputs (already in repo)

- `data/bars_2023-01-01_to_2024-01-01.parquet`
- `data/bars_2024-01-01_to_2025-01-01.parquet`
- `constituents.csv` — S&P 500 universe with GICS sector/sub-industry

## M1 — Bring up Postgres + schema + metadata

```powershell
powershell -ExecutionPolicy Bypass -File scripts/init_db.ps1
```

What it does, in order:
1. `docker compose up -d postgres`
2. Applies `sql/00_extensions.sql`, `01_schema.sql`, `02_partitions.sql`,
   `99_seed_exchanges.sql`
3. Creates `.venv`, installs `requirements.txt`
4. Runs `ingest/seed_meta.py` (sectors/industries/503 tickers)
5. Runs `ingest/seed_calendar.py` (XNYS/XNAS trading sessions)

## M2 — Bulk-load 5-min bars

```powershell
.\.venv\Scripts\python.exe ingest\load_bars.py
```

COPY-based bulk load from parquet → partitioned `bar_5m` (24 monthly
RANGE partitions). Drops + recreates CHECK constraints around the load,
then `ANALYZE`s. Expect ~3–5 minutes on an SSD.

## M3 — Indexes + materialized view + benchmarks

After `load_bars.py` finishes:

```bash
MSYS_NO_PATHCONV=1 docker exec -i equitydb-pg psql -U equity -d equitydb -f /sql/03_indexes.sql
MSYS_NO_PATHCONV=1 docker exec -i equitydb-pg psql -U equity -d equitydb -f /sql/05_matviews.sql
.venv/Scripts/python benchmarks/run_bench.py --target pg --label baseline
```

(`MSYS_NO_PATHCONV=1` is only needed under Git Bash on Windows, which
otherwise rewrites `/sql/...` into a Windows path.)

- BRIN(ts) on every partition (`sql/03_indexes.sql`)
- `bar_1d` daily matview with ET-correct RTH filter (`sql/05_matviews.sql`)
- `benchmarks/run_bench.py` records best-of-3 warm timings to
  `benchmarks/results.csv` and dumps `EXPLAIN (ANALYZE, BUFFERS)` plans
  to `benchmarks/plans/`.

## M4 — Streamlit demo

```bash
.venv/Scripts/streamlit run app/ui_streamlit.py
```

Five tabs:
- Ticker chart (Scenario 1 + 9b) — matview vs raw timing
- Top movers (Scenario 3) — window function over all 503 tickers
- Cross-section snapshot (Scenario 2) — partition pruning
- Rolling volatility (Scenario 6) — LAG + windowed SUM
- DB internals — live partition list, index list, `pg_stat_statements`

All controls use America/New_York; data range is clamped to 2023-01-01 →
2024-12-31.

## M5 — TimescaleDB A/B + I/O-pressure story

A second container `equitydb-ts` runs `timescale/timescaledb:latest-pg16`
on port 5434 with `bar_5m` promoted to a 1-month hypertable. Same schema,
same indexes, same queries, same matview — only the storage engine differs.

```bash
# apply TS-side schema (extensions, plain bar_5m, hypertable, BRIN)
for f in 00_extensions 01_schema 02_hypertable 03_indexes; do
  MSYS_NO_PATHCONV=1 docker exec -i equitydb-ts psql -U equity -d equitydb \
    -v ON_ERROR_STOP=1 -f /sql/ts/$f.sql
done
MSYS_NO_PATHCONV=1 docker exec -i equitydb-ts psql -U equity -d equitydb -f /sql/99_seed_exchanges.sql

# seed + load into TS
.venv/Scripts/python ingest/seed_meta.py     --target ts
.venv/Scripts/python ingest/seed_calendar.py --target ts
.venv/Scripts/python ingest/load_bars.py     --target ts

# matview + benchmark
MSYS_NO_PATHCONV=1 docker exec -i equitydb-ts psql -U equity -d equitydb -f /sql/05_matviews.sql
.venv/Scripts/python benchmarks/run_bench.py --target ts --label timescale
```

**I/O-pressure mode** — to get real `shared read=N` numbers instead of
always-warm `shared hit`, use the limited override:

```bash
bash scripts/mode.sh limited     # 256 MB mem_limit, 64 MB shared_buffers
.venv/Scripts/python benchmarks/run_bench.py       --target pg --label limited_pg
.venv/Scripts/python benchmarks/run_bench_cold.py  --target pg --label cold_limited_pg
bash scripts/mode.sh demo        # back to unlimited for the Streamlit demo
```

`run_bench_cold.py` `docker restart`s the container before every query so
each timing reflects first-touch I/O. Results go to `results_cold.csv`.

## Layout

```
sql/            DDL: extensions, schema, partitions, indexes, matviews, seed
sql/queries/    Benchmark queries (Scenarios 1, 2, 3, 6, 9, 9b, 10)
sql/ts/         TimescaleDB variants (hypertable instead of RANGE partitions)
ingest/         Python loaders (seed_meta, seed_calendar, load_bars)
app/            Streamlit UI + parametrised query helpers
benchmarks/     run_bench.py (warm), run_bench_cold.py (cold), results CSVs
scripts/        init_db.ps1 (M1 bring-up), mode.sh (demo ↔ limited)
docs/           optimization_notes.md — the report material
data/           Parquet inputs (gitignored)
```

## Known limitations

- `cross_section` returns `NULL` for the 1-day return on Mondays, since
  `asof - INTERVAL '1 day'` lands on Sunday (no data). A
  calendar-aware "previous session" lookup is future work.
- The BRIN index is kept but unused by the planner on our workload —
  this is deliberate, discussed as a teaching moment in
  `docs/optimization_notes.md` §3.
