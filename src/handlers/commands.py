import logging
import urllib.parse
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import ADMIN_IDS, PROJECT_ROOT, SUPPORT_BOT_USERNAME
from src.antispam_state import reset_user_spam
from src.private_rate_limit import reset_private_rate
from src.database import (
    add_credits,
    apply_referral,
    clear_dialog_messages,
    ensure_user,
    get_credits,
    get_monthly_image_generation_usage,
    get_referral_count,
    get_user_admin_profile,
    subscription_is_active,
    take_credits,
)
from src.subscription_catalog import PLANS
from src.formatting import HTML, esc
from src.handlers.img_commands import CB_CREATE_IMAGE, CB_MENU_BACK_START, CB_READY_IDEAS

# Короткий callback_data (старые кнопки с «menu:ref» всё ещё обрабатываются в handler)
CB_MENU_REF = "ref_menu"

router = Router(name="commands")


def _start_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 Создать картинку", callback_data=CB_CREATE_IMAGE),
                InlineKeyboardButton(text="💡 Готовые идеи", callback_data=CB_READY_IDEAS),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Что умеет бот", callback_data="menu:about"),
                InlineKeyboardButton(text="👥 Реферальная система", callback_data=CB_MENU_REF),
            ],
            [
                InlineKeyboardButton(text="💳 Оплатить", callback_data="menu:pay"),
                InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support"),
            ],
        ]
    )


_BACK_TO_MENU_ROW = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]


def _back_to_main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_BACK_TO_MENU_ROW])


def _main_screen_text(balance: int, bonus_note: str = "") -> str:
    bonus_html = esc(bonus_note) if bonus_note else ""
    return (
        "🖼 <b>Создай или измени фото</b> с помощью ИИ.\n\n"
        "<b>Главное:</b> 🎨 <i>Создать картинку</i> и 💡 <i>Готовые идеи</i>.\n"
        "<i>Остальное — оплата, поддержка и справка.</i>\n\n"
        f"<blockquote><i>💰 Баланс: {esc(balance)} кредитов.</i>{bonus_html}</blockquote>"
    )


def _start_banner_path() -> Path | None:
    """Баннер приветствия /start. Файл: assets/start/start_banner.png"""
    p = PROJECT_ROOT / "assets" / "start" / "start_banner.png"
    return p if p.is_file() else None


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
    kb = _start_menu_kb()
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
    await ensure_user(user_id, callback.from_user.username)
    balance = await get_credits(user_id)
    await callback.answer()
    text = _main_screen_text(balance, "")
    kb = _start_menu_kb()
    banner = _start_banner_path()
    if banner:
        await callback.message.answer_photo(
            FSInputFile(banner),
            caption=text,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await callback.message.answer(text, reply_markup=kb, parse_mode=HTML)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📌 <b>Что доступно</b>\n\n"
        "🏠 <code>/start</code> — <i>главное меню, баланс и картинки</i>\n"
        "❓ <code>/help</code> — <i>этот список</i>\n"
        "💳 <code>/pay</code> — <i>подписка и оплата</i>\n"
        "👥 <code>/ref</code> — <i>реферальная система</i>\n"
        "💡 <code>/ideas</code> — <i>готовые идеи для фото</i>\n"
        "📋 <code>/faq</code> — <i>частые вопросы</i>\n"
        "🔄 <code>/newchat</code> или <code>/clear</code> — <i>очистить память диалога</i>\n"
        "💬 <code>/support</code> — <i>обращение в поддержку</i>\n"
        "✅ <code>/resolved</code> — <i>закрыть тикет (в боте поддержки)</i>\n"
        "🆔 <code>/myid</code> — <i>твой Telegram ID</i>\n\n"
        "<blockquote>🎨 Картинки — через кнопки в <code>/start</code>.</blockquote>",
        parse_mode=HTML,
    )


