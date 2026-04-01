"""Общие настройки разметки Telegram (HTML)."""

from __future__ import annotations

import html

from aiogram.enums import ParseMode

HTML = ParseMode.HTML


def esc(value: str | int | float) -> str:
    """Экранирование для вставки в HTML-сообщения."""
    return html.escape(str(value), quote=False)
