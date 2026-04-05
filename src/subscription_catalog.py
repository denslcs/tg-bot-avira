"""Тарифы подписки: кредиты на баланс, цены ₽ / $ / ⭐ Telegram Stars.

Подписка даёт кредиты и срок доступа. Без подписки: не более NONSUB_IMAGE_WINDOW_MAX
генераций картинок за цикл; после полного исчерпания слотов следующий цикл через
NONSUB_IMAGE_WINDOW_DAYS суток от момента исчерпания (UTC, то же время суток).
Каждая генерация со списанием кредитов; кредиты не обходят лимит.

Звёзды (XTR): опорная точка Nova — $2.99 → 225 ⭐ (согласовано с прежним
соотношением ~159 ₽ → 150 ⭐, масштаб по курсу Nova). Остальные тарифы:
round(usd / 2.99 * 225).

Ориентир по кредитам (зависит от OPENROUTER_*_COST_CREDITS и модели в панели):
Starter 100 (3 дня, как Universe по лимитам и моделям, без токенов при покупке), Nova 500, Supernova 1100, Galaxy 2400, Universe 5000.
Матрица моделей по тарифам: см. _model_choices_for_subscription_plan в img_commands.
"""

from __future__ import annotations

from dataclasses import dataclass


# Без подписки: лимит картинок за цикл; сброс через NONSUB_IMAGE_WINDOW_DAYS после исчерпания (UTC).
NONSUB_IMAGE_WINDOW_DAYS: int = 30
NONSUB_IMAGE_WINDOW_MAX: int = 3
# Без подписки: «готовые идеи» — 1 слот за цикл; сброс через те же сутки после исчерпания.
NONSUB_READY_IDEA_WINDOW_MAX: int = 1

# Дневной лимит «готовых идей» по тарифу (календарные сутки МСК, см. database._day_msk_now). None = без лимита.
# Токены не списываются, пока есть место в дневном лимите; после исчерпания — 1 токен за генерацию.
def ready_idea_daily_cap_for_plan(plan_id: str | None) -> int | None:
    p = (plan_id or "").strip().lower()
    if p in ("starter", "universe"):
        return None
    if p == "nova":
        return 8
    if p == "supernova":
        return 11
    if p == "galaxy":
        return 13
    return 11

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
    # Токены «готовых идей» при покупке подписки (если > 0).
    idea_tokens_on_purchase: int = 0


@dataclass(frozen=True)
class BonusPack:
    id: str
    title: str
    credits: int
    price_rub: int
    price_usd: float
    stars: int
    # Токены генераций «готовых идей» (после исчерпания дневного лимита по подписке / вне лимитов).
    idea_tokens: int = 0


PLANS_ORDER: tuple[str, ...] = ("starter", "nova", "supernova", "galaxy", "universe")

# Текст, если пользователь снова нажимает на Starter после единственной покупки.
STARTER_ALREADY_PURCHASED_TEXT = (
    "Вы уже оформляли пробную подписку <b>Starter</b> — купить её повторно нельзя.\n\n"
    "<blockquote>Выбери полный тариф: Nova, SuperNova, Galaxy или Universe в разделе "
    "<code>/start</code> → <b>Оплатить</b>.</blockquote>"
)

PLANS: dict[str, SubscriptionPlan] = {
    "starter": SubscriptionPlan(
        id="starter",
        title="🚀 Starter",
        price_rub=129,
        price_usd=1.60,
        stars=120,  # round(1.60 / 2.99 * 225)
        bonus_credits=100,
        period_days=3,
    ),
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
        title="🌍 Universe",
        price_rub=1599,
        price_usd=19.99,
        stars=1504,  # round(19.99 / 2.99 * 225) — как у остальных тарифов
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
        idea_tokens=5,
    ),
    "pack500": BonusPack(
        id="pack500",
        title="500 кредитов",
        credits=500,
        price_rub=499,
        price_usd=6.22,
        stars=468,
        idea_tokens=7,
    ),
    "pack1000": BonusPack(
        id="pack1000",
        title="1000 кредитов",
        credits=1000,
        price_rub=999,
        price_usd=12.45,
        stars=936,
        idea_tokens=12,
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
