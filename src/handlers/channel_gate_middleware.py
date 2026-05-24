"""Блокировка бота до одноразового прохождения gate подписки на канал."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.keyboards.callback_data import CB_CHANNEL_GATE_CHECK
from src.services.channel_gate import channel_gate_active, needs_channel_gate, send_channel_gate_screen


class ChannelGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not channel_gate_active():
            return await handler(event, data)

        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            text = (event.text or event.caption or "").strip().lower()
            if text.startswith("/start"):
                return await handler(event, data)
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
            if event.data == CB_CHANNEL_GATE_CHECK:
                return await handler(event, data)

        if user_id is None:
            return await handler(event, data)

        if not await needs_channel_gate(user_id):
            return await handler(event, data)

        bot = data.get("bot")
        chat_id: int | None = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id

        if bot is not None and chat_id is not None:
            await send_channel_gate_screen(bot, chat_id)

        if isinstance(event, CallbackQuery):
            await event.answer("Вы не подписаны на канал!", show_alert=True)
            return None

        return None
