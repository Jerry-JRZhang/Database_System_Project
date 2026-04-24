# Database Systems Final Project — Plan

## 1. Project Overview

**Title (working):** *EquityDB — A 5-Minute OHLCV Analytics Platform for US Equities*

A PostgreSQL-backed analytics system over 5-minute OHLCV bars for ~500 US tickers across 2023–2024 (~20–25M rows). Python is used for ingestion, automation, benchmarking, and visualization. The project demonstrates schema design, indexing strategies, query processing, query optimization, normalization, and selected advanced DB topics (partitioning, materialized views, window queries, optional time-series extensions).

---

## 2. Mapping Course Topics → Project Deliverables

| Course Topic | Where It Appears in the Project |
|---|---|
| Data Models / ER | ER diagram for tickers, exchanges, sectors, bars, corporate actions, watchlists, users, alerts |
| Relational Algebra / Calculus | Documented algebraic expressions for the headline queries (selection, projection, joins, aggregation, window) |
| SQL | DDL, complex analytic SQL: window functions, CTEs, lateral joins, gap-fill, rollups |
| Application Programming | Python ingestion service + Streamlit/Flask demo UI + CLI |
| Storage & Indexes | Heap layout, B-tree vs. BRIN vs. composite indexes; partitioning by time |
| Query Processing 1 & 2 | `EXPLAIN (ANALYZE, BUFFERS)` walkthroughs of seq scans, index scans, hash/merge joins, hash agg, sort |
| Query Optimization | Compare plans before/after indexes, partitioning, statistics; algebraic equivalences (predicate pushdown, join reorder) |
| Schema Refinement / Normal Forms | 1NF→3NF/BCNF justification; controlled denormalization for the bar table with rationale |
| Advanced Concepts | Partitioning, materialized views with incremental refresh, optional `TimescaleDB` hypertables, basic concurrency (MVCC), window analytics |

---

## 3. Database Design (Draft)

### 3.1 Entities

- **Exchange** (exchange_id PK, code UNIQUE, name, country, tz)
- **Sector** (sector_id PK, name UNIQUE)
- **Industry** (industry_id PK, sector_id FK, name)
- **Ticker** (ticker_id PK, symbol UNIQUE, exchange_id FK, industry_id FK, name, is_active, listed_date, delisted_date)
- **Bar5m** (ticker_id FK, ts TIMESTAMPTZ, open, high, low, close, volume, vwap, trade_count) — composite PK `(ticker_id, ts)`
- **CorporateAction** (action_id PK, ticker_id FK, ex_date, type {SPLIT, DIVIDEND}, ratio, cash_amount)
- **TradingCalendar** (exchange_id FK, session_date, open_ts, close_ts) — used for gap detection
- **AppUser** (user_id PK, username UNIQUE, created_at)
- **Watchlist** (watchlist_id PK, user_id FK, name)
- **WatchlistItem** (watchlist_id FK, ticker_id FK) — PK pair
- **Alert** (alert_id PK, user_id FK, ticker_id FK, predicate JSONB, created_at, last_fired_ts)

### 3.2 Normalization Notes
- Bar5m intentionally stores `vwap` (derivable from trades) for analytic speed; documented as controlled denormalization.
- Sector/Industry split into separate tables to preserve 3NF (industry → sector is a transitive dependency removed from `Ticker`).
- Corporate actions kept separate so historical bars remain immutable; an *adjusted close* view is derived.

### 3.3 Physical Design
- `bar_5m` partitioned by `RANGE (ts)` monthly (24 partitions for 2023–2024).
- Composite PK `(ticker_id, ts)` → clustered access for per-ticker scans.
- BRIN index on `ts` for cross-ticker time-range scans.
- B-tree on `ticker.symbol`; partial indexes on `ticker.is_active`.

---

## 4. User Scenarios (Challenging by Design)

Each scenario doubles as a SQL/optimization showcase.

