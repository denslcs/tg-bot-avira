from __future__ import annotations

"""
Подписки, бонус-пакеты, Stars и внешние способы оплаты (callback + pre_checkout).
"""

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    User,
)
from aiogram.enums import ContentType

from src.config import (
    ADMIN_SALES_NOTIFY_CHAT_ID,
    ADMIN_SALES_THREAD_BONUS_PACKS,
    ADMIN_SALES_THREAD_GALAXY,
    ADMIN_SALES_THREAD_NOVA,
    ADMIN_SALES_THREAD_SUPERNOVA,
    ADMIN_SALES_THREAD_STARTER,
    ADMIN_SALES_THREAD_UNIVERSE,
    PAY_URL_CARD_INTL,
    PAY_URL_CARD_RU,
    PAY_URL_CRYPTO,
    PROJECT_ROOT,
    SUPPORT_BOT_USERNAME,
)
from src.database import (
    add_credits_with_reason,
    add_budget_history_event,
    ensure_user,
    get_user_admin_profile,
    mark_starter_trial_purchased,
    record_subscription_purchase_now,
    reset_subscription_days,
    release_star_payment_claim,
    subscription_can_purchase_plan,
    try_claim_star_payment,
)
from src.formatting import HTML, esc, format_subscription_ends_at
from src.handlers.commands import edit_or_send_nav_message
from src.keyboards.callback_data import (
    CB_MENU_BACK_START,
    CB_MENU_PAY,
    CB_PAY_BONUS_MENU,
    CB_PAY_CRYPTO_PREFIX,
    CB_PAY_INTL_PREFIX,
    CB_PAY_MENU,
    CB_PAY_PACK_PREFIX,
    CB_PAY_PLAN_PREFIX,
    CB_PAY_RUB_PREFIX,
    CB_PAY_STARS_PREFIX,
)
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS
from src.subscription_catalog import (
    BONUS_PACKS,
    BONUS_PACKS_ORDER,
    NONSUB_IMAGE_WINDOW_DAYS,
    NONSUB_IMAGE_WINDOW_MAX,
    PLANS,
    PLANS_ORDER,
    STARTER_ALREADY_PURCHASED_TEXT,
    SUBSCRIPTION_PERIOD_DAYS,
    SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS,
)

router = Router(name="payments")

logger = logging.getLogger(__name__)


def _admin_sales_thread_for_plan(plan_id: str) -> int:
    return {
        "starter": ADMIN_SALES_THREAD_STARTER,
        "nova": ADMIN_SALES_THREAD_NOVA,
        "supernova": ADMIN_SALES_THREAD_SUPERNOVA,
        "galaxy": ADMIN_SALES_THREAD_GALAXY,
        "universe": ADMIN_SALES_THREAD_UNIVERSE,
    }.get((plan_id or "").strip().lower(), 0)


def _payment_type_label(sp: SuccessfulPayment) -> str:
    """Канал оплаты для админ-уведомления (по валюте из Telegram Payments)."""
    cur = (sp.currency or "").strip().upper()
    if cur == "XTR":
        return "⭐ Звёзды (Telegram Stars, XTR)"
    if cur == "RUB":
        return "₽ Рубли (RUB)"
    if cur == "USD":
        return "$ Доллары (USD)"
    return f"Другое ({esc(cur)})" if cur else "Не указано"


def _user_line_html(user: User) -> str:
    uid = user.id
    un = (user.username or "").strip().lstrip("@")
    fn = esc((user.first_name or "").strip() or "—")
    ln = esc((user.last_name or "").strip())
    name = f"{fn} {ln}".strip() if ln else fn
    if un:
        u_esc = esc(un)
        return f'{name} · <a href="https://t.me/{un}">@{u_esc}</a> · <code>{uid}</code>'
    return f'{name} · <a href="tg://user?id={uid}">id {uid}</a>'


async def _notify_admin_sales(
    bot,
    *,
    thread_id: int,
    text: str,
) -> None:
    if not ADMIN_SALES_NOTIFY_CHAT_ID:
        return
    kwargs: dict = {
        "chat_id": ADMIN_SALES_NOTIFY_CHAT_ID,
        "text": text,
        "parse_mode": HTML,
        "disable_web_page_preview": True,
    }
    if thread_id > 0:
        kwargs["message_thread_id"] = thread_id
    try:
        await bot.send_message(**kwargs)
    except Exception:
        logger.exception("Не удалось отправить уведомление о продаже в ADMIN_SALES_NOTIFY_CHAT_ID")


