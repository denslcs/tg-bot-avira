import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
    get_open_ticket_by_user,
    list_all_user_ids,
    get_support_rating_rollups,
    get_user_admin_profile,
    list_open_tickets_preview,
    set_subscription_plan_only,
    subscription_is_active,
    sum_users_credits,
)
from src.formatting import HTML, esc, format_subscription_ends_at
from src.handlers.commands import edit_or_send_nav_message
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS
from src.subscription_catalog import PLANS, PLANS_ORDER

router = Router(name="admin_panel")

_PLAN_PREMIUM_EMOJI_IDS: dict[str, str] = {
    "nova": "5242331214848756985",
    "supernova": "5242714407535939345",
    "galaxy": "5242227706136924612",
    "universe": "5242285645245745392",
}


def _plan_title_html(plan_id: str) -> str:
    pid = (plan_id or "").strip().lower()
    if pid in PLANS:
        raw_title = PLANS[pid].title
    else:
        raw_title = pid or "—"
    title_wo_emoji = raw_title.split(" ", 1)[-1]
    emoji_id = _PLAN_PREMIUM_EMOJI_IDS.get(pid)
    if not emoji_id:
        return esc(raw_title)
    return f'<tg-emoji emoji-id="{emoji_id}">🤩</tg-emoji> {esc(title_wo_emoji)}'


def _plans_hint() -> str:
    return "|".join(PLANS_ORDER)


def _plans_readable() -> str:
    return ", ".join(PLANS_ORDER)


def _admin_home_html() -> str:
    """Главный экран /admin: структура, короткие блоки, единый стиль."""
    tariffs = esc(_plans_readable())
    return (
        "<b>🛡️ Админ-панель · Shard Creator</b>\n\n"
        "<blockquote><i>Быстрые отчёты — кнопками ниже. Команды вводите в этот чат.</i></blockquote>\n\n"
        "<b>👤 Пользователь и баланс</b>\n"
        "• <code>/user ID</code> — кредиты, подписка, тикет, число сообщений в диалоге\n"
        "• <code>/addcredits ID сумма</code> · <code>/takecredits ID сумма</code>\n\n"
        "<b>📅 Подписка</b>\n"
        "• <code>/setsub ID дни</code> — продлить срок; в конце можно указать тариф\n"
        f"• Тарифы: <i>{tariffs}</i>\n"
        "• <code>/setplan ID тариф</code> — сменить тариф в БД <b>без</b> сдвига даты окончания\n"
        "• <code>/clearsub ID</code> — снять подписку (срок и тариф)\n"
        "<blockquote><i>Бонусные кредиты по тарифу при ручной выдаче не начисляются — только при оплате Stars.</i></blockquote>\n\n"
        "<b>🗑 Диалог и рассылка</b>\n"
        "• <code>/wipechat ID</code> — очистить историю диалога у пользователя\n"
        "• <code>/broadcast текст</code> — рассылка в ЛС всем из базы\n\n"
        "<b>📎 Прочее</b>\n"
        "• <code>/stats</code> — сводка (дублирует кнопку «Статистика»)\n"
        "• <code>/faq</code> — шаблоны ответов · <code>/chatid</code> — ID чата в группе"
    )


def _admin_help_html() -> str:
    """Подробная шпаргалка (кнопка «Справка»)."""
    p = esc(_plans_readable())
    return (
        "<b>📖 Справка по командам</b>\n\n"
        "<b>Обзор</b>\n"
        "• <code>/stats</code> — пользователи, подписки, кредиты, объём диалогов\n"
        "• <code>/user ID</code> — полная карточка пользователя\n\n"
        "<b>Кредиты</b>\n"
        "• <code>/addcredits ID сумма</code>\n"
        "• <code>/takecredits ID сумма</code>\n\n"
        "<b>Подписка</b>\n"
        "• <code>/setsub ID дни</code> — +дни к сроку; тариф в БД не меняется, если не указать\n"
        f"• Пример с тарифом: <code>/setsub 123 30 nova</code> · доступно: <i>{p}</i>\n"
        "• <code>/setplan ID тариф</code> — только тариф, дата окончания без изменений\n"
        "• <code>/clearsub ID</code> — обнулить подписку\n\n"
        "<b>Сервис</b>\n"
        "• <code>/wipechat ID</code> — очистить <code>dialog_messages</code>\n"
        "• <code>/broadcast текст</code> — рассылка в ЛС\n"
        "• <code>/faq</code> — шаблоны для пользователей\n"
        "• <code>/chatid</code> — ID чата (в группе)"
    )


def _main_kb_rows(*, with_home: bool) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🎫 Тикеты",
                callback_data="adm:tickets",
                style=BTN_PRIMARY,
            ),
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data="adm:stats",
                style=BTN_SUCCESS,
            ),
        ],
        [
            InlineKeyboardButton(
                text="⭐ Оценки поддержки",
                callback_data="adm:ratings",
                style=BTN_PRIMARY,
            ),
        ],
        [
            InlineKeyboardButton(
                text="📋 Справка по командам",
                callback_data="adm:help",
                style=BTN_PRIMARY,
            ),
        ],
    ]
    if with_home:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ К обзору",
                    callback_data="adm:home",
                    style=BTN_PRIMARY,
                ),
            ]
        )
    return rows


