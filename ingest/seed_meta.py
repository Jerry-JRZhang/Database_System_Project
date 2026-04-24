"""Seed sector / industry / ticker tables from constituents.csv.

Run AFTER the schema (sql/01_schema.sql) and exchange seed (sql/99_seed_exchanges.sql).
Idempotent — safe to re-run.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from db import connect

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "constituents.csv"

# Symbols that trade primarily on Nasdaq vs NYSE.
# We keep this list small and let everything else default to NYSE; for a
# course project this is precise enough and is documented as a limitation.
NASDAQ_SYMBOLS = {
    "AAPL","ABNB","ADBE","ADI","ADP","ADSK","AEP","ALGN","AMAT","AMD",
    "AMGN","AMZN","ANSS","APP","ARM","ASML","AVGO","AZN","BIIB","BKNG",
    "BKR","CCEP","CDNS","CDW","CEG","CHTR","CMCSA","COST","CPRT","CRWD",
    "CSCO","CSGP","CSX","CTAS","CTSH","DASH","DDOG","DLTR","DXCM","EA",
    "EBAY","EXC","FANG","FAST","FI","FTNT","GEHC","GFS","GILD","GOOG",
    "GOOGL","HON","IDXX","ILMN","INTC","INTU","ISRG","KDP","KHC","KLAC",
    "LIN","LRCX","LULU","MAR","MCHP","MDB","MDLZ","MELI","META","MNST",
    "MRNA","MRVL","MSFT","MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY",
    "PANW","PAYX","PCAR","PDD","PEP","PYPL","QCOM","REGN","ROP","ROST",
    "SBUX","SIRI","SMCI","SNPS","TEAM","TMUS","TSLA","TTD","TTWO","TXN",
    "VRSK","VRTX","WBA","WBD","WDAY","XEL","ZS",
}


def exchange_for(symbol: str) -> str:
    return "XNAS" if symbol in NASDAQ_SYMBOLS else "XNYS"


def parse_int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["pg", "ts"], default="pg")
    args = p.parse_args(argv)

    rows: list[dict] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"Loaded {len(rows)} constituents from {CSV_PATH.name}  (target={args.target})")

    sectors  = sorted({r["GICS Sector"] for r in rows})
    pairs    = sorted({(r["GICS Sector"], r["GICS Sub-Industry"]) for r in rows})

    with connect(target=args.target, autocommit=False) as conn, conn.cursor() as cur:
        # Sectors
        cur.executemany(
            "INSERT INTO sector(name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            [(s,) for s in sectors],
        )

        # Industries
        cur.execute("SELECT sector_id, name FROM sector")
        sector_id = {n: sid for sid, n in cur.fetchall()}
        cur.executemany(
            "INSERT INTO industry(sector_id, name) VALUES (%s, %s) "
            "ON CONFLICT (sector_id, name) DO NOTHING",
            [(sector_id[s], i) for s, i in pairs],
        )

        # Lookups for tickers
        cur.execute("SELECT exchange_id, code FROM exchange")
        ex_id = {code: xid for xid, code in cur.fetchall()}
        cur.execute("SELECT industry_id, sector_id, name FROM industry")
        ind_id = {(sid, n): iid for iid, sid, n in cur.fetchall()}

        ticker_rows = []
        for r in rows:
            sym = r["Symbol"].strip()
            sec = r["GICS Sector"]
            ind = r["GICS Sub-Industry"]
            ticker_rows.append((
                sym,
                r["Security"].strip(),
                ex_id[exchange_for(sym)],
                ind_id[(sector_id[sec], ind)],
                parse_int(r.get("CIK", "")),
                r.get("Headquarters Location", "").strip() or None,
                r.get("Date added") or None,
            ))
        cur.executemany(
            """
            INSERT INTO ticker(symbol, name, exchange_id, industry_id, cik, headquarters, date_added)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
              name         = EXCLUDED.name,
              exchange_id  = EXCLUDED.exchange_id,
              industry_id  = EXCLUDED.industry_id,
              cik          = EXCLUDED.cik,
              headquarters = EXCLUDED.headquarters,
              date_added   = EXCLUDED.date_added
            """,
            ticker_rows,
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM sector")
        n_sec = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM industry")
        n_ind = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ticker")
        n_tic = cur.fetchone()[0]

    print(f"OK: {n_sec} sectors, {n_ind} industries, {n_tic} tickers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