1. **Single-ticker chart load** — fetch all bars for `AAPL` for a date range; return as candle data (uses partition pruning + composite PK).
2. **Cross-section snapshot** — for a single timestamp, return last close + 1-day return for all 500 tickers (lateral join + window function).
3. **Top movers** — top-N tickers by intraday return for arbitrary `[t0, t1]` (CTE + window + ranking; benchmarked with vs. without supporting index).
4. **Adjusted price view** — close adjusted for splits/dividends using running product of corporate actions (recursive CTE or window product).
5. **Gap-filled returns** — handle missing 5-min bars vs. trading calendar (anti-join on `TradingCalendar`).
6. **Volatility scan** — rolling 20-bar realized volatility per ticker; filter > threshold (window functions, large hash agg).
7. **Watchlist alert evaluation** — evaluate JSONB-based alert predicates over a recent window (showcase indexing on JSONB).
8. **Bulk re-ingestion** — idempotent upsert of a corrected day's bars (`INSERT ... ON CONFLICT`, transactional).
9. **Materialized daily summary** — daily OHLCV per ticker rolled up from 5-min bars; refreshed nightly; used by Scenario 3 to demonstrate optimization gains.
10. **Concurrent reader/writer** — show MVCC: long analytical query runs while ingestion appends new bars without blocking.

---

## 5. Database Concepts to Demonstrate

- **Indexing trade-offs**: B-tree vs. BRIN on `ts`; covering index for top-movers query.
- **Partitioning**: monthly range partitions; pruning visible in `EXPLAIN`.
- **Query optimization**: side-by-side plans (before/after stats, before/after index, before/after partition).
- **Algebraic equivalences**: rewrite a join+filter query three ways; show planner converges; one rewrite that defeats it.
- **Materialized views**: daily rollup with incremental refresh strategy.
- **Window functions & CTEs**: returns, ranks, rolling stats.
- **Transactions & isolation**: demo `READ COMMITTED` vs. `REPEATABLE READ` with concurrent ingestion.
- **Constraints**: PK, FK, CHECK (`high >= low`, `volume >= 0`), UNIQUE.
- **Schema refinement**: walk through normalization decisions in the report.

---

## 6. Tech Stack & Repo Layout

```
EquityDB/
├── README.md
├── PROJECT_PLAN.md           ← this file
├── docker-compose.yml        ← Postgres 16 (+ optional TimescaleDB)
├── .env.example
├── sql/
│   ├── 00_extensions.sql
│   ├── 01_schema.sql
│   ├── 02_partitions.sql
│   ├── 03_indexes.sql
│   ├── 04_views.sql
│   ├── 05_matviews.sql
│   └── 99_seed_meta.sql      ← exchanges, sectors, calendar
├── ingest/
│   ├── fetch_bars.py         ← downloader (e.g., Polygon/Alpaca/yfinance fallback)
│   ├── load_bars.py          ← COPY-based bulk loader
│   ├── corp_actions.py
│   └── universe.py           ← S&P 500 + adds
├── app/
│   ├── api.py                ← FastAPI (or Flask) endpoints for the demo
│   ├── ui_streamlit.py       ← interactive demo
│   └── queries.py            ← parametrized SQL
├── benchmarks/
│   ├── run_bench.py          ← runs scenarios w/ EXPLAIN ANALYZE
│   ├── plans/                ← saved EXPLAIN outputs
│   └── results.csv
├── notebooks/
│   └── analysis.ipynb        ← plots for the report
├── docs/
│   ├── er_diagram.png
│   ├── schema.png
│   └── design_notes.md
└── tests/
    └── test_queries.py
```

---

## 7. Milestones (Pre-Presentation)

> **Presentation date constraint:** schedule TBD on the 24th. Plan assumes ≥10 working days from today (2026-04-23). Compress as needed.

**M1 — Foundations (Day 1–2)**
- Stand up Postgres 16 in Docker; create roles, DB, extensions.
- Commit `01_schema.sql` with all tables + constraints.
- Seed exchanges, sectors, trading calendar.