async def _can_buy_plan(user_id: int, plan_id: str) -> tuple[bool, str | None]:
    return await subscription_can_purchase_plan(user_id, plan_id)


def _subscriptions_pricing_image_path() -> Path | None:
    p = PROJECT_ROOT / "assets" / "pay" / "subscriptions_pricing.png"
    return p if p.is_file() else None


def _plans_menu_caption() -> str:
    st = PLANS["starter"]
    return (
        "<b>Тарифы</b> — при оплате на баланс начисляются <b>кредиты</b>.\n"
        f"<blockquote><b>Starter</b> — пробный пакет на <b>{esc(st.period_days)}</b> дн., все модели как у Universe, "
        f"<b>одна покупка на аккаунт</b> (повторно недоступен). Остальные тарифы — <b>{esc(SUBSCRIPTION_PERIOD_DAYS)}</b> дн.</blockquote>\n"
        "Ограничений на число генераций по подписке нет — списываются кредиты.\n\n"
        f"<blockquote><i>Полные тарифы:</i> не чаще <b>одного раза в {esc(SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS)}</b> дней "
        f"между покупками. При активной подписке сменить тариф нельзя до окончания срока. "
        f"<i>Starter в эту паузу не входит.</i></blockquote>\n\n"
        f"<blockquote><i>Без подписки:</i> до <b>{esc(NONSUB_IMAGE_WINDOW_MAX)}</b> картинок за цикл; после полного исчерпания "
        f"новый цикл через <b>{esc(NONSUB_IMAGE_WINDOW_DAYS)}</b> суток от момента исчерпания (UTC). "
        "Кредиты лимит не обходят.</blockquote>"
    )


def _plans_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for pid in PLANS_ORDER:
        p = PLANS[pid]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p.title} — +{p.bonus_credits} кр. · {p.price_rub} ₽",
                    callback_data=f"{CB_PAY_PLAN_PREFIX}{pid}",
                    style=BTN_PRIMARY,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🎁 Пакеты бонусов",
                callback_data=CB_PAY_BONUS_MENU,
                style=BTN_SUCCESS,
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _methods_keyboard(item_id: str, *, is_pack: bool, back_callback_data: str = CB_PAY_MENU) -> InlineKeyboardMarkup:
    if is_pack:
        pack = BONUS_PACKS[item_id]
        stars = pack.stars
        usd = pack.price_usd
        rub = pack.price_rub
    else:
        p = PLANS[item_id]
        stars = p.stars
        usd = p.price_usd
        rub = p.price_rub
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{rub} ₽ · картой РФ",
                    callback_data=f"{CB_PAY_RUB_PREFIX}{item_id}",
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"${usd:g} · карта другой страны",
                    callback_data=f"{CB_PAY_INTL_PREFIX}{item_id}",
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐ {stars} · Звёздами",
                    callback_data=f"{CB_PAY_STARS_PREFIX}{item_id}",
                    style=BTN_SUCCESS,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"${usd:g} · криптовалютой",
                    callback_data=f"{CB_PAY_CRYPTO_PREFIX}{item_id}",
                    style=BTN_PRIMARY,
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback_data)],
        ]
    )


