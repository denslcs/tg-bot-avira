"""
Глобальный перехват необработанных исключений в хендлерах: полный лог в stderr,
пользователю — нейтральный текст; процесс бота не завершается из‑за одного сбоя.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

# Единый нейтральный ответ (без технических деталей).
USER_GENERIC_ERROR = (
    "Что-то пошло не так. Попробуй ещё раз позже или открой главное меню командой /start."
)

CALLBACK_ERROR_ALERT = "Не получилось выполнить действие. Попробуй позже."

PRE_CHECKOUT_UNAVAILABLE = "Сервис временно недоступен. Попробуй позже."


async def _safe_answer_message_chat(bot: Bot, chat_id: int, *, thread_id: int | None = None) -> None:
    try:
        await bot.send_message(chat_id, USER_GENERIC_ERROR, message_thread_id=thread_id)
    except TelegramBadRequest:
        logger.debug("Could not send generic error to chat_id=%s", chat_id, exc_info=True)
    except Exception:
        logger.warning("Could not send generic error to chat_id=%s", chat_id, exc_info=True)


async def global_error_handler(event: ErrorEvent, bot: Bot, **kwargs: Any) -> bool:
    update_id = getattr(event.update, "update_id", None)
    logger.error(
        "Unhandled handler exception (update_id=%s): %s",
        update_id,
        event.exception,
        exc_info=event.exception,
    )
    u = event.update

    try:
        if u.message and u.message.chat:
            try:
                await u.message.answer(USER_GENERIC_ERROR)
            except TelegramBadRequest:
                await _safe_answer_message_chat(bot, u.message.chat.id)
        elif u.callback_query:
            cq = u.callback_query
            try:
                await cq.answer(CALLBACK_ERROR_ALERT, show_alert=True)
            except TelegramBadRequest:
                logger.debug("callback.answer failed after error", exc_info=True)
            if cq.message and cq.message.chat:
                try:
                    await cq.message.answer(USER_GENERIC_ERROR)
                except TelegramBadRequest:
                    await _safe_answer_message_chat(
                        bot,
                        cq.message.chat.id,
                        thread_id=cq.message.message_thread_id,
                    )
        elif u.edited_message and u.edited_message.chat:
            try:
                await u.edited_message.answer(USER_GENERIC_ERROR)
            except TelegramBadRequest:
                await _safe_answer_message_chat(bot, u.edited_message.chat.id)
        elif u.pre_checkout_query:
            try:
                await u.pre_checkout_query.answer(ok=False, error_message=PRE_CHECKOUT_UNAVAILABLE)
            except TelegramBadRequest:
                logger.debug("pre_checkout_query.answer failed after error", exc_info=True)
    except Exception:
        logger.exception("Failed to notify user after global error handler")

    return True


def register_global_error_handler(dp: Dispatcher) -> None:
    dp.errors.register(global_error_handler)