**M2 — Ingestion (Day 3–5)**
- Inspect parquet schema; map columns → `bar_5m`.
- Load `constituents.csv` → `sector`, `industry`, `ticker` (parse GICS sector/sub-industry, dedupe).
- Stream parquet → Postgres via `COPY FROM STDIN (FORMAT BINARY)` using `pyarrow` batches into a staging table, then `INSERT ... ON CONFLICT` into partitioned `bar_5m`.
- Validate row counts vs. trading calendar; record ingestion time per partition.

**M3 — Indexes & Partitions (Day 6)**
- Create monthly partitions; add BRIN + composite indexes.
- `ANALYZE` and capture baseline plans.

**M4 — Application Layer (Day 7–8)**
- FastAPI endpoints for the 10 user scenarios.
- Streamlit demo: ticker chart, top movers, watchlist + alerts, ingestion status.

**M5 — Benchmarks & Optimization (Day 9)**
- Run each scenario with cold/warm cache; capture `EXPLAIN (ANALYZE, BUFFERS)`.
- Produce before/after comparison for at least 3 optimizations (index added, MV used, partition prune).

**M6 — Polish (Day 10)**
- ER diagram (dbdiagram.io or graphviz), schema doc, README run instructions.
- Dry-run the 10–12 minute presentation.

---

## 8. Demo Script (10–12 min)

1. **(1 min)** Problem statement + dataset scale.
2. **(2 min)** ER diagram + schema walkthrough; normalization choices.
3. **(3 min)** Live demo: ticker chart → top movers → alert firing → concurrent ingest.
4. **(3 min)** Optimization story: pick *Top Movers* query; show plan w/o index → add index → add MV; show timing chart.
5. **(1 min)** Highlights (Timescale comparison if done; JSONB alerts).
6. **(1 min)** Limitations & future work.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Free data source rate limits | Pre-download once into local parquet; loader reads from disk |
| 25M-row ingest is slow | Use `COPY` + unlogged staging table + parallel workers per partition |
| Plans differ between dev and demo machine | Pin Postgres version; ship Docker image; pre-warm cache for demo |
| Time crunch | Scenarios 1–6 are MVP; 7–10 are stretch |

---

## 10. Report Outline (Due 1 Week After Presentation)

1. Introduction & motivation
2. Database design — ERD, schema, normalization rationale
3. Physical design — partitioning, indexing
4. Features & user scenarios
5. Database concepts applied (with `EXPLAIN` excerpts)
6. Benchmarks — methodology, results, plots
7. Limitations
8. Future extensions (real-time websocket ingest, options data, columnar store comparison, ML feature store)
9. References

---

## 11. Confirmed Decisions

- **Data source**: ✅ Pre-downloaded Parquet in `data/` (two yearly files for 2023 and 2024). No live API needed.
- **Universe**: ✅ Static S&P 500 from `constituents.csv` (503 symbols). Survivorship-bias is acknowledged as a documented limitation.
- **TimescaleDB**: ✅ Included as an optional comparison track in M5 (hypertable vs. plain partitioned table on the same workload).
- **UI**: ✅ Streamlit.

## 12. Inputs On Disk

```
data/
  bars_2023-01-01_to_2024-01-01.parquet   (~435 MB)
  bars_2024-01-01_to_2025-01-01.parquet   (~451 MB)
constituents.csv                          (503 S&P 500 rows: Symbol, Security, GICS Sector, GICS Sub-Industry, HQ, Date added, CIK, Founded)
```

The parquet schema will be inspected as the first action of M2 to confirm column names/types and adjust the loader.

## 13. Environment Prerequisites

- Docker Desktop (for Postgres 16 + optional TimescaleDB image)
- Python 3.11+ — **not currently on PATH**; install before M2. We'll create a project venv at `.venv/` with `pyarrow`, `pandas`, `psycopg[binary]`, `streamlit`, `python-dotenv`, `pyyaml`.
- `psql` client (bundled with Postgres install or via `scoop install postgresql-client`).
