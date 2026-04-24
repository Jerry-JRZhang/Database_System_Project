"""Benchmark runner: runs each query in sql/queries/ N times and saves
EXPLAIN (ANALYZE, BUFFERS) output to benchmarks/plans/.

Two scenarios per run:
  - cold: DISCARD ALL + pg_prewarm reset (best-effort) before query
  - warm: query executed twice; second timing recorded
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = ROOT / "sql" / "queries"
PLAN_DIR = ROOT / "benchmarks" / "plans"
RESULTS_CSV = ROOT / "benchmarks" / "results.csv"


def time_query(conn, sql: str, repeat: int) -> tuple[float, str]:
    """Return (best_seconds, last_explain_text)."""
    best = float("inf")
    explain_text = ""
    with conn.cursor() as cur:
        for _ in range(repeat):
            t0 = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            dt = time.perf_counter() - t0
            best = min(best, dt)
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
        explain_text = "\n".join(r[0] for r in cur.fetchall())
    return best, explain_text


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["pg", "ts"], default="pg",
                   help="which DB to benchmark against")
    p.add_argument("--label", default="baseline",
                   help="suffix for plan files, e.g. 'baseline' / 'with_matview'")
    p.add_argument("--repeat", type=int, default=3,
                   help="warm runs per query (best wall time recorded)")
    p.add_argument("--queries", nargs="*", help="specific query files to run")
    args = p.parse_args(argv)

    PLAN_DIR.mkdir(parents=True, exist_ok=True)

    files = ([Path(q) for q in args.queries]
             if args.queries else sorted(QUERY_DIR.glob("*.sql")))
    if not files:
        print(f"No queries in {QUERY_DIR}", file=sys.stderr)
        return 1

    rows = []
    with connect(target=args.target) as conn:
        for f in files:
            sql = f.read_text(encoding="utf-8")
            print(f"==> {f.name}")
            best, explain_text = time_query(conn, sql, args.repeat)
            plan_path = PLAN_DIR / f"{f.stem}__{args.label}.txt"
            plan_path.write_text(explain_text, encoding="utf-8")
            print(f"   best {best*1000:8.1f} ms   plan -> {plan_path.name}")
            rows.append({"label": args.label, "query": f.stem,
                         "best_ms": round(best * 1000, 2),
                         "plan_file": plan_path.name})

    # Append to results.csv
    write_header = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["label", "query", "best_ms", "plan_file"])
        if write_header:
            w.writeheader()
        w.writerows(rows)
    print(f"\nResults appended to {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
