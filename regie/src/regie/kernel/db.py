from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()
_local = threading.local()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def J(value: Any) -> Jsonb:
    """Wrap a python value for a jsonb parameter (UUID, dates, Decimal → texte)."""
    return Jsonb(value, dumps=dumps)


def pool_for(dsn: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=12,
                kwargs={"row_factory": dict_row, "autocommit": False},
                open=True,
                name="regie",
            )
            _pools[dsn] = pool
        return pool


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            try:
                pool.close()
            except Exception:
                pass
        _pools.clear()


@contextmanager
def connect(dsn: str) -> Iterator[psycopg.Connection]:
    """One connection per thread with nested-transaction sharing (same pattern as Inbox Zero)."""
    stack: dict[str, list[psycopg.Connection]] = getattr(_local, "stack", None) or {}
    _local.stack = stack
    active = stack.get(dsn)
    if active:
        yield active[-1]
        return
    pool = pool_for(dsn)
    with pool.connection() as conn:
        stack[dsn] = [conn]
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            stack.pop(dsn, None)


transaction = connect


def fetch_all(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def fetch_one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(conn: psycopg.Connection, sql: str, params: Any = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount if cur.rowcount is not None else 0


def scalar(conn: psycopg.Connection, sql: str, params: Any = None) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        return list(row.values())[0]


def jsonable(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Datetimes to ISO strings so rows can go straight to JSON."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = iso(value)
        elif isinstance(value, (uuid.UUID, Decimal, date)):
            out[key] = str(value) if not isinstance(value, Decimal) else float(value)
        else:
            out[key] = value
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