def _main_kb() -> InlineKeyboardMarkup:
    """Главный экран /admin — без лишней кнопки «назад»."""
    return InlineKeyboardMarkup(inline_keyboard=_main_kb_rows(with_home=False))


def _main_kb_nav() -> InlineKeyboardMarkup:
    """Внутренние экраны панели — с возвратом к обзору."""
    return InlineKeyboardMarkup(inline_keyboard=_main_kb_rows(with_home=True))


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return
    await message.answer(
        _admin_home_html(),
        reply_markup=_main_kb(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == "adm:home")
async def adm_home(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=_admin_home_html(),
            reply_markup=_main_kb(),
            parse_mode=HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "adm:help")
async def adm_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=_admin_help_html(),
            reply_markup=_main_kb_nav(),
            parse_mode=HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "adm:tickets")
async def adm_tickets(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    n = await count_open_tickets()
    lines = await list_open_tickets_preview(limit=20)
    if not lines:
        body = "<i>Открытых обращений нет.</i>"
    else:
        body = "\n".join(f"• <code>{esc(line)}</code>" for line in lines)
    text = (
        "<b>🎫 Открытые тикеты</b>\n"
        f"<blockquote><i>Всего открытых: <b>{esc(n)}</b></i></blockquote>\n\n"
        f"{body}"
    )
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text[:4000],
            reply_markup=_main_kb_nav(),
            parse_mode=HTML,
        )
    await callback.answer()


async def _main_bot_stats_html() -> str:
    users_n = await count_users_total()
    new7 = await count_new_users_days(7)
    sub_n = await count_users_active_subscription()
    credits_sum = await sum_users_credits()
    dialog_n = await count_dialog_messages_total()
    tickets_n = await count_open_tickets()
    return (
        "<b>📊 Статистика бота</b>\n"
        "<blockquote><i>Срез на сейчас</i></blockquote>\n\n"
        "<b>Пользователи</b>\n"
        f"• В базе: <b>{esc(users_n)}</b>\n"
        f"• Новых за 7 дней: <b>{esc(new7)}</b>\n"
        f"• С активной подпиской: <b>{esc(sub_n)}</b>\n\n"
        "<b>Кредиты и диалоги</b>\n"
        f"• Сумма кредитов (все пользователи): <b>{esc(credits_sum)}</b>\n"
        f"• Сообщений в историях диалогов (всего): <b>{esc(dialog_n)}</b>\n\n"
        '<b><tg-emoji emoji-id="5443038326535759644">💬</tg-emoji> Поддержка</b>\n'
        f"• Открытых тикетов: <b>{esc(tickets_n)}</b>\n\n"
        "<i>Подробные оценки и SLA — в support-боте:</i> <code>/report</code>, <code>/sla</code>"
    )


@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = await _main_bot_stats_html()
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text[:4000],
            reply_markup=_main_kb_nav(),
            parse_mode=HTML,
        )
    await callback.answer()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    await message.answer((await _main_bot_stats_html())[:4000], parse_mode=HTML)


_BROADCAST_DELAY_SEC = 0.035


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Формат:\n"
            "/broadcast Текст одним сообщением (до 4096 символов).\n\n"
            "Уйдёт всем user_id из базы — тем, кто хоть раз писал боту или нажал /start. "
            "Кто заблокировал бота, попадёт в счётчик «не доставлено»."
        )
        return
    text = parts[1].strip()
    if len(text) > 4096:
        await message.answer("Слишком длинно: максимум 4096 символов.")
        return
    user_ids = await list_all_user_ids()
    status_msg = await message.answer(
        f"Рассылка… получателей в базе: {len(user_ids)}. Это может занять время."
    )
    ok = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=text)
            ok += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            failed += 1
        except Exception:
            failed += 1
            logging.exception("broadcast uid=%s", uid)
        await asyncio.sleep(_BROADCAST_DELAY_SEC)
    try:
        await status_msg.edit_text(
            f"Готово.\n• Доставлено: {ok}\n• Не удалось: {failed}\n• Всего в базе: {len(user_ids)}"
        )
    except Exception:
        await message.answer(
            f"Готово.\n• Доставлено: {ok}\n• Не удалось: {failed}\n• Всего в базе: {len(user_ids)}"
        )


