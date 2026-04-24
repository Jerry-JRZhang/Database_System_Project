# Presentation Outline — 10–12 minutes

Target: 10 minutes of content, 2 minutes of buffer before Q&A.

Supporting material on the demo laptop:
- Slide deck (see `docs/slides_guideline.md` for structure).
- ER diagram at `docs/er_diagram.png`.
- Streamlit app running on the "Ticker chart" tab with AAPL / March 2024
  already loaded.
- `benchmarks/results.csv` and `results_cold.csv` open in a side window.
- Pre-rendered plan files under `benchmarks/plans/`.
- A terminal at `E:/Database Project` for the live `psql` moments.

Before the talk:
- `bash scripts/mode.sh demo` so the UI is snappy.
- Verify Streamlit renders the AAPL chart in < 1 s.
- Confirm the slide deck is on the right display; dock the terminal on
  the left.

---

## 0 · Cold open (≤ 30 s)

> "22.3 million 5-minute bars for 503 S&P 500 tickers over 2023–2024,
> in Postgres 16. Every optimization decision is backed by a
> `shared read=N` number — not a vibe."

One sentence on motivation: financial time-series is a workload where
*physical layout* matters as much as the query plan, so it's a good
vehicle for teaching the distinction.

---

## 1 · Schema walkthrough — 2 min

Bring up `docs/er_diagram.png`. Two groups, left to right:

**Reference data** (`sector`, `industry`, `ticker`) — classic 3NF.
Point at `sector → industry → ticker` and say: *"If we collapsed these
into one table we'd carry the transitive dependency
`ticker → industry_name → sector_name`; splitting them into FKs means a
sector rename is one row, not 500."*

**Market data** — the fact table and its rollup.
- `bar_5m` is partitioned monthly. Composite PK is `(ticker_id, ts)`.
  *"Why that order? Chart queries hit one ticker at a time — putting
  `ticker_id` first clusters each partition so a month-long chart scan
  reads a contiguous slice of pages, not every page."*
- `bar_1d` is the daily rollup — a materialized view, not a base table.
  *"Formally redundant. The whole point is the optimization it enables."*

Q&A prep (say only if asked):
- *Why no `exchange` / `trading_calendar` boxes?* — *"They're in the
  running schema and driven by the ingest scripts, but no benchmarked
  query joins them, so I kept them off the diagram to keep the story
  focused on the fact table."*
- *Users / watchlists / alerts / corporate actions?* — *"Sketched in
  the original plan, descoped during execution. The ER diagram should
  reflect what's actually queried, not what I thought I'd build in
  week 1."*

---

## 2 · Live demo — 3 min

Drive the Streamlit tabs top-to-bottom; narrate what's happening.

1. **Ticker chart** (Q1 + Q9b). AAPL, default 1-month range. Point at
   the two timing chips: *"Left chip is the 5-min raw query hitting
   `bar_5m`. Right chip is the same answer served from the matview.
   25× speedup on the same question."* Flip the daily toggle off to
   reveal the intraday candles; flip RTH on/off briefly to show the
   overnight gaps disappear.
2. **Top movers** (Q3). Default: 2024-09-18, 09:30–10:00 ET, top 20.
   *"A window function over all 503 tickers in a 30-minute slice.
   Comes back in ~17 ms warm."*
3. **Cross-section snapshot** (Q2). *"503 tickers at a single
   timestamp — looks like it has to scan every partition, but the
   planner does **runtime partition pruning** and only one monthly
   partition is actually executed."* Toggle "Show EXPLAIN ANALYZE" and
   point at a `(never executed)` branch inside the `Append` node.
4. **DB internals.** Show the partition table — 24 monthlies + default.
   Scroll to `pg_stat_statements` to show exactly which queries this
   session ran.

---

## 3 · Optimization story — 3 min

This is the graded centrepiece. Pick **Q9 daily rollup vs. Q9b matview**
as the headline (bigger number than any index swap).

