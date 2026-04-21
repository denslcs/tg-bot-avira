from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from src.subscription_catalog import SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS


def parse_dt_utc(raw: str | datetime) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    s = str(raw).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_subscription_ends_at_value(raw: object | None) -> str | None:
    """Значение subscription_ends_at из БД: в единый ISO UTC str или None."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    if isinstance(raw, (bytes, bytearray)):
        try:
            s = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        return s if s else None
    s = str(raw).strip()
    return s if s else None


def subscription_is_active(ends_at: object | None) -> bool:
    normalized = normalize_subscription_ends_at_value(ends_at)
    if not normalized:
        return False
    try:
        return parse_dt_utc(normalized) > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def subscription_cooldown_days_remaining(last_purchase_iso: str | None) -> int:
    """0 — можно оформить новую подписку; иначе оценка дней до следующей покупки."""
    raw = normalize_subscription_ends_at_value(last_purchase_iso)
    if not raw:
        return 0
    try:
        dt = parse_dt_utc(raw)
    except (ValueError, TypeError):
        return 0
    now = datetime.now(timezone.utc)
    end = dt + timedelta(days=SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS)
    if now >= end:
        return 0
    left = end - now
    return max(1, math.ceil(left.total_seconds() / 86400))


def is_within_subscription_renewal_grace(ends_at: object | None, *, grace_days: int = 2) -> bool:
    """True, если подписка уже закончилась, но не более grace_days назад (UTC)."""
    raw = normalize_subscription_ends_at_value(ends_at)
    if not raw:
        return False
    try:
        end_dt = parse_dt_utc(raw)
    except (ValueError, TypeError):
        return False
    now = datetime.now(timezone.utc)
    if now <= end_dt:
        return False
    return now <= end_dt + timedelta(days=max(0, int(grace_days)))

