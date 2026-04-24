#!/usr/bin/env python3
"""
Phase 1: Download Alpaca historical 5-minute bars to Parquet.

1) Reads S&P 500 constituents from `constituents.csv`
2) Resolves them to Alpaca-tradable tickers
3) Downloads via GET https://data.alpaca.markets/v2/stocks/bars
4) Writes one Parquet file per calendar year under `outputs/phase1_bars_parquet/`
   (all symbol chunks append as row groups in the same file).
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from alpaca_trade_api.rest import REST


PARQUET_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("timestamp", pa.timestamp("ns", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("vwap", pa.float64()),
        ("trade_count", pa.int64()),
    ]
)


@dataclass(frozen=True)
class Settings:
    alpaca_key: str
    alpaca_secret: str
    alpaca_base_url: str
    start_date: str
    end_date: str
    timeframe: str = "5Min"
    limit: int = 10000
    adjustment: str = "raw"
    feed: str = "sip"
    chunk_size: int = 50
    symbol_count: int = 500
    request_pause_seconds: float = 0.1


def load_settings() -> Settings:
    required = {
        "APCA_API_KEY_ID": os.getenv("APCA_API_KEY_ID", "").strip(),
        "APCA_API_SECRET_KEY": os.getenv("APCA_API_SECRET_KEY", "").strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    alpaca_base_url = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").strip()
    alpaca_base_url = alpaca_base_url.rstrip("/")
    if alpaca_base_url.lower().endswith("/v2"):
        alpaca_base_url = alpaca_base_url[: -len("/v2")]

    return Settings(
        alpaca_key=required["APCA_API_KEY_ID"],
        alpaca_secret=required["APCA_API_SECRET_KEY"],
        alpaca_base_url=alpaca_base_url,
        start_date=os.getenv("PHASE1_START_DATE", "2023-01-01"),
        end_date=os.getenv("PHASE1_END_DATE", "2025-01-01"),
        feed=os.getenv("APCA_DATA_FEED", "sip"),
    )


def resolve_sp500_to_alpaca_symbols(
    alpaca_api: REST, constituents_csv_path: Path, max_symbols: int
) -> List[str]:
    df = pd.read_csv(constituents_csv_path, dtype=str)
    required_cols = {"Symbol", "Security", "GICS Sector"}
    missing = required_cols - set(df.columns)
    if missing:
        raise RuntimeError(f"Invalid constituents.csv header. Missing columns: {sorted(missing)}")

    assets = alpaca_api.list_assets(status="active", asset_class="us_equity")
    tradable = {a.symbol for a in assets if getattr(a, "tradable", False) and getattr(a, "status", "") == "active"}

    resolved: List[str] = []
    seen: set[str] = set()
    skipped = 0

    for _, r in df.iterrows():
        raw = str(r.get("Symbol", "")).strip().upper()
        if not raw:
            continue
        candidates = [raw, raw.replace(".", "-"), raw.replace("-", ".")]
        pick = next((c for c in candidates if c in tradable), None)
        if pick and pick not in seen:
            resolved.append(pick)
            seen.add(pick)
        else:
            skipped += 1
        if len(resolved) >= max_symbols:
            break

    print(f"Resolved {len(resolved)} Alpaca-tradable tickers from constituents.csv (skipped {skipped}).")
    return resolved


def year_windows(start_date: str, end_date: str) -> Iterable[Tuple[str, str]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start >= end:
        return
    for year in range(start.year, end.year + 1):
        year_start = pd.Timestamp(year=year, month=1, day=1)
        year_end = pd.Timestamp(year=year + 1, month=1, day=1)
        ws = max(start, year_start)
        we = min(end, year_end)
        if ws < we:
            yield ws.date().isoformat(), we.date().isoformat()


def _bars_parquet_path(out_dir: Path, window_start: str, window_end: str) -> Path:
    return out_dir / f"bars_{window_start}_to_{window_end}.parquet"


def _page_rows_to_dataframe(page_rows: List[Dict[str, object]]) -> pd.DataFrame:
    df = pd.DataFrame(page_rows)
    df = df[["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap", "trade_count"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    for col in ("open", "high", "low", "close", "vwap"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["trade_count"] = pd.to_numeric(df["trade_count"], errors="coerce").fillna(0).astype("int64")
    df["vwap"] = df["vwap"].fillna(0.0)
    return df


def download_bars_chunk_to_parquet_writer(
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    api_key: str,
    secret_key: str,
    writer: pq.ParquetWriter,
    settings: Settings,
) -> int:
    url = "https://data.alpaca.markets/v2/stocks/bars"
    headers = {
        "accept": "application/json",
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }

    params: Dict[str, object] = {
        "symbols": ",".join(symbols),
        "timeframe": settings.timeframe,
        "start": start_date,
        "end": end_date,
        "limit": settings.limit,
        "adjustment": settings.adjustment,
        "feed": settings.feed,
    }

    written = 0
    page = 0

    while True:
        page += 1
        resp = None
        max_attempts = 8
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=60)
                if resp.status_code == 200:
                    break
                if resp.status_code == 429:
                    sleep_s = min(120, (2**attempt)) + (0.0 + (attempt % 3))
                    print(f"  Page {page}: rate-limited (429). Retry {attempt}/{max_attempts} after {sleep_s:.1f}s...")
                    time.sleep(sleep_s)
                    continue
                raise RuntimeError(f"Alpaca bars request failed ({resp.status_code}): {resp.text[:500]}")
            except Exception as exc:
                last_error = exc
                sleep_s = min(120, (2**attempt)) * 0.5
                print(f"  Page {page}: request error ({exc.__class__.__name__}). Retry {attempt}/{max_attempts} after {sleep_s:.1f}s...")
                time.sleep(sleep_s)
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"Alpaca bars request failed after retries: {last_error}")

        data = resp.json()
        bars_by_symbol = data.get("bars") or {}
        next_token = data.get("next_page_token")

        page_rows: List[Dict[str, object]] = []
        for sym, bars in bars_by_symbol.items():
            if not bars:
                continue
            for b in bars:
                t = b.get("t")
                if t is None:
                    continue
                if isinstance(t, (int, float)):
                    ts = pd.to_datetime(int(t), unit="ns", utc=True)
                else:
                    t_str = str(t)
                    if t_str.isdigit():
                        ts = pd.to_datetime(int(t_str), unit="ns", utc=True)
                    else:
                        ts = pd.to_datetime(t_str, utc=True, errors="coerce")
                if pd.isna(ts):
                    continue

                page_rows.append(
                    {
                        "symbol": sym,
                        "timestamp": ts,
                        "open": b.get("o"),
                        "high": b.get("h"),
                        "low": b.get("l"),
                        "close": b.get("c"),
                        "volume": b.get("v"),
                        "vwap": b.get("vw"),
                        "trade_count": b.get("n"),
                    }
                )

        if page_rows:
            df = _page_rows_to_dataframe(page_rows)
            table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)
            writer.write_table(table)
            written += len(df)
            print(f"  Page {page}: wrote {len(df)} rows (running {written})")
        else:
            print(f"  Page {page}: no rows returned (running {written})")

        if next_token:
            params["page_token"] = next_token
            time.sleep(settings.request_pause_seconds)
        else:
            break

    return written


def download_all_to_parquet(settings: Settings, output_dir: Path, constituents_csv_path: Path) -> None:
    api = REST(settings.alpaca_key, settings.alpaca_secret, base_url=settings.alpaca_base_url)
    symbols = resolve_sp500_to_alpaca_symbols(api, constituents_csv_path, max_symbols=settings.symbol_count)

    symbol_chunks = [symbols[i : i + settings.chunk_size] for i in range(0, len(symbols), settings.chunk_size)]
    print(f"Total symbol chunks: {len(symbol_chunks)} (chunk_size={settings.chunk_size})")

    output_dir.mkdir(parents=True, exist_ok=True)
    total_written = 0

    for win_start, win_end in year_windows(settings.start_date, settings.end_date):
        out_path = _bars_parquet_path(output_dir, win_start, win_end)
        print(f"Downloading one year ({win_start} -> {win_end}) -> {out_path.name}")
        print(f"  (API requests use {len(symbol_chunks)} symbol chunk(s), one Parquet file per year.)")

        if out_path.exists():
            out_path.unlink()

        writer = pq.ParquetWriter(out_path, schema=PARQUET_SCHEMA)
        try:
            for chunk_idx, chunk in enumerate(symbol_chunks, start=0):
                print(f"  Symbol chunk {chunk_idx + 1}/{len(symbol_chunks)} ({len(chunk)} symbols) ...")
                written = download_bars_chunk_to_parquet_writer(
                    symbols=chunk,
                    start_date=win_start,
                    end_date=win_end,
                    api_key=settings.alpaca_key,
                    secret_key=settings.alpaca_secret,
                    writer=writer,
                    settings=settings,
                )
                total_written += written
        finally:
            writer.close()

    print(f"Download complete. Total rows written to Parquet: {total_written}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Alpaca historical bars to Parquet (Phase 1)")
    parser.add_argument("--output-dir", default="outputs/phase1_bars_parquet", help="Directory for output Parquet files.")
    parser.add_argument("--constituents-csv", default="constituents.csv", help="Path to constituents.csv.")
    parser.add_argument("--symbol-count", type=int, default=None)
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--feed", default=None)
    args = parser.parse_args()

    settings = load_settings()
    if args.symbol_count is not None:
        settings = Settings(**{**settings.__dict__, "symbol_count": args.symbol_count})
    if args.chunk_size is not None:
        settings = Settings(**{**settings.__dict__, "chunk_size": args.chunk_size})
    if args.start_date is not None:
        settings = Settings(**{**settings.__dict__, "start_date": args.start_date})
    if args.end_date is not None:
        settings = Settings(**{**settings.__dict__, "end_date": args.end_date})
    if args.feed is not None:
        settings = Settings(**{**settings.__dict__, "feed": args.feed})

    repo_root = Path(__file__).resolve().parents[2]
    output_dir = (repo_root / args.output_dir).resolve()
    constituents_csv_path = (repo_root / args.constituents_csv).resolve()

    download_all_to_parquet(settings=settings, output_dir=output_dir, constituents_csv_path=constituents_csv_path)


if __name__ == "__main__":
    main()
