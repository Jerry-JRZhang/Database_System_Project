"""Cold-cache benchmark runner — restarts the container before every query
so each timing reflects true first-touch I/O.

Outputs rows to benchmarks/results_cold.csv with columns:
    label,query,exec_ms,planning_ms,shared_hit,shared_read,plan_file

Use with limited-mode containers (scripts/mode.sh limited) for the I/O story.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = ROOT / "sql" / "queries"
PLAN_DIR = ROOT / "benchmarks" / "plans"
RESULTS_CSV = ROOT / "benchmarks" / "results_cold.csv"

CONTAINER = {"pg": "equitydb-pg", "ts": "equitydb-ts"}


def restart_and_wait(container: str, wait_s: int = 60) -> None:
    subprocess.run(["docker", "restart", container], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_s):
        r = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "equity", "-d", "equitydb"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"{container} did not become ready in {wait_s}s")


BUF_RE = re.compile(r"shared\s+hit=(\d+)(?:\s+read=(\d+))?")


def parse_plan(plan_text: str) -> tuple[float, float, int, int]:
    """Return (exec_ms, planning_ms, total_hit, total_read) from EXPLAIN output."""
    exec_ms = planning_ms = 0.0
    m = re.search(r"Execution Time:\s*([\d.]+)\s*ms", plan_text)
    if m:
        exec_ms = float(m.group(1))
    m = re.search(r"Planning Time:\s*([\d.]+)\s*ms", plan_text)
    if m:
        planning_ms = float(m.group(1))
    total_hit = total_read = 0
    for hit, read in BUF_RE.findall(plan_text):
        total_hit += int(hit)
        if read:
            total_read += int(read)
    return exec_ms, planning_ms, total_hit, total_read


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["pg", "ts"], default="pg")
    p.add_argument("--label", required=True,
                   help="e.g. 'cold_limited_pg' or 'cold_limited_ts'")
    p.add_argument("--queries", nargs="*", help="specific query files")
    args = p.parse_args(argv)

    PLAN_DIR.mkdir(parents=True, exist_ok=True)

    files = ([Path(q) for q in args.queries]
             if args.queries else sorted(QUERY_DIR.glob("*.sql")))
    if not files:
        print(f"No queries in {QUERY_DIR}", file=sys.stderr)
        return 1

    rows = []
    container = CONTAINER[args.target]
    for f in files:
        sql = f.read_text(encoding="utf-8").strip().rstrip(";")
        print(f"==> {f.name}")
        print(f"    restarting {container} ... ", end="", flush=True)
        restart_and_wait(container)
        print("ready")

        with connect(target=args.target) as conn, conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
            plan_text = "\n".join(r[0] for r in cur.fetchall())

        exec_ms, planning_ms, hit, read = parse_plan(plan_text)
        plan_path = PLAN_DIR / f"{f.stem}__{args.label}.txt"
        plan_path.write_text(plan_text, encoding="utf-8")
        print(f"    exec {exec_ms:7.1f} ms  plan {planning_ms:5.1f} ms  "
              f"pages hit={hit:>6}  read={read:>6}")

        rows.append({
            "label": args.label, "query": f.stem,
            "exec_ms": round(exec_ms, 2),
            "planning_ms": round(planning_ms, 2),
            "shared_hit": hit, "shared_read": read,
            "plan_file": plan_path.name,
        })

    write_header = not RESULTS_CSV.exists()
    with RESULTS_CSV.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "label", "query", "exec_ms", "planning_ms",
            "shared_hit", "shared_read", "plan_file",
        ])
        if write_header:
            w.writeheader()
        w.writerows(rows)
    print(f"\nResults appended to {RESULTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
