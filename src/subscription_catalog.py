"""Тарифы подписки на генерацию изображений: лимиты в месяц (UTC), цены для UI.

Курсы (опорная точка как в референсе Nova): 159 ₽ ≈ 150 ⭐ ≈ $1.99
→ 1 ⭐ ≈ 1.059 ₽; 1 $ ≈ 79.90 ₽ (плавающие, ориентир для отображения).

Кредиты за покупку подписки (начисляются при успешной оплате в боте):
сбалансированы так, чтобы давать запас на текстовый чат и будущие «дорогие»
модели (Pro и т.д.), не подменяя месячный лимит генераций картинок.

Позже сюда же логично добавить allowlist моделей по тарифу (дешевле → проще
генераторы, дороже → Nano Banana Pro и аналоги), и в img_commands фильтровать
выбор по subscription_plan.
"""

from __future__ import annotations

from dataclasses import dataclass


# Без оплаченной подписки (пока есть/нет кредитов — не важно): лимит генераций картинок в календарном месяце UTC.
FREE_MONTHLY_IMAGE_GENERATIONS: int = 5

# Ровно 30 календарных дней с момента оплаты (или продления от текущего срока).
SUBSCRIPTION_PERIOD_DAYS: int = 30

# Опорный курс для пересчёта (Nova).
_REF_RUB: float = 159.0
_REF_STARS: int = 150
_REF_USD: float = 1.99


def stars_from_rub(rub: int) -> int:
    return max(1, int(round(rub * _REF_STARS / _REF_RUB)))


def usd_from_rub(rub: int) -> float:
    return round(rub * _REF_USD / _REF_RUB, 2)


@dataclass(frozen=True)
class SubscriptionPlan:
    id: str
    title: str
    monthly_generations: int
    price_rub: int
    # Разовый бонус на баланс при покупке этой подписки (см. successful_payment).
    bonus_credits: int

    @property
    def stars(self) -> int:
        return stars_from_rub(self.price_rub)

    @property
    def price_usd(self) -> float:
        return usd_from_rub(self.price_rub)


PLANS_ORDER: tuple[str, ...] = ("nova", "supernova", "galaxy", "universe")

PLANS: dict[str, SubscriptionPlan] = {
    "nova": SubscriptionPlan(
        id="nova",
        title="Nova",
        monthly_generations=20,
        price_rub=159,
        bonus_credits=90,
    ),
    "supernova": SubscriptionPlan(
        id="supernova",
        title="SuperNova",
        monthly_generations=40,
        price_rub=399,
        bonus_credits=220,
    ),
    "galaxy": SubscriptionPlan(
        id="galaxy",
        title="Galaxy",
        monthly_generations=90,
        price_rub=699,
        bonus_credits=500,
    ),
    "universe": SubscriptionPlan(
        id="universe",
        title="Universe",
        monthly_generations=270,
        price_rub=1399,
        bonus_credits=1050,
    ),
}


def plan_limit_generations(plan_id: str | None, subscription_active: bool) -> int:
    if not subscription_active:
        return FREE_MONTHLY_IMAGE_GENERATIONS
    if not plan_id or plan_id not in PLANS:
        return PLANS["nova"].monthly_generations
    return PLANS[plan_id].monthly_generations
