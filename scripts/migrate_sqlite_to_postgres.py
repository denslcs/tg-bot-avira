from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

SKIP_TABLES = {"sqlite_sequence"}
CRITICAL_TABLES = {
    "users",
    "user_budget_history",
    "support_tickets",
    "support_ticket_notes",
    "support_ratings",
    "subscription_bonus_pending",
    "star_payment_charges",
    "wata_payment_orders",
    "bot_meta",
}


def _table_names(src: sqlite3.Connection) -> list[str]:
    cur = src.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    names = [str(r[0]) for r in cur.fetchall() if str(r[0]) not in SKIP_TABLES]
    # FK-safe order: parent table first.
    if "users" in names:
        names.remove("users")
        names.insert(0, "users")
    return names


def _table_columns(src: sqlite3.Connection, table: str) -> list[tuple[str, int]]:
    cur = src.execute(f"PRAGMA table_info({table})")
    return [(str(r[1]), int(r[5])) for r in cur.fetchall()]


def _build_upsert_sql(table: str, cols: list[tuple[str, int]]) -> str:
    names = [c for c, _ in cols]
    pk = [c for c, is_pk in cols if is_pk > 0]
    placeholders = ", ".join(f"${i}" for i in range(1, len(names) + 1))
    cols_csv = ", ".join(names)
    if not pk:
        return f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders})"
    non_pk = [c for c in names if c not in pk]
    if not non_pk:
        return (
            f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(pk)}) DO NOTHING"
        )
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in non_pk)
    return (
        f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}"
    )


async def _copy_table(src: sqlite3.Connection, pg: asyncpg.Connection, table: str, batch_size: int) -> tuple[int, int]:
    cols = _table_columns(src, table)
    names = [c for c, _ in cols]
    sql = _build_upsert_sql(table, cols)
    count_cur = src.execute(f"SELECT COUNT(*) FROM {table}")
    total = int(count_cur.fetchone()[0] or 0)
    copied = 0
    offset = 0
    while True:
        rows = src.execute(
            f"SELECT {', '.join(names)} FROM {table} LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break
        async with pg.transaction():
            for row in rows:
                await pg.execute(sql, *row)
        copied += len(rows)
        offset += len(rows)
    return copied, total


async def _seed_orphan_users_for_table(src: sqlite3.Connection, pg: asyncpg.Connection, table: str) -> int:
    """Create placeholder users for orphan user_id references in sqlite."""
    cols = [name for name, _ in _table_columns(src, table)]
    if table == "users" or "user_id" not in cols:
        return 0
    cur = src.execute(
        f"""
        SELECT DISTINCT t.user_id
        FROM {table} t
        LEFT JOIN users u ON u.user_id = t.user_id
        WHERE t.user_id IS NOT NULL AND u.user_id IS NULL
        """
    )
    orphan_ids = [int(r[0]) for r in cur.fetchall()]
    if not orphan_ids:
        return 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sql = (
        "INSERT INTO users (user_id, username, credits, created_at) "
        "VALUES ($1, $2, 0, $3) "
        "ON CONFLICT (user_id) DO NOTHING"
    )
    async with pg.transaction():
        for uid in orphan_ids:
            await pg.execute(sql, uid, f"migrated_orphan_{uid}", now_utc)
    return len(orphan_ids)


async def _validate_counts(src: sqlite3.Connection, pg: asyncpg.Connection, tables: list[str]) -> list[str]:
    report: list[str] = []
    for table in tables:
        src_n = int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        pg_n = int(await pg.fetchval(f"SELECT COUNT(*) FROM {table}") or 0)
        status = "OK" if src_n == pg_n else "MISMATCH"
        report.append(f"{table}: sqlite={src_n}, postgres={pg_n} [{status}]")
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate data from SQLite to PostgreSQL.")
    parser.add_argument("--sqlite-path", required=True, help="Path to sqlite file.")
    parser.add_argument("--postgres-url", required=True, help="PostgreSQL DSN.")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size per table.")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite DB not found: {sqlite_path}")

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = None
    tables = _table_names(src)

    pg = await asyncpg.connect(args.postgres_url)
    try:
        print(f"Tables to migrate: {len(tables)}")
        for table in tables:
            orphan_seeded = await _seed_orphan_users_for_table(src, pg, table)
            if orphan_seeded:
                print(f"{table}: seeded orphan users={orphan_seeded}")
            copied, total = await _copy_table(src, pg, table, max(1, int(args.batch_size)))
            print(f"{table}: copied {copied}/{total}")
        print("\nValidation (critical tables):")
        report = await _validate_counts(src, pg, sorted(t for t in tables if t in CRITICAL_TABLES))
        for line in report:
            print(line)
    finally:
        await pg.close()
        src.close()


if __name__ == "__main__":
    asyncio.run(main())

