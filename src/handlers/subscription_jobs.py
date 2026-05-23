"""Фоновые напоминания об окончании подписки (за 3 и 1 сутки до конца)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import SUBSCRIPTION_REMINDER_CHECK_INTERVAL_MINUTES
from src.database import (
    list_subscription_reminder_candidates,
    mark_subscription_reminder_sent,
)
from src.formatting import HTML
from src.keyboards.callback_data import CB_MENU_PAY
from src.services.subscription_reminders import (
    should_send_subscription_reminder,
    subscription_expiry_reminder_html,
    subscription_reminder_kind_for_remaining_seconds,
)
from src.services.subscription_time import normalize_subscription_ends_at_value, parse_dt_utc

logger = logging.getLogger(__name__)

_REMINDER_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Продлить подписку",
                callback_data=CB_MENU_PAY,
            )
        ]
    ]
)


async def _process_subscription_reminders(bot: Bot) -> None:
    candidates = await list_subscription_reminder_candidates()
    now = datetime.now(timezone.utc)
    for row in candidates:
        ends_norm = normalize_subscription_ends_at_value(row.subscription_ends_at)
        if not ends_norm:
            continue
        try:
            seconds_left = (parse_dt_utc(ends_norm) - now).total_seconds()
        except (ValueError, TypeError):
            continue
        kind = subscription_reminder_kind_for_remaining_seconds(seconds_left)
        if kind is None:
            continue
        if not should_send_subscription_reminder(
            ends_at=row.subscription_ends_at,
            kind=kind,
            remind_3d_for=row.subscription_remind_3d_for,
            remind_1d_for=row.subscription_remind_1d_for,
        ):
            continue
        text = subscription_expiry_reminder_html(
            plan_id=row.subscription_plan,
            ends_at=row.subscription_ends_at,
            kind=kind,
        )
        try:
            await bot.send_message(
                row.user_id,
                text,
                parse_mode=HTML,
                reply_markup=_REMINDER_KEYBOARD,
            )
        except TelegramForbiddenError:
            logger.info(
                "subscription reminder: user %s blocked bot (%s)",
                row.user_id,
                kind,
            )
            await mark_subscription_reminder_sent(
                row.user_id, kind=kind, ends_at=row.subscription_ends_at
            )
            continue
        except TelegramBadRequest as exc:
            logger.warning(
                "subscription reminder send failed uid=%s kind=%s: %s",
                row.user_id,
                kind,
                exc,
            )
            continue
        except Exception:
            logger.exception(
                "subscription reminder failed uid=%s kind=%s",
                row.user_id,
                kind,
            )
            continue
        await mark_subscription_reminder_sent(
            row.user_id, kind=kind, ends_at=row.subscription_ends_at
        )
        logger.info(
            "subscription reminder sent uid=%s kind=%s ends=%s",
            row.user_id,
            kind,
            row.subscription_ends_at,
        )


async def run_subscription_reminder_jobs(bot: Bot) -> None:
    """Запускать через asyncio.create_task из bot.main."""

    async def loop() -> None:
        await asyncio.sleep(30)
        interval = SUBSCRIPTION_REMINDER_CHECK_INTERVAL_MINUTES * 60
        while True:
            try:
                await _process_subscription_reminders(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscription_reminder_loop")
            await asyncio.sleep(interval)

    asyncio.create_task(loop())
