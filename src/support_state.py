from __future__ import annotations

import asyncio

from src.config import MAX_SUPPORT_DRAFT_TOTAL_CHARS

# Simple in-memory draft state for MVP.
# Key = user_id, value = accumulated support message text.
_SUPPORT_DRAFTS: dict[int, str] = {}
_DRAFT_TICKET_IDS: dict[int, int] = {}

# Timer session: bump when /support (or "continue") starts so old asyncio tasks exit.
_DRAFT_TIMER_SEQ: dict[int, int] = {}


def start_support_draft(user_id: int, ticket_id: int) -> None:
    _SUPPORT_DRAFTS[user_id] = ""
    _DRAFT_TICKET_IDS[user_id] = ticket_id


def in_support_draft(user_id: int) -> bool:
    return user_id in _SUPPORT_DRAFTS


def append_support_draft(user_id: int, text: str) -> bool:
    old = _SUPPORT_DRAFTS.get(user_id, "")
    if old:
        new = f"{old}\n{text}"
    else:
        new = text
    if len(new) > MAX_SUPPORT_DRAFT_TOTAL_CHARS:
        return False
    _SUPPORT_DRAFTS[user_id] = new
    return True


def get_support_draft(user_id: int) -> str:
    return _SUPPORT_DRAFTS.get(user_id, "")


def clear_support_draft(user_id: int) -> None:
    _SUPPORT_DRAFTS.pop(user_id, None)
    _DRAFT_TICKET_IDS.pop(user_id, None)
    clear_draft_timer_seq(user_id)


def get_draft_ticket_id(user_id: int) -> int | None:
    return _DRAFT_TICKET_IDS.get(user_id)


def bump_draft_timer_seq(user_id: int) -> int:
    seq = _DRAFT_TIMER_SEQ.get(user_id, 0) + 1
    _DRAFT_TIMER_SEQ[user_id] = seq
    return seq


def clear_draft_timer_seq(user_id: int) -> None:
    _DRAFT_TIMER_SEQ.pop(user_id, None)


async def run_support_draft_timers(bot, user_id: int, seq: int, ticket_id: int) -> None:
    await asyncio.sleep(45)
    if _DRAFT_TIMER_SEQ.get(user_id) != seq or not in_support_draft(user_id):
        return
    await bot.send_message(chat_id=user_id, text="Напоминание: когда закончишь, отправь словом: готово")

    await asyncio.sleep(45)
    if _DRAFT_TIMER_SEQ.get(user_id) != seq or not in_support_draft(user_id):
        return
    await bot.send_message(
        chat_id=user_id,
        text="Давно нет ответа. Если передумал, можно закрыть заявку командой /resolved",
    )

    await asyncio.sleep(40)
    if _DRAFT_TIMER_SEQ.get(user_id) != seq or not in_support_draft(user_id):
        return
    # Закрытие тикета и темы в Telegram — в отдельном модуле (без циклического импорта на уровне файла).
    from src.handlers.support_inactivity import close_ticket_after_inactivity

    await close_ticket_after_inactivity(bot, user_id, ticket_id)


def schedule_support_draft_timers(bot, user_id: int, ticket_id: int) -> None:
    seq = bump_draft_timer_seq(user_id)
    asyncio.create_task(run_support_draft_timers(bot, user_id, seq, ticket_id))


# --- Admin topic: one pending chunk per admin message (by chunk_id) + outbox before user send ---

_ADMIN_CHUNK_TEXT: dict[tuple[int, int], str] = {}
_ADMIN_CHUNK_SEQ: dict[int, int] = {}
_ADMIN_OUTBOX: dict[int, list[str]] = {}
_ADMIN_CONTROL_MSG: dict[int, tuple[int, int]] = {}


def register_admin_chunk(ticket_id: int, text: str) -> int:
    chunk_id = _ADMIN_CHUNK_SEQ.get(ticket_id, 0) + 1
    _ADMIN_CHUNK_SEQ[ticket_id] = chunk_id
    _ADMIN_CHUNK_TEXT[(ticket_id, chunk_id)] = text
    return chunk_id


def pop_admin_chunk(ticket_id: int, chunk_id: int) -> str | None:
    return _ADMIN_CHUNK_TEXT.pop((ticket_id, chunk_id), None)


def admin_outbox_append(ticket_id: int, text: str) -> int:
    lst = _ADMIN_OUTBOX.setdefault(ticket_id, [])
    lst.append(text)
    return len(lst)


def admin_outbox_join(ticket_id: int) -> str:
    parts = _ADMIN_OUTBOX.get(ticket_id, [])
    return "\n\n".join(parts)


def admin_outbox_len(ticket_id: int) -> int:
    return len(_ADMIN_OUTBOX.get(ticket_id, []))


def set_admin_control_message(ticket_id: int, chat_id: int, message_id: int) -> None:
    _ADMIN_CONTROL_MSG[ticket_id] = (chat_id, message_id)


def get_admin_control_message(ticket_id: int) -> tuple[int, int] | None:
    return _ADMIN_CONTROL_MSG.get(ticket_id)


def clear_admin_ticket_flow(ticket_id: int) -> None:
    keys = [k for k in _ADMIN_CHUNK_TEXT if k[0] == ticket_id]
    for k in keys:
        del _ADMIN_CHUNK_TEXT[k]
    _ADMIN_CHUNK_SEQ.pop(ticket_id, None)
    _ADMIN_OUTBOX.pop(ticket_id, None)
    _ADMIN_CONTROL_MSG.pop(ticket_id, None)
