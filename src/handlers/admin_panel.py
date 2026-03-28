from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import ADMIN_IDS
from src.database import (
    add_subscription_days,
    clear_dialog_messages,
    count_dialog_messages,
    count_open_tickets,
    count_users_total,
    ensure_user,
    get_open_ticket_by_user,
    get_support_rating_rollups,
    get_user_admin_profile,
    list_open_tickets_preview,
    subscription_is_active,
)

router = Router(name="admin_panel")


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Открытые тикеты", callback_data="adm:tickets"),
                InlineKeyboardButton(text="Статистика", callback_data="adm:stats"),
            ],
            [
                InlineKeyboardButton(text="Оценки поддержки", callback_data="adm:ratings"),
            ],
            [
                InlineKeyboardButton(text="Справка по командам", callback_data="adm:help"),
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
        "• /setsub ID дни — продлить подписку на N дней\n"
        "• /wipechat ID — очистить историю диалога у пользователя",
        reply_markup=_main_kb(),
    )


@router.callback_query(F.data == "adm:help")
async def adm_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = (
        "Команды:\n"
        "/user 123 — кредиты, подписка, тикет, сообщения в диалоге\n"
        "/addcredits 123 50\n"
        "/takecredits 123 20\n"
        "/setsub 123 30 — +30 дней подписки от текущего срока или от сейчас\n"
        "/wipechat 123 — очистить dialog_messages\n"
        "/faq — шаблоны ответов для пользователей\n"
        "/chatid — id чата (в группе)"
    )
    if callback.message:
        await callback.message.answer(text)
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
        await callback.message.answer(text[:4000])
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    users_n = await count_users_total()
    tickets_n = await count_open_tickets()
    avg, rate_n = await get_support_rating_rollups()
    avg_txt = f"{avg:.2f}" if avg is not None else "—"
    text = (
        f"Пользователей в базе: {users_n}\n"
        f"Открытых тикетов: {tickets_n}\n"
        f"Оценок поддержки: {rate_n} (средняя {avg_txt} из 5)"
    )
    if callback.message:
        await callback.message.answer(text)
    await callback.answer()


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
        await callback.message.answer(text)
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
    sub_line = f"Подписка: {sub_human}, до: {sub}"
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
    if len(raw) != 3 or not raw[1].isdigit() or not raw[2].isdigit():
        await message.answer("Формат:\n/setsub 123456789 30\n(+30 дней подписки)")
        return
    uid = int(raw[1])
    days = int(raw[2])
    await ensure_user(uid, None)
    new_end = await add_subscription_days(uid, days)
    if not new_end:
        await message.answer("Не удалось продлить подписку.")
        return
    await message.answer(
        f"Подписка для {uid} продлена на {days} д.\n"
        f"Новая дата окончания (UTC): {new_end}"
    )


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
