# Optimization Notes (for the report & presentation)

All numbers are **best of 3 warm runs**, captured by `benchmarks/run_bench.py`.
Full `EXPLAIN (ANALYZE, BUFFERS)` plans live in `benchmarks/plans/`.

## Headline numbers

| Query | Vanilla baseline | + BRIN | + matview | **TimescaleDB** |
|---|---:|---:|---:|---:|
| Q1 single-ticker chart (1 month, AAPL)          | 9.7 ms  | 8.9 ms  | –          | 11.4 ms |
| Q2 cross-section snapshot (503 tickers @ one ts)| 7.2 ms  | 6.7 ms  | –          | 11.5 ms |
| Q3 top movers (30-min window, 503 tickers)      | 17.2 ms | 16.6 ms | –          | **6.9 ms** |
| Q6 rolling volatility (NVDA, 1 day)             | 0.93 ms | 0.92 ms | –          | 2.5 ms  |
| Q9 daily rollup (AAPL, all 2024)                | 41.3 ms | –       | –          | 43.7 ms |
| **Q9b matview rewrite** (same answer as Q9)     | –       | –       | **1.6 ms** | 1.9 ms  |
| Q10 cross-ticker time-range scan (1 week)       | 53.3 ms | 51.8 ms | –          | 78.4 ms |

Vanilla = Postgres 16 with 24 monthly RANGE partitions.
TimescaleDB = same schema, same queries, same PK/BRIN — only difference is
`bar_5m` is a hypertable with 1-month chunks.

## Four lessons

### 1. Materialized view turned a 41 ms query into a 1.6 ms one (~25×)

Q9 computes daily OHLCV from 5-min bars at query time: ~20 k rows aggregated
into 252 day groups, using `array_agg(... ORDER BY ts)` to get the day's
first open and last close in a single pass. Q9b answers the same question
from `bar_1d` (a precomputed daily rollup with a unique index on
`(ticker_id, session_date)`), and the plan collapses to a single index scan.

**Trade-off**: `bar_1d` must be refreshed when underlying bars change. For
an OHLCV history this is a once-a-day cost; run
`REFRESH MATERIALIZED VIEW CONCURRENTLY bar_1d;` after each end-of-day load.

**Correctness note**: both Q9 and `bar_1d` filter the regular session using
`(ts AT TIME ZONE 'America/New_York')::time BETWEEN '09:30' AND '16:00'`.
A naïve UTC filter (`ts::time BETWEEN '13:30' AND '20:00'`) looks right in
summer but silently drops the last hour of every EST session and picks up
an hour of pre-market — a DST bug that would quietly corrupt daily closes
for ~4 months of every year.

### 2. Partition pruning cut work by 24× without any code change

The parent table `bar_5m` has 24 monthly partitions. EXPLAIN plans for Q1 and
Q2 show two flavours of pruning:

- **Static pruning** (Q1, March 2024 query): the planner sees `ts >= '2024-03-01'
  AND ts < '2024-04-01'` and emits a scan of only `bar_5m_2024_03`.
- **Runtime pruning** (Q2, single timestamp from a CTE): the planner can't
  resolve the partition at plan time, so the plan contains an `Append` over
  all 25 partitions — but the EXPLAIN ANALYZE output marks 24 of them
  `(never executed)`. The cost is paid only in planning time (~2 ms).

This is the classic partitioning trade-off: planning-time overhead in exchange
for execution-time savings. For workloads with mostly time-bounded queries,
the trade is overwhelmingly positive.

### 3. BRIN(ts) did NOT help — and that's a deliberate teaching moment

We added a BRIN index on `ts` expecting a big win for cross-ticker time-range
scans. Q10's plan revealed why it didn't:

```
Index Scan using bar_5m_2024_01_pkey on bar_5m_2024_01
  Index Cond: ((ts >= '2024-01-02 ...') AND (ts < '2024-01-06 ...'))
```

The planner ignored the BRIN and used the composite **primary key** index
instead. The reason is physical: our PK is `(ticker_id, ts)`, so within each
partition rows are physically clustered by `ticker_id` first, then by `ts`.
Every 8 KB heap page therefore contains rows from many different timestamps
spanning roughly the whole month. BRIN summaries store
`(min_ts, max_ts)` per page range — and for our layout that range is "the
whole month" on every page, so BRIN can't skip anything.

Three real fixes (any one would make BRIN useful):

1. `CLUSTER bar_5m_2024_01 USING bar_5m_ts_brin_idx;` — physically reorder
   each partition by `ts`. Trade-off: per-ticker reads become non-sequential.
2. Use a `(ts, ticker_id)`-first PK. Trade-off: per-ticker chart loads slow
   down — and that's the most common query.