- Open `benchmarks/plans/09_daily_rollup__baseline.txt` and
  `09b_daily_rollup_matview__baseline.txt` side by side.
- Q9: aggregation over ~20 000 5-min bars → 252 day groups. 41 ms warm.
- Q9b: index scan on `bar_1d` → 252 rows already grouped. 1.6 ms warm.
- *"Same answer, ~25× speedup. The cost is an EOD `REFRESH MATERIALIZED
  VIEW CONCURRENTLY`. For an OHLCV history, that's a batch job."*

Then the BRIN teaching moment — open `10_brin_friendly_scan__brin_off.txt`
next to the `brin_on` version:

- *"I added a BRIN index on `ts` expecting a big win. It bought
  nothing. Why? My PK is `(ticker_id, ts)`, so every 8 KB page has
  bars from the whole month scattered across tickers. BRIN summaries
  store `(min_ts, max_ts)` per page range — and on this layout that
  range is 'the whole month' on every page. The right index depends
  on the physical layout, not just the columns."*

Optional if time: flash the I/O-pressure table from
`docs/optimization_notes.md` §5. *"Under `mem_limit=256m`, Q3 jumps
from 17 ms to 930 ms on vanilla Postgres and stays at 6 ms on
TimescaleDB — 145× gap from the same query on the same data, purely
because TS's chunk-aware planner fits in 64 MB of shared_buffers and
vanilla's doesn't."*

---

## 4 · TimescaleDB comparison — 1 min

*"I A/B'd this against a TimescaleDB hypertable — same schema, same
queries, same indexes, same matview. Two results worth sharing:"*

- **Warm, plenty of RAM**: TS loses or ties on 5 of 7 queries. Per-chunk
  planning overhead dominates when disk isn't the bottleneck.
- **Cold or memory-pressured**: TS wins Q3 by 16× on cold, 145× warm
  under tight limits. Chunk exclusion pays off exactly when I/O is the
  bottleneck.

*"Takeaway: TimescaleDB isn't a drop-in speedup. It's a product
optimized for streaming, compression, and memory-tight analytics —
not for 3 GB workloads that fit in RAM."*

---

## 5 · Limitations + closing — 0:30–1:00

Four limitations, one line each:
- Static 2024 S&P 500 universe → survivorship bias.
- 5-min grain, not tick-level.
- `cross_section` doesn't know about holidays — returns NULL on
  Mondays. A calendar-aware previous-session lookup is the obvious
  next step.
- BRIN retained as a teaching artefact rather than dropped.

Future extensions (mention only if time): real-time ingest with
continuous aggregates, adjusted-price view driven by corporate-action
tables, and an options-data schema layered on the same partitioning
pattern.

End on the optimization-notes tagline: *"The cost model is counted in
pages, not milliseconds. When you pick an index, a PK order, or a
storage engine, you're picking a page-access pattern — everything else
follows."*

---

## Timing budget

| Section              | Target | Hard ceiling |
|---|---:|---:|
| Cold open            |  0:30  |  0:45 |
| Schema walkthrough   |  2:00  |  2:30 |
| Live demo            |  3:00  |  3:45 |
| Optimization story   |  3:00  |  3:30 |
| Timescale comparison |  1:00  |  1:30 |
| Limitations + close  |  0:45  |  1:00 |
| **Total**            | **10:15** | **13:00** |

## Fail-safes

- **Streamlit dies on stage**: fall back to `psql` in the open terminal
  and walk through Q9 vs. Q9b directly. Plan files are pre-rendered
  under `benchmarks/plans/` so even `EXPLAIN` demo still works.
- **Postgres container dies**: `bash scripts/mode.sh demo` recreates
  both containers in under 10 s; data is on the bind-mount.
- **Time overrun in the demo**: skip the cross-section snapshot tab and
  go straight from top movers to the optimization story.
- **Timescale question in Q&A**: `bash scripts/mode.sh limited` →
  `scripts/mode.sh demo` lets me flip the story from "not a speedup"
  to "145× speedup" in one command.
