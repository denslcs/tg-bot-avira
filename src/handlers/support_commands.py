import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from src.config import ADMIN_IDS, SUPPORT_CHAT_ID
from src.database import (
    add_support_ticket_note,
    close_ticket,
    create_support_ticket,
    get_latest_ticket_by_user,
    get_open_ticket_by_thread,
    get_open_ticket_by_user,
    get_ticket_detail_by_id,
    get_ticket_detail_by_thread,
    list_open_tickets_sla_rows,
    list_support_ticket_notes,
    reopen_ticket,
    set_ticket_tag,
    update_ticket_thread,
)
from src.handlers.support_jobs import build_weekly_report_text
from src.support_topic_naming import VALID_TAGS, topic_title
from src.support_state import (
    clear_admin_ticket_flow,
    clear_support_draft,
    schedule_support_draft_timers,
    start_support_draft,
)

logger = logging.getLogger(__name__)


router = Router(name="support_commands")


async def _create_topic_for_ticket(message: Message, ticket_id: int, username: str) -> int:
    topic = await message.bot.create_forum_topic(
        chat_id=SUPPORT_CHAT_ID,
        name=topic_title(ticket_id, username, "OPEN", None),
    )
    await update_ticket_thread(ticket_id=ticket_id, thread_id=topic.message_thread_id, username=username)
    return topic.message_thread_id


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Это поддержка Shard Creator.\n"
        "Нажми /support и опиши проблему."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "Команды поддержки:\n"
        "- /support — открыть тикет\n"
        "- /resolved — закрыть тикет (клиент)\n"
        "- /help — эта справка\n\n"
        "Сотрудникам с доступом админа: в меню бота есть /admin и остальные команды; "
        "в панели — кнопки с подробными пояснениями."
    )
    if message.from_user and message.from_user.id in ADMIN_IDS:
        text += "\n\nОткройте /admin для панели (теги, заметки, SLA, отчёт)."
    await message.answer(text)


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
                    name=topic_title(ticket_id, username, "OPEN", None),
                )
                await update_ticket_thread(ticket_id=ticket_id, thread_id=latest.thread_id, username=username)
            except TelegramBadRequest:
                logger.warning(
                    "cmd_support: edit_forum_topic after reopen ticket_id=%s",
                    ticket_id,
                    exc_info=True,
                )
        else:
            ticket_id = await create_support_ticket(
                user_id=message.from_user.id,
                username=username,
                thread_id=-1,
            )
            await _create_topic_for_ticket(message, ticket_id, username)
    except Exception:
        logger.exception("cmd_support: failed to create or reopen support topic")
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
    det = await get_ticket_detail_by_id(ticket.ticket_id)
    try:
        await message.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=ticket.thread_id,
            name=topic_title(
                ticket.ticket_id,
                ticket.username,
                "CLOSED",
                det.tag if det else None,
            ),
        )
    except Exception:
        logger.warning(
            "cmd_resolved: edit_forum_topic ticket_id=%s thread_id=%s",
            ticket.ticket_id,
            ticket.thread_id,
            exc_info=True,
        )
    try:
        await message.bot.close_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=ticket.thread_id,
        )
    except Exception:
        logger.warning(
            "cmd_resolved: close_forum_topic ticket_id=%s thread_id=%s",
            ticket.ticket_id,
            ticket.thread_id,
            exc_info=True,
        )
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
    det = await get_ticket_detail_by_id(ticket.ticket_id)
    try:
        await message.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=message.message_thread_id,
            name=topic_title(
                ticket.ticket_id,
                ticket.username,
                "CLOSED",
                det.tag if det else None,
            ),
        )
    except Exception:
        logger.warning(
            "cmd_close_ticket: edit_forum_topic ticket_id=%s thread_id=%s",
            ticket.ticket_id,
            message.message_thread_id,
            exc_info=True,
        )
    try:
        await message.bot.close_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=message.message_thread_id,
        )
    except Exception:
        logger.warning(
            "cmd_close_ticket: close_forum_topic ticket_id=%s thread_id=%s",
            ticket.ticket_id,
            message.message_thread_id,
            exc_info=True,
        )
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
    full = await get_ticket_detail_by_thread(message.message_thread_id)
    if not full:
        await message.answer("Тикет не найден.")
        return
    tag = full.tag or "—"
    first = full.first_admin_reply_at or "ещё не было ответа пользователю"
    await message.answer(
        f"Тикет #{full.ticket_id}\n"
        f"Статус: {full.status}\n"
        f"Тег: {tag}\n"
        f"Создан: {full.created_at}\n"
        f"Первый ответ пользователю: {first}\n"
        f"user_id: {full.user_id}\n"
        f"username: {full.username}\n"
        f"thread_id: {full.thread_id}"
    )


