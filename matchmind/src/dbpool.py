"""Database helpers shared by the API and the bot.

* ``get_pool(env_var)``: one small connection pool per role/URL (avoids opening a new
  connection per request, which exhausts the free-tier pooler).
* ``fetch_df``: run a query and return a DataFrame without pandas' DBAPI warnings.
"""
from __future__ import annotations

import os

import pandas as pd

_POOLS: dict[str, object] = {}


def get_pool(env_var: str = "DATABASE_URL", min_size: int = 1, max_size: int = 5):
    from psycopg_pool import ConnectionPool
    pool = _POOLS.get(env_var)
    if pool is None:
        url = os.environ[env_var]
        # prepare_threshold=None keeps this safe behind Supabase's transaction pooler.
        pool = ConnectionPool(url, min_size=min_size, max_size=max_size, open=True,
                              kwargs={"prepare_threshold": None}, timeout=10)
        _POOLS[env_var] = pool
    return pool


def close_pools() -> None:
    for pool in _POOLS.values():
        pool.close()
    _POOLS.clear()


def fetch_df(conn, sql: str, params=None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description] if cur.description else []
        return pd.DataFrame(cur.fetchall() if cur.description else [], columns=cols)
