"""Общие настройки разметки Telegram (HTML)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from aiogram.enums import ParseMode

from src.subscription_catalog import PLANS, PLANS_ORDER, PLAN_PREMIUM_EMOJI_FALLBACK, PLAN_PREMIUM_EMOJI_IDS

HTML = ParseMode.HTML

CREDITS_COIN_TG_HTML = '<tg-emoji emoji-id="5382164415019768638">🪙</tg-emoji>'


def esc(value: str | int | float) -> str:
    """Экранирование для вставки в HTML-сообщения."""
    return html.escape(str(value), quote=False)


def html_escape_preserve_tg_emoji(text: str) -> str:
    """Экранирует HTML, но оставляет теги <tg-emoji>...</tg-emoji> без изменений."""
    if "<tg-emoji" not in text:
        return esc(text)
    pattern = re.compile(r"(<tg-emoji\b[^>]*>.*?</tg-emoji>)", re.DOTALL)
    return "".join(
        part if part.startswith("<tg-emoji") else esc(part) for part in pattern.split(text)
    )


def plan_subscription_title_html(plan_id: str) -> str:
    """Премиум-эмодзи тарифа + название (Starter, Nova, …) для HTML."""
    pid = (plan_id or "").strip().lower()
    if pid not in PLANS:
        return esc(plan_id or "—")
    raw_title = PLANS[pid].title
    title_wo_emoji = raw_title.split(" ", 1)[-1]
    emoji_id = PLAN_PREMIUM_EMOJI_IDS.get(pid)
    if not emoji_id:
        return esc(raw_title)
    fb = PLAN_PREMIUM_EMOJI_FALLBACK.get(pid, "⭐")
    return f'<tg-emoji emoji-id="{emoji_id}">{fb}</tg-emoji> {esc(title_wo_emoji)}'


def plans_premium_sequence_html(plan_ids: list[str], *, sep: str = ", ") -> str:
    """Несколько тарифов подряд с премиум-эмодзи (HTML)."""
    parts: list[str] = []
    for raw in plan_ids:
        pid = (raw or "").strip().lower()
        if pid in PLANS:
            parts.append(plan_subscription_title_html(pid))
    return sep.join(parts)


def full_plans_after_starter_html(*, sep: str = ", ") -> str:
    """Nova, SuperNova, Galaxy, Universe с премиум-эмодзи."""
    return plans_premium_sequence_html(["nova", "supernova", "galaxy", "universe"], sep=sep)


def all_plans_premium_line_html(*, sep: str = " · ") -> str:
    """Все тарифы каталога по порядку."""
    return plans_premium_sequence_html(list(PLANS_ORDER), sep=sep)


def starter_already_purchased_message_html() -> str:
    """Сообщение «Starter уже покупали» для HTML."""
    return (
        f"Вы уже оформляли пробную подписку {plan_subscription_title_html('starter')} — купить её повторно нельзя.\n\n"
        f"<blockquote>Выбери полный тариф: {full_plans_after_starter_html(sep=', ')} в разделе "
        "<code>/start</code> → <b>Оплатить</b>.</blockquote>"
    )


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
