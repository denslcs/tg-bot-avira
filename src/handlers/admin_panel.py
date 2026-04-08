import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import ADMIN_IDS
from src.database import (
    clear_dialog_messages,
    clear_subscription,
    ensure_user,
    extend_subscription,
    count_dialog_messages,
    count_dialog_messages_total,
    count_new_users_days,
    count_open_tickets,
    count_users_active_subscription,
    count_users_total,
    ensure_user,
    get_open_ticket_by_user,
    get_support_rating_rollups,
    get_user_admin_profile,
    list_open_tickets_preview,
    set_subscription_plan_only,
    subscription_is_active,
    sum_users_credits,
)
from src.formatting import HTML, esc, format_subscription_ends_at
from src.handlers.commands import edit_or_send_nav_message
from src.keyboards.styles import BTN_PRIMARY
from src.subscription_catalog import PLANS, PLANS_ORDER

router = Router(name="admin_panel")


def _plans_hint() -> str:
    return "|".join(PLANS_ORDER)


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открытые тикеты", callback_data="adm:tickets", style=BTN_PRIMARY
                ),
                InlineKeyboardButton(
                    text="Статистика бота", callback_data="adm:stats", style=BTN_PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Оценки поддержки", callback_data="adm:ratings", style=BTN_PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Справка по командам", callback_data="adm:help", style=BTN_PRIMARY
                ),
            ],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return
    await message.answer(
        "Админ-панель Avira\n\n"
        "Быстрые кнопки ниже. Команды в чате:\n"
        "• /user ID — профиль пользователя\n"
        "• /addcredits ID сумма — начислить кредиты\n"
        "• /takecredits ID сумма — списать кредиты\n"
        f"• /setsub ID дни [{_plans_hint()}] — продлить подписку; бонусы по тарифу <b>не</b> начисляются (только при оплате)\n"
        f"• /setplan ID [{_plans_hint()}] — сменить тариф в БД <b>без</b> продления срока\n"
        "• /clearsub ID — снять подписку (срок и тариф)\n"
        "• /wipechat ID — очистить историю диалога у пользователя\n"
        "• /stats — сводка по пользователям, подпискам и кредитам",
        reply_markup=_main_kb(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == "adm:help")
async def adm_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = (
        "Команды:\n"
        "/stats — пользователи, подписки, кредиты, объём диалогов\n"
        "/user 123 — кредиты, подписка, тикет, сообщения в диалоге\n"
        "/addcredits 123 50\n"
        "/takecredits 123 20\n"
        "/setsub 123 30 — +30 дней (тариф в БД не трогаем); бонусы не начисляются\n"
        "/setsub 123 30 nova — +30 дней и запись тарифа; бонусы не начисляются\n"
        "/setplan 123 nova — только тариф, дата окончания без изменений\n"
        "/clearsub 123 — обнулить подписку\n"
        "/wipechat 123 — очистить dialog_messages\n"
        "/faq — шаблоны ответов для пользователей\n"
        "/chatid — id чата (в группе)"
    )
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text,
            reply_markup=_main_kb(),
            parse_mode=None,
        )
    await callback.answer()


@router.callback_query(F.data == "adm:tickets")
async def adm_tickets(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    n = await count_open_tickets()
    lines = await list_open_tickets_preview(limit=20)
    body = "\n".join(lines) if lines else "(нет открытых)"
    text = f"Открытых тикетов: {n}\n\n{body}"
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text[:4000],
            reply_markup=_main_kb(),
            parse_mode=None,
        )
    await callback.answer()


