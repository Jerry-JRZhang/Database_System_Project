#!/usr/bin/env python3
"""
Load Phase 1 Parquet bar files into PostgreSQL (`symbols` + `market_data`).

Expects Parquet columns: symbol, timestamp, open, high, low, close, volume, vwap, trade_count
(as produced by `phase1_download_bars_to_parquet.py`).

Uses `constituents.csv` to fill company_name and sector for each ticker (with BRK.B / BRK-B resolution).
The Phase 1 schema partitions `market_data` by month on `ts`, so inserts target the parent table
and PostgreSQL routes rows to the correct child partition automatically.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd
import pyarrow.parquet as pq
from psycopg2 import connect, sql
from psycopg2.extras import execute_values

SUPPORTED_START_TS = pd.Timestamp("2023-01-01 00:00:00+00")
SUPPORTED_END_TS = pd.Timestamp("2025-01-01 00:00:00+00")


def load_constituents_map(constituents_csv: Path) -> Dict[str, Tuple[str, str]]:
    """
    Map Alpaca ticker -> (company_name, sector).
    Tries raw Symbol and dot/hyphen variants from constituents.csv.
    """
    df = pd.read_csv(constituents_csv, dtype=str)
    out: Dict[str, Tuple[str, str]] = {}
    for _, r in df.iterrows():
        raw = str(r.get("Symbol", "")).strip().upper()
        if not raw:
            continue
        company = str(r.get("Security", raw)).strip()
        sector = str(r.get("GICS Sector", "Unknown")).strip()
        for key in {raw, raw.replace(".", "-"), raw.replace("-", ".")}:
            out[key] = (company, sector)
    return out


def upsert_symbols(conn, rows: Sequence[Tuple[str, str, str]]) -> Dict[str, int]:
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO symbols (ticker, company_name, sector)
            VALUES %s
            ON CONFLICT (ticker) DO UPDATE
              SET company_name = EXCLUDED.company_name,
                  sector = EXCLUDED.sector
            """,
            list(rows),
            page_size=1000,
        )
        cur.execute(
            "SELECT id, ticker FROM symbols WHERE ticker = ANY(%s)",
            ([t[0] for t in rows],),
        )
        m = {ticker: sid for sid, ticker in cur.fetchall()}
    conn.commit()
    return m


