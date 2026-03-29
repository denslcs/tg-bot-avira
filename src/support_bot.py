import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeChatAdministrators, BotCommandScopeDefault

from src.config import ADMIN_IDS, SUPPORT_BOT_TOKEN, SUPPORT_CHAT_ID
from src.database import init_db
from src.handlers.support_admin_panel import router as support_admin_panel_router
from src.handlers.support_commands import router as support_commands_router
from src.handlers.support_jobs import run_support_background_jobs
from src.handlers.support_messages import router as support_messages_router

_USER_COMMANDS = [
    BotCommand(command="start", description="Запуск поддержки"),
    BotCommand(command="support", description="Открыть новый тикет"),
    BotCommand(command="resolved", description="Отметить проблему решенной"),
    BotCommand(command="help", description="Помощь"),
]

_ADMIN_COMMANDS = [
    BotCommand(command="admin", description="Панель админа: теги, заметки, SLA"),
    BotCommand(command="sla", description="Все открытые тикеты и SLA"),
    BotCommand(command="report", description="Сводка за 7 дней"),
    BotCommand(command="ticket_status", description="Статус тикета (в теме)"),
    BotCommand(command="tag", description="Тег темы: bug / payment / general / clear"),
    BotCommand(command="note", description="Внутренняя заметка (не видит клиент)"),
    BotCommand(command="notes", description="Список заметок по тикету"),
    BotCommand(command="close_ticket", description="Закрыть тикет админом"),
]


async def _register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(_USER_COMMANDS, BotCommandScopeDefault())
    merged = _USER_COMMANDS + _ADMIN_COMMANDS
    if SUPPORT_CHAT_ID:
        try:
            await bot.set_my_commands(merged, BotCommandScopeChatAdministrators(chat_id=SUPPORT_CHAT_ID))
        except Exception as exc:
            logging.warning("Не удалось выставить команды для админов группы поддержки: %s", exc)
    for aid in ADMIN_IDS:
        try:
            await bot.set_my_commands(merged, BotCommandScopeChat(chat_id=aid))
        except Exception as exc:
            logging.warning("Не удалось выставить команды админу в ЛС (id=%s): %s", aid, exc)


async def main() -> None:
    if not SUPPORT_BOT_TOKEN:
        raise RuntimeError("Missing SUPPORT_BOT_TOKEN in .env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()
    bot = Bot(token=SUPPORT_BOT_TOKEN)
    await _register_bot_commands(bot)

    asyncio.create_task(run_support_background_jobs(bot))

    dp = Dispatcher()
    dp.include_router(support_admin_panel_router)
    dp.include_router(support_commands_router)
    dp.include_router(support_messages_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

