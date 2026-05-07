from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

_BUSY_TIMEOUT_MS = 8000


@asynccontextmanager
async def open_sqlite(path: str) -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(path) as db:
        await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        yield db

