from __future__ import annotations

"""
Подписки, бонус-пакеты, Stars и внешние способы оплаты (callback + pre_checkout).
"""

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
)
from aiogram.enums import ContentType

from src.config import PAY_URL_CARD_INTL, PAY_URL_CARD_RU, PAY_URL_CRYPTO, PROJECT_ROOT, SUPPORT_BOT_USERNAME
from src.database import (
    add_credits,
    ensure_user,
    get_user_admin_profile,
    reset_subscription_days,
    release_star_payment_claim,
    subscription_is_active,
    try_claim_star_payment,
)
from src.formatting import HTML, esc
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
    FREE_DAILY_READY_IMAGE_GENERATIONS,
    FREE_DAILY_SELF_IMAGE_GENERATIONS,
    PLANS,
    PLANS_ORDER,
    SUBSCRIPTION_PERIOD_DAYS,
)

router = Router(name="payments")


async def _can_buy_plan(user_id: int, plan_id: str) -> tuple[bool, str | None]:
    profile = await get_user_admin_profile(user_id)
    if not profile:
        return True, None
    active = subscription_is_active(profile.subscription_ends_at)
    if active:
        return False, "Подписка уже активна. Продлить можно после окончания срока."
    return True, None


def _subscriptions_pricing_image_path() -> Path | None:
    p = PROJECT_ROOT / "assets" / "pay" / "subscriptions_pricing.png"
    return p if p.is_file() else None