def _pay_methods_text(plan_id: str) -> str:
    p = PLANS[plan_id]
    title = esc(p.title)
    days = p.period_days
    starter_block = ""
    cooldown_block = (
        f"<blockquote><i>Оплата полного тарифа не чаще одного раза в {esc(SUBSCRIPTION_PURCHASE_COOLDOWN_DAYS)} дней</i> "
        f"(после последней такой покупки). Оплата ⭐ учитывается автоматически.</blockquote>\n"
    )
    if plan_id == "starter":
        starter_block = (
            "<blockquote><b>Пробный Starter (3 дня):</b> как Universe — полный набор моделей, "
            f"без лимита «готовых идей», +{esc(p.bonus_credits)} кредитов. "
            "После окончания — полные тарифы Nova / SuperNova / Galaxy / Universe. "
            "<b>Повторно Starter купить нельзя.</b></blockquote>\n"
        )
        cooldown_block = (
            "<blockquote><i>Starter не увеличивает паузу между покупками полных тарифов.</i></blockquote>\n"
        )
    return (
        "<b>💳 Выбери способ оплаты</b>\n\n"
        f"🎁 <b>Подписка:</b> {title}\n"
        f"<i>Кредиты на баланс:</i> <b>+{esc(p.bonus_credits)}</b>\n"
        f"Срок: <b>{esc(days)}</b> дн.\n"
        f"{starter_block}"
        "<blockquote><i>Картинки по своему описанию</i> — без дневного лимита по числу запросов (с кредитами). "
        "<i>Готовые идеи</i> — без лимита при активной подписке.</blockquote>\n"
        f"{cooldown_block}"
        f"<blockquote><i>Без подписки:</i> до <b>{esc(NONSUB_IMAGE_WINDOW_MAX)}</b> картинок за цикл; после исчерпания цикла "
        f"следующий через <b>{esc(NONSUB_IMAGE_WINDOW_DAYS)}</b> суток от этого момента (UTC). Кредиты лимит не обходят.</blockquote>\n\n"
        "<i>Оформляя оплату, ты соглашаешься с условиями сервиса и политикой возврата "
        "(подробности — в поддержке или на странице оплаты).</i>"
    )


def _pack_methods_text(pack_id: str) -> str:
    p = BONUS_PACKS[pack_id]
    return (
        "<b>💳 Выбери способ оплаты</b>\n\n"
        f"🎁 <b>Пакет бонусов:</b> <b>{esc(p.title)}</b>\n"
        f"<i>Начисление на баланс:</i> <b>+{esc(p.credits)}</b> кредитов\n"
        "<blockquote><i>Пакет не продлевает подписку — только кредиты на баланс.</i></blockquote>\n\n"
        "<i>Оформляя оплату, ты соглашаешься с условиями сервиса и политикой возврата "
        "(подробности — в поддержке или на странице оплаты).</i>"
    )


def _bonus_packs_caption() -> str:
    lines = [
        "<b>🎁 Пакеты бонусов</b>\n"
        "<blockquote><i>Докупка кредитов без продления подписки.</i></blockquote>",
    ]
    for pid in BONUS_PACKS_ORDER:
        p = BONUS_PACKS[pid]
        lines.append(
            f"<b>{esc(p.credits)} кредитов</b>\n"
            f"💰 Цена: {esc(p.price_rub)} ₽, ${p.price_usd:g}"
        )
    return "\n\n".join(lines)


def _bonus_packs_keyboard() -> InlineKeyboardMarkup:
    """Сверху зелёные (два младших пакета в ряд), ниже синий крупный, внизу нейтральный «Назад»."""
    rows: list[list[InlineKeyboardButton]] = []
    order = list(BONUS_PACKS_ORDER)
    if not order:
        rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data=CB_PAY_MENU)])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if len(order) == 1:
        b = BONUS_PACKS[order[0]]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🎁 {b.credits} кр. · {b.price_rub} ₽",
                    callback_data=f"{CB_PAY_PACK_PREFIX}{order[0]}",
                    style=BTN_PRIMARY,
                )
            ]
        )
    else:
        b0 = BONUS_PACKS[order[0]]
        b1 = BONUS_PACKS[order[1]]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🎁 {b0.credits} кр. · {b0.price_rub} ₽",
                    callback_data=f"{CB_PAY_PACK_PREFIX}{order[0]}",
                    style=BTN_SUCCESS,
                ),
                InlineKeyboardButton(
                    text=f"🎁 {b1.credits} кр. · {b1.price_rub} ₽",
                    callback_data=f"{CB_PAY_PACK_PREFIX}{order[1]}",
                    style=BTN_SUCCESS,
                ),
            ]
        )
        for bid in order[2:]:
            b = BONUS_PACKS[bid]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⭐ {b.credits} кр. · {b.price_rub} ₽",
                        callback_data=f"{CB_PAY_PACK_PREFIX}{bid}",
                        style=BTN_PRIMARY,
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data=CB_PAY_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_plans_menu_to_chat(bot, chat_id: int) -> None:
    caption = _plans_menu_caption()
    kb = _plans_keyboard()
    pricing_img = _subscriptions_pricing_image_path()
    if pricing_img and pricing_img.is_file():
        await bot.send_photo(
            chat_id,
            photo=FSInputFile(pricing_img),
            caption=caption,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode=HTML)


