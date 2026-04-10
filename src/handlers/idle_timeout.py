from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.handlers.commands import restore_main_menu_message

_IDLE_TIMEOUT_SECONDS = 600
_LAST_ACTIVITY_TS: dict[int, float] = {}


class UserIdleMiddleware(BaseMiddleware):
    """Глобальный idle-контроль: если пользователь молчал >10 минут, сбрасываем flow в /start-меню."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        username: str | None = None
        message: Message | None = None
        is_start_command = False

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            username = event.from_user.username
            message = event
            text = (event.text or event.caption or "").strip().lower()
            is_start_command = text.startswith("/start")
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
            username = event.from_user.username
            message = event.message

        if user_id is None:
            return await handler(event, data)

        now = time.time()
        prev = _LAST_ACTIVITY_TS.get(user_id)
        _LAST_ACTIVITY_TS[user_id] = now

        if prev is not None and (now - prev) > _IDLE_TIMEOUT_SECONDS and not is_start_command:
            state = data.get("state")
            if isinstance(state, FSMContext):
                await state.clear()
            if message is not None:
                await restore_main_menu_message(message, user_id, username)
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None

        return await handler(event, data)

