import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config import TELEGRAM_BOT_TOKEN
from src.database import init_db
from src.handlers.commands import router as commands_router
from src.handlers.messages import router as messages_router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="profile", description="Баланс кредитов"),
            BotCommand(command="newchat", description="Очистить историю диалога"),
            BotCommand(command="support", description="Открыть обращение в поддержку"),
            BotCommand(command="resolved", description="Отметить тикет как решенный"),
            BotCommand(command="myid", description="Показать мой Telegram ID"),
            BotCommand(command="chatid", description="Показать ID текущего чата"),
            BotCommand(command="admin", description="Админ-панель"),
        ]
    )
    dp = Dispatcher()

    dp.include_router(commands_router)
    dp.include_router(messages_router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

