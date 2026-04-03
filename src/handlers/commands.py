"""
Команды и главное меню: /start, профиль, рефералка, справка, часть админ-команд в ЛС.
Клавиатура старта: src/keyboards/main_menu.py.
"""

import logging
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
)

from src.config import ADMIN_IDS, PROJECT_ROOT, SUPPORT_BOT_USERNAME
from src.antispam_state import reset_user_spam
from src.private_rate_limit import reset_private_rate
from src.database import (
    add_credits,
    apply_referral,
    clear_dialog_messages,
    count_generated_images_total,
    ensure_user,
    get_credits,
    get_nonsub_image_quota_status,
    get_referral_count,
    get_user_admin_profile,
    subscription_is_active,
    take_credits,
)
from src.subscription_catalog import NONSUB_IMAGE_WINDOW_DAYS, PLANS
from src.formatting import HTML, esc, format_subscription_ends_at
from src.keyboards.callback_data import (
    CB_IMG_OK,
    CB_MENU_ABOUT,
    CB_MENU_BACK_START,
    CB_MENU_PROFILE,
    CB_MENU_REF,
    CB_MENU_REF_LEGACY,
    CB_MENU_SUPPORT,
    CB_REGEN,
)
from src.keyboards.main_menu import back_to_main_menu_keyboard, start_menu_keyboard
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS

router = Router(name="commands")

_BACK_TO_MENU_ROW = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]


def _main_screen_text(balance: int, bonus_note: str = "") -> str:
    bonus_html = esc(bonus_note) if bonus_note else ""
    return (
        "🖼 <b>Создай или измени фото</b> с помощью ИИ.\n\n"
        "<b>Главное:</b> 🎨 <i>Создать картинку</i> и 💡 <i>Готовые идеи</i>.\n"
        "<i>Остальное — профиль, оплата, поддержка и справка.</i>\n\n"
        "<blockquote><i>Открой «Профиль», чтобы посмотреть баланс, статус подписки и лимиты.</i>"
        f"{bonus_html}</blockquote>"
    )


def _days_in_bot(created_at: str) -> int:
    text = (created_at or "").strip()
    if not text:
        return 0
    candidates = (text.replace("Z", "+00:00"), text)
    dt: datetime | None = None
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            break
        except ValueError:
            continue
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _start_banner_path() -> Path | None:
    """Баннер приветствия /start. Файл: assets/start/start_banner.png"""
    p = PROJECT_ROOT / "assets" / "start" / "start_banner.png"
    return p if p.is_file() else None


def _is_generated_image_result_message(message: Message) -> bool:
    """Сообщение с готовой картинкой из генерации — такие не трогаем при смене меню."""
    if not message.photo:
        return False
    cap = message.caption or ""
    if "Картинка сохранена" in cap:
        return True
    if "Готово" in cap and "✅" in cap:
        return True
    kb = message.reply_markup
    if kb and kb.inline_keyboard:
        for row in kb.inline_keyboard:
            for btn in row:
                cd = getattr(btn, "callback_data", None)
                if cd in (CB_REGEN, CB_IMG_OK, "img:save"):
                    return True
    return False


async def delete_nav_source_message(message: Message | None) -> None:
    """Удалить сообщение с кнопкой навигации, если API позволяет (чтобы не копить чат)."""
    if message is None:
        return
    if _is_generated_image_result_message(message):
        return
    try:
        await message.delete()
    except Exception:
        logging.debug("delete_nav_source_message: не удалось удалить сообщение", exc_info=True)


async def send_main_menu_screen(
    bot: Bot,
    chat_id: int,
    user_id: int,
    username: str | None,
) -> None:
    """Главный экран как после /start: баланс в тексте, меню, при наличии — фото-баннер."""
    await ensure_user(user_id, username)
    balance = await get_credits(user_id)
    text = _main_screen_text(balance, "")
    kb = start_menu_keyboard()
    banner = _start_banner_path()
    if banner:
        await bot.send_photo(
            chat_id,
            photo=FSInputFile(banner),
            caption=text,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=HTML)


