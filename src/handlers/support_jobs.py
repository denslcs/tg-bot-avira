"""Фоновые задачи support-бота: SLA-алерты и еженедельная сводка."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from src.config import (
    SLA_ALERT_INTERVAL_MINUTES,
    SLA_WARNING_HOURS,
    SUPPORT_CHAT_ID,
    WEEKLY_REPORT_HOUR_UTC,
    WEEKLY_REPORT_WEEKDAY,
)
from src.database import (
    count_new_users_days,
    count_open_tickets,
    get_meta,
    get_support_rating_rollups,
    list_open_tickets_sla_rows,
    set_meta,
)

logger = logging.getLogger(__name__)


def _hours_waiting(created_at: str, first_reply: str | None) -> tuple[float, bool]:
    """Возраст тикета в часах; bool — есть ли ответ пользователю."""
    try:
        c = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - c).total_seconds() / 3600.0
    except ValueError:
        age = 0.0
    return age, first_reply is not None


def _iso_week_key() -> str:
    d = datetime.now(timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


async def _send_weekly_report(bot: Bot) -> None:
    if SUPPORT_CHAT_ID == 0:
        return
    now = datetime.now(timezone.utc)
    if now.weekday() != WEEKLY_REPORT_WEEKDAY or now.hour != WEEKLY_REPORT_HOUR_UTC:
        return
    week_key = _iso_week_key()
    if await get_meta("weekly_report_week") == week_key:
        return

    new_u = await count_new_users_days(7)
    open_n = await count_open_tickets()
    avg, n_ratings = await get_support_rating_rollups()
    avg_s = f"{avg:.2f}" if avg is not None else "—"
    text = (
        "📊 Еженедельная сводка Avira\n\n"
        f"Новых пользователей за 7 дней: {new_u}\n"
        f"Открытых тикетов сейчас: {open_n}\n"
        f"Оценок поддержки всего: {n_ratings} (средняя {avg_s} / 5)\n\n"
        f"Неделя (ISO): {week_key}"
    )
    try:
        await bot.send_message(chat_id=SUPPORT_CHAT_ID, text=text)
        await set_meta("weekly_report_week", week_key)
    except Exception:
        logger.exception("weekly report failed")


async def _send_sla_reminder(bot: Bot) -> None:
    if SUPPORT_CHAT_ID == 0:
        return
    rows = await list_open_tickets_sla_rows()
    stale: list[str] = []
    for t in rows:
        if t.first_admin_reply_at:
            continue
        age, _ = _hours_waiting(t.created_at, t.first_admin_reply_at)
        if age >= SLA_WARNING_HOURS:
            tag = f"[{t.tag}] " if t.tag else ""
            stale.append(
                f"#{t.ticket_id} {tag}user {t.user_id} — без ответа ~{age:.1f} ч (создан {t.created_at})"
            )
    if not stale:
        return
    body = "\n".join(stale[:25])
    if len(stale) > 25:
        body += f"\n… и ещё {len(stale) - 25}"
    text = (
        f"⏱ SLA: нет первого ответа пользователю дольше {SLA_WARNING_HOURS} ч:\n\n"
        f"{body}\n\n"
        "/sla — полный список открытых тикетов"
    )
    try:
        await bot.send_message(chat_id=SUPPORT_CHAT_ID, text=text[:4000])
    except Exception:
        logger.exception("sla reminder failed")


async def run_support_background_jobs(bot: Bot) -> None:
    """Запускать через asyncio.create_task из support_bot.main."""
    async def sla_loop() -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await _send_sla_reminder(bot)
                await asyncio.sleep(max(60, SLA_ALERT_INTERVAL_MINUTES * 60))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sla_loop")

    async def weekly_loop() -> None:
        while True:
            try:
                await asyncio.sleep(3600)
                await _send_weekly_report(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("weekly_loop")

    asyncio.create_task(sla_loop())
    asyncio.create_task(weekly_loop())


async def build_weekly_report_text() -> str:
    """Ручной вызов /report."""
    new_u = await count_new_users_days(7)
    open_n = await count_open_tickets()
    avg, n_ratings = await get_support_rating_rollups()
    avg_s = f"{avg:.2f}" if avg is not None else "—"
    return (
        "📊 Сводка (сейчас)\n\n"
        f"Новых пользователей за 7 дней: {new_u}\n"
        f"Открытых тикетов: {open_n}\n"
        f"Оценок поддержки: {n_ratings} (средняя {avg_s} / 5)"
    )
