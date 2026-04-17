"""Ограничение параллельных запросов к API генерации с приоритетом для Galaxy/Universe."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Максимум одновременных генераций (все пользователи).
_MAX_CONCURRENT: int = 5

_lock = asyncio.Lock()
_available: int = _MAX_CONCURRENT
_high_waiters: deque[asyncio.Future[None]] = deque()
_low_waiters: deque[asyncio.Future[None]] = deque()


async def _acquire(*, priority: bool) -> None:
    loop = asyncio.get_running_loop()
    async with _lock:
        global _available
        if _available > 0:
            _available -= 1
            return
        fut: asyncio.Future[None] = loop.create_future()
        (_high_waiters if priority else _low_waiters).append(fut)
    await fut


async def _release_async() -> None:
    async with _lock:
        global _available
        if _high_waiters:
            _high_waiters.popleft().set_result(None)
            return
        if _low_waiters:
            _low_waiters.popleft().set_result(None)
            return
        _available += 1


@asynccontextmanager
async def image_generation_slot(*, priority: bool) -> AsyncIterator[None]:
    """priority=True — очередь обрабатывается раньше обычной (Galaxy/Universe)."""
    await _acquire(priority=priority)
    try:
        yield
    finally:
        await _release_async()
