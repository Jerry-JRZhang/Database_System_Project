# Design Notes

Companion to `PROJECT_PLAN.md` and `docs/optimization_notes.md`. This
document explains *why* the schema and physical layout look the way they
do — the material a grader or reviewer will want alongside the ER
diagram.

## 1. Dataset at a glance

| Object                       | Count        | Notes |
|---|---:|---|
| 5-min bars (`bar_5m`)        | 22 275 335   | 2023-01-03 → 2024-12-31 |
| Tickers                      | 503          | Static S&P 500 snapshot |
| Daily rollup rows (`bar_1d`) | 248 599      | matview of RTH-only 5-min bars |
| Monthly RANGE partitions     | 24 + default | ≈ 110–134 MB each |
| Total `bar_5m` + indexes     | ≈ 3.5 GB     | across all 24 partitions |

The working set fits comfortably in RAM on the demo host but spills to
disk under `scripts/mode.sh limited` (`shared_buffers=64m`,
`mem_limit=256m`) — see optimization notes §5 for the I/O-pressure
contrast with TimescaleDB.

## 2. Entity–Relationship model

The ER diagram is scoped to the tables central to the teaching story:
the classification hierarchy plus the fact table and its rollup.
Cardinalities:

- `sector 1 ── ∞ industry 1 ── ∞ ticker` — GICS hierarchy; preserved as
  separate tables so `ticker` never repeats a sector name.
- `ticker 1 ── ∞ bar_5m` — the main fact table (22.3 M rows).
- `ticker 1 ── ∞ bar_1d` — materialized daily rollup over `bar_5m`.

The running schema also keeps `exchange` and `trading_calendar` tables
(used by the ingest and seed scripts), but they're suppressed from the
ER diagram because no benchmarked query joins them — showing them would
add boxes without adding to the story.

The plan also sketched `corporate_action`, `app_user`, `watchlist`,
`watchlist_item`, and `alert` tables. Those user-scenarios (4, 7, 10)
were descoped during execution; the tables are dropped from the shipped
schema — every table in the diagram is populated and queried.

The ER diagram source is in `docs/schema.dbml` — paste into dbdiagram.io
to render.

## 3. Normalization walkthrough

### 3.1 First normal form (1NF)
Every column is atomic. No arrays-as-values, no CSV-in-a-string, no
JSONB columns in the shipped schema.

### 3.2 Second normal form (2NF)
The only composite key in the schema is `bar_5m (ticker_id, ts)`.
Every non-key column (`open, high, low, close, volume, vwap,
trade_count`) depends on the *whole* key — each describes one ticker
at one instant. No partial dependencies.

### 3.3 Third normal form (3NF)
The key 3NF decision is the **sector / industry split**. GICS guarantees
every sub-industry belongs to exactly one sector, so storing
`sector_name` on `ticker` alongside `industry_name` would carry a
transitive dependency:

  `ticker_id → industry_name → sector_name`

A single sector rename would then require updating every row for every
affected ticker. Extracting `sector` and `industry` into their own
tables eliminates the transitive dependency and reduces each rename to
one `UPDATE … WHERE sector_id = ?`.

### 3.4 Controlled denormalization
Two deliberate deviations from strict normalization, documented here so
they aren't mistaken for accidents:

1. **`bar_5m.vwap`** — volume-weighted average price is derivable from
   trade-level data (`Σ price·qty / Σ qty`), but trade tapes aren't in
   the dataset and recomputing per-bar VWAP from 5-min OHLCV would be
   approximate anyway. We store the source value so the daily rollup
   can volume-weight it without a second data pull. Documented as a
   design choice, not a 3NF violation, because the source of truth
   *is* the stored value.

2. **`bar_1d` materialized view** — a derived daily OHLCV rollup.
   Formally redundant with `bar_5m`, but materializing it is the whole
   optimization story of Q9 vs. Q9b (≈25× speedup). Refresh policy is
   documented in `sql/05_matviews.sql` and the optimization notes:
   `REFRESH MATERIALIZED VIEW CONCURRENTLY bar_1d;` at end of each
   trading day.

### 3.5 What the schema deliberately does *not* model
- **Corporate actions** — not modeled in the shipped schema. The plan
  called for split/dividend tables feeding an adjusted-price view
  (Scenario 4), but that was descoped to keep the focus on the storage
  / indexing / partitioning story. Historical bars stay immutable;
  adjustment would be a derived concern layered on top.
- **User/watchlist/alert application layer** — also descoped. JSONB
  predicate evaluation (Scenario 7) was interesting but tangential to
  the DB-systems teaching goals.
- **No tick/trade-level data.** The grain is 5-minute aggregates. This
  is a dataset limitation, explicitly called out.

## 4. Physical design

### 4.1 Why `bar_5m` is partitioned by `RANGE (ts)` monthly

Time-range filters appear in every interesting query, so monthly
granularity is the natural unit:

