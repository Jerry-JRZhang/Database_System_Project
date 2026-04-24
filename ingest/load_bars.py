"""Bulk-load 5-minute OHLCV bars from parquet into the partitioned bar_5m table.

Approach:
  - Read the whole parquet file with pyarrow (≈1 GB of decoded data per file).
  - Vectorized symbol -> ticker_id mapping in pandas.
  - Stream the dataframe out as CSV bytes in big chunks via psycopg3
    `copy.write(bytes)` — this avoids the per-row Python overhead of
    `write_row` and is dramatically faster on Windows / WSL2.
  - Temporarily drop CHECK constraints during load and recreate after.

Examples:
    python ingest/load_bars.py                # truncate + full load (default)
    python ingest/load_bars.py --no-truncate
    python ingest/load_bars.py --chunk-rows 1000000
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

from db import connect

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

COLS = ["symbol", "timestamp", "open", "high", "low",
        "close", "volume", "vwap", "trade_count"]
TARGET_COLS = ["ticker_id", "ts", "open", "high", "low",
               "close", "volume", "vwap", "trade_count"]


def load_symbol_map(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, ticker_id FROM ticker")
        return {s: i for s, i in cur.fetchall()}


def drop_check_constraints(conn) -> list[tuple[str, str]]:
    """Drop CHECK constraints on bar_5m parent so children inherit the change.
    Returns the list of (name, definition) so we can recreate them."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'bar_5m'::regclass AND contype = 'c'
            """
        )
        constraints = cur.fetchall()
        for name, _ in constraints:
            cur.execute(f'ALTER TABLE bar_5m DROP CONSTRAINT "{name}"')
    return constraints


def restore_check_constraints(conn, constraints: list[tuple[str, str]]) -> None:
    with conn.cursor() as cur:
        for name, defn in constraints:
            # NOT VALID skips re-checking existing rows; we trust the parquet data.
            cur.execute(f'ALTER TABLE bar_5m ADD CONSTRAINT "{name}" {defn} NOT VALID')
            cur.execute(f'ALTER TABLE bar_5m VALIDATE CONSTRAINT "{name}"')


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["pg", "ts"], default="pg",
                   help="which DB to load into (pg=vanilla, ts=TimescaleDB)")
    p.add_argument("--truncate", dest="truncate", action="store_true", default=True)
    p.add_argument("--no-truncate", dest="truncate", action="store_false")
    p.add_argument("--chunk-rows", type=int, default=500_000,
                   help="rows per CSV chunk written to COPY (default 500k)")
    p.add_argument("--files", nargs="*", help="parquet files (default: data/*.parquet)")
    args = p.parse_args(argv)
    print(f"Loading into target={args.target}")

    files = [Path(f) for f in args.files] if args.files else sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        print(f"No parquet files in {DATA_DIR}", file=sys.stderr)
        return 1
    print("Files:", *[f.name for f in files], sep="\n  ")

    with connect(target=args.target, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit = OFF")
            cur.execute("SET maintenance_work_mem = '512MB'")
            cur.execute("SET work_mem = '128MB'")

        sym2id = load_symbol_map(conn)
        print(f"{len(sym2id)} symbols in ticker table")

        if args.truncate:
            print("TRUNCATE bar_5m ...")
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE bar_5m")
            conn.commit()

        print("Dropping CHECK constraints for the load ...")
        dropped = drop_check_constraints(conn)
        conn.commit()
        print(f"  dropped {len(dropped)} constraints")

        try:
            total_rows = 0
            total_skipped = 0
            t0 = time.time()

            for path in files:
                print(f"\n==> {path.name}")
                t_read = time.time()
                table = pq.read_table(path, columns=COLS)
                df = table.to_pandas(types_mapper=None)
                print(f"   read parquet: {len(df):,} rows in {time.time()-t_read:.1f}s")

                # Map symbol -> ticker_id, drop unknowns
                t_map = time.time()
                df["ticker_id"] = df["symbol"].map(sym2id).astype("Int64")
                before = len(df)
                df = df.dropna(subset=["ticker_id"]).copy()
                df["ticker_id"] = df["ticker_id"].astype("int32")
                skipped = before - len(df)
                # Reorder to TARGET_COLS
                df = df[["ticker_id", "timestamp", "open", "high", "low",
                         "close", "volume", "vwap", "trade_count"]]
                df.rename(columns={"timestamp": "ts"}, inplace=True)
                # Format ts as ISO; pandas does this fast by default in to_csv
                print(f"   mapped/cleaned in {time.time()-t_map:.1f}s "
                      f"(skipped {skipped} rows)")

                # Stream CSV in chunks
                t_copy = time.time()
                with conn.cursor() as cur:
                    with cur.copy(
                        "COPY bar_5m (ticker_id, ts, open, high, low, close, "
                        "volume, vwap, trade_count) FROM STDIN WITH (FORMAT CSV)"
                    ) as cp:
                        n = len(df)
                        for start in range(0, n, args.chunk_rows):
                            chunk = df.iloc[start:start + args.chunk_rows]
                            buf = io.StringIO()
                            chunk.to_csv(buf, index=False, header=False)
                            cp.write(buf.getvalue().encode("utf-8"))
                            done = min(start + args.chunk_rows, n)
                            pct = 100.0 * done / n
                            elapsed = time.time() - t_copy
                            rate = done / max(elapsed, 1e-3)
                            print(f"   COPY {done:>10,d}/{n:,} ({pct:5.1f}%)  "
                                  f"{rate:>10,.0f} rows/s  elapsed {elapsed:5.1f}s",
                                  end="\r", flush=True)
                conn.commit()
                dt = time.time() - t_copy
                print(f"\n   committed {len(df):,} rows in {dt:.1f}s "
                      f"({len(df)/max(dt,1e-3):,.0f} rows/s)")
                total_rows += len(df)
                total_skipped += skipped
                del df, table

            print(f"\nTOTAL: {total_rows:,} rows in {time.time()-t0:.1f}s "
                  f"(skipped {total_skipped} unknown-symbol rows)")

        finally:
            print("\nRestoring CHECK constraints ...")
            restore_check_constraints(conn, dropped)
            conn.commit()

        print("ANALYZE bar_5m ...")
        with conn.cursor() as cur:
            cur.execute("ANALYZE bar_5m")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker_id), MIN(ts), MAX(ts) FROM bar_5m")
            n, ndist, mn, mx = cur.fetchone()
        print(f"\nbar_5m: {n:,} rows | {ndist} tickers | {mn} .. {mx}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