async def _main_bot_stats_text() -> str:
    users_n = await count_users_total()
    new7 = await count_new_users_days(7)
    sub_n = await count_users_active_subscription()
    credits_sum = await sum_users_credits()
    dialog_n = await count_dialog_messages_total()
    tickets_n = await count_open_tickets()
    return (
        "📊 Статистика основного бота\n\n"
        "Пользователи:\n"
        f"• Всего в базе: {users_n}\n"
        f"• Новых за 7 дней: {new7}\n"
        f"• С активной подпиской сейчас: {sub_n}\n\n"
        "Кредиты и диалоги:\n"
        f"• Сумма кредитов у всех: {credits_sum}\n"
        f"• Сообщений в историях диалогов (всего): {dialog_n}\n\n"
        "Поддержка (срез):\n"
        f"• Открытых тикетов сейчас: {tickets_n}\n\n"
        "Оценки и SLA по тикетам — в чате support-бота: /report, /sla."
    )


@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = await _main_bot_stats_text()
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text[:4000],
            reply_markup=_main_kb(),
            parse_mode=None,
        )
    await callback.answer()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    await message.answer((await _main_bot_stats_text())[:4000])


@router.callback_query(F.data == "adm:ratings")
async def adm_ratings(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    avg, rate_n = await get_support_rating_rollups()
    if rate_n == 0:
        text = "Пока нет оценок закрытых тикетов."
    else:
        text = (
            f"Средняя оценка поддержки: {avg:.2f} / 5\n"
            f"Всего ответов: {rate_n}\n\n"
            "Оценки собираются после того, как пользователь нажимает "
            "«вопрос решён» в чате поддержки — так мы видим качество ответов."
        )
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text,
            reply_markup=_main_kb(),
            parse_mode=None,
        )
    await callback.answer()