async def send_subscription_menu(message: Message, *, replace_previous: bool = False) -> None:
    """Тарифы и оплата — то же, что кнопка «Оплатить» в /start."""
    if not message.from_user:
        return
    await ensure_user(message.from_user.id, message.from_user.username)
    chat_id = message.chat.id
    bot = message.bot
    if replace_previous:
        caption = _plans_menu_caption()
        kb = _plans_keyboard()
        edited = await edit_or_send_nav_message(
            message,
            text=caption,
            reply_markup=kb,
            parse_mode=HTML,
        )
        if edited is not None:
            return
    await _send_plans_menu_to_chat(bot, chat_id)


@router.callback_query(F.data == CB_MENU_PAY)
async def menu_pay(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    await callback.answer()
    await send_subscription_menu(callback.message, replace_previous=True)


@router.message(Command("pay"))
async def cmd_pay(message: Message) -> None:
    await send_subscription_menu(message)


@router.callback_query(F.data == CB_PAY_MENU)
async def pay_back_plans(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    caption = _plans_menu_caption()
    kb = _plans_keyboard()
    await edit_or_send_nav_message(
        callback.message,
        text=caption,
        reply_markup=kb,
        parse_mode=HTML,
    )


@router.callback_query(F.data.startswith(CB_PAY_PLAN_PREFIX))
async def pay_pick_plan(callback: CallbackQuery) -> None:
    if callback.message is None or not callback.data:
        await callback.answer()
        return
    plan_id = callback.data.removeprefix(CB_PAY_PLAN_PREFIX)
    if plan_id not in PLANS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    allowed, reason = await _can_buy_plan(callback.from_user.id, plan_id)
    if not allowed:
        if plan_id == "starter" and callback.from_user is not None:
            prof = await get_user_admin_profile(callback.from_user.id)
            if prof and prof.starter_trial_used:
                await callback.answer()
                await callback.message.answer(
                    STARTER_ALREADY_PURCHASED_TEXT,
                    parse_mode=HTML,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="⬅️ К тарифам", callback_data=CB_PAY_MENU)]
                        ]
                    ),
                )
                return
        await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
        return
    await callback.answer()
    await edit_or_send_nav_message(
        callback.message,
        text=_pay_methods_text(plan_id),
        reply_markup=_methods_keyboard(plan_id, is_pack=False, back_callback_data=CB_PAY_MENU),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_PAY_BONUS_MENU)
async def pay_bonus_menu(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    await edit_or_send_nav_message(
        callback.message,
        text=_bonus_packs_caption(),
        reply_markup=_bonus_packs_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data.startswith(CB_PAY_PACK_PREFIX))
async def pay_pick_pack(callback: CallbackQuery) -> None:
    if callback.message is None or not callback.data:
        await callback.answer()
        return
    pack_id = callback.data.removeprefix(CB_PAY_PACK_PREFIX)
    if pack_id not in BONUS_PACKS:
        await callback.answer("Неизвестный пакет", show_alert=True)
        return
    await callback.answer()
    await edit_or_send_nav_message(
        callback.message,
        text=_pack_methods_text(pack_id),
        reply_markup=_methods_keyboard(pack_id, is_pack=True, back_callback_data=CB_PAY_BONUS_MENU),
        parse_mode=HTML,
    )


def _pay_item_info(item_id: str) -> tuple[str, int]:
    if item_id in PLANS:
        p = PLANS[item_id]
        return p.title, p.price_rub
    b = BONUS_PACKS[item_id]
    return b.title, b.price_rub


async def _external_pay_hint(callback: CallbackQuery, item_id: str, label: str, url: str | None) -> None:
    title, price_rub = _pay_item_info(item_id)
    if url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"Перейти к оплате ({label})", url=url, style=BTN_PRIMARY)],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"{CB_PAY_PLAN_PREFIX}{item_id}"
                        if item_id in PLANS
                        else f"{CB_PAY_PACK_PREFIX}{item_id}",
                    )
                ],
            ]
        )
        if callback.message:
            await edit_or_send_nav_message(
                callback.message,
                text=(
                    "<blockquote><i>Открой страницу оплаты.</i> Если на кассе есть выбор тарифа — "
                    f"укажи: <b>{esc(title)}</b>.</blockquote>"
                ),
                reply_markup=keyboard,
                parse_mode=HTML,
            )
        await callback.answer()
        return
    support_line = (
        f"Напиши в @{SUPPORT_BOT_USERNAME} с текстом «{title}» ({price_rub} ₽)."
        if SUPPORT_BOT_USERNAME
        else f"Напиши в поддержку (бот в настройках проекта) с текстом «{title}» ({price_rub} ₽)."
    )
    if callback.message:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_PAY_MENU)]]
        )
        await edit_or_send_nav_message(
            callback.message,
            text=(
                f"<b>Оплата «{esc(label)}»</b> пока подключается.\n"
                f"<blockquote>{esc(support_line)} Мы выставим счёт или дадим ссылку.</blockquote>"
            ),
            reply_markup=kb,
            parse_mode=HTML,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PAY_RUB_PREFIX))
