# Presentation Transcript — ~10 minutes spoken

Target pace: **120 words per minute** (a comfortable non-native-speaker
pace with breathing room). Total script ≈ 1 200 words → about 10
minutes of speaking. Each section is timed from the start of the talk.

Conventions:
- `[Slide N]` — advance to the indicated slide at this moment.
- *`[stage direction]`* — things you *do*, not say.
- Numbers are written out (*twenty-two million*) so they read aloud
  cleanly.
- Sentences are kept short on purpose. Don't merge them.

---

## 0 · Cold open  `[0:00 – 0:30]`

`[Slide 1 — Title]`

> Good afternoon. This project is called **EquityDB**. It's a
> PostgreSQL sixteen database of US stock market data — twenty-two
> million five-minute bars, for every S&P five-hundred ticker, over
> two years.

`[Slide 2 — Cold open]`

> My goal today is to show how **physical layout** — partitions,
> primary-key order, materialized views — decides performance as much
> as the query plan. Every number I'll show you is backed by a
> `shared read` page count, not a guess.

---

## 1 · Schema walkthrough  `[0:30 – 2:30]`

`[Slide 3 — ER diagram]`

> Here's the ER diagram. Two groups. On the **left**, reference data:
> `sector`, `industry`, `ticker`. On the **right**, the fact side:
> `bar_5m` and its rollup `bar_1d`.

> The reference side is textbook third normal form. GICS — the
> classification standard — says every sub-industry belongs to exactly
> one sector. If I stored the sector name directly on the ticker
> table, I would carry a transitive dependency. A single sector
> rename would cost five hundred row updates. By splitting sector and
> industry into their own tables, a rename is one row.

`[Slide 4 — Physical design]`

> Now the fact table. `bar_5m` holds twenty-two million rows. Two
> decisions matter.

> **First**: it's partitioned by timestamp, monthly. Twenty-four
> children plus a default. Each partition is about a hundred and
> twenty megabytes. Every interesting query filters by time, so the
> planner can prune twenty-three partitions out of twenty-four.

> **Second**: the primary key is composite — `ticker_id` first,
> timestamp second. That order matters. The most common query is
> *"give me one ticker for a date range."* With `ticker_id` first,
> each partition is clustered by ticker, so a chart query reads a
> small contiguous slice of pages. I benchmarked the reverse order.
> It was three times slower.

> Below `bar_5m` is `bar_1d` — the daily rollup. It's a materialized
> view, not a base table. Formally redundant. The whole point of
> showing it is the optimization story I'll get to in a minute.

---

## 2 · Live demo  `[2:30 – 5:30]`

`[Slide 5 — DEMO; switch to Streamlit]`

> Four quick scenarios.

*`[Tab 1: Ticker chart]`*

> **Ticker chart.** AAPL, one-month range. Two timing chips on the
> right. The left chip is the raw five-minute query. The right chip
> is the same answer from the matview. Same chart, **twenty-five
> times faster**.

*`[Flip daily toggle off, then the RTH toggle]`*

> If I turn the daily toggle off, the five-minute candles appear.
> Turn the trading-hours filter off, and the overnight gaps go away.

*`[Tab 2: Top movers]`*

> **Top movers.** Nine-thirty to ten AM, September eighteenth. A
> window function over all five hundred tickers in a thirty-minute
> slice. About seventeen milliseconds warm.

*`[Tab 3: Cross-section snapshot]`*

> **Cross-section.** Five hundred tickers at one timestamp. Looks
> like a full scan — but watch the plan.

*`[Toggle "Show EXPLAIN ANALYZE"]`*

> See the `Append` node and the twenty-four sub-plans inside. Only
> one ran. The rest say `(never executed)`. That's **runtime
> partition pruning** — the planner eliminates partitions at
> execution time.

*`[Tab 4: DB internals]`*

> Last tab — internals. The partition table. And
> `pg_stat_statements`, showing every query this session ran, with
> the page-read columns I mentioned.

---

## 3 · Optimization story  `[5:30 – 8:30]`

`[Slide 6 — Q9 vs Q9b]`

> The main result. The biggest speedup came from the daily rollup
> matview.

> Two plans, side by side. On the **left**, Q9: compute a daily series
> for AAPL directly from five-minute bars. Aggregation over twenty
> thousand rows. **Forty-one milliseconds.** On the **right**, Q9b:
> the same answer, from the matview, by index scan. **One point six
> milliseconds.** Same output. Twenty-five times faster.