3. Switch the storage engine to **TimescaleDB hypertables**, which chunk by
   time automatically and keep each chunk physically time-clustered.
   This is what M5 evaluates.

The takeaway for the presentation: **the right index depends on the physical
layout, not just the column types.** Adding an index that the planner ignores
is a cost (write amplification, vacuum work) with no benefit.

### 4. TimescaleDB was NOT a speed win — and that's also deliberate

We added a second container (`equitydb-ts`, port 5434) running
`timescale/timescaledb:latest-pg16`, promoted `bar_5m` to a hypertable with
1-month chunks (matching the vanilla RANGE partitions 1:1), re-loaded all
22.3 M rows, and re-ran every query. The schema, PK, BRIN index, and
matview are identical; the *only* difference is the storage engine.

Result: Timescale was slower on 5 of 7 queries, roughly tied on Q9, and
faster only on Q3 (17.2 → 6.9 ms). The Q3 win comes from a different plan
shape: Timescale parallelised the window-function pass across chunks, while
vanilla did it in a single worker. Most other queries paid a visible
per-chunk cost.

Why the regressions? Compare the Q2 plans:

- **Vanilla** (`plans/02_cross_section_snapshot__baseline.txt`): the `Append`
  over 25 partitions contains one real scan and 24 "(never executed)"
  branches — runtime pruning eliminated them.
- **Timescale** (`plans/02_cross_section_snapshot__timescale.txt`): the
  `Append` over 25 chunks contains 25 real index scans. Each finds zero
  rows in 3 µs, but the constant cost adds up — and planning time went
  from 0.1 ms to 4.3 ms because Timescale's chunk exclusion code runs
  per query regardless of whether it fires.

For Q10, Timescale's per-chunk index is `(ts DESC)`, which is good for
raw time-range scans but not for our composite-PK lookups. The vanilla
children inherited `(ticker_id, ts)` from the parent PK, which is more
selective for single-ticker queries.

Three times Timescale would actually pay off — none apply to us:

1. **Continuous aggregates** — incrementally-maintained rollups that
   auto-update as new bars arrive. Our matview is a one-shot refresh; for
   a streaming ingest it would need a cron job. Trade-off: migration.
2. **Native compression** — per-chunk columnar compression typically hits
   10–15× on OHLCV data. Our 3 GB dataset already fits in shared_buffers,
   so disk isn't the bottleneck. For a 10-year dataset it would be.
3. **Retention policies** — one-line `add_retention_policy` vs. a cron job
   that drops old partitions. Cosmetic for us, operational for production.

The takeaway for the presentation: **features that sound like "free
performance" often aren't.** Timescale is a *product* optimised for
streaming, compression, and ops — not a faster query engine. A well-tuned
vanilla Postgres with hand-rolled partitioning beats it on a cold-storage
analytical workload that already fits in RAM. Use it for what it's
actually good at, not as a drop-in speedup.

### 5. Under real I/O pressure, the story changes — and Timescale finally wins

Everything above was measured with `mem_limit` unset (~22 GB host RAM
available to the container). Every EXPLAIN plan shows `Buffers: shared
hit=N` with `read=0` — we were timing **CPU cost of plan shape**, not
disk I/O. That's a legitimate measurement of the planner, but it ducks
the whole point of a DB-systems course: *the cost model is counted in
pages, not milliseconds.*

