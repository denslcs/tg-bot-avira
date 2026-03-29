import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config import SUPPORT_BOT_TOKEN
from src.database import init_db
from src.handlers.support_commands import router as support_commands_router
from src.handlers.support_jobs import run_support_background_jobs
from src.handlers.support_messages import router as support_messages_router


async def main() -> None:
    if not SUPPORT_BOT_TOKEN:
        raise RuntimeError("Missing SUPPORT_BOT_TOKEN in .env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()
    bot = Bot(token=SUPPORT_BOT_TOKEN)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск поддержки"),
            BotCommand(command="support", description="Открыть новый тикет"),
            BotCommand(command="resolved", description="Отметить проблему решенной"),
            BotCommand(command="help", description="Помощь"),
        ]
    )

    asyncio.create_task(run_support_background_jobs(bot))

    dp = Dispatcher()
    dp.include_router(support_commands_router)
    dp.include_router(support_messages_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

