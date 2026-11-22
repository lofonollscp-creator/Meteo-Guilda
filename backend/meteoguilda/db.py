"""Acceso a PostgreSQL/PostGIS (pool síncrono; los endpoints async usan asyncio.to_thread)."""
from __future__ import annotations

import os
from importlib import resources

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def url() -> str:
    return os.environ.get("MG_DATABASE_URL", "postgresql://mg:mg@localhost:5432/meteoguilda")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(url(), min_size=1, max_size=8, kwargs={"autocommit": True, "row_factory": dict_row},
                               open=True)
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    sql = resources.files("meteoguilda").joinpath("sql/schema.sql").read_text()
    with pool().connection() as con:
        con.execute(sql)


def available() -> bool:
    try:
        with pool().connection(timeout=3) as con:
            con.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