def ensure_target_table(conn, target_table: str) -> None:
    if not target_table:
        raise RuntimeError("Target table name must not be empty.")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
            """,
            (target_table,),
        )
        exists = bool(cur.fetchone()[0])

        if exists:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_partitioned_table pt
                    JOIN pg_class c ON c.oid = pt.partrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = %s
                )
                """,
                (target_table,),
            )
            is_partitioned = bool(cur.fetchone()[0])
            if not is_partitioned:
                raise RuntimeError(
                    f"Existing table {target_table!r} is not partitioned. "
                    "Choose a different table name or recreate it as a partitioned table."
                )
            return

        pk_name = f"{target_table}_symbol_ts_pk"
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE {table_name} (
                    symbol_id BIGINT NOT NULL REFERENCES symbols(id) ON DELETE RESTRICT,
                    ts TIMESTAMPTZ NOT NULL,
                    open NUMERIC(18, 6) NOT NULL CHECK (open >= 0),
                    high NUMERIC(18, 6) NOT NULL CHECK (high >= 0),
                    low NUMERIC(18, 6) NOT NULL CHECK (low >= 0),
                    close NUMERIC(18, 6) NOT NULL CHECK (close >= 0),
                    volume BIGINT NOT NULL CHECK (volume >= 0),
                    CHECK (high >= low),
                    CHECK (high >= open AND high >= close),
                    CHECK (low <= open AND low <= close),
                    CONSTRAINT {pk_name} PRIMARY KEY (symbol_id, ts)
                ) PARTITION BY RANGE (ts)
                """
            ).format(
                table_name=sql.Identifier(target_table),
                pk_name=sql.Identifier(pk_name),
            )
        )

        partition_start = SUPPORTED_START_TS
        while partition_start < SUPPORTED_END_TS:
            partition_end = partition_start + pd.offsets.MonthBegin(1)
            partition_name = (
                f"{target_table}_y{partition_start.year:04d}m{partition_start.month:02d}"
            )
            cur.execute(
                sql.SQL(
                    """
                    CREATE TABLE {partition_name}
                    PARTITION OF {table_name}
                    FOR VALUES FROM (%s) TO (%s)
                    """
                ).format(
                    partition_name=sql.Identifier(partition_name),
                    table_name=sql.Identifier(target_table),
                ),
                (
                    partition_start.isoformat(),
                    partition_end.isoformat(),
                ),
            )
            partition_start = partition_end

    conn.commit()


def bulk_insert_market_data(
    conn,
    rows: List[Tuple[int, object, float, float, float, float, int]],
    page_size: int,
    target_table: str,
) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            sql.SQL(
                """
                INSERT INTO {table_name} (symbol_id, ts, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (symbol_id, ts) DO NOTHING
                """
            ).format(table_name=sql.Identifier(target_table)).as_string(cur),
            rows,
            page_size=page_size,
        )
    conn.commit()


def load_parquet_file(
    path: Path,
    conn,
    meta: Dict[str, Tuple[str, str]],
    insert_batch: int,
    target_table: str,
) -> int:
    pf = pq.ParquetFile(path)
    total_inserted = 0

    for batch in pf.iter_batches(batch_size=50_000):
        df = batch.to_pandas()
        if df.empty:
            continue

        # Ensure expected columns
        need = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
        missing = need - set(df.columns)
        if missing:
            raise RuntimeError(f"{path.name}: missing columns {missing}")

        tickers = df["symbol"].astype(str).unique().tolist()
        sym_rows: List[Tuple[str, str, str]] = []
        for t in tickers:
            company, sector = meta.get(t, (t, "Unknown"))
            sym_rows.append((t, company, sector))
        symbol_id_map = upsert_symbols(conn, sym_rows)

        sym = df["symbol"].astype(str)
        df = df.assign(symbol_id=sym.map(symbol_id_map)).dropna(subset=["symbol_id"])
        if df.empty:
            continue
        df["symbol_id"] = df["symbol_id"].astype("int64")
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
        min_ts = df["ts"].min()
        max_ts = df["ts"].max()
        if min_ts < SUPPORTED_START_TS or max_ts >= SUPPORTED_END_TS:
            before = len(df)
            df = df[(df["ts"] >= SUPPORTED_START_TS) & (df["ts"] < SUPPORTED_END_TS)].copy()
            dropped = before - len(df)
            print(
                f"  {path.name}: filtered {dropped} out-of-range row(s) outside "
                f"[{SUPPORTED_START_TS}, {SUPPORTED_END_TS}) to match the monthly partitions."
            )
            if df.empty:
                continue
        df = df.sort_values(["ts", "symbol_id"]).reset_index(drop=True)

        chunk_start = 0
        n = len(df)
        while chunk_start < n:
            sub = df.iloc[chunk_start : chunk_start + insert_batch]
            chunk_start += insert_batch
            ts_values = [
                ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                for ts in sub["ts"].tolist()
            ]
            rows = list(
                zip(
                    sub["symbol_id"].tolist(),
                    ts_values,
                    sub["open"].astype(float).tolist(),
                    sub["high"].astype(float).tolist(),
                    sub["low"].astype(float).tolist(),
                    sub["close"].astype(float).tolist(),
                    sub["volume"].astype("int64").tolist(),
                )
            )
            bulk_insert_market_data(conn, rows, insert_batch, target_table)
            total_inserted += len(rows)

    return total_inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Parquet bar files into PostgreSQL")
    parser.add_argument(
        "--input-dir",
        default="outputs/phase1_bars_parquet",
        help="Directory containing bars_*.parquet files.",
    )
    parser.add_argument("--constituents-csv", default="constituents.csv")
    parser.add_argument("--insert-batch", type=int, default=10_000)
    parser.add_argument(
        "--file-glob",
        default="bars_*.parquet",
        help="Glob used to select Parquet files under --input-dir.",
    )
    parser.add_argument(
        "--target-table",
        default="market_data",
        help="Partitioned fact table to load into. Created automatically if missing.",
    )
    args = parser.parse_args()

    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("Environment variable POSTGRES_DSN is not set.")

    repo_root = Path(__file__).resolve().parents[2]
    input_dir = (repo_root / args.input_dir).resolve()
    constituents_path = (repo_root / args.constituents_csv).resolve()

    if not constituents_path.exists():
        raise FileNotFoundError(f"Missing {constituents_path}")

    meta = load_constituents_map(constituents_path)
    files = sorted(input_dir.glob(args.file_glob))
    if not files:
        raise RuntimeError(f"No files matching {args.file_glob!r} found under {input_dir}")

    grand_total = 0
    with connect(dsn) as conn:
        ensure_target_table(conn, args.target_table)
        for f in files:
            print(f"Loading {f.name} ...")
            n = load_parquet_file(f, conn, meta, args.insert_batch, args.target_table)
            grand_total += n
            print(f"  inserted/attempted rows: {n}")

    print(f"Done. Total rows processed (insert attempts) into {args.target_table}: {grand_total}")


if __name__ == "__main__":
    main()
