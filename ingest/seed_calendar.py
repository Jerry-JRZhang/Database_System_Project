"""Build the trading_calendar for XNYS + XNAS over 2023-2024 using
pandas_market_calendars (both exchanges share the NYSE calendar)."""
from __future__ import annotations

import argparse
from datetime import datetime

import pandas_market_calendars as mcal
import pandas as pd

from db import connect


def build_rows(start: str, end: str) -> list[tuple]:
    cal = mcal.get_calendar("XNYS")
    sched = cal.schedule(start_date=start, end_date=end)
    out: list[tuple] = []
    # Determine early-close days (close < 21:00 UTC ~= 16:00 ET; NYSE early close is 13:00 ET)
    for session_date, row in sched.iterrows():
        open_ts: pd.Timestamp = row["market_open"]
        close_ts: pd.Timestamp = row["market_close"]
        # 13:00 ET == 17:00 UTC (DST) or 18:00 UTC (standard time). Just compare to 19:30 UTC as cutoff.
        is_early = close_ts.time() < datetime.strptime("19:30", "%H:%M").time()
        out.append((session_date.date(), open_ts.to_pydatetime(), close_ts.to_pydatetime(), is_early))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["pg", "ts"], default="pg")
    args = p.parse_args(argv)

    rows = build_rows("2023-01-01", "2024-12-31")
    print(f"Built {len(rows)} session rows  (target={args.target})")
    with connect(target=args.target) as conn, conn.cursor() as cur:
        cur.execute("SELECT exchange_id FROM exchange WHERE code IN ('XNYS','XNAS') ORDER BY code")
        ex_ids = [r[0] for r in cur.fetchall()]
        for ex_id in ex_ids:
            cur.executemany(
                """
                INSERT INTO trading_calendar(exchange_id, session_date, open_ts, close_ts, is_early_close)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (exchange_id, session_date) DO UPDATE SET
                  open_ts        = EXCLUDED.open_ts,
                  close_ts       = EXCLUDED.close_ts,
                  is_early_close = EXCLUDED.is_early_close
                """,
                [(ex_id, *r) for r in rows],
            )
        conn.commit()
    print("OK: trading_calendar populated for XNYS + XNAS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
