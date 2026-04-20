"""
Один актуальный inline-интерфейс на чат: перед новой панелью снимаем inline-клавиатуры
со всех ранее отправленных ботом сообщений (кроме правки того же сообщения на месте).

ReplyKeyboard (нижняя панель) не трогаем — только InlineKeyboardMarkup.

В aiogram 3 исходящие запросы идут через ``await bot(TelegramMethod)``, поэтому перехват
делается в ``Bot.__call__``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from aiogram import Bot
from aiogram.methods import (
    CopyMessage,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

_locks: dict[Any, asyncio.Lock] = defaultdict(asyncio.Lock)
_tracked: dict[Any, list[int]] = defaultdict(list)


def _is_inline_markup(markup: Any) -> bool:
    return isinstance(markup, InlineKeyboardMarkup)


async def relinquish_inline_panels_except(
    bot: Bot,
    chat_id: Any,
    *,
    keep_message_id: int | None = None,
) -> None:
    """Снять inline-клавиатуры со всех отслеживаемых сообщений, кроме ``keep_message_id``."""
    key = chat_id
    async with _locks[key]:
        mids = list(_tracked.get(key, []))
        for mid in mids:
            if keep_message_id is not None and mid == keep_message_id:
                continue
            try:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=mid, reply_markup=None)
            except Exception:
                logger.debug(
                    "relinquish_inline_panels_except: strip failed chat=%s mid=%s",
                    chat_id,
                    mid,
                    exc_info=True,
                )
        if keep_message_id is not None:
            _tracked[key] = [keep_message_id]
        else:
            _tracked[key] = []


def remember_inline_panel_message(chat_id: Any, message_id: int) -> None:
    _tracked[chat_id] = [message_id]


def _forget_message(chat_id: Any, message_id: int) -> None:
    key = chat_id
    if key not in _tracked:
        return
    _tracked[key] = [m for m in _tracked[key] if m != message_id]
    if not _tracked[key]:
        del _tracked[key]


def apply_exclusive_inline_panels() -> None:
    """Один раз патчит ``Bot.__call__`` для всех экземпляров :class:`aiogram.Bot`."""
    if getattr(Bot, "_exclusive_inline_panels_applied", False):
        return
    setattr(Bot, "_exclusive_inline_panels_applied", True)

    orig_call = Bot.__call__

    async def __call__(self: Bot, method: TelegramMethod, request_timeout: int | None = None):
        # --- до запроса к API
        if isinstance(method, (SendMessage, SendPhoto)):
            if _is_inline_markup(method.reply_markup):
                await relinquish_inline_panels_except(self, method.chat_id, keep_message_id=None)
        elif isinstance(method, CopyMessage):
            if _is_inline_markup(method.reply_markup):
                await relinquish_inline_panels_except(self, method.chat_id, keep_message_id=None)
        elif isinstance(method, EditMessageCaption):
            if (
                method.chat_id is not None
                and method.message_id is not None
                and not method.inline_message_id
                and _is_inline_markup(method.reply_markup)
            ):
                await relinquish_inline_panels_except(
                    self, method.chat_id, keep_message_id=method.message_id
                )
        elif isinstance(method, EditMessageText):
            if (
                method.chat_id is not None
                and method.message_id is not None
                and not method.inline_message_id
                and _is_inline_markup(method.reply_markup)
            ):
                await relinquish_inline_panels_except(
                    self, method.chat_id, keep_message_id=method.message_id
                )
        elif isinstance(method, EditMessageMedia):
            if (
                method.chat_id is not None
                and method.message_id is not None
                and not method.inline_message_id
                and _is_inline_markup(method.reply_markup)
            ):
                await relinquish_inline_panels_except(
                    self, method.chat_id, keep_message_id=method.message_id
                )
        elif isinstance(method, EditMessageReplyMarkup):
            if (
                method.chat_id is not None
                and method.message_id is not None
                and _is_inline_markup(method.reply_markup)
            ):
                await relinquish_inline_panels_except(
                    self, method.chat_id, keep_message_id=method.message_id
                )

        result = await orig_call(self, method, request_timeout=request_timeout)

        # --- после успешного ответа
        if isinstance(method, SendMessage) and _is_inline_markup(method.reply_markup):
            remember_inline_panel_message(method.chat_id, result.message_id)
        elif isinstance(method, SendPhoto) and _is_inline_markup(method.reply_markup):
            remember_inline_panel_message(method.chat_id, result.message_id)
        elif isinstance(method, CopyMessage) and _is_inline_markup(method.reply_markup):
            remember_inline_panel_message(method.chat_id, result.message_id)
        elif isinstance(method, EditMessageCaption) and (
            method.chat_id is not None
            and method.message_id is not None
            and not method.inline_message_id
            and _is_inline_markup(method.reply_markup)
        ):
            remember_inline_panel_message(method.chat_id, method.message_id)
        elif isinstance(method, EditMessageText) and (
            method.chat_id is not None
            and method.message_id is not None
            and not method.inline_message_id
            and _is_inline_markup(method.reply_markup)
        ):
            remember_inline_panel_message(method.chat_id, method.message_id)
        elif isinstance(method, EditMessageMedia) and (
            method.chat_id is not None
            and method.message_id is not None
            and not method.inline_message_id
            and _is_inline_markup(method.reply_markup)
        ):
            remember_inline_panel_message(method.chat_id, method.message_id)
        elif isinstance(method, EditMessageReplyMarkup) and method.chat_id is not None and method.message_id is not None:
            if method.reply_markup is None:
                _forget_message(method.chat_id, method.message_id)
            elif _is_inline_markup(method.reply_markup):
                remember_inline_panel_message(method.chat_id, method.message_id)

        return result

    Bot.__call__ = __call__
