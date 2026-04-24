# Slide Deck Guideline

Companion to `docs/presentation_outline.md`. The outline is what you
*say*; this document is what the audience *sees* behind you.

Target: 10 content slides + 2 bookend slides (title, closing) + 3–4
backup slides pinned after the closing slide. Aim for ~1 slide per
minute of content; never more than two minutes on a single slide.

---

## 1. Design principles

1. **One idea per slide.** If a slide needs two headings, split it.
2. **Talk the content, don't read it.** Every slide should have less
   text than you will say about it. Rule of thumb: ≤ 20 words on any
   non-quote slide.
3. **Body font ≥ 28 pt, headings ≥ 40 pt.** If text needs to be
   smaller to fit, cut the text, not the font.
4. **Code and plans are screenshots, not re-typed.** Use a monospace
   screenshot with a coloured rectangle over the 1–2 lines you want
   the audience to look at. Never paste raw `EXPLAIN` into a text box.
5. **Every number carries a unit and a comparator.** "41 ms" alone is
   noise; "41 ms → 1.6 ms (25×)" is a result.
6. **Consistent visual vocabulary.** Pick one accent colour for "the
   thing we changed" (e.g. bright orange for the optimized path) and
   use it everywhere — plan callouts, bar charts, timing chips.
7. **No builds / animations** except for side-by-side reveal of a
   before/after plan. Animations eat clock and break on alt-display.
8. **Dark background + light text** reads best on the demo laptop's
   projector; but whichever you pick, commit to one theme.

---

## 2. Deck structure

| # | Section | Slide purpose | Time |
|---|---|---|---:|
| 1  | Title            | Project name, your name, course, date                | — |
| 2  | Cold open        | The one-sentence tagline, no chart                   | 0:30 |
| 3  | Schema (ER)      | `docs/er_diagram.png` full-bleed                     | 1:00 |
| 4  | Physical design  | Partitioning + PK-order decision + matview           | 1:00 |
| 5  | Live demo        | Single-word "DEMO" slide (switch to Streamlit)       | 3:00 |
| 6  | Optimization #1  | Q9 vs Q9b plan-diff + timings                        | 1:30 |
| 7  | Optimization #2  | BRIN teaching moment (why it bought nothing)         | 1:00 |
| 8  | I/O pressure     | `mem_limit=256m` table from optimization notes §5    | 0:30 |
| 9  | Timescale A/B    | Two bar charts: warm-free-RAM vs cold/limited        | 1:00 |
| 10 | Limitations      | Four bullets: survivorship, 5-min grain, etc.        | 0:30 |
| 11 | Closing          | The "cost model is counted in pages" tagline         | 0:20 |
| 12 | Questions        | "Questions?" + your contact + repo URL               | — |
| B1 | Backup: dataset  | Row counts, storage, partition sizes                 | Q&A |
| B2 | Backup: plans    | Full Q9 / Q9b / Q10 `EXPLAIN ANALYZE` screenshots    | Q&A |
| B3 | Backup: schema   | `sql/01_schema.sql` screenshot for deeper Q&A        | Q&A |

**Total content runtime: 10:20**. Keeps 1:40 of slack inside the 12-min
ceiling from the timing budget.

---

## 3. Slide-by-slide specs

### Slide 1 — Title
- **Title**: *EquityDB — Physical Design and Query Optimization on a
  22 M-row OHLCV Workload*
- **Sub**: Your name · course · date
- Small logo / course branding if the course requires it. No tagline
  here — that's slide 2's job.

### Slide 2 — Cold open
- Pure text slide, left-aligned, 44 pt:
  > 22.3 million 5-minute bars.
  > 503 S&P 500 tickers.
  > Postgres 16.
  > Every decision backed by `shared read = N`.
- No chart. Let the claim land before the schema appears.

### Slide 3 — Schema (ER)
- Full-bleed `docs/er_diagram.png`.
- Overlay two labels: *Reference data* (left) and *Market data* (right).
- Bottom-right footnote, 16 pt: *"Diagram scope: benchmarked tables
  only. `exchange` and `trading_calendar` live in the running schema
  but aren't on the critical-query path."* (There for a close reader,
  not to be narrated.)

### Slide 4 — Physical design
Three bullets, one visual:
- **RANGE partition by month** — 24 children + default.
- **Composite PK `(ticker_id, ts)`** — clusters a ticker's bars on
  contiguous heap pages. *(Reversed PK → 3× slower on chart queries.)*
- **`bar_1d` materialized view** — EOD rollup over RTH-filtered 5-min.

Visual: a tiny diagram of one partition with rows grouped by ticker
colour, contrasted with the `(ts, ticker_id)` alternative where rows
interleave. Hand-drawn in PowerPoint shapes is fine.

### Slide 5 — DEMO
- Single word "**DEMO**" at 120 pt, centre.
- Small line at the bottom: *"Streamlit · localhost:8501"*.
- Switch to the Streamlit window. Come back to this slide after the
  demo to re-anchor before moving to the optimization story.

### Slide 6 — Optimization #1: Q9 vs Q9b
Two-column layout:

