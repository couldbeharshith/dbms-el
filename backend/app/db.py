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


DEFAULT_SETTINGS = {
    "tracking_enabled": True,
    "screenshots_enabled": True,
    "screenshot_interval_sec": 30,
    "location_enabled": False,
    "injection_enabled": True,
    "retention_days": 14,
}


def get_default_user() -> dict:
    row = query_one(
        "INSERT INTO users (username) VALUES (%s) "
        "ON CONFLICT (username) DO NOTHING "
        "RETURNING id, username, settings",
        [config.DEFAULT_USERNAME],
    )
    if row is None:
        row = query_one(
            "SELECT id, username, settings FROM users WHERE username = %s",
            [config.DEFAULT_USERNAME],
        )
    # Fill in any settings keys added after the row was first created.
    merged = {**DEFAULT_SETTINGS, **(row["settings"] or {})}
    if merged != row["settings"]:
        execute(
            "UPDATE users SET settings = %s::jsonb WHERE id = %s",
            [_jsonb(merged), row["id"]],
        )
    row["settings"] = merged
    return row


def _jsonb(obj: Any) -> str:
    return json.dumps(obj)
