from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import ADMIN_IDS, SUPPORT_BOT_USERNAME
from src.antispam_state import reset_user_spam
from src.database import (
    add_credits,
    clear_dialog_messages,
    ensure_user,
    get_credits,
    get_user_admin_profile,
    subscription_is_active,
    take_credits,
)


router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    balance = await get_credits(message.from_user.id)

    await message.answer(
        "Привет! Я Avira.\n\n"
        "Напиши сообщение — и я отвечу.\n"
        f"Твой баланс: {balance} кредитов.\n\n"
        "Команды: /help /profile"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Доступно сейчас:\n"
        "- /start — запуск\n"
        "- /help — помощь\n"
        "- /faq — частые вопросы (шаблоны ответов)\n"
        "- /profile — баланс кредитов\n"
        "- /newchat (/clear) — очистить память диалога\n"
        "- /support — отдельный чат поддержки\n"
        "- /myid — твой Telegram ID\n\n"
        "1 текстовый запрос = 1 кредит.\n"
        "Дальше подключим ИИ (Gemini через прокси)."
    )


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
    await message.answer(f"Твой текущий баланс: {balance} кредитов.{sub_extra}")


@router.message(Command("newchat"))
@router.message(Command("clear"))
async def cmd_newchat(message: Message) -> None:
    if not message.from_user:
        return
    await clear_dialog_messages(message.from_user.id)
    reset_user_spam(message.from_user.id)
    await message.answer(
        "Готово ✅ История этого диалога очищена.\n"
        "Можешь начать новую тему."
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
            [InlineKeyboardButton(text="Открыть чат поддержки", url=support_url)]
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
    ok = await take_credits(target_user_id, amount)
    if not ok:
        await message.answer("Не удалось списать кредиты.")
        return

    new_balance = await get_credits(target_user_id)
    taken = before_balance - new_balance
    await message.answer(
        f"Готово ✅ У пользователя {target_user_id} списано {taken} кредитов.\n"
        f"Новый баланс: {new_balance}."
    )