@router.message(Command("user"))
async def cmd_user_lookup(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат:\n/user 123456789")
        return
    uid = int(parts[1].strip())
    profile = await get_user_admin_profile(uid)
    if not profile:
        await message.answer("Пользователь не найден в базе (ни разу не писал боту).")
        return
    sub = profile.subscription_ends_at or "—"
    active = subscription_is_active(profile.subscription_ends_at)
    sub_human = "активна" if active else "не активна"
    plan = profile.subscription_plan or "—"
    last_buy = profile.subscription_last_purchase_at or "—"
    sub_line = f"Подписка: {sub_human}, до: {sub}, тариф: {plan}\nПоследняя покупка подписки (UTC): {last_buy}"
    ticket = await get_open_ticket_by_user(uid)
    ticket_line = (
        f"Открытый тикет: #{ticket.ticket_id}" if ticket else "Открытых тикетов нет"
    )
    msgs = await count_dialog_messages(uid)
    un = profile.username or "—"
    await message.answer(
        f"Пользователь {uid}\n"
        f"username в БД: {un}\n"
        f"Кредиты: {profile.credits}\n"
        f"{sub_line}\n"
        f"Регистрация в боте: {profile.created_at}\n"
        f"Сообщений в истории диалога: {msgs}\n"
        f"{ticket_line}"
    )


@router.message(Command("setsub"))
async def cmd_setsub(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    raw = (message.text or "").split()
    valid_plans = set(PLANS.keys())
    plan: str | None = None
    if len(raw) == 4:
        plan_raw = raw[3].strip().lower()
        if plan_raw in valid_plans:
            plan = plan_raw
        else:
            await message.answer(
                "Неизвестный тариф.\n"
                f"Доступно: {', '.join(PLANS_ORDER)}"
            )
            return
    elif len(raw) == 3:
        plan = None
    else:
        await message.answer(
            "Формат:\n"
            "/setsub USER_ID дни\n"
            f"/setsub USER_ID дни {_plans_hint()}"
        )
        return
    if not raw[1].isdigit() or not raw[2].isdigit():
        await message.answer("USER_ID и дни должны быть числами.")
        return
    uid = int(raw[1])
    days = int(raw[2])
    await ensure_user(uid, None)
    new_end = await extend_subscription(uid, days, plan)
    if not new_end:
        await message.answer("Не удалось продлить подписку (проверь ID и тариф).")
        return
    profile_after = await get_user_admin_profile(uid)
    end_h = format_subscription_ends_at(new_end)
    stored = (profile_after.subscription_plan or "").strip().lower() if profile_after else ""
    plan_lines: list[str] = []
    if plan:
        plan_lines.append(f"Тариф записан: {PLANS[plan].title} (<code>{esc(plan)}</code>)")
    elif stored and stored in PLANS:
        plan_lines.append(
            f"Тариф в профиле без изменений: {PLANS[stored].title} (<code>{esc(stored)}</code>)"
        )
    plan_block = ("\n" + "\n".join(plan_lines)) if plan_lines else ""
    await message.answer(
        f"Подписка пользователя <code>{uid}</code> продлена на <b>{days}</b> д.\n"
        f"Окончание: <b>{esc(end_h)}</b>{plan_block}\n\n"
        "<blockquote><i>Бонусные кредиты по тарифу при /setsub не начисляются (только при оплате).</i></blockquote>",
        parse_mode=HTML,
    )
    try:
        if stored and stored in PLANS:
            title_line = f"Подписка: <b>{esc(PLANS[stored].title)}</b>\n"
        elif plan and plan in PLANS:
            title_line = f"Подписка: <b>{esc(PLANS[plan].title)}</b>\n"
        else:
            title_line = "Подписка активирована.\n"
        await message.bot.send_message(
            uid,
            "<b>Вам выдана подписка администратором</b>\n\n"
            f"{title_line}"
            f"Добавлено дней: <b>{esc(days)}</b>\n"
            f"Действует до: <b>{esc(end_h)}</b>\n\n"
            "<i>Бонусные кредиты по тарифу при выдаче админом не начисляются (они есть только при покупке).</i>",
            parse_mode=HTML,
        )
    except Exception:
        logging.warning("setsub: не удалось уведомить пользователя uid=%s", uid, exc_info=True)


@router.message(Command("clearsub"))
async def cmd_clearsub(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат:\n/clearsub USER_ID")
        return
    uid = int(parts[1])
    await ensure_user(uid, None)
    await clear_subscription(uid)
    await message.answer(
        f"Подписка у пользователя <code>{uid}</code> снята (срок и тариф в БД обнулены).",
        parse_mode=HTML,
    )
    try:
        await message.bot.send_message(
            uid,
            "<b>Подписка снята администратором.</b> Доступ по тарифу отключён.",
            parse_mode=HTML,
        )
    except Exception:
        logging.warning("clearsub: не удалось уведомить uid=%s", uid, exc_info=True)


@router.message(Command("setplan"))
async def cmd_setplan(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    raw = (message.text or "").split()
    if len(raw) != 3:
        await message.answer(
            "Формат:\n"
            f"/setplan USER_ID {_plans_hint()}"
        )
        return
    if not raw[1].isdigit():
        await message.answer("USER_ID должен быть числом.")
        return
    plan = raw[2].strip().lower()
    if plan not in PLANS:
        await message.answer(
            "Неизвестный тариф.\n"
            f"Доступно: {', '.join(PLANS_ORDER)}"
        )
        return
    uid = int(raw[1])
    await ensure_user(uid, None)
    if not await set_subscription_plan_only(uid, plan):
        await message.answer("Не удалось обновить тариф.")
        return
    title = PLANS[plan].title
    await message.answer(
        f"Тариф пользователя <code>{uid}</code> установлен: <b>{esc(title)}</b> (<code>{esc(plan)}</code>). "
        "<blockquote><i>Срок окончания подписки не менялся — только поле тарифа в БД.</i></blockquote>",
        parse_mode=HTML,
    )
    try:
        await message.bot.send_message(
            uid,
            f"<b>Тариф в профиле обновлён администратором:</b> {esc(title)}.\n"
            "<i>Дата окончания подписки не продлевалась.</i>",
            parse_mode=HTML,
        )
    except Exception:
        logging.warning("setplan: не удалось уведомить uid=%s", uid, exc_info=True)


@router.message(Command("wipechat"))
async def cmd_wipechat(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Формат:\n/wipechat 123456789")
        return
    uid = int(parts[1].strip())
    await clear_dialog_messages(uid)
    await message.answer(f"История диалога для {uid} очищена.")


# keep addcredits/takecredits in commands.py — already there
