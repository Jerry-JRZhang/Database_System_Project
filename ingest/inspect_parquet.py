"""Quick look at the parquet files so we can map columns to bar_5m."""
from __future__ import annotations

import sys
from pathlib import Path

import pyarrow.parquet as pq

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def inspect(path: Path) -> None:
    print(f"\n=== {path.name} ===")
    pf = pq.ParquetFile(path)
    print(f"num_rows         : {pf.metadata.num_rows:,}")
    print(f"num_row_groups   : {pf.metadata.num_row_groups}")
    print(f"created_by       : {pf.metadata.created_by}")
    print("schema:")
    print(pf.schema_arrow)
    # Peek at first batch
    batch = next(pf.iter_batches(batch_size=5))
    print("\nfirst 5 rows:")
    print(batch.to_pandas().to_string(index=False))


def main() -> int:
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        print(f"No parquet files found in {DATA_DIR}", file=sys.stderr)
        return 1
    for p in files:
        inspect(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