def _hours_since_created(created_at: str) -> float:
    from datetime import datetime, timezone

    try:
        c = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - c).total_seconds() / 3600.0
    except ValueError:
        return 0.0


@router.message(Command("tag"))
async def cmd_tag(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID or not message.message_thread_id:
        await message.answer("Используй внутри темы тикета.")
        return
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: /tag bug | payment | general | clear\n"
            "clear — убрать тег."
        )
        return
    key = parts[1].strip().lower()
    if key in ("clear", "none", "-", "0"):
        tag_val = None
    elif key in VALID_TAGS:
        tag_val = key
    else:
        await message.answer(f"Допустимо: {', '.join(sorted(VALID_TAGS))} или clear")
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        await message.answer("Открытый тикет не найден.")
        return
    await set_ticket_tag(ticket.ticket_id, tag_val)
    d = await get_ticket_detail_by_id(ticket.ticket_id)
    try:
        await message.bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=message.message_thread_id,
            name=topic_title(
                ticket.ticket_id,
                ticket.username,
                "OPEN",
                d.tag if d else None,
            ),
        )
    except TelegramBadRequest:
        logger.debug(
            "cmd_tag: edit_forum_topic ticket_id=%s thread_id=%s",
            ticket.ticket_id,
            message.message_thread_id,
            exc_info=True,
        )
    await message.answer(f"Тег обновлён: {tag_val or 'нет'}")


@router.message(Command("note"))
async def cmd_note(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID or not message.message_thread_id:
        await message.answer("Используй внутри темы тикета.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: /note текст заметки")
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        await message.answer("Открытый тикет не найден.")
        return
    await add_support_ticket_note(ticket.ticket_id, message.from_user.id, parts[1].strip())
    await message.answer("Заметка сохранена (видна только админам).")


@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID or not message.message_thread_id:
        await message.answer("Используй внутри темы тикета.")
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        await message.answer("Открытый тикет не найден.")
        return
    rows = await list_support_ticket_notes(ticket.ticket_id, limit=25)
    if not rows:
        await message.answer("Заметок пока нет.")
        return
    lines = [f"[{r[3]}] admin {r[1]}: {r[2]}" for r in reversed(rows)]
    text = f"Заметки по тикету #{ticket.ticket_id}:\n\n" + "\n".join(lines)
    await message.answer(text[:4000])


@router.message(Command("sla"))
async def cmd_sla(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID:
        await message.answer("Команда только в группе поддержки.")
        return
    rows = await list_open_tickets_sla_rows()
    if not rows:
        await message.answer("Открытых тикетов нет.")
        return
    lines: list[str] = []
    for t in rows:
        age = _hours_since_created(t.created_at)
        replied = "да" if t.first_admin_reply_at else "нет"
        tg = f" [{t.tag}]" if t.tag else ""
        lines.append(
            f"#{t.ticket_id}{tg} user {t.user_id} | ответ юзеру: {replied} | возраст ~{age:.1f} ч"
        )
    body = "\n".join(lines[:40])
    if len(lines) > 40:
        body += f"\n… всего {len(lines)}"
    await message.answer(body[:4000])


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Только для админов.")
        return
    if message.chat.id != SUPPORT_CHAT_ID:
        await message.answer("Команда только в группе поддержки.")
        return
    await message.answer(await build_weekly_report_text())

