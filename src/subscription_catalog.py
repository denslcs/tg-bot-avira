"""Тарифы подписки: кредиты на баланс, цены ₽ / $ / ⭐ Telegram Stars.

Подписка даёт кредиты и срок доступа. Без подписки: не более NONSUB_IMAGE_WINDOW_MAX
генераций картинок за цикл; после полного исчерпания слотов следующий цикл через
NONSUB_IMAGE_WINDOW_DAYS суток от момента исчерпания (UTC, то же время суток).
Каждая генерация со списанием кредитов; кредиты не обходят лимит.

Звёзды (XTR): round(usd / 2.99 * 225) — та же шкала, что раньше (опорная точка Nova $2.99 → 225 ⭐).

Ориентир маржи (переменные затраты): типичный промпт = 30 кр., ~11 ₽ себестоимости;
при полном расходе бонусных кр. на такие промпты: затраты = (bonus_credits / 30) * 11 ₽.
Целевое соотношение выручка / эти затраты ≈ 1,5–1,7× (баланс с объёмом кредитов для пользователя).
Пакеты бонусов — без изменений.

Оценка валовой маржи по подпискам (выручка price_rub − затраты при полном расходе бонусных кр. на промпты 30 кр. × 11 ₽):
  Starter:   149 − 88   = +61 ₽   (~41% от выручки)
  Nova:      279 − 165  = +114 ₽  (~41%)
  SuperNova: 499 − 301⅓ = +198 ₽  (~40%)
  Galaxy:    929 − 568⅓ = +361 ₽  (~39%)
  Universe: 1799 − 1045 = +754 ₽  (~42%)
"""

from __future__ import annotations

from dataclasses import dataclass


# Без подписки: лимит картинок за цикл; сброс через NONSUB_IMAGE_WINDOW_DAYS после исчерпания (UTC).
NONSUB_IMAGE_WINDOW_DAYS: int = 30
NONSUB_IMAGE_WINDOW_MAX: int = 3
# Без подписки: «готовые идеи» — 1 слот за цикл; сброс через те же сутки после исчерпания.
NONSUB_READY_IDEA_WINDOW_MAX: int = 1

# Для подписчиков «готовые идеи» без лимита.
def ready_idea_daily_cap_for_plan(plan_id: str | None) -> int | None:
    return None

# Для подписчиков «безлимит» в счётчике суток (внутренний резерв в БД).
UNLIMITED_DAILY_IMAGE_GENERATIONS: int = 1_000_000_000

# Устарело: дневные лимиты бесплатного тарифа заменены окном 30 дней.
FREE_DAILY_SELF_IMAGE_GENERATIONS: int = 2
FREE_DAILY_READY_IMAGE_GENERATIONS: int = 4

# Ровно 30 календарных дней с момента оплаты (или продления от текущего срока).
SUBSCRIPTION_PERIOD_DAYS: int = 30
# Между двумя *оплаченными* подписками (Stars и т.д.) — не чаще чем раз в столько дней (анти-абуз).
SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS: int = SUBSCRIPTION_PERIOD_DAYS


@dataclass(frozen=True)
class SubscriptionPlan:
    id: str
    title: str
    price_rub: int
    price_usd: float
    stars: int
    # Начисляется на баланс при успешной оплате подписки.
    bonus_credits: int
    # Срок доступа в днях (полные тарифы — 30; пробный Starter — 3).
    period_days: int = 30
    # Legacy-поле (обратная совместимость).
    idea_tokens_on_purchase: int = 0


@dataclass(frozen=True)
class BonusPack:
    id: str
    title: str
    credits: int
    price_rub: int
    price_usd: float
    stars: int
    # Legacy-поле (обратная совместимость).
    idea_tokens: int = 0


PLANS_ORDER: tuple[str, ...] = ("starter", "nova", "supernova", "galaxy", "universe")

def _stars_from_usd(usd: float) -> int:
    """Та же формула, что в каталоге до смены: Nova $2.99 → 225 ⭐."""
    return max(1, round(usd / 2.99 * 225))


PLANS: dict[str, SubscriptionPlan] = {
    "starter": SubscriptionPlan(
        id="starter",
        title="🚀 Starter",
        price_rub=149,
        price_usd=1.86,
        stars=_stars_from_usd(1.86),
        bonus_credits=240,
        period_days=3,
    ),
    "nova": SubscriptionPlan(
        id="nova",
        title="✨ Nova",
        price_rub=299,
        price_usd=3.99,
        stars=289,
        bonus_credits=450,
    ),
    "supernova": SubscriptionPlan(
        id="supernova",
        title="🌟 SuperNova",
        price_rub=499,
        price_usd=6.59,
        stars=479,
        bonus_credits=820,
    ),
    "galaxy": SubscriptionPlan(
        id="galaxy",
        title="🌌 Galaxy",
        price_rub=999,
        price_usd=13.29,
        stars=969,
        bonus_credits=1550,
    ),
    "universe": SubscriptionPlan(
        id="universe",
        title="🌍 Universe",
        price_rub=1899,
        price_usd=25.29,
        stars=1829,
        bonus_credits=2850,
    ),
}

# document_id премиум-эмодзи тарифов (HTML <tg-emoji> и icon_custom_emoji_id в кнопках).
PLAN_PREMIUM_EMOJI_IDS: dict[str, str] = {
    "starter": "5287702390370242449",
    "nova": "5242331214848756985",
    "supernova": "5242505745139797503",
    "galaxy": "5242227706136924612",
    "universe": "5242285645245745392",
}

# Заглушка внутри <tg-emoji> для клиентов без custom emoji (совпадает с темой тира в PLANS.title).
PLAN_PREMIUM_EMOJI_FALLBACK: dict[str, str] = {
    "starter": "🌙",
    "nova": "✨",
    "supernova": "🌟",
    "galaxy": "🌌",
    "universe": "🌍",
}


BONUS_PACKS_ORDER: tuple[str, ...] = ("pack300", "pack500", "pack1000")

# Скидки на бонус-паки (₽, $, ⭐) при активной подписке.
BONUS_PACK_DISCOUNT_MULTIPLIER_BY_PLAN: dict[str, float] = {
    "starter": 0.85,
    "galaxy": 0.95,
    "universe": 0.85,
}

BONUS_PACKS: dict[str, BonusPack] = {
    "pack300": BonusPack(
        id="pack300",
        title="300 кредитов",
        credits=300,
        price_rub=299,
        price_usd=2.89,
        stars=289,
    ),
    "pack500": BonusPack(
        id="pack500",
        title="500 кредитов",
        credits=500,
        price_rub=499,
        price_usd=6.49,
        stars=489,
    ),
    "pack1000": BonusPack(
        id="pack1000",
        title="1000 кредитов",
        credits=1000,
        price_rub=999,
        price_usd=12.99,
        stars=989,
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
