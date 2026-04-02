from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import ADMIN_IDS
from src.keyboards.styles import BTN_PRIMARY

router = Router(name="support_admin_panel")


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="В теме тикета", callback_data="supadm:topic", style=BTN_PRIMARY
                ),
                InlineKeyboardButton(
                    text="В группе поддержки", callback_data="supadm:group", style=BTN_PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Закрытие и статус", callback_data="supadm:close", style=BTN_PRIMARY
                ),
            ],
        ]
    )


@router.message(Command("admin"))
async def cmd_support_admin_panel(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return
    await message.answer(
        "Панель поддержки — команды для админов\n\n"
        "Ниже кнопки с подробными пояснениями. Кратко:\n"
        "• Внутри темы форума тикета — теги, внутренние заметки, статус.\n"
        "• В группе поддержки (общий чат или любая ветка) — очередь / SLA и отчёт.\n\n"
        "Команды дублируются в меню бота (у админов в группе и в личке с ботом).",
        reply_markup=_main_kb(),
    )


@router.callback_query(F.data == "supadm:topic")
async def cb_topic_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = (
        "Команды в теме тикета (forum topic)\n\n"
        "Откройте ветку с тикетом и пишите там.\n\n"
        "/tag bug|payment|general|clear\n"
        "— Классификация обращения. Меняет метку в названии темы (удобно сортировать глазами "
        "и по типу: баг, оплата, общее). clear — убрать метку.\n\n"
        "/note текст\n"
        "— Внутренняя заметка только для админов (хранится в БД). Пользователь в личке "
        "это не видит; используйте для передачи смене, контекста, ссылок на платёж и т.п.\n\n"
        "/notes\n"
        "— Список последних заметок по этому тикету (кто и когда писал).\n\n"
        "Подсказка: сначала отметьте тег, потом ведите переписку — так тема сразу читаемая."
    )
    if callback.message:
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "supadm:group")
async def cb_group_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = (
        "Команды в группе поддержки\n\n"
        "Работают из админ-группы (ID из SUPPORT_CHAT_ID), можно писать в общий топик "
        "или не в тему — не обязательно открывать ветку тикета.\n\n"
        "/sla\n"
        "— Срез по всем открытым тикетам: номер, user_id, метка, был ли уже первый ответ "
        "пользователю в личку, примерный возраст тикета в часах. Нужно, чтобы не терять "
        "очередь и ловить просрочки по SLA (фоновые напоминания завязаны на те же данные).\n\n"
        "В общий чат (General) бот шлёт SLA-дайджесты и еженедельную сводку; сами тексты обращений по тикетам — "
        "только в темах. Интервал SLA в .env (по умолчанию раз в 8 ч).\n\n"
        "/report\n"
        "— Сводка поддержки за 7 дней (автоматика — тоже в группу, в General). "
        "Статистика пользователей основного бота: /stats."
    )
    if callback.message:
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "supadm:close")
async def cb_close_help(callback: CallbackQuery) -> None:
    if not callback.from_user or callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text = (
        "Закрытие и диагностика\n\n"
        "/ticket_status (только внутри темы тикета)\n"
        "— Статус, тег, даты, user_id, thread_id, отметка первого ответа пользователю.\n\n"
        "/close_ticket (внутри темы тикета)\n"
        "— Принудительно закрыть тикет со стороны поддержки (пользователь мог не нажать "
        "«решено»). Тема закроется как при обычном завершении.\n\n"
        "Пользователь в личке: /resolved — закрывает свой тикет."
    )
    if callback.message:
        await callback.message.answer(text)
    await callback.answer()