async def restore_main_menu_message(message: Message, user_id: int, username: str | None) -> None:
    """Удалить текущее сообщение меню и отправить главный экран заново."""
    chat_id = message.chat.id
    await delete_nav_source_message(message)
    await send_main_menu_screen(message.bot, chat_id, user_id, username)


def _parse_ref_start_arg(args: str | None) -> int | None:
    """Аргумент команды /start (диплинк t.me/bot?start=ref_<id>)."""
    if not args:
        return None
    rest = args.strip()
    if not rest:
        return None
    first = rest.split()[0]
    payload = first[4:] if first.startswith("ref_") else first
    if payload.isdigit():
        return int(payload)
    return None


def _parse_ref_payload(raw_text: str) -> int | None:
    """Fallback: полный текст сообщения, если args недоступен."""
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return _parse_ref_start_arg(parts[1])


@router.message(Command("start", ignore_mention=True))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject) -> None:
    if not message.from_user:
        return

    await state.clear()
    user_id = message.from_user.id
    await ensure_user(user_id, message.from_user.username)
    raw = (message.text or message.caption or "").strip()
    referrer_id = _parse_ref_start_arg(command.args)
    if referrer_id is None and raw:
        referrer_id = _parse_ref_payload(raw)
    if referrer_id is None and raw and ("ref_" in raw or raw.split(maxsplit=1)[-1].strip().isdigit()):
        logging.warning(
            "referral: не распарсили диплинк raw=%r command.args=%r",
            raw,
            command.args,
        )
    bonus_note = ""
    if referrer_id:
        # Пригласитель должен быть в БД, иначе apply_referral тихо вернёт False
        await ensure_user(referrer_id, None)
        applied = await apply_referral(invitee_user_id=user_id, inviter_user_id=referrer_id)
        if applied:
            bonus_note = "\n🎉 Реферальный бонус: тебе +5 кредитов."
            logging.info("referral applied: invitee=%s inviter=%s", user_id, referrer_id)
    balance = await get_credits(user_id)

    text = _main_screen_text(balance, bonus_note)
    kb = start_menu_keyboard()
    banner = _start_banner_path()
    if banner:
        await message.answer_photo(
            FSInputFile(banner),
            caption=text,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await message.answer(text, reply_markup=kb, parse_mode=HTML)


@router.callback_query(F.data == CB_MENU_BACK_START)
async def menu_back_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await state.clear()
    user_id = callback.from_user.id
    await callback.answer()
    await restore_main_menu_message(callback.message, user_id, callback.from_user.username)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📌 <b>Что доступно</b>\n\n"
        "🏠 <code>/start</code> — <i>главное меню, баланс и картинки</i>\n"
        "❓ <code>/help</code> — <i>этот список</i>\n"
        "💳 <code>/pay</code> — <i>подписка и оплата</i>\n"
        "👤 <code>/profile</code> — <i>статус аккаунта и подписки</i>\n"
        "👥 <code>/ref</code> — <i>реферальная система</i>\n"
        "💡 <code>/ideas</code> — <i>готовые промпты для картинок</i>\n"
        "📋 <code>/faq</code> — <i>частые вопросы</i>\n"
        "🔄 <code>/newchat</code> или <code>/clear</code> — <i>очистить память диалога</i>\n"
        "💬 <code>/support</code> — <i>обращение в поддержку</i>\n"
        "✅ <code>/resolved</code> — <i>закрыть тикет (в боте поддержки)</i>\n"
        "🆔 <code>/myid</code> — <i>твой Telegram ID</i>\n\n"
        "<blockquote>🎨 Картинки — через кнопки в <code>/start</code>.</blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_MENU_ABOUT)
async def menu_about(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    await callback.answer()
    text = (
        "<b>Что умеет бот</b>\n"
        "<blockquote>"
        "• Сгенерировать картинку по тексту.\n"
        "• Готовые идеи — пресеты промптов (если добавлены)."
        "</blockquote>"
    )
    await delete_nav_source_message(callback.message)
    await callback.bot.send_message(
        chat_id,
        text,
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_MENU_SUPPORT)
async def menu_support(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    await callback.answer()
    if not SUPPORT_BOT_USERNAME:
        await delete_nav_source_message(callback.message)
        await callback.bot.send_message(
            chat_id,
            (
                "<blockquote><i>Поддержка пока не настроена</i> "
                "(пустой <code>SUPPORT_BOT_USERNAME</code>).</blockquote>"
            ),
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_avira"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть поддержку", url=support_url)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await delete_nav_source_message(callback.message)
    await callback.bot.send_message(
        chat_id,
        "<b>Поддержка</b>\n<i>Нажми кнопку ниже,</i> чтобы открыть чат.",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


def _referral_share_url(bot_username: str | None, user_id: int) -> str:
    """Ссылка для кнопки «Пригласить»: открывает шаринг в Telegram без callback."""
    text_share = "Заходи в Avira по моей ссылке 👇"
    if bot_username:
        ref_https = f"https://t.me/{bot_username}?start=ref_{user_id}"
        return "https://t.me/share/url?" + urllib.parse.urlencode(
            {"url": ref_https, "text": text_share}
        )
    ref_plain = f"/start ref_{user_id}"
    return "https://t.me/share/url?" + urllib.parse.urlencode({"text": f"{text_share}\n{ref_plain}"})


async def _build_referral_message(
    user_id: int,
    username: str | None,
    bot_username: str | None,
) -> tuple[str, InlineKeyboardMarkup]:
    """bot_username — из await bot.me(); у aiogram.Bot нет атрибута .username."""
    await ensure_user(user_id, username)
    try:
        invited = await get_referral_count(user_id)
    except Exception:
        invited = 0
    try:
        balance = await get_credits(user_id)
    except Exception:
        balance = 0
    ref_link = (
        f"https://t.me/{bot_username}?start=ref_{user_id}"
        if bot_username
        else f"/start ref_{user_id}"
    )
    uname_html = f"@{esc(username)}" if username else "<i>без username</i>"
    text = (
        "<b>👥 Реферальная программа</b>\n\n"
        "<blockquote>"
        f"<i>👤 Профиль</i> {uname_html}\n"
        f"<i>💳 ID</i> <code>{esc(user_id)}</code>\n"
        f"<i>💵 Кредиты</i> <b>{esc(balance)}</b>\n"
        f"<i>✉️ Приглашения</i> <b>{esc(invited)}</b>"
        "</blockquote>\n\n"
        "<blockquote><i>"
        "За каждого приглашённого друга — <b>+10</b> кредитов тебе. "
        "Новому пользователю при первом <code>/start</code> по твоей ссылке — <b>+5</b> кредитов."
        "</i></blockquote>\n\n"
        "<b>🔗 Твоя ссылка</b>\n"
        f"<code>{esc(ref_link)}</code>"
    )
    share_url = _referral_share_url(bot_username, user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Пригласить", url=share_url, style=BTN_SUCCESS)],
            _BACK_TO_MENU_ROW,
        ]
    )
    return text, kb


async def deliver_referral_screen(bot: Bot, user_id: int, username: str | None, reply_via: Message | None) -> None:
    """Отправить экран рефералки (callback или команда /ref)."""
    me = await bot.me()
    text, kb = await _build_referral_message(user_id, username, me.username)
    try:
        if reply_via:
            chat_id = reply_via.chat.id
            await delete_nav_source_message(reply_via)
            await bot.send_message(
                chat_id,
                text=text,
                reply_markup=kb,
                parse_mode=HTML,
                disable_web_page_preview=True,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=kb,
                parse_mode=HTML,
                disable_web_page_preview=True,
            )
    except Exception:
        logging.exception("deliver_referral_screen: не удалось отправить сообщение с реферальной ссылкой")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="Не удалось показать реферальное сообщение. Нажми /start и попробуй снова.",
            )
        except Exception:
            logging.exception("deliver_referral_screen: не удалось отправить сообщение об ошибке")


@router.callback_query((F.data == CB_MENU_REF) | (F.data == CB_MENU_REF_LEGACY))
async def menu_ref(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    # Сразу снимаем «часики» у кнопки; иначе клиент ждёт до конца отправки сообщения (до ~20 с).
    await callback.answer()
    await deliver_referral_screen(
        callback.bot,
        callback.from_user.id,
        callback.from_user.username,
        callback.message,
    )


@router.message(Command("ref"))
async def cmd_ref(message: Message) -> None:
    if not message.from_user:
        return
    await deliver_referral_screen(message.bot, message.from_user.id, message.from_user.username, message)


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if not message.from_user:
        return

    await send_profile_card(message, message.from_user.id, message.from_user.username)


@router.callback_query(F.data == CB_MENU_PROFILE)
async def menu_profile(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await callback.answer()
    await send_profile_card(
        callback.message,
        callback.from_user.id,
        callback.from_user.username,
        edit_existing=True,
    )


async def _profile_card_html(user_id: int, username_raw: str | None) -> tuple[str, InlineKeyboardMarkup]:
    """Текст профиля и клавиатура «Назад» (не вызывать для админов — у них отдельный экран)."""
    await ensure_user(user_id, username_raw)
    profile = await get_user_admin_profile(user_id)
    if not profile:
        missing = (
            "<blockquote><i>Профиль пока не найден. Нажми</i> <code>/start</code> <i>и попробуй снова.</i></blockquote>"
        )
        return missing, back_to_main_menu_keyboard()
    balance = await get_credits(user_id)
    username = f"@{profile.username}" if profile.username else "—"
    active_sub = subscription_is_active(profile.subscription_ends_at)
    if active_sub:
        sub_status = "активна"
        sub_till = format_subscription_ends_at(profile.subscription_ends_at)
        plan_name = (
            PLANS[profile.subscription_plan].title
            if profile.subscription_plan and profile.subscription_plan in PLANS
            else "—"
        )
    else:
        sub_status = "не активна"
        sub_till = (
            format_subscription_ends_at(profile.subscription_ends_at)
            if profile.subscription_ends_at
            else "—"
        )
        plan_name = "—"
    gen_total = await count_generated_images_total(user_id)
    days_in_bot = _days_in_bot(profile.created_at)
    if active_sub:
        img_limits_line = (
            "<i>Картинки:</i> без дневного лимита, списание по кредитам.\n"
        )
    else:
        fu, flim = await get_nonsub_image_quota_status(user_id)
        img_limits_line = (
            f"<i>Генераций картинок без подписки за {NONSUB_IMAGE_WINDOW_DAYS} дн. (UTC):</i> "
            f"<b>{esc(fu)}/{esc(flim)}</b> (со списанием кредитов; дальше — только подписка или сброс окна).\n"
        )
    body = (
        "<b>👤 Профиль</b>\n"
        "<blockquote>"
        f"<i>Ник:</i> <b>{esc(username)}</b>\n"
        f"<i>ID:</i> <code>{esc(user_id)}</code>\n"
        f"<i>💰 Кредиты:</i> <b>{esc(balance)}</b>\n"
        f"<i>Подписка:</i> <b>{esc(sub_status)}</b>\n"
        f"<i>Тариф:</i> <b>{esc(plan_name)}</b>\n"
        f"<i>Окончание:</i> <b>{esc(sub_till)}</b>\n"
        f"<i>Сгенерировано изображений:</i> <b>{esc(gen_total)}</b>\n"
        f"<i>Дней в боте:</i> <b>{esc(days_in_bot)}</b>\n"
        f"{img_limits_line}"
        "</blockquote>"
    )
    return body, back_to_main_menu_keyboard()


async def send_profile_card(
    message: Message,
    user_id: int,
    username_raw: str | None,
    *,
    edit_existing: bool = False,
) -> None:
    if user_id in ADMIN_IDS:
        text = "<blockquote><b>Режим админа</b> — безлимит по кредитам.</blockquote>"
        kb = back_to_main_menu_keyboard()
        if edit_existing:
            chat_id = message.chat.id
            await delete_nav_source_message(message)
            await message.bot.send_message(chat_id, text, reply_markup=kb, parse_mode=HTML)
        else:
            await message.answer(text, reply_markup=kb, parse_mode=HTML)
        return
    text, kb = await _profile_card_html(user_id, username_raw)
    if edit_existing:
        chat_id = message.chat.id
        await delete_nav_source_message(message)
        await message.bot.send_message(chat_id, text, reply_markup=kb, parse_mode=HTML)
    else:
        await message.answer(text, reply_markup=kb, parse_mode=HTML)


@router.message(Command("newchat"))
@router.message(Command("clear"))
async def cmd_newchat(message: Message) -> None:
    if not message.from_user:
        return
    await clear_dialog_messages(message.from_user.id)
    reset_user_spam(message.from_user.id)
    reset_private_rate(message.from_user.id)
    await message.answer(
        "<b>Готово ✅</b>\n"
        "<blockquote><i>История этого диалога очищена.</i> Можно начать новую тему.</blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.message(Command("resolved"))
async def cmd_resolved_main(message: Message) -> None:
    """В основном боте тикеты ведёт support-бот — направляем пользователя туда."""
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "<blockquote><i>Чат поддержки не подключён.</i> Нужен "
            "<code>SUPPORT_BOT_USERNAME</code> в <code>.env</code>.</blockquote>",
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть поддержку и закрыть тикет", url=support_url)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await message.answer(
        "<b>Закрытие тикета</b>\n"
        "<blockquote>Тикеты ведутся в <i>отдельном боте поддержки</i>. "
        "Команду <code>/resolved</code> отправь там, где открывал обращение.</blockquote>",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "<blockquote><i>Поддержка не подключена</i> — проверь <code>SUPPORT_BOT_USERNAME</code> в <code>.env</code>.</blockquote>",
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_avira"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть чат поддержки", url=support_url, style=BTN_PRIMARY)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await message.answer(
        "<b>Поддержка</b>\n"
        "<blockquote><i>Отдельный чат для тикетов.</i> Нажми кнопку ниже.</blockquote>",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(
        f"<blockquote><code>{esc(message.from_user.id)}</code> — <i>твой Telegram ID</i></blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """ID группы и темы для .env (ADMIN_SALES_*): в группе/топике или подсказка в ЛС."""
    if not message.from_user:
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    chat = message.chat
    if chat.type == "private":
        o = message.forward_origin
        cid: int | None = None
        if o is not None:
            if isinstance(o, MessageOriginChat) and o.sender_chat:
                cid = o.sender_chat.id
            elif isinstance(o, MessageOriginChannel) and o.chat:
                cid = o.chat.id
        if cid is None:
            await message.answer(
                "<b>Как узнать ID для уведомлений о покупках</b>\n\n"
                "1) Добавь <b>этого же бота</b> в свою админ-группу (форум с темами).\n"
                "2) <b>ID группы</b> — напиши в группе команду <code>/chatid</code> "
                "(можно в любой теме или в «Общем»). Бот пришлёт число вида <code>-100…</code> "
                "— его клади в <code>ADMIN_SALES_NOTIFY_CHAT_ID</code>.\n"
                "3) <b>ID каждой темы</b> — зайди <i>внутрь темы</i> (Nova, Galaxy и т.д.) и "
                "в этой теме снова напиши <code>/chatid</code>. Появится "
                "<code>message_thread_id</code> — его в соответствующий "
                "<code>ADMIN_SALES_THREAD_*</code> в <code>.env</code>.\n"
                "4) Повтори шаг 3 для всех пяти тем.\n"
                "5) Перезапусти бота.\n\n"
                "<blockquote><i>Если написать <code>/chatid</code> только в личке без пересылки — "
                "показывается эта памятка. Пересланное из группы иногда даёт только chat id, "
                "без id темы — надёжнее писать <code>/chatid</code> прямо в каждой теме.</i></blockquote>",
                parse_mode=HTML,
            )
            return
        lines = [
            "<b>Пересланное из группы/канала</b>",
            f"<b>chat id:</b> <code>{cid}</code>",
        ]
        if message.message_thread_id:
            lines.append(f"<b>message_thread_id:</b> <code>{message.message_thread_id}</code>")
        else:
            lines.append(
                "<i>ID темы обычно не передаётся при пересылке — открой тему в группе и напиши там</i> <code>/chatid</code>."
            )
        await message.answer("\n".join(lines), parse_mode=HTML)
        return

    lines = [
        f"<b>Тип:</b> <code>{esc(chat.type)}</code>",
        f"<b>chat id</b> → <code>ADMIN_SALES_NOTIFY_CHAT_ID</code>:\n<code>{chat.id}</code>",
    ]
    if message.message_thread_id:
        lines.append(
            f"<b>message_thread_id</b> (эта тема) → один из <code>ADMIN_SALES_THREAD_*</code>:\n"
            f"<code>{message.message_thread_id}</code>"
        )
    else:
        lines.append(
            "<i>Топик не определён — если это форум, открой нужную <b>тему</b> и повтори <code>/chatid</code> там.</i>"
        )
    await message.answer("\n".join(lines), parse_mode=HTML)


@router.message(Command("addcredits"))
async def cmd_addcredits(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer(
            "Формат:\n"
            "/addcredits <user_id> <amount>\n\n"
            "Пример:\n"
            "/addcredits 123456789 50"
        )
        return

    target_user_id = int(parts[1])
    amount = int(parts[2])
    if amount <= 0:
        await message.answer("Количество кредитов должно быть больше 0.")
        return

    await ensure_user(target_user_id, None)
    ok = await add_credits(target_user_id, amount)
    if not ok:
        await message.answer("Не удалось начислить кредиты.")
        return

    new_balance = await get_credits(target_user_id)
    await message.answer(
        f"Готово ✅ Пользователю {target_user_id} начислено {amount} кредитов.\n"
        f"Новый баланс: {new_balance}."
    )


@router.message(Command("takecredits"))
async def cmd_takecredits(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer(
            "Формат:\n"
            "/takecredits <user_id> <amount>\n\n"
            "Пример:\n"
            "/takecredits 123456789 20"
        )
        return

    target_user_id = int(parts[1])
    amount = int(parts[2])
    if amount <= 0:
        await message.answer("Количество кредитов должно быть больше 0.")
        return

    await ensure_user(target_user_id, None)
    before_balance = await get_credits(target_user_id)
    if before_balance < amount:
        await message.answer(
            f"Недостаточно кредитов: у пользователя {target_user_id} сейчас {before_balance}, "
            f"запрошено списать {amount}. Списание не выполнено."
        )
        return
    ok = await take_credits(target_user_id, amount)
    if not ok:
        await message.answer("Не удалось списать кредиты (попробуй ещё раз).")
        return

    new_balance = await get_credits(target_user_id)
    await message.answer(
        f"Готово ✅ У пользователя {target_user_id} списано {amount} кредитов.\n"
        f"Новый баланс: {new_balance}."
    )

