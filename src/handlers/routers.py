"""
Порядок подключения роутеров главного бота.

Сначала узкие хендлеры (меню, оплата, FAQ, картинки, админ), в конце — общий личный чат
(messages: любой текст и лимиты), чтобы не перехватывать специализированные обработчики.
"""

from aiogram import Dispatcher

from src.handlers.admin_panel import router as admin_panel_router
from src.handlers.commands import router as commands_router
from src.handlers.faq_handlers import router as faq_router
from src.handlers.img_commands import router as img_commands_router
from src.handlers.messages import router as messages_router
from src.handlers.payments import router as payments_router


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(commands_router)
    dp.include_router(payments_router)
    dp.include_router(faq_router)
    dp.include_router(img_commands_router)
    dp.include_router(admin_panel_router)
    dp.include_router(messages_router)
