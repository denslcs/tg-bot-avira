"""Общие настройки разметки Telegram (HTML)."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from aiogram.enums import ParseMode

HTML = ParseMode.HTML


def esc(value: str | int | float) -> str:
    """Экранирование для вставки в HTML-сообщения."""
    return html.escape(str(value), quote=False)


def format_subscription_ends_at(iso_str: str | None, *, default: str = "—") -> str:
    """Дата окончания подписки для людей: ДД.ММ.ГГГГ ЧЧ:ММ UTC."""
    text = (iso_str or "").strip()
    if text.lower() in ("none", "null"):
        return default
    if not text:
        return default
    dt: datetime | None = None
    for candidate in (text.replace("Z", "+00:00"), text):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if dt is None:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")