- **24 partitions over two years** — small enough that planner overhead
  for enumerating them is ~1–2 ms, large enough that static pruning
  usually eliminates 23 of the 24 on a typical query.
- **Each partition ≈ 110–134 MB** — one partition fits entirely in
  `effective_cache_size` even under `limited` mode.
- **Monthly boundaries match the query vocabulary** — users ask for
  "March 2024", and the matview's `session_date` groups stay within one
  partition.

Alternatives considered: *weekly* (too many partitions, marginal
benefit) and *quarterly* (pruning too coarse — a one-week query still
reads a quarter of data).

### 4.2 Why the PK is `(ticker_id, ts)` and not `(ts, ticker_id)`

The most common single query is "give me one ticker's bars for a date
range" (Scenario 1, the chart view). With `(ticker_id, ts)`:

- Each partition is physically clustered by `ticker_id` first.
- A single-ticker range scan reads a contiguous, narrow slice of heap
  pages: one per ~78 bars.

With `(ts, ticker_id)` the opposite would be true — the chart query
would touch pages spanning all 503 tickers for each timestamp. We
benchmarked both; chart queries were ~3× slower with the reversed PK.

The cost of this choice is borne by Q10 (cross-ticker time-range scan
with no ticker filter), which reads every heap page in the partition
because the physical clustering is by the wrong axis. That's the
teaching moment in §3 of the optimization notes: BRIN(ts) *could*
help if the layout matched, but it doesn't, so BRIN buys nothing on
this workload.

### 4.3 Index set and what each is for

| Index | Where | Purpose |
|---|---|---|
| `bar_5m_*_pkey`   | composite PK on each partition | Q1, Q6, Q9 (per-ticker scans) |
| `bar_5m_ts_brin_idx` | BRIN(ts) per partition | Kept as teaching artefact; unused by the planner on our layout |
| `bar_1d_pk`       | unique B-tree on `(ticker_id, session_date)` | Q9b matview lookup |
| `bar_1d_date_idx` | B-tree on `session_date` | Cross-ticker daily scans |

### 4.4 Constraints

- **Primary keys** everywhere; composite PK on `bar_5m`.
- **Foreign keys** on every cross-table reference.
- **CHECK** constraints on `bar_5m`:
  - `bar_5m_ohlc_chk` — `high ≥ {open, close, low}`, `low ≤ {open, close}`.
  - `bar_5m_vol_chk`  — `volume ≥ 0`.
  These are dropped during bulk load and re-validated with
  `NOT VALID` + `VALIDATE CONSTRAINT` for speed.
- **UNIQUE** on `sector.name`, `(sector_id, industry.name)`, and
  `ticker.symbol`.

### 4.5 Time zones

All timestamps are `TIMESTAMPTZ`. Storage is UTC (Postgres's internal
representation), and every query that cares about "regular trading
hours" converts via `ts AT TIME ZONE 'America/New_York'`. See the DST
bug write-up in optimization notes §1 — a naïve UTC-only filter silently
drops the last hour of every EST session.

## 5. Reference-data design decisions

- **Sectors + industries** use `SMALLSERIAL` PKs since the cardinalities
  are ≤ 11 and ≤ 75 respectively. `ticker_id` uses `SERIAL`.
- **Ticker.symbol** carries a `UNIQUE` constraint — the primary key is
  still the surrogate `ticker_id`, but all ingest lookups go through
  the symbol, and enforcing uniqueness there catches duplicates at load
  time instead of at query time.

## 6. Trade-offs and known limitations

| Decision | What it costs | Why we made it |
|---|---|---|
| Monthly partitions | ~2 ms planning overhead per query | 10–20× execution saving via static pruning |
| `(ticker_id, ts)` PK | Q10 (full time-range scan) reads whole partition | Chart queries are ~3× faster |
| BRIN kept but unused | ~few MB per partition; write-time overhead | Keeps the teaching contrast with Timescale chunk layout |
| `bar_1d` is a manual refresh | Not real-time | Matches an EOD batch ingest pattern; Timescale continuous aggregates would be the upgrade path |
| Static S&P 500 universe | Survivorship bias | Dataset limitation — any as-of-2024 constituent list has this |
| `cross_section` uses `asof − INTERVAL '1 day'` | Returns NULL on Mondays | Calendar-aware "previous session" lookup is future work |

## 7. What's in the repo vs. the plan

The plan (PROJECT_PLAN.md §6) sketches a wider layout than what
shipped. Cleanups:

- `sql/04_views.sql` — not needed; adjusted-price view (Scenario 4) was
  descoped.
- `app/api.py` — not shipped; the Streamlit UI talks to Postgres
  directly via `ingest/db.py`. FastAPI would add a layer without
  teaching anything new about databases.
- `notebooks/analysis.ipynb` — not shipped; the Streamlit "DB Internals"
  tab covers the plan-inspection use case live.
- `tests/test_queries.py` — not shipped; `benchmarks/run_bench.py` runs
  every query every time it's executed, which acts as a regression
  harness.
