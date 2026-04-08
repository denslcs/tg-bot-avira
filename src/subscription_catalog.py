"""Тарифы подписки: кредиты на баланс, цены ₽ / $ / ⭐ Telegram Stars.

Подписка даёт кредиты и срок доступа. Без подписки: не более NONSUB_IMAGE_WINDOW_MAX
генераций картинок за цикл; после полного исчерпания слотов следующий цикл через
NONSUB_IMAGE_WINDOW_DAYS суток от момента исчерпания (UTC, то же время суток).
Каждая генерация со списанием кредитов; кредиты не обходят лимит.

Звёзды (XTR): все тарифы с одной ценой $1.99 → 150 ⭐ (шкала как у Nova: $2.99 → 225 ⭐,
т.е. round(usd / 2.99 * 225)).

Бонусные кредиты на баланс при оплате: Starter 250 (3 дня), Nova 480, Supernova 900,
Galaxy 1700, Universe 3400. Матрица моделей по тарифам: _model_choices_for_subscription_plan в img_commands.
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

# Текст, если пользователь снова нажимает на Starter после единственной покупки.
STARTER_ALREADY_PURCHASED_TEXT = (
    "Вы уже оформляли пробную подписку <b>Starter</b> — купить её повторно нельзя.\n\n"
    "<blockquote>Выбери полный тариф: Nova, SuperNova, Galaxy или Universe в разделе "
    "<code>/start</code> → <b>Оплатить</b>.</blockquote>"
)

# Единые цены по всем тарифам; различаются только бонусные кредиты и срок (Starter — 3 дня).
_STARS_USD_199: int = round(1.99 / 2.99 * 225)  # 150

PLANS: dict[str, SubscriptionPlan] = {
    "starter": SubscriptionPlan(
        id="starter",
        title="🚀 Starter",
        price_rub=159,
        price_usd=1.99,
        stars=_STARS_USD_199,
        bonus_credits=250,
        period_days=3,
    ),
    "nova": SubscriptionPlan(
        id="nova",
        title="✨ Nova",
        price_rub=159,
        price_usd=1.99,
        stars=_STARS_USD_199,
        bonus_credits=480,
    ),
    "supernova": SubscriptionPlan(
        id="supernova",
        title="🌟 SuperNova",
        price_rub=159,
        price_usd=1.99,
        stars=_STARS_USD_199,
        bonus_credits=900,
    ),
    "galaxy": SubscriptionPlan(
        id="galaxy",
        title="🌌 Galaxy",
        price_rub=159,
        price_usd=1.99,
        stars=_STARS_USD_199,
        bonus_credits=1700,
    ),
    "universe": SubscriptionPlan(
        id="universe",
        title="🌍 Universe",
        price_rub=159,
        price_usd=1.99,
        stars=_STARS_USD_199,
        bonus_credits=3400,
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
    ),
    "pack500": BonusPack(
        id="pack500",
        title="500 кредитов",
        credits=500,
        price_rub=499,
        price_usd=6.22,
        stars=468,
    ),
    "pack1000": BonusPack(
        id="pack1000",
        title="1000 кредитов",
        credits=1000,
        price_rub=999,
        price_usd=12.45,
        stars=936,
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
