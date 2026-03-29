"""Закрытие тикета по таймауту неактивности: синхронизация с БД и с темой в Telegram."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from src.config import SUPPORT_CHAT_ID
from src.database import close_ticket, get_open_ticket_by_id, get_ticket_detail_by_id
from src.support_topic_naming import topic_title
from src.support_state import (
    clear_admin_ticket_flow,
    clear_draft_timer_seq,
    clear_support_draft,
)

logger = logging.getLogger(__name__)


async def close_ticket_after_inactivity(bot: Bot, user_id: int, ticket_id: int) -> None:
    """
    Вызывается из таймера черновика: тикет ещё открыт, пользователь не завершил описание.
    Закрываем в БД и в форуме так же, как при /resolved в support-боте.
    """
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        clear_support_draft(user_id)
        clear_draft_timer_seq(user_id)
        return
    if ticket.user_id != user_id:
        clear_support_draft(user_id)
        clear_draft_timer_seq(user_id)
        return

    await close_ticket(ticket_id)
    clear_support_draft(user_id)
    clear_draft_timer_seq(user_id)
    clear_admin_ticket_flow(ticket_id)

    det = await get_ticket_detail_by_id(ticket_id)
    if SUPPORT_CHAT_ID and ticket.thread_id and ticket.thread_id > 0:
        try:
            await bot.edit_forum_topic(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=ticket.thread_id,
                name=topic_title(
                    ticket.ticket_id,
                    ticket.username,
                    "CLOSED",
                    det.tag if det else None,
                ),
            )
        except TelegramBadRequest:
            logger.debug("edit_forum_topic after inactivity close", exc_info=True)
        except Exception:
            logger.exception("edit_forum_topic after inactivity close")
        try:
            await bot.close_forum_topic(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=ticket.thread_id,
            )
        except TelegramBadRequest:
            logger.debug("close_forum_topic after inactivity", exc_info=True)
        except Exception:
            logger.exception("close_forum_topic after inactivity")

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "Заявка автоматически закрыта из‑за неактивности (долго не было ответа в описании).\n"
                "Если вопрос снова актуален — нажми /support."
            ),
        )
    except Exception:
        logger.exception("notify user after inactivity close")
