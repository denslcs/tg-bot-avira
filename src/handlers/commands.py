from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
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
from src.handlers.img_commands import CB_CREATE_IMAGE, CB_MENU_BACK_START, CB_READY_IDEAS


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
                InlineKeyboardButton(text="👥 Реферальная система", callback_data="menu:ref"),
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
    return (
        "🖼 Измени фото или создай новое изображение с ИИ.\n\n"
        "Главное: 🎨 «Создать картинку» и 💡 «Готовые идеи».\n"
        "Остальные кнопки — оплата, поддержка и справка.\n"
        f"💰 Баланс: {balance} кредитов.{bonus_note}"
    )


def _start_banner_path() -> Path | None:
    """Баннер приветствия /start. Файл: assets/start/start_banner.png"""
    p = PROJECT_ROOT / "assets" / "start" / "start_banner.png"
    return p if p.is_file() else None


def _parse_ref_payload(raw_text: str) -> int | None:
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    if payload.startswith("ref_"):
        payload = payload[4:]
    if payload.isdigit():
        return int(payload)
    return None


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    await state.clear()
    user_id = message.from_user.id
    await ensure_user(user_id, message.from_user.username)
    referrer_id = _parse_ref_payload((message.text or "").strip())
    bonus_note = ""
    if referrer_id:
        applied = await apply_referral(invitee_user_id=user_id, inviter_user_id=referrer_id)
        if applied:
            bonus_note = "\n🎉 Реферальный бонус: тебе +5 кредитов."
    balance = await get_credits(user_id)

    text = _main_screen_text(balance, bonus_note)
    kb = _start_menu_kb()
    banner = _start_banner_path()
    if banner:
        await message.answer_photo(
            FSInputFile(banner),
            caption=text,
            reply_markup=kb,
        )
    else:
        await message.answer(text, reply_markup=kb)


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
        )
    else:
        await callback.message.answer(text, reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📌 Что доступно:\n\n"
        "🏠 /start — главное меню, баланс и картинки\n"
        "❓ /help — этот список\n"
        "📋 /faq — частые вопросы и шаблоны ответов\n"
        "🔄 /newchat или /clear — очистить память диалога\n"
        "💬 /support — открыть обращение в поддержку\n"
        "✅ /resolved — как закрыть тикет (в боте поддержки)\n"
        "🆔 /myid — твой Telegram ID\n\n"
        "🎨 Картинки — кнопки в /start («Создать картинку», «Готовые идеи»)."
    )


@router.callback_query(F.data == "menu:about")
async def menu_about(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "Что умеет бот:\n"
        "• Сгенерировать картинку из текста.\n"
        "• Изменить картинку по фото + тексту.\n"
        "• Применить готовые промпты к фото.\n"
        "• Использовать разные ИИ-модели для генерации.",
        reply_markup=_back_to_main_menu_kb(),
    )


@router.callback_query(F.data == "menu:support")
async def menu_support(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    if not SUPPORT_BOT_USERNAME:
        await callback.message.answer(
            "Поддержка пока не настроена (пустой SUPPORT_BOT_USERNAME).",
            reply_markup=_back_to_main_menu_kb(),
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_avira"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть поддержку", url=support_url)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await callback.message.answer("Нажми кнопку, чтобы написать в поддержку:", reply_markup=keyboard)


@router.callback_query(F.data == "menu:ref")
async def menu_ref(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    await callback.answer()
    user_id = callback.from_user.id
    try:
        invited = await get_referral_count(user_id)
    except Exception:
        invited = 0
    ref_link = (
        f"https://t.me/{callback.bot.username}?start=ref_{user_id}"
        if callback.bot.username
        else f"/start ref_{user_id}"
    )
    text = (
        "Реферальная система:\n"
        f"• Приглашено друзей: {invited}\n"
        "• За каждого друга тебе +10 кредитов.\n"
        "• Другу при старте по ссылке +5 кредитов.\n\n"
        f"Твоя ссылка:\n{ref_link}"
    )
    try:
        if callback.message:
            await callback.message.answer(text, reply_markup=_back_to_main_menu_kb())
        else:
            await callback.bot.send_message(user_id, text, reply_markup=_back_to_main_menu_kb())
    except Exception:
        await callback.bot.send_message(user_id, text, reply_markup=_back_to_main_menu_kb())


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if not message.from_user:
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    balance = await get_credits(message.from_user.id)
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Твой текущий баланс: безлимит (режим админа).")
        return
    profile = await get_user_admin_profile(message.from_user.id)
    sub_extra = ""
    if profile and profile.subscription_ends_at:
        if subscription_is_active(profile.subscription_ends_at):
            sub_extra = f"\nПодписка активна до: {profile.subscription_ends_at}"
        else:
            sub_extra = f"\nПодписка (истекла): {profile.subscription_ends_at}"
    if profile and profile.subscription_plan and profile.subscription_plan in PLANS:
        sub_extra += f"\nТариф: {PLANS[profile.subscription_plan].title}."
    used_m, limit_m = await get_monthly_image_generation_usage(message.from_user.id)
    sub_extra += f"\nГенераций картинок в этом месяце (UTC): {used_m}/{limit_m}."
    await message.answer(f"Твой текущий баланс: {balance} кредитов.{sub_extra}")


@router.message(Command("newchat"))
@router.message(Command("clear"))
async def cmd_newchat(message: Message) -> None:
    if not message.from_user:
        return
    await clear_dialog_messages(message.from_user.id)
    reset_user_spam(message.from_user.id)
    reset_private_rate(message.from_user.id)
    await message.answer(
        "Готово ✅ История этого диалога очищена.\n"
        "Можешь начать новую тему."
    )


@router.message(Command("resolved"))
async def cmd_resolved_main(message: Message) -> None:
    """В основном боте тикеты ведёт support-бот — направляем пользователя туда."""
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "Чат поддержки пока не подключен.\n"
            "Админ должен задать SUPPORT_BOT_USERNAME в .env — тогда здесь будет ссылка на бот, "
            "где можно закрыть тикет командой /resolved."
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
        "Тикеты обрабатываются в отдельном чате поддержки.\n\n"
        "Чтобы отметить проблему решённой, открой бот поддержки и отправь там команду /resolved "
        "(в том же чате, где открывал обращение).",
        reply_markup=keyboard,
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "Чат поддержки пока не подключен.\n"
            "Админ должен заполнить SUPPORT_BOT_USERNAME в .env"
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
        "Для обращений используй отдельный чат поддержки.\n"
        "Нажми кнопку ниже:",
        reply_markup=keyboard,
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


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

