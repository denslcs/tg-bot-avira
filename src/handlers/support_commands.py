from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from src.config import ADMIN_IDS, SUPPORT_CHAT_ID
from src.database import (
    close_ticket,
    create_support_ticket,
    get_ticket_by_id,
    get_latest_ticket_by_user,
    get_open_ticket_by_thread,
    get_open_ticket_by_user,
    reopen_ticket,
    update_ticket_thread,
)
from src.support_state import (
    clear_admin_ticket_flow,
    clear_support_draft,
    schedule_support_draft_timers,
    start_support_draft,
)


router = Router(name="support_commands")


def _topic_name(ticket_id: int, username: str, status: str = "OPEN") -> str:
    return f"[{status}] Тикет #{ticket_id} | {username}"[:120]


async def _create_topic_for_ticket(message: Message, ticket_id: int, username: str) -> int:
    topic = await message.bot.create_forum_topic(
        chat_id=SUPPORT_CHAT_ID,
        name=_topic_name(ticket_id, username, "OPEN"),
    )
    await update_ticket_thread(ticket_id=ticket_id, thread_id=topic.message_thread_id, username=username)
    return topic.message_thread_id


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Это поддержка Avira.\n"
        "Нажми /support и опиши проблему."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды поддержки:\n"
        "- /support — открыть новый тикет\n"
        "- /resolved — отметить как решено\n"
        "- /ticket_status — статус тикета (для админа в теме)"
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    if not message.from_user:
        return
    if SUPPORT_CHAT_ID == 0:
        await message.answer("Поддержка не настроена: пустой SUPPORT_CHAT_ID.")
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "без_username"
    open_ticket = await get_open_ticket_by_user(message.from_user.id)
    if open_ticket:
        start_support_draft(message.from_user.id, open_ticket.ticket_id)
        schedule_support_draft_timers(message.bot, message.from_user.id, open_ticket.ticket_id)
        await message.answer(
            f"У тебя уже есть открытый тикет #{open_ticket.ticket_id}.\n"
            "Опиши проблему/дополнение и отправь: готово"
        )
        return

    latest = await get_latest_ticket_by_user(message.from_user.id)
    try:
        if latest:
            await reopen_ticket(latest.ticket_id)
            ticket_id = latest.ticket_id
            try:
                await message.bot.reopen_forum_topic(
                    chat_id=SUPPORT_CHAT_ID,
                    message_thread_id=latest.thread_id,
                )
            except TelegramBadRequest:
                await _create_topic_for_ticket(message, ticket_id, username)
            try:
                await message.bot.edit_forum_topic(
                    chat_id=SUPPORT_CHAT_ID,
                    message_thread_id=latest.thread_id,
                    name=_topic_name(ticket_id, username, "OPEN"),
                )
                await update_ticket_thread(ticket_id=ticket_id, thread_id=latest.thread_id, username=username)
            except TelegramBadRequest:
                pass
        else:
            ticket_id = await create_support_ticket(
                user_id=message.from_user.id,
                username=username,
                thread_id=-1,
            )
            await _create_topic_for_ticket(message, ticket_id, username)
    except Exception:
        await message.answer(
            "Не удалось создать тему поддержки.\n"
            "Проверь Topics и права бота в админ-группе."
        )
        return

    start_support_draft(message.from_user.id, ticket_id)
    schedule_support_draft_timers(message.bot, message.from_user.id, ticket_id)
    await message.answer(
        "Тикет открыт ✅\n"
        "Опишите проблему (можно несколькими сообщениями),\n"
        "потом отправьте: готово"
    )


@router.message(Command("resolved"))
async def cmd_resolved(message: Message) -> None:
    if not message.from_user:
        return
    ticket = await get_open_ticket_by_user(message.from_user.id)
    if not ticket:
        await message.answer("Открытых тикетов не найдено.")
        return
    await close_ticket(ticket.ticket_id)
    clear_support_draft(ticket.user_id)
    clear_admin_ticket_flow(ticket.ticket_id)
    try:
        await message.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=ticket.thread_id,
            name=_topic_name(ticket.ticket_id, ticket.username, "CLOSED"),
        )
    except Exception:
        pass
    try:
        await message.bot.close_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=ticket.thread_id,
        )
    except Exception:
        pass
    await message.answer(f"Тикет #{ticket.ticket_id} закрыт. Спасибо!")


@router.message(Command("close_ticket"))
async def cmd_close_ticket(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID or not message.message_thread_id:
        await message.answer("Используй внутри темы в админ-группе.")
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        await message.answer("Открытый тикет не найден.")
        return
    await close_ticket(ticket.ticket_id)
    clear_support_draft(ticket.user_id)
    clear_admin_ticket_flow(ticket.ticket_id)
    try:
        await message.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=message.message_thread_id,
            name=_topic_name(ticket.ticket_id, ticket.username, "CLOSED"),
        )
    except Exception:
        pass
    try:
        await message.bot.close_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=message.message_thread_id,
        )
    except Exception:
        pass
    await message.answer(f"Тикет #{ticket.ticket_id} закрыт.")


@router.message(Command("ticket_status"))
async def cmd_ticket_status(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID or not message.message_thread_id:
        await message.answer("Используй внутри темы тикета.")
        return

    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        await message.answer("Открытый тикет не найден в этой теме.")
        return
    full = await get_ticket_by_id(ticket.ticket_id)
    if not full:
        await message.answer("Тикет не найден.")
        return
    await message.answer(
        f"Тикет #{full.ticket_id}\n"
        f"Статус: {full.status}\n"
        f"user_id: {full.user_id}\n"
        f"username: {full.username}\n"
        f"thread_id: {full.thread_id}"
    )