async def pay_rub(callback: CallbackQuery) -> None:
    if not callback.data:
        await callback.answer()
        return
    item_id = callback.data.removeprefix(CB_PAY_RUB_PREFIX)
    if item_id not in PLANS and item_id not in BONUS_PACKS:
        await callback.answer("Ошибка", show_alert=True)
        return
    if item_id in PLANS and callback.from_user:
        allowed, reason = await _can_buy_plan(callback.from_user.id, item_id)
        if not allowed:
            await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
            return
    await _external_pay_hint(callback, item_id, "карта РФ", PAY_URL_CARD_RU or None)


@router.callback_query(F.data.startswith(CB_PAY_INTL_PREFIX))
async def pay_intl(callback: CallbackQuery) -> None:
    if not callback.data:
        await callback.answer()
        return
    item_id = callback.data.removeprefix(CB_PAY_INTL_PREFIX)
    if item_id not in PLANS and item_id not in BONUS_PACKS:
        await callback.answer("Ошибка", show_alert=True)
        return
    if item_id in PLANS and callback.from_user:
        allowed, reason = await _can_buy_plan(callback.from_user.id, item_id)
        if not allowed:
            await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
            return
    await _external_pay_hint(callback, item_id, "карта другой страны", PAY_URL_CARD_INTL or None)


@router.callback_query(F.data.startswith(CB_PAY_CRYPTO_PREFIX))
async def pay_crypto(callback: CallbackQuery) -> None:
    if not callback.data:
        await callback.answer()
        return
    item_id = callback.data.removeprefix(CB_PAY_CRYPTO_PREFIX)
    if item_id not in PLANS and item_id not in BONUS_PACKS:
        await callback.answer("Ошибка", show_alert=True)
        return
    if item_id in PLANS and callback.from_user:
        allowed, reason = await _can_buy_plan(callback.from_user.id, item_id)
        if not allowed:
            await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
            return
    await _external_pay_hint(callback, item_id, "крипта", PAY_URL_CRYPTO or None)


