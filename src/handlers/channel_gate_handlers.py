"""Кнопка «Проверить подписку» после первого /start."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.handlers.commands import deliver_post_start_experience
from src.keyboards.callback_data import CB_CHANNEL_GATE_CHECK
from src.database import mark_user_channel_gate_passed
from src.services.channel_gate import (
    channel_gate_active,
    needs_channel_gate,
    user_is_channel_subscriber,
)

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == CB_CHANNEL_GATE_CHECK)
async def channel_gate_check(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    user_id = callback.from_user.id
    if not channel_gate_active():
        await callback.answer("Проверка подписки отключена.", show_alert=True)
        return
    if not await needs_channel_gate(user_id):
        await callback.answer("Подписка уже подтверждена.", show_alert=True)
        await deliver_post_start_experience(
            callback.message,
            user_id=user_id,
            username=callback.from_user.username,
            bonus_note="",
        )
        return

    subscribed, err = await user_is_channel_subscriber(callback.message.bot, user_id)
    if err:
        await callback.answer(err, show_alert=True)
        return
    if not subscribed:
        await callback.answer("Вы не подписаны на канал!", show_alert=True)
        return

    await mark_user_channel_gate_passed(user_id)
    await callback.answer()
    logger.info("channel gate passed uid=%s", user_id)
    await callback.message.answer("Вы подписаны, хороших вам впечатлений!")
    await deliver_post_start_experience(
        callback.message,
        user_id=user_id,
        username=callback.from_user.username,
        bonus_note="",
    )