| Left column (Q9 baseline)              | Right column (Q9b matview)          |
|---|---|
| `09_daily_rollup__baseline.txt` snippet | `09b_daily_rollup_matview__baseline.txt` snippet |
| `Aggregate` over ~20 000 5-min bars     | `Index Scan` on `bar_1d_pk` — 252 rows |
| **41 ms** (warm)                        | **1.6 ms** (warm) — **25×**         |

Use callout rectangles to highlight the plan node that differs.

### Slide 7 — Optimization #2: BRIN teaching moment
- Title: *"When a textbook-correct index buys nothing"*
- Two-column: plan with BRIN on / BRIN off — both same runtime.
- Key bullet (38 pt):
  > PK is `(ticker_id, ts)`. Each 8 KB page has bars from the whole
  > month, scattered across tickers. BRIN's `(min_ts, max_ts)` per
  > page range equals "the whole month" on every page.
- Takeaway line in accent colour:
  *"The right index depends on the physical layout, not just the columns."*

### Slide 8 — I/O pressure (optional; skip when time is tight)
Small table, 3 columns × 3 rows:

| Query                 | Vanilla PG, `mem_limit=256m` | TimescaleDB, same limits |
|---|---:|---:|
| Q3 (top movers, warm) |  930 ms | 6 ms  |
| Q3 (cold)             | 1.5 s   | 90 ms |
| Q1 (chart scan)       |   80 ms | 70 ms |

Punchline below the table: *"Chunk-aware planning pays off exactly
when I/O is the bottleneck."*

### Slide 9 — Timescale A/B summary
Two side-by-side bar charts (generate from `benchmarks/results.csv`):
- **Warm, plenty of RAM** — bars clustered around parity; TS loses or
  ties 5 of 7.
- **Cold or memory-pressured** — TS wins Q3 by 16× (cold) and 145×
  (warm-limited).

Closing line: *"TimescaleDB isn't a drop-in speedup. It's a product
optimized for streaming, compression, and tight-memory analytics."*

### Slide 10 — Limitations
Four bullets, one per line:
- Static 2024 S&P 500 universe → survivorship bias.
- 5-min grain, not tick-level.
- `cross_section` returns NULL on Mondays (no calendar-aware fallback).
- BRIN retained as a teaching artefact, not because the planner uses it.

Below, one line: *"Next step: calendar-aware previous-session lookup +
continuous aggregates for streaming ingest."*

### Slide 11 — Closing
Single quote, 44 pt, centre:

> "The cost model is counted in pages, not milliseconds.
> When you pick an index, a PK order, or a storage engine,
> you're picking a page-access pattern — everything else follows."

No other decoration. Hold for 2–3 seconds before advancing.

### Slide 12 — Questions
- *"Questions?"* at 80 pt.
- Beneath: your name, email, and the repo URL in small monospace.
- Optional QR code to the repo.

### Backup slides (B1–B3)
Pinned after slide 12. Not shown unless a question calls for one.

- **B1 — Dataset specs** — the table from `design_notes.md §1`.
- **B2 — Plans gallery** — one screenshot per relevant plan file.
  Label each with file name + headline timing.
- **B3 — Schema source** — screenshot of `sql/01_schema.sql` so you
  can point at a specific constraint or FK when asked.

---

## 4. Visual assets checklist

Collect these into `docs/slide_assets/` before you start building:

| Asset | Source |
|---|---|
| `er_diagram.png`                     | dbdiagram.io export of `docs/schema.dbml` |
| `plan_q9.png`, `plan_q9b.png`        | Screenshot `benchmarks/plans/09_*` |
| `plan_brin_on.png`, `plan_brin_off.png` | Screenshot `benchmarks/plans/10_brin_friendly_scan__*` |
| `chart_warm.png`, `chart_cold.png`   | Bar charts from `benchmarks/results.csv` (matplotlib) |
| `streamlit_demo.png`                 | Fallback screenshot in case the demo crashes |

---

## 5. Build order

Build the deck in this order — it converges on a working talk fastest:

1. Title + closing slides first (commits the tagline).
2. ER diagram slide (longest single asset to produce).
3. Optimization slides 6–7 (they are the graded centrepiece).
4. Cold open, physical-design, limitations.
5. Timescale + I/O slides.
6. Demo / Questions / Backup.

Between steps 3 and 4, do a full 10-minute dry-run using just the
existing slides. If a slide is hard to talk to, redesign it; don't paper
over with filler text.

---

## 6. Fail-safes inside the deck

- **Streamlit crashes**: skip slide 5, go straight to slide 6. Slide 6
  carries enough content to make the optimization point without the app.
- **Projector drops to 1024×768**: the 28 pt minimum ensures legibility
  at that resolution. Verify by previewing once in 4:3.
- **Time overrun before slide 8**: skip slide 8 (I/O pressure table) —
  slide 9 references cold / limited numbers already.
- **No questions**: use slide 11 to invite one: "Happy to talk about
  the BRIN layout result or the Timescale comparison in more depth."
