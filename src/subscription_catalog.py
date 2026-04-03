"""Тарифы подписки: кредиты на баланс, цены ₽ / $ / ⭐ Telegram Stars.

Подписка даёт кредиты и срок доступа; лимитов на количество генераций для
подписчиков нет. Без подписки: не более NONSUB_IMAGE_WINDOW_MAX генераций картинок
за скользящие NONSUB_IMAGE_WINDOW_DAYS суток (UTC) — каждая со списанием кредитов;
после исчерпания лимита нужна подписка или ожидание сброса окна (кредиты не помогают).

Звёзды (XTR): опорная точка Nova — $2.99 → 225 ⭐ (согласовано с прежним
соотношением ~159 ₽ → 150 ⭐, масштаб по курсу Nova). Остальные тарифы:
round(usd / 2.99 * 225).

Ориентир по кредитам (зависит от OPENROUTER_IMAGE_COST_CREDITS и модели):
Nova 500, Supernova 1100, Galaxy 2400, Universe 5000 — на баланс при оплате подписки.
"""

from __future__ import annotations

from dataclasses import dataclass


# Без подписки: лимит генераций картинок за скользящее окно (UTC), со списанием кредитов.
NONSUB_IMAGE_WINDOW_DAYS: int = 30
NONSUB_IMAGE_WINDOW_MAX: int = 3

# Для подписчиков «безлимит» в счётчике суток (внутренний резерв в БД).
UNLIMITED_DAILY_IMAGE_GENERATIONS: int = 1_000_000_000

# Устарело: дневные лимиты бесплатного тарифа заменены окном 30 дней.
FREE_DAILY_SELF_IMAGE_GENERATIONS: int = 2
FREE_DAILY_READY_IMAGE_GENERATIONS: int = 4

# Ровно 30 календарных дней с момента оплаты (или продления от текущего срока).
SUBSCRIPTION_PERIOD_DAYS: int = 30


@dataclass(frozen=True)
class SubscriptionPlan:
    id: str
    title: str
    price_rub: int
    price_usd: float
    stars: int
    # Начисляется на баланс при успешной оплате подписки.
    bonus_credits: int


@dataclass(frozen=True)
class BonusPack:
    id: str
    title: str
    credits: int
    price_rub: int
    price_usd: float
    stars: int
    prompt_estimate: int


PLANS_ORDER: tuple[str, ...] = ("nova", "supernova", "galaxy", "universe")

PLANS: dict[str, SubscriptionPlan] = {
    "nova": SubscriptionPlan(
        id="nova",
        title="✨ Nova",
        price_rub=239,
        price_usd=2.99,
        stars=225,
        bonus_credits=500,
    ),
    "supernova": SubscriptionPlan(
        id="supernova",
        title="🌟 SuperNova",
        price_rub=399,
        price_usd=4.99,
        stars=376,
        bonus_credits=1100,
    ),
    "galaxy": SubscriptionPlan(
        id="galaxy",
        title="🌌 Galaxy",
        price_rub=799,
        price_usd=9.99,
        stars=752,
        bonus_credits=2400,
    ),
    "universe": SubscriptionPlan(
        id="universe",
        title="👾 Universe",
        price_rub=1449,
        price_usd=17.99,
        stars=1354,
        bonus_credits=5000,
    ),
}


BONUS_PACKS_ORDER: tuple[str, ...] = ("pack300", "pack500", "pack1000")

BONUS_PACKS: dict[str, BonusPack] = {
    "pack300": BonusPack(
        id="pack300",
        title="300 кредитов",
        credits=300,
        price_rub=299,
        price_usd=3.73,
        stars=281,
        prompt_estimate=15,
    ),
    "pack500": BonusPack(
        id="pack500",
        title="500 кредитов",
        credits=500,
        price_rub=499,
        price_usd=6.22,
        stars=468,
        prompt_estimate=25,
    ),
    "pack1000": BonusPack(
        id="pack1000",
        title="1000 кредитов",
        credits=1000,
        price_rub=999,
        price_usd=12.45,
        stars=936,
        prompt_estimate=50,
    ),
}


def free_daily_generation_limit(usage_kind: str) -> int:
    if usage_kind == "ready":
        return FREE_DAILY_READY_IMAGE_GENERATIONS
    return FREE_DAILY_SELF_IMAGE_GENERATIONS


def daily_image_generation_limit(subscription_active: bool, usage_kind: str) -> int:
    if subscription_active:
        return UNLIMITED_DAILY_IMAGE_GENERATIONS
    # Без подписки дневной лимит в БД не используется (окно 30 дней в users).
    return free_daily_generation_limit(usage_kind)
