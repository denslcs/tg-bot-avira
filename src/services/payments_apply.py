from __future__ import annotations

import logging
from dataclasses import dataclass

from src.database import (
    add_budget_history_event,
    add_credits_with_reason,
    extend_subscription,
    get_user_admin_profile,
    is_within_subscription_renewal_grace,
    mark_starter_trial_purchased,
    queue_subscription_bonus_credits,
    record_subscription_purchase_now,
    reset_subscription_days,
    subscription_is_active,
)
from src.subscription_catalog import PLANS

logger = logging.getLogger(__name__)


def repeat_plan_bonus_extra_credits(*, plan_id: str, base_credits: int, renewal_eligible: bool) -> int:
    """Бонус за продление того же тарифа: +5%; для Universe всегда +10%."""
    if base_credits <= 0 or not renewal_eligible:
        return 0
    if plan_id == "universe":
        return int(base_credits * 0.10)
    return int(base_credits * 0.05)


@dataclass
class PlanPurchaseApplyResult:
    period_days: int
    new_end: str
    total_bonus_credits: int
    renewal_extra: int
    had_active_renewal: bool
    same_plan_repeat: bool
    renewal_bonus_eligible: bool
    renewal_release_at: str | None
    credited: bool
    sub_active_ok: bool


async def apply_plan_purchase_from_stars(*, user_id: int, item_id: str) -> PlanPurchaseApplyResult | None:
    """
    Применяет покупку тарифа атомами бизнес-логики (без claim/payment валидации).
    Возвращает None только если не удалось записать новый срок подписки.
    """
    p = PLANS[item_id]
    prof_before = await get_user_admin_profile(user_id)
    prev_plan_id = (prof_before.subscription_plan or "").strip().lower() if prof_before else ""
    same_plan_repeat = bool(prev_plan_id and prev_plan_id == item_id)
    had_active_renewal = bool(
        prof_before
        and subscription_is_active(prof_before.subscription_ends_at)
        and item_id != "starter"
    )
    in_grace_renewal_window = bool(
        prof_before
        and item_id != "starter"
        and is_within_subscription_renewal_grace(prof_before.subscription_ends_at, grace_days=2)
    )
    renewal_bonus_eligible = bool(
        item_id != "starter" and same_plan_repeat and (had_active_renewal or in_grace_renewal_window)
    )
    renewal_extra = (
        repeat_plan_bonus_extra_credits(
            plan_id=item_id,
            base_credits=p.bonus_credits,
            renewal_eligible=renewal_bonus_eligible,
        )
        if renewal_bonus_eligible
        else 0
    )
    renewal_release_at: str | None = None
    if item_id == "starter":
        new_end = await reset_subscription_days(user_id, p.period_days, item_id)
    elif had_active_renewal:
        renewal_release_at = str(prof_before.subscription_ends_at or "").strip() or None
        new_end = await extend_subscription(user_id, p.period_days, item_id)
    else:
        new_end = await reset_subscription_days(user_id, p.period_days, item_id)
    if not new_end:
        return None
    if item_id == "starter":
        await mark_starter_trial_purchased(user_id)
    else:
        await record_subscription_purchase_now(user_id)
    total_bonus_credits = p.bonus_credits + renewal_extra
    prof_verify = await get_user_admin_profile(user_id)
    sub_active_ok = bool(
        prof_verify and subscription_is_active(prof_verify.subscription_ends_at)
    )
    if not sub_active_ok:
        logger.error(
            "Stars plan purchase: subscription still inactive after DB write uid=%s new_end=%s profile_end=%s",
            user_id,
            new_end,
            getattr(prof_verify, "subscription_ends_at", None),
        )
    if had_active_renewal and renewal_release_at:
        credited = await queue_subscription_bonus_credits(
            user_id,
            total_bonus_credits,
            release_at_utc=renewal_release_at,
            details=f"plan {item_id} renewal bonus",
        )
    else:
        credited = await add_credits_with_reason(
            user_id,
            total_bonus_credits,
            source="subscription_bonus",
            details=f"plan {item_id}" + (" renewal" if renewal_extra else ""),
        )
    await add_budget_history_event(
        user_id,
        source="subscription_purchase",
        details=f"plan {item_id}",
        delta=0,
    )
    return PlanPurchaseApplyResult(
        period_days=p.period_days,
        new_end=new_end,
        total_bonus_credits=total_bonus_credits,
        renewal_extra=renewal_extra,
        had_active_renewal=had_active_renewal,
        same_plan_repeat=same_plan_repeat,
        renewal_bonus_eligible=renewal_bonus_eligible,
        renewal_release_at=renewal_release_at,
        credited=credited,
        sub_active_ok=sub_active_ok,
    )