So we added a second mode. `docker-compose.limited.yml` caps both
containers at **256 MB RAM** with `shared_buffers=64 MB` — roughly
**6 %** of the ~4 GB working set fits in RAM. `Buffers: shared read=N`
finally shows up in every plan. For a fair cold-cache measurement we
use `benchmarks/run_bench_cold.py`, which `docker restart`s the
container before every query (wiping shared_buffers *and* the
container's OS page cache) and records the first-run
`EXPLAIN (ANALYZE, BUFFERS)`.

#### Cold-cache results (limited mode, one run per query)

| Query | vanilla exec | vanilla reads | timescale exec | timescale reads | Winner |
|---|---:|---:|---:|---:|---|
| Q1 single-ticker 1-mo | 17 ms | 206 | 17 ms | 261 | tie |
| Q2 cross-section | 344 ms | 9 337 | 336 ms | 4 940 | ts (fewer reads) |
| **Q3 top movers (all tickers, 30 min)** | **1 825 ms** | **111 252** | **116 ms** | **4 813** | **ts 16×** |
| Q6 rolling volatility | 2 ms | 94 | 3 ms | 129 | tie |
| Q9 daily rollup (raw) | 220 ms | 2 574 | 251 ms | 2 912 | tie |
| **Q9b daily rollup (matview)** | **2 ms** | **80** | **2 ms** | **101** | **tie, both ~100×** |
| **Q10 cross-ticker week scan** | **1 460 ms** | **6 459** | **728 ms** | **5 751** | **ts 2×** |

Three things the cold-cache view teaches that warm-cache hides:

1. **The matview win is even bigger in pages than in ms.** Q9 → Q9b
   drops from 2 574 disk pages read to 80 — a **32× reduction in I/O**,
   and ~100× in wall time. Warm cache underreported this because once
   Q9's pages are resident, subsequent runs skip the I/O entirely.
2. **The BRIN failure becomes dramatic, not academic.** Q10 reads 6 459
   pages — that's 50 MB of 8 KB heap fetches from the 2024-01 partition
   because the (ticker_id, ts) clustering lets no page be excluded.
   CLUSTERing that partition by `ts` would drop it to <200 pages.
3. **Timescale's chunk exclusion is the real prize.** Q3 goes from
   111 252 reads (vanilla scans all ticker stripes inside the Sept-2024
   partition) to 4 813 (Timescale's per-chunk planner uses the
   `(ts DESC)` chunk index to bound the page window tightly). That's
   a **23× I/O reduction** from a storage-engine decision, not an
   index or query rewrite.

Lesson 4 said Timescale wasn't a speed win. **Lesson 5 is the
asterisk: it isn't a speed win *when disk isn't the bottleneck*.**
Flip that assumption by shrinking the cache and the chunk-aware planner
starts earning its per-query overhead many times over on scans that
span the time dimension without a ticker filter. This is the same
data, same queries, same indexes — only the memory budget changed.

#### Picking the limit — and why cold numbers barely move with it

Final setting: `mem_limit=256m`, `shared_buffers=64MB`. With this the
worst-case query (Q3, 111 k pages, ~900 MB touched) still finishes in
under 2 s on the Windows Docker Desktop VirtioFS bind-mount.

A subtle methodology point worth mentioning: we also tried
`mem_limit=1g` / `shared_buffers=256MB`, and the **cold-cache** numbers
were within noise of the tighter setting. That's not a bug — it's a
consequence of the measurement design. Because `run_bench_cold.py`
restarts the container before **every** query, shared_buffers and the
container's OS page cache both start empty every time. The first
`pread()` for a page goes to the bind-mount regardless of how large
the cache would have been.

But the **warm best-of-3** numbers (`results.csv`, `limited_pg` label)
do shift, dramatically, on queries whose working set exceeds
`shared_buffers`:

| Query | warm demo (pg) | warm limited (pg) | warm limited (ts) |
|---|---:|---:|---:|
| Q1 single ticker (1-mo) | 10 ms | 11 ms | 10 ms |
| Q2 cross-section | 7 ms | 8 ms | 11 ms |
| **Q3 top movers** | **17 ms** | **930 ms** | **6 ms** |
| Q6 volatility | 1 ms | 1.5 ms | 2 ms |
| Q9 raw rollup | 41 ms | 46 ms | 44 ms |
| Q9b matview | 1.6 ms | 1.2 ms | 2.1 ms |
| Q10 week scan | 53 ms | 63 ms | 68 ms |

Q3 is the cache-thrash case: it reads ~111 k pages per run, but our
64 MB shared_buffers holds only ~8 k. Even on the second and third
warm runs, pages keep evicting each other and being re-read. The
timescale column is the punchline — TS's chunk-aware plan reads
~4.8 k pages (fits in cache) and stays at 6 ms regardless of run
number. **Same data, same query, same RAM cap — 145× different
because of storage layout.** This is the clearest "storage engine
matters" result in the whole project.

So the right reading is:
- *Cold first-run I/O* is what `run_bench_cold.py` measures; it's
  dominated by working-set size vs. bind-mount throughput, not
  by the container's RAM cap.
- *Warm steady-state* is what `run_bench.py --target X --label limited_X`
  measures; it reveals which queries fit in cache and which thrash.
  This is where storage-layout wins like Timescale's chunk pruning
  show up most dramatically.

Operationally: `scripts/mode.sh demo` for live Streamlit sessions
(full RAM, snappy UI); `scripts/mode.sh limited` before running
`run_bench_cold.py`. Data is preserved across mode switches via the
bind-mount, so no reload is needed.

## Plan-reading tips for the demo

When walking through `EXPLAIN ANALYZE` live, point at:

- **`(never executed)`** branches in Q2's `Append` → runtime partition pruning.
- **`Index Cond:`** vs `Filter:` → things in `Index Cond` were resolved by the
  index; things in `Filter` were rechecked after the index returned candidates.
- **`Buffers: shared hit=N`** → cache effectiveness. Compare cold vs warm runs
  by restarting the container between them.
- **`Sort Method: quicksort Memory: ...kB`** vs `external merge` → tells you
  whether `work_mem` was sufficient.
