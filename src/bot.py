import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeChatAdministrators, BotCommandScopeDefault

from src.config import ADMIN_IDS, SUPPORT_CHAT_ID, TELEGRAM_BOT_TOKEN
from src.database import init_db
from src.inline_panel_exclusive import apply_exclusive_inline_panels
from src.handlers.routers import register_routers
from src.selfcheck import run_self_check

_USER_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню и баланс"),
    BotCommand(command="profile", description="👤 Профиль и подписка"),
    BotCommand(command="help", description="❓ Список команд"),
    BotCommand(command="pay", description="💳 Подписка и оплата"),
    BotCommand(command="ref", description="🫂 Реферальная система"),
    BotCommand(command="ideas", description="💡 Готовые идеи"),
    BotCommand(command="faq", description="📋 Частые вопросы"),
    BotCommand(command="newchat", description="🔄 Очистить историю диалога"),
    BotCommand(command="support", description="💬 Обращение в поддержку"),
    BotCommand(command="resolved", description="✔️ Как закрыть тикет"),
    BotCommand(command="myid", description="🆔 Мой Telegram ID"),
]

_ADMIN_COMMANDS = [
    BotCommand(command="admin", description="⚙️ Админ-панель"),
    BotCommand(command="stats", description="📊 Пользователи и подписки"),
    BotCommand(command="chatid", description="🗨️ ID чата/темы (.env)"),
    BotCommand(command="user", description="👤 Профиль по ID"),
    BotCommand(command="addcredits", description="➕ Начислить 🪙 кредиты"),
    BotCommand(command="takecredits", description="➖ Списать 🪙 кредиты"),
    BotCommand(command="setsub", description="📅 Подписка (дни)"),
    BotCommand(command="setplan", description="📅 Тариф без продления"),
    BotCommand(command="clearsub", description="📅 Снять подписку"),
    BotCommand(command="wipechat", description="🧹 Очистить диалог пользователя"),
    BotCommand(command="broadcast", description="📢 Рассылка в ЛС всем из базы"),
]


async def _register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(_USER_COMMANDS, BotCommandScopeDefault())
    merged = _USER_COMMANDS + _ADMIN_COMMANDS
    if SUPPORT_CHAT_ID:
        try:
            await bot.set_my_commands(merged, BotCommandScopeChatAdministrators(chat_id=SUPPORT_CHAT_ID))
        except Exception as exc:
            logging.warning("Не удалось выставить команды админам группы (SUPPORT_CHAT_ID): %s", exc)
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands(merged, BotCommandScopeChat(chat_id=aid))
        except Exception as exc:
            logging.warning("Не удалось выставить команды админу в ЛС (id=%s): %s", aid, exc)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if "--self-check" in sys.argv or os.getenv("BOT_RUN_SELF_CHECK", "0") == "1":
        result = await run_self_check()
        for line in result.checks:
            logging.info("[self-check] %s", line)
        if result.errors:
            for line in result.errors:
                logging.error("[self-check] %s", line)
            raise RuntimeError("Self-check failed. Bot start aborted.")
        logging.info("Self-check passed.")
        if "--self-check" in sys.argv:
            return

    await init_db()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    apply_exclusive_inline_panels()
    await _register_bot_commands(bot)
    dp = Dispatcher(storage=MemoryStorage())
    register_routers(dp)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