@router.callback_query(F.data == "adm:ratings")
async def adm_ratings(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    avg, rate_n = await get_support_rating_rollups()
    if rate_n == 0:
        text = (
            "<b>⭐ Оценки поддержки</b>\n\n"
            "<i>Пока нет оценок по закрытым тикетам.</i>"
        )
    else:
        text = (
            "<b>⭐ Оценки поддержки</b>\n"
            f"<blockquote>Средняя оценка: <b>{avg:.2f}</b> из 5 · ответов с оценкой: <b>{esc(rate_n)}</b></blockquote>\n"
            "<i>Оценка ставится, когда пользователь отмечает «вопрос решён» в чате поддержки.</i>"
        )
    if callback.message:
        await edit_or_send_nav_message(
            callback.message,
            text=text,
            reply_markup=_main_kb_nav(),
            parse_mode=HTML,
        )
    await callback.answer()


@router.message(Command("user"))
async def cmd_user_lookup(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для администраторов.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(
            "<b>Формат</b>\n<code>/user ID</code>\n<i>Пример:</i> <code>/user 123456789</code>",
            parse_mode=HTML,
        )
        return
    uid = int(parts[1].strip())
    profile = await get_user_admin_profile(uid)
    if not profile:
        await message.answer("Пользователь не найден в базе (ни разу не писал боту).")
        return
    active = subscription_is_active(profile.subscription_ends_at)
    sub_human = "активна ✔️" if active else "не активна"
    sub_till = (
        format_subscription_ends_at(profile.subscription_ends_at)
        if profile.subscription_ends_at
        else "—"
    )
    pr = profile.subscription_plan
    if pr and pr in PLANS:
        plan_line = f"{_plan_title_html(pr)} (<code>{esc(pr)}</code>)"
    elif pr:
        plan_line = esc(pr)
    else:
        plan_line = "—"
    last_buy = (
        format_subscription_ends_at(profile.subscription_last_purchase_at)
        if profile.subscription_last_purchase_at
        else "—"
    )
    ticket = await get_open_ticket_by_user(uid)
    if ticket:
        ticket_block = f"<i>Открыт тикет</i> <code>#{esc(ticket.ticket_id)}</code>"
    else:
        ticket_block = "<i>Открытых тикетов нет</i>"
    msgs = await count_dialog_messages(uid)
    un_html = f"@{esc(profile.username)}" if profile.username else "—"
    await message.answer(
        "<b>👤 Карточка пользователя</b>\n"
        "<blockquote>"
        f"<i>Telegram ID:</i> <code>{esc(uid)}</code>\n"
        f"<i>Username в БД:</i> <b>{un_html}</b>\n"
        f'<i><tg-emoji emoji-id="5305699699204837855">🍀</tg-emoji> Кредиты:</i> <b>{esc(profile.credits)}</b>\n'
        f"<i>В боте с:</i> <code>{esc(profile.created_at)}</code>\n"
        f"<i>Сообщений в диалоге:</i> <b>{esc(msgs)}</b>\n"
        "</blockquote>\n"
        "<b>📅 Подписка</b>\n"
        "<blockquote>"
        f"<i>Статус:</i> <b>{esc(sub_human)}</b>\n"
        f"<i>Действует до:</i> {esc(sub_till)}\n"
        f"<i>Тариф:</i> {plan_line}\n"
        f"<i>Последняя покупка подписки:</i> {esc(last_buy)}\n"
        "</blockquote>\n"
        f"<b>🎫 Поддержка</b>\n{ticket_block}",
        parse_mode=HTML,
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
        plan_lines.append(f"Тариф записан: {_plan_title_html(plan)} (<code>{esc(plan)}</code>)")
    elif stored and stored in PLANS:
        plan_lines.append(
            f"Тариф в профиле без изменений: {_plan_title_html(stored)} (<code>{esc(stored)}</code>)"
        )
    plan_block = ("\n" + "\n".join(plan_lines)) if plan_lines else ""
    is_active_now = bool(
        profile_after and subscription_is_active(profile_after.subscription_ends_at)
    )
    active_line = (
        f"\n<i>Проверка бота:</i> подписка сейчас <b>{'активна' if is_active_now else 'не активна'}</b> "
        f"(как в <code>/profile</code> у пользователя и в <code>/user {uid}</code>)."
    )
    if not is_active_now:
        active_line += (
            "\n⚠️ Ожидалось «активна» — проверь значение <code>subscription_ends_at</code> в БД или перезапусти бота."
        )
    await message.answer(
        f"Подписка пользователя <code>{uid}</code> продлена на <b>{days}</b> д.\n"
        f"Окончание: <b>{esc(end_h)}</b>{plan_block}{active_line}\n\n"
        "<blockquote><i>Бонусные кредиты по тарифу при /setsub не начисляются (только при оплате).</i></blockquote>",
        parse_mode=HTML,
    )
    try:
        if stored and stored in PLANS:
            title_line = f"Подписка: <b>{_plan_title_html(stored)}</b>\n"
        elif plan and plan in PLANS:
            title_line = f"Подписка: <b>{_plan_title_html(plan)}</b>\n"
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
    title = _plan_title_html(plan)
    await message.answer(
        f"Тариф пользователя <code>{uid}</code> установлен: <b>{title}</b> (<code>{esc(plan)}</code>). "
        "<blockquote><i>Срок окончания подписки не менялся — только поле тарифа в БД.</i></blockquote>",
        parse_mode=HTML,
    )
    try:
        await message.bot.send_message(
            uid,
            f"<b>Тариф в профиле обновлён администратором:</b> {title}.\n"
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
