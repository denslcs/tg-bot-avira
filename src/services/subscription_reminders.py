"""Тексты и логика напоминаний об окончании подписки."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from src.formatting import (
    PROFILE_SUBSCRIPTION_LABEL_TG_HTML,
    PROFILE_VALID_UNTIL_LABEL_TG_HTML,
    esc,
    format_subscription_ends_at,
    plan_subscription_title_html,
)
from src.services.subscription_time import (
    normalize_subscription_ends_at_value,
    parse_dt_utc,
    subscription_is_active,
)

ReminderKind = Literal["3d", "1d"]

# Окна в часах (UTC): одно напоминание за 3 суток до конца, одно — за 1 сутки.
_REMINDER_3D_MIN_HOURS = 48
_REMINDER_3D_MAX_HOURS = 72
_REMINDER_1D_MIN_HOURS = 0
_REMINDER_1D_MAX_HOURS = 24


def subscription_reminder_kind_for_remaining_seconds(seconds_left: float) -> ReminderKind | None:
    """Какое напоминание сейчас уместно (или None)."""
    if seconds_left <= 0:
        return None
    hours = seconds_left / 3600.0
    if _REMINDER_3D_MIN_HOURS < hours <= _REMINDER_3D_MAX_HOURS:
        return "3d"
    if _REMINDER_1D_MIN_HOURS < hours <= _REMINDER_1D_MAX_HOURS:
        return "1d"
    return None


def should_send_subscription_reminder(
    *,
    ends_at: str,
    kind: ReminderKind,
    remind_3d_for: str | None,
    remind_1d_for: str | None,
) -> bool:
    """
    Одно сообщение «за 3 дня» и одно «за 1 день» на каждый subscription_ends_at.
    Если пользователь продлил заранее — дата сдвинулась, старые отметки не совпадают → не шлём лишнее.
    """
    ends_norm = normalize_subscription_ends_at_value(ends_at)
    if not ends_norm or not subscription_is_active(ends_norm):
        return False
    try:
        end_dt = parse_dt_utc(ends_norm)
    except (ValueError, TypeError):
        return False
    seconds_left = (end_dt - datetime.now(timezone.utc)).total_seconds()
    if subscription_reminder_kind_for_remaining_seconds(seconds_left) != kind:
        return False
    if kind == "3d":
        sent_for = normalize_subscription_ends_at_value(remind_3d_for)
        return sent_for != ends_norm
    sent_for = normalize_subscription_ends_at_value(remind_1d_for)
    return sent_for != ends_norm


def subscription_expiry_reminder_html(
    *,
    plan_id: str | None,
    ends_at: str,
    kind: ReminderKind,
) -> str:
    title = plan_subscription_title_html(plan_id) if plan_id else "подписка"
    end_h = format_subscription_ends_at(ends_at)
    if kind == "3d":
        when = "через <b>3 дня</b>"
    else:
        when = "<b>завтра</b>"
    return (
        f"{PROFILE_SUBSCRIPTION_LABEL_TG_HTML} <b>Подписка скоро закончится</b>\n\n"
        f"<blockquote>"
        f"<i>Тариф:</i> <b>{title}</b>\n"
        f"<i>{PROFILE_VALID_UNTIL_LABEL_TG_HTML} Действует до:</i> <b>{esc(end_h)}</b>\n\n"
        f"<i>Срок истекает {when}. Продли заранее — доступ и лимиты сохранятся без паузы.</i>"
        f"</blockquote>"
    )