@router.callback_query(F.data == "menu:about")
async def menu_about(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "<b>Что умеет бот</b>\n"
        "<blockquote>"
        "• Сгенерировать картинку из текста.\n"
        "• Изменить картинку по фото + тексту.\n"
        "• Готовые промпты к фото.\n"
        "• Разные <i>ИИ-модели</i> для генерации."
        "</blockquote>",
        reply_markup=_back_to_main_menu_kb(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    if not SUPPORT_BOT_USERNAME:
        await callback.message.answer(
            "<blockquote><i>Поддержка пока не настроена</i> "
            "(пустой <code>SUPPORT_BOT_USERNAME</code>).</blockquote>",
            reply_markup=_back_to_main_menu_kb(),
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
    await callback.message.answer(
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


async def _build_referral_message(bot: Bot, user_id: int, username: str | None) -> tuple[str, InlineKeyboardMarkup]:
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
        f"https://t.me/{bot.username}?start=ref_{user_id}" if bot.username else f"/start ref_{user_id}"
    )
    uname_line = f"@{username}" if username else "без username"
    # Без HTML: разметка цитат/вложенных тегов у части клиентов ломала отправку (parse entities)
    text = (
        f"👤 Профиль {uname_line}\n"
        f"💳 ID: {user_id}\n"
        f"💵 Кредиты: {balance}\n\n"
        f"✉️ Приглашения: {invited}\n\n"
        "Приглашай друзей и получай 10 кредитов за каждого приглашённого.\n"
        "Другу по твоей ссылке при первом /start — +5 кредитов.\n\n"
        f"Твоя реферальная ссылка:\n{ref_link}"
    )
    share_url = _referral_share_url(bot.username, user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Пригласить", url=share_url)],
            _BACK_TO_MENU_ROW,
        ]
    )
    return text, kb


async def deliver_referral_screen(bot: Bot, user_id: int, username: str | None, reply_via: Message | None) -> None:
    """Отправить экран рефералки (callback или команда /ref)."""
    text, kb = await _build_referral_message(bot, user_id, username)
    try:
        if reply_via:
            await reply_via.answer(text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        logging.exception("deliver_referral_screen: не удалось отправить сообщение с реферальной ссылкой")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="Не удалось показать реферальное сообщение. Нажми /start и попробуй снова.",
            )
        except Exception:
            logging.exception("deliver_referral_screen: не удалось отправить сообщение об ошибке")


@router.callback_query((F.data == CB_MENU_REF) | (F.data == "menu:ref"))
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

    await ensure_user(message.from_user.id, message.from_user.username)
    balance = await get_credits(message.from_user.id)
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "<blockquote><b>Режим админа</b> — безлимит по кредитам.</blockquote>",
            parse_mode=HTML,
        )
        return
    profile = await get_user_admin_profile(message.from_user.id)
    sub_extra = ""
    if profile and profile.subscription_ends_at:
        if subscription_is_active(profile.subscription_ends_at):
            sub_extra = f"\nПодписка активна до: {esc(profile.subscription_ends_at)}"
        else:
            sub_extra = f"\nПодписка (истекла): {esc(profile.subscription_ends_at)}"
    if profile and profile.subscription_plan and profile.subscription_plan in PLANS:
        sub_extra += f"\nТариф: {esc(PLANS[profile.subscription_plan].title)}."
    used_m, limit_m = await get_monthly_image_generation_usage(message.from_user.id)
    sub_extra += f"\nГенераций картинок в этом месяце (UTC): {esc(used_m)}/{esc(limit_m)}."
    await message.answer(
        "<b>Профиль</b>\n"
        f"<blockquote><i>💰 Баланс:</i> <b>{esc(balance)}</b> кредитов{sub_extra}</blockquote>",
        parse_mode=HTML,
    )


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
        parse_mode=HTML,
    )


@router.message(Command("resolved"))
async def cmd_resolved_main(message: Message) -> None:
    """В основном боте тикеты ведёт support-бот — направляем пользователя туда."""
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "<blockquote><i>Чат поддержки не подключён.</i> Нужен "
            "<code>SUPPORT_BOT_USERNAME</code> в <code>.env</code>.</blockquote>",
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
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_avira"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть чат поддержки", url=support_url)],
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
        parse_mode=HTML,
    )


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    if not message.from_user:
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return
    await message.answer(f"ID этого чата: {message.chat.id}")


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