def _plans_menu_caption() -> str:
    return (
        "<b>Тарифы</b> — при оплате на баланс начисляются <b>кредиты</b> "
        f"(срок подписки <b>{esc(SUBSCRIPTION_PERIOD_DAYS)}</b> дн.). "
        "Ограничений на количество генераций по подписке нет — всё зависит от баланса кредитов.\n\n"
        f"<blockquote><i>Без подписки (UTC сутки):</i> свои генерации — <b>{esc(FREE_DAILY_SELF_IMAGE_GENERATIONS)}</b>, "
        f"готовые промпты — <b>{esc(FREE_DAILY_READY_IMAGE_GENERATIONS)}</b>.</blockquote>"
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
    return (
        "<b>💳 Выбери способ оплаты</b>\n\n"
        f"🎁 <b>Подписка:</b> {title}\n"
        f"<i>Кредиты на баланс:</i> <b>+{esc(p.bonus_credits)}</b>\n"
        f"Срок: <b>{esc(SUBSCRIPTION_PERIOD_DAYS)}</b> дн.\n"
        "<blockquote><i>С подпиской</i> — ограничений на число генераций нет: списываются кредиты.</blockquote>\n"
        f"<blockquote><i>Без подписки (UTC сутки):</i> свои генерации — <b>{esc(FREE_DAILY_SELF_IMAGE_GENERATIONS)}</b>, "
        f"готовые промпты — <b>{esc(FREE_DAILY_READY_IMAGE_GENERATIONS)}</b>.</blockquote>\n\n"
        "<i>Оформляя оплату, ты соглашаешься с условиями сервиса и политикой возврата "
        "(подробности — в поддержке или на странице оплаты).</i>"
    )


def _pack_methods_text(pack_id: str) -> str:
    p = BONUS_PACKS[pack_id]
    return (
        "<b>💳 Выбери способ оплаты</b>\n\n"
        f"🎁 <b>Пакет бонусов:</b> <b>{esc(p.title)}</b>\n"
        f"<i>Начисление на баланс:</i> <b>+{esc(p.credits)}</b> кредитов\n"
        f"<i>Примерно готовых промптов:</i> <b>{esc(p.prompt_estimate)}</b>\n"
        "<blockquote><i>Пакет не продлевает подписку — только пополняет кредиты.</i></blockquote>\n\n"
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
            f"💰 Цена: {esc(p.price_rub)} ₽, ${p.price_usd:g}\n"
            f"🎯 Промптов: {esc(p.prompt_estimate)}"
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


async def send_subscription_menu(message: Message) -> None:
    """Тарифы и оплата — то же, что кнопка «Оплатить» в /start."""
    if not message.from_user:
        return
    await ensure_user(message.from_user.id, message.from_user.username)
    caption = _plans_menu_caption()
    kb = _plans_keyboard()
    pricing_img = _subscriptions_pricing_image_path()
    if pricing_img:
        await message.answer_photo(
            FSInputFile(pricing_img),
            caption=caption,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await message.answer(caption, reply_markup=kb, parse_mode=HTML)


@router.callback_query(F.data == CB_MENU_PAY)
async def menu_pay(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    await callback.answer()
    await send_subscription_menu(callback.message)


@router.message(Command("pay"))
async def cmd_pay(message: Message) -> None:
    await send_subscription_menu(message)


@router.callback_query(F.data == CB_PAY_MENU)
async def pay_back_plans(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    caption = _plans_menu_caption()
    kb = _plans_keyboard()
    pricing_img = _subscriptions_pricing_image_path()
    if pricing_img:
        await callback.message.answer_photo(
            FSInputFile(pricing_img),
            caption=caption,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await callback.message.answer(
            "<blockquote><i>Выбери тариф ниже.</i></blockquote>",
            reply_markup=kb,
            parse_mode=HTML,
        )
    await callback.answer()


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
        await callback.answer(reason or "Покупка тарифа недоступна.", show_alert=True)
        return
    await callback.message.answer(
        _pay_methods_text(plan_id),
        reply_markup=_methods_keyboard(plan_id, is_pack=False, back_callback_data=CB_PAY_MENU),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data == CB_PAY_BONUS_MENU)
async def pay_bonus_menu(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.answer(
        _bonus_packs_caption(),
        reply_markup=_bonus_packs_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PAY_PACK_PREFIX))
async def pay_pick_pack(callback: CallbackQuery) -> None:
    if callback.message is None or not callback.data:
        await callback.answer()
        return
    pack_id = callback.data.removeprefix(CB_PAY_PACK_PREFIX)
    if pack_id not in BONUS_PACKS:
        await callback.answer("Неизвестный пакет", show_alert=True)
        return
    await callback.message.answer(
        _pack_methods_text(pack_id),
        reply_markup=_methods_keyboard(pack_id, is_pack=True, back_callback_data=CB_PAY_BONUS_MENU),
        parse_mode=HTML,
    )
    await callback.answer()


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
            await callback.message.answer(
                "<blockquote><i>Открой страницу оплаты.</i> Если на кассе есть выбор тарифа — "
                f"укажи: <b>{esc(title)}</b>.</blockquote>",
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
        await callback.message.answer(
            f"<b>Оплата «{esc(label)}»</b> пока подключается.\n"
            f"<blockquote>{esc(support_line)} Мы выставим счёт или дадим ссылку.</blockquote>",
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
        await callback.message.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Avira — {p.title}",
            description=(
                f"Подписка {p.title}: +{p.bonus_credits} кредитов, "
                f"{SUBSCRIPTION_PERIOD_DAYS} дн. Лимитов по количеству генераций нет."
            ),
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"{p.title} ({SUBSCRIPTION_PERIOD_DAYS} дн.)", amount=p.stars)],
            provider_token="",
        )
    elif item_id in BONUS_PACKS:
        b = BONUS_PACKS[item_id]
        payload = f"pack:{callback.from_user.id}:{item_id}"
        await callback.message.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Avira — бонус-пакет {b.credits} кредитов",
            description=f"Пакет бонусов: +{b.credits} кредитов на баланс (без продления подписки).",
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
        p = PLANS[item_id]
        new_end = await reset_subscription_days(message.from_user.id, SUBSCRIPTION_PERIOD_DAYS, item_id)
        if not new_end:
            await release_star_payment_claim(charge_id)
            await message.answer(
                "Ошибка записи подписки в базу. Сохрани это сообщение и напиши в поддержку — проверим оплату."
            )
            return
        credited = await add_credits(message.from_user.id, p.bonus_credits)
        bonus_line = (
            f"Начислено кредитов: +{p.bonus_credits}."
            if credited
            else (
                f"Подписка записана, но бонус +{p.bonus_credits} кредитов не удалось начислить автоматически — "
                "напиши в поддержку, начислим вручную."
            )
        )
        bonus_html = esc(bonus_line)
        await message.answer(
            "<b>Оплата прошла ✅</b>\n"
            f"Тариф: <b>{esc(p.title)}</b> — начислены кредиты.\n"
            f"<blockquote><i>Подписка:</i> +{esc(SUBSCRIPTION_PERIOD_DAYS)} дн., активна до (UTC): <b>{esc(new_end)}</b>\n"
            f"{bonus_html}</blockquote>\n\n"
            "<i>Можно снова открыть «Создать картинку» в</i> <code>/start</code>.",
            parse_mode=HTML,
        )
        return
    if item_id not in BONUS_PACKS:
        await release_star_payment_claim(charge_id)
        await message.answer("Оплата получена, но пакет не найден. Напиши в поддержку.")
        return
    b = BONUS_PACKS[item_id]
    credited = await add_credits(message.from_user.id, b.credits)
    if credited:
        await message.answer(
            "<b>Оплата прошла ✅</b>\n"
            f"Пакет: <b>{esc(b.title)}</b>\n"
            f"<blockquote><i>Начислено:</i> +{esc(b.credits)} кредитов на баланс.</blockquote>",
            parse_mode=HTML,
        )
    else:
        await release_star_payment_claim(charge_id)
        await message.answer(
            "Оплата получена, но кредиты не удалось начислить автоматически. "
            "Напиши в поддержку — начислим вручную."
        )
