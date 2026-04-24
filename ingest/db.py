"""DB helper — single source of truth for connection strings.

Two targets:
    pg  -> vanilla Postgres 16 container (port 5433 by default)
    ts  -> TimescaleDB container         (port 5434 by default)

Pick with the TARGET env var or the `target` kwarg. Default is 'pg'.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _resolve(target: str | None) -> str:
    t = (target or os.getenv("TARGET") or "pg").lower()
    if t not in {"pg", "ts"}:
        raise ValueError(f"unknown target {t!r}; expected 'pg' or 'ts'")
    return t


def dsn(target: str | None = None) -> str:
    t = _resolve(target)
    if t == "ts":
        host = os.getenv("TS_HOST", "localhost")
        port = os.getenv("TS_PORT", "5434")
    else:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5433")
    return (
        f"host={host} port={port} "
        f"dbname={os.getenv('POSTGRES_DB', 'equitydb')} "
        f"user={os.getenv('POSTGRES_USER', 'equity')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'equity')}"
    )


def connect(target: str | None = None, **kwargs) -> psycopg.Connection:
    return psycopg.connect(dsn(target), **kwargs)