> The cost is a one-line refresh at end of day. For a historical
> dataset, that's a batch job.

`[Slide 7 — BRIN teaching moment]`

> Now the contrast. This is where my intuition failed.

> I added a BRIN index on the timestamp column. I expected a big win.
> BRIN is famous for time-series data. I measured with it on, with
> it off. **Same runtime.** Why?

> Because BRIN stores, for each page range, the minimum and maximum
> of the indexed column. That works when the table is sorted by that
> column. But my primary key is `(ticker_id, ts)`. So every eight-
> kilobyte page holds bars from the whole month, across many tickers.
> The min-timestamp is the first of the month. The max is the end of
> the month. Every page summary says *"the whole month."* Useless.

> The lesson: the right index depends on the physical layout, not
> just the column. BRIN would win if I sorted the table by timestamp
> — but then I'd lose the chart-query locality from the previous
> slide. The two choices are mutually exclusive here.

`[Slide 8 — I/O pressure; skip if running long]`

> And briefly, under memory pressure. Top-movers takes seventeen
> milliseconds normally. Drop `shared_buffers` to sixty-four
> megabytes, and the same query jumps to **nine hundred and thirty
> milliseconds**. That's where TimescaleDB starts to win.

---

## 4 · TimescaleDB comparison  `[8:30 – 9:30]`

`[Slide 9 — Timescale A/B]`

> One minute on TimescaleDB. I tested the same schema, same queries,
> same indexes, on a hypertable.

> Two results. **Warm, with plenty of RAM**, TimescaleDB loses or ties
> on five of seven queries. The per-chunk planning overhead costs
> more than it saves. But with **cold cache or tight memory**,
> TimescaleDB wins the top-movers query by **sixteen times cold**,
> and **one hundred and forty-five times** under tight memory.

> So the takeaway is not "TimescaleDB is faster." It's that
> TimescaleDB wins in a specific regime — streaming, compression,
> tight memory. For three gigabytes of data in RAM, vanilla Postgres
> with good partitioning is already fast enough.

---

## 5 · Limitations + close  `[9:30 – 10:15]`

`[Slide 10 — Limitations]`

> Four limitations. The universe is a static twenty twenty-four
> snapshot, so survivorship bias is built in. The grain is
> five-minute, not tick-level. The cross-section query returns NULL
> on Mondays — it doesn't know about holidays yet. And the BRIN
> index is kept as a teaching artefact, not because the planner uses
> it.

`[Slide 11 — Closing tagline]`

> To close:

> *"The cost model is counted in pages, not milliseconds. When you
> pick an index, a primary-key order, or a storage engine, you're
> picking a page-access pattern — everything else follows."*

`[Slide 12 — Questions]`

> Thank you. I'm happy to take questions.

---

## Cue-card answers for likely Q&A

Keep these in your head, not on a slide.

- **"Why not TimescaleDB from the start?"** — Because the teaching
  goal is *why* chunk-aware planning helps. Starting from vanilla
  Postgres lets me show the contrast.

- **"Why monthly partitions?"** — Weekly doubles the count with
  little gain. Daily pushes past seven hundred partitions and
  planning overhead shows up. Monthly is the sweet spot.

- **"Could you re-sort the table on `ts` and get BRIN back?"** — Yes,
  but at the cost of chart-query locality. It's a workload choice.

- **"How long does the matview refresh take?"** — About thirty-five
  seconds on the full dataset. Fits inside an end-of-day window.

- **"Why no corporate actions, watchlists, alerts?"** — Descoped. The
  ER diagram shows what I actually query, not what I planned in week
  one.

---

## Delivery notes

- **Speak slowly.** One hundred and twenty words per minute is
  comfortable. If you feel rushed, pause between sections.
- **Breathe at every `[Slide N]` marker.** Those are natural stops.
- **Don't read the slide.** The slide carries two or three words;
  your voice carries the sentence.
- **Number pronunciation.** "Twenty-five times" not "two-five X."
  "Forty-one milliseconds" not "forty-one ems." These are already
  written out above — just read them as written.
- **If you go long on the demo**, skip the cross-section EXPLAIN step
  and go straight to optimization.
- **If you go long overall**, cut slide 8. Slide 9 covers the same
  ground.
- **End clean.** After the closing tagline, say only *"Thank you. I'm
  happy to take questions."* Nothing else.
