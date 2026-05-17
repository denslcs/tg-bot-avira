from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg

_RE_QMARK = re.compile(r"\?")
_RE_SQLITE_NOW = re.compile(r"datetime\('now'\)", re.IGNORECASE)
_RE_SQLITE_NOW_WITH_ARG = re.compile(r"datetime\('now',\s*\$([0-9]+)\)", re.IGNORECASE)
_RE_SQLITE_NOW_WITH_LITERAL = re.compile(r"datetime\('now',\s*'([^']+)'\)", re.IGNORECASE)
_RE_SQLITE_DATETIME_FN = re.compile(r"datetime\(([\w\.]+)\)", re.IGNORECASE)
_RE_ID_INTEGER = re.compile(r"\b([a-z_]+_id)\s+INTEGER\b", re.IGNORECASE)


def _convert_qmark_placeholders(sql: str) -> str:
    idx = 0
    in_single = False
    out: list[str] = []
    for ch in sql:
        if ch == "'":
            in_single = not in_single
            out.append(ch)
            continue
        if ch == "?" and not in_single:
            idx += 1
            out.append(f"${idx}")
            continue
        out.append(ch)
    return "".join(out)


def _translate_sql_for_postgres(sql: str) -> str:
    text = sql.strip()
    upper = text.upper()
    if upper.startswith("PRAGMA "):
        return ""
    if upper == "BEGIN IMMEDIATE":
        return "BEGIN"
    text = _convert_qmark_placeholders(text)
    text = text.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    # Telegram/user-related IDs can exceed int32. Ensure *_id columns are bigint on Postgres.
    text = _RE_ID_INTEGER.sub(r"\1 BIGINT", text)
    text = _RE_SQLITE_NOW.sub("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')", text)
    text = _RE_SQLITE_NOW_WITH_LITERAL.sub(
        "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' + '\\1'::interval)",
        text,
    )
    text = _RE_SQLITE_NOW_WITH_ARG.sub(
        "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' + ($\\1)::interval)",
        text,
    )
    text = _RE_SQLITE_DATETIME_FN.sub("CAST(\\1 AS timestamptz)", text)
    return text


@dataclass
class PostgresCursor:
    rows: list[tuple[Any, ...]]
    lastrowid: int | None = None
    rowcount: int = 0
    _idx: int = 0

    async def fetchone(self) -> tuple[Any, ...] | None:
        if self._idx >= len(self.rows):
            return None
        row = self.rows[self._idx]
        self._idx += 1
        return row

    async def fetchall(self) -> list[tuple[Any, ...]]:
        if self._idx <= 0:
            return list(self.rows)
        return list(self.rows[self._idx :])

    async def __aenter__(self) -> "PostgresCursor":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


class PostgresCompatConnection:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        return _ExecuteProxy(self, sql, tuple(params or ()))

    async def _run_execute(self, sql: str, params: Sequence[Any]) -> PostgresCursor:
        stripped = sql.strip()
        upper_raw = stripped.upper()
        if upper_raw.startswith("PRAGMA TABLE_INFO(") and stripped.endswith(")"):
            table = stripped[stripped.find("(") + 1 : -1].strip()
            records = await self._conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position
                """,
                table,
            )
            rows = [(idx, str(r[0]), "", 0, None, 0) for idx, r in enumerate(records)]
            return PostgresCursor(rows=rows)
        query = _translate_sql_for_postgres(sql)
        if not query:
            return PostgresCursor(rows=[])
        q_upper = query.lstrip().upper()
        if q_upper.startswith(("SELECT ", "WITH ")):
            records = await self._conn.fetch(query, *params)
            return PostgresCursor(rows=[tuple(r) for r in records])
        lastrowid: int | None = None
        if q_upper.startswith("INSERT INTO SUPPORT_TICKETS"):
            row = await self._conn.fetchrow(f"{query} RETURNING ticket_id", *params)
            if row:
                lastrowid = int(row[0])
        else:
            status = await self._conn.execute(query, *params)
            rowcount = 0
            if status:
                parts = str(status).split()
                if parts and parts[-1].isdigit():
                    rowcount = int(parts[-1])
        return PostgresCursor(rows=[], lastrowid=lastrowid, rowcount=rowcount)

    async def commit(self) -> None:
        try:
            await self._conn.execute("COMMIT")
        except asyncpg.InvalidTransactionStateError:
            return None

    async def rollback(self) -> None:
        try:
            await self._conn.execute("ROLLBACK")
        except asyncpg.InvalidTransactionStateError:
            return None


class _ExecuteProxy:
    def __init__(self, db: PostgresCompatConnection, sql: str, params: tuple[Any, ...]) -> None:
        self._db = db
        self._sql = sql
        self._params = params
        self._cursor: PostgresCursor | None = None

    async def _get_cursor(self) -> PostgresCursor:
        if self._cursor is None:
            self._cursor = await self._db._run_execute(self._sql, self._params)
        return self._cursor

    def __await__(self):
        return self._get_cursor().__await__()

    async def __aenter__(self) -> PostgresCursor:
        return await self._get_cursor()

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        return None


class PostgresPool:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def connection(self):
        async with self._pool.acquire() as conn:
            yield PostgresCompatConnection(conn)

    async def close(self) -> None:
        await self._pool.close()