@router.callback_query(F.data.startswith(CB_PAY_STARS_PREFIX))
async def pay_stars_invoice(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None or not callback.data:
        await callback.answer()
        return
    item_id = callback.data.removeprefix(CB_PAY_STARS_PREFIX)
    await ensure_user(callback.from_user.id, callback.from_user.username)
    if item_id in PLANS:
        allowed, reason = await _can_buy_plan(callback.from_user.id, item_id)
        if not allowed:
            await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
            return
        p = PLANS[item_id]
        payload = f"plan:{callback.from_user.id}:{item_id}"
        pd = p.period_days
        await callback.message.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Shard Creator — {p.title}",
            description=(
                f"Подписка {p.title}: +{p.bonus_credits} кредитов, "
                f"{pd} дн. Картинки и готовые идеи — по кредитам."
            ),
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"{p.title} ({pd} дн.)", amount=p.stars)],
            provider_token="",
        )
    elif item_id in BONUS_PACKS:
        b = BONUS_PACKS[item_id]
        payload = f"pack:{callback.from_user.id}:{item_id}"
        await callback.message.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Shard Creator — бонус-пакет {b.credits} кредитов",
            description=(f"Пакет бонусов: +{b.credits} кредитов на баланс (без продления подписки)."),
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"{b.credits} кредитов", amount=b.stars)],
            provider_token="",
        )
    else:
        await callback.answer("Неизвестный тариф/пакет", show_alert=True)
        return
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery) -> None:
    payload = (q.invoice_payload or "").strip()
    parts = payload.split(":")
    if q.from_user is None:
        await q.answer(ok=False, error_message="Платёж не подходит к этому аккаунту. Запроси счёт заново.")
        return
    if len(parts) == 2 and parts[0].isdigit():
        parts = ["plan", parts[0], parts[1]]
    if len(parts) != 3 or parts[0] not in ("plan", "pack") or not parts[1].isdigit():
        await q.answer(ok=False, error_message="Платёж не подходит к этому аккаунту. Запроси счёт заново.")
        return
    if int(parts[1]) != q.from_user.id:
        await q.answer(ok=False, error_message="Платёж не подходит к этому аккаунту. Запроси счёт заново.")
        return
    item_id = parts[2]
    if parts[0] == "plan":
        if item_id not in PLANS:
            await q.answer(ok=False, error_message="Неизвестный тариф. Запроси новый счёт.")
            return
        allowed, reason = await subscription_can_purchase_plan(q.from_user.id, item_id)
        if not allowed:
            await q.answer(ok=False, error_message=(reason or "Покупка подписки сейчас недоступна.")[:250])
            return
        amount_expected = PLANS[item_id].stars
    else:
        if item_id not in BONUS_PACKS:
            await q.answer(ok=False, error_message="Неизвестный пакет. Запроси новый счёт.")
            return
        amount_expected = BONUS_PACKS[item_id].stars
    if (q.currency or "").upper() != "XTR" or int(q.total_amount or 0) != amount_expected:
        await q.answer(ok=False, error_message="Сумма или валюта счёта не совпадают. Запроси новый счёт.")
        return
    await q.answer(ok=True)


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message) -> None:
    sp = message.successful_payment
    if sp is None or not message.from_user:
        return
    payload = (sp.invoice_payload or "").strip()
    parts = payload.split(":")
    if len(parts) == 2 and parts[0].isdigit():
        parts = ["plan", parts[0], parts[1]]
    if len(parts) != 3 or parts[0] not in ("plan", "pack") or not parts[1].isdigit():
        await message.answer("Оплата получена, но не удалось распознать покупку. Напиши в поддержку с скрином.")
        return
    if int(parts[1]) != message.from_user.id:
        await message.answer("Оплата на другой Telegram ID. Обратись в поддержку.")
        return
    kind, item_id = parts[0], parts[2]
    await ensure_user(message.from_user.id, message.from_user.username)
    charge_id = (sp.telegram_payment_charge_id or "").strip()
    if not await try_claim_star_payment(charge_id, message.from_user.id):
        await message.answer(
            "Этот платёж уже учтён. Если подписка или кредиты не отображаются — напиши в поддержку."
        )
        return
    if kind == "plan":
        if item_id not in PLANS:
            await release_star_payment_claim(charge_id)
            await message.answer("Оплата получена, но тариф не найден. Напиши в поддержку.")
            return
        allowed, reason = await subscription_can_purchase_plan(message.from_user.id, item_id)
        if not allowed:
            await release_star_payment_claim(charge_id)
            await message.answer(
                "<b>Платёж получен</b>, но оформить подписку сейчас нельзя: "
                f"{esc(reason or 'условия сервиса')}.\n\n"
                "<blockquote><i>Если Stars уже списались, напиши в поддержку — подскажем, что делать.</i></blockquote>",
                parse_mode=HTML,
            )
            return
        p = PLANS[item_id]
        new_end = await reset_subscription_days(message.from_user.id, p.period_days, item_id)
        if not new_end:
            await release_star_payment_claim(charge_id)
            await message.answer(
                "Ошибка записи подписки в базу. Сохрани это сообщение и напиши в поддержку — проверим оплату."
            )
            return
        if item_id == "starter":
            await mark_starter_trial_purchased(message.from_user.id)
        else:
            await record_subscription_purchase_now(message.from_user.id)
        credited = await add_credits_with_reason(
            message.from_user.id,
            p.bonus_credits,
            source="subscription_bonus",
            details=f"plan {item_id}",
        )
        await add_budget_history_event(
            message.from_user.id,
            source="subscription_purchase",
            details=f"plan {item_id}",
            delta=0,
        )
        end_h = format_subscription_ends_at(new_end)
        q_lines = [
            f"<i>Срок:</i> <b>{esc(p.period_days)}</b> дн.; действует до <b>{esc(end_h)}</b>",
        ]
        if credited:
            q_lines.append(
                f"<i>Бонус по тарифу на баланс:</i> <b>+{esc(p.bonus_credits)}</b> кредитов"
            )
        else:
            q_lines.append(
                f"<i>Бонус +{esc(p.bonus_credits)} не начислен — напиши в поддержку.</i>"
            )
        quote_inner = "\n".join(q_lines)
        starter_tail = ""
        if item_id == "starter":
            starter_tail = (
                "\n\n<blockquote><i>После окончания Starter оформи полный тариф в</i> "
                "<code>/start</code> <i>→</i> <b>Оплатить</b><i>. Повторно Starter купить нельзя.</i></blockquote>"
            )
        await message.answer(
            "<b>Спасибо за покупку!</b>\n"
            f"Вы приобрели подписку <b>{esc(p.title)}</b>.\n\n"
            f"<blockquote>{quote_inner}</blockquote>\n\n"
            "<i>Можно снова открыть «Создать картинку» в</i> <code>/start</code>."
            f"{starter_tail}",
            parse_mode=HTML,
        )
        stars_amt = int(sp.total_amount or 0)
        cur = (sp.currency or "XTR").upper()
        credit_ok = "да" if credited else "нет (проверить вручную)"
        pay_kind = _payment_type_label(sp)
        admin_txt = (
            "<b>Подписка оплачена</b>\n"
            f"<b>Тип оплаты:</b> {pay_kind}\n"
            f"Тариф: {esc(p.title)} · <code>{esc(item_id)}</code>\n"
            f"Пользователь: {_user_line_html(message.from_user)}\n"
            f"Кредиты по тарифу: <b>+{esc(p.bonus_credits)}</b> · начислено: <i>{esc(credit_ok)}</i>\n"
            f"До (UTC): <code>{esc(end_h)}</code>\n"
            f"Сумма: <b>{esc(stars_amt)}</b> {esc(cur)}\n"
            f"charge: <code>{esc(charge_id)}</code>"
        )
        await _notify_admin_sales(
            message.bot,
            thread_id=_admin_sales_thread_for_plan(item_id),
            text=admin_txt,
        )
        return
    if item_id not in BONUS_PACKS:
        await release_star_payment_claim(charge_id)
        await message.answer("Оплата получена, но пакет не найден. Напиши в поддержку.")
        return
    b = BONUS_PACKS[item_id]
    credited = await add_credits_with_reason(
        message.from_user.id,
        b.credits,
        source="bonus_pack",
        details=f"pack {item_id}",
    )
    if credited:
        await message.answer(
            "<b>Оплата прошла ✅</b>\n"
            f"Пакет: <b>{esc(b.title)}</b>\n"
            f"<blockquote><i>Начислено:</i> +{esc(b.credits)} кредитов на баланс.</blockquote>",
            parse_mode=HTML,
        )
        stars_amt = int(sp.total_amount or 0)
        cur = (sp.currency or "XTR").upper()
        pay_kind = _payment_type_label(sp)
        admin_txt = (
            "<b>Пакет бонусов оплачен</b>\n"
            f"<b>Тип оплаты:</b> {pay_kind}\n"
            f"Пакет: {esc(b.title)} · <code>{esc(item_id)}</code> · <b>+{esc(b.credits)}</b> кр.\n"
            f"Пользователь: {_user_line_html(message.from_user)}\n"
            f"Сумма: <b>{esc(stars_amt)}</b> {esc(cur)}\n"
            f"charge: <code>{esc(charge_id)}</code>"
        )
        await _notify_admin_sales(
            message.bot,
            thread_id=ADMIN_SALES_THREAD_BONUS_PACKS,
            text=admin_txt,
        )
    else:
        await release_star_payment_claim(charge_id)
        await message.answer(
            "Оплата получена, но кредиты не удалось начислить автоматически. "
            "Напиши в поддержку — начислим вручную."
        )
