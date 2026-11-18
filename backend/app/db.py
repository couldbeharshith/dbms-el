"""PostgreSQL access layer: a psycopg3 connection pool + small helpers.

All SQL in the project is hand-written, parameterized SQL — the point of this
project is to demonstrate the DBMS, not to hide it behind an ORM.
"""

from typing import Any, Sequence

import json
import psycopg
from psycopg_pool import ConnectionPool

from backend.app import config

_pool: ConnectionPool | None = None


def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=1,
            max_size=8,
            open=True,
            kwargs={"autocommit": True, "row_factory": psycopg.rows.dict_row},
        )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def query(sql: str, params: Sequence[Any] | dict | None = None) -> list[dict]:
    with _pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: Sequence[Any] | dict | None = None) -> dict | None:
    with _pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Sequence[Any] | dict | None = None) -> None:
    with _pool.connection() as conn:
        conn.execute(sql, params)


def executemany(sql: str, params_seq: Sequence[Sequence[Any]]) -> None:
    with _pool.connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(sql, params_seq)


def apply_schema(sql_path) -> None:
    """Run the (idempotent) schema file. psycopg3 executes multi-statement
    strings fine when no parameters are used."""
    sql = open(sql_path, encoding="utf-8").read()
    with _pool.connection() as conn:
        conn.execute(sql)


def vec_lit(vec: Sequence[float]) -> str:
    """Format a vector as a pgvector literal for `::vector` casts."""
    return "[" + ",".join(f"{float(x):.8g}" for x in vec) + "]"


SETTING_COLUMNS = (
    "tracking_enabled", "screenshots_enabled", "screenshot_interval_sec",
    "location_enabled", "injection_enabled", "retention_days",
)


def get_default_user() -> dict:
    """Default user row; the per-user settings columns are exposed as a
    `settings` dict (the shape the API and clients use)."""
    execute(
        "INSERT INTO users (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
        [config.DEFAULT_USERNAME],
    )
    row = query_one(
        f"SELECT id, username, {', '.join(SETTING_COLUMNS)} FROM users WHERE username = %s",
        [config.DEFAULT_USERNAME],
    )
    return {
        "id": row["id"],
        "username": row["username"],
        "settings": {c: row[c] for c in SETTING_COLUMNS},
    }


def _jsonb(obj: Any) -> str:
    return json.dumps(obj)
