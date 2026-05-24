"""Одноразовая проверка подписки на канал при первом входе в бота."""

from __future__ import annotations

import logging
import re

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import ADMIN_IDS, CHANNEL_GATE_ENABLED, CHANNEL_ID, CHANNEL_URL
from src.database import user_needs_channel_gate
from src.keyboards.callback_data import CB_CHANNEL_GATE_CHECK
from src.keyboards.styles import BTN_SUCCESS

logger = logging.getLogger(__name__)

_TME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?P<slug>[A-Za-z0-9_]{4,32})(?:/|\?|$)",
    re.IGNORECASE,
)


def resolve_channel_chat_id() -> str | int | None:
    """@username или числовой id канала для getChatMember."""
    raw = (CHANNEL_ID or "").strip()
    if raw:
        if raw.lstrip("-").isdigit():
            return int(raw)
        if raw.startswith("@"):
            return raw
        return f"@{raw}"
    url = (CHANNEL_URL or "").strip()
    if not url:
        return None
    m = _TME_RE.search(url)
    if m:
        return f"@{m.group('slug')}"
    if url.startswith("@"):
        return url
    return None


def channel_gate_active() -> bool:
    return bool(CHANNEL_GATE_ENABLED and resolve_channel_chat_id() and CHANNEL_URL)


async def needs_channel_gate(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return False
    if not channel_gate_active():
        return False
    return await user_needs_channel_gate(user_id)


CHANNEL_GATE_WELCOME_EMOJI_ID = "5195033767969839232"


def channel_gate_screen_html() -> str:
    icon = (
        f'<tg-emoji emoji-id="{CHANNEL_GATE_WELCOME_EMOJI_ID}">👋</tg-emoji>'
    )
    return (
        f"{icon}Привет! Я Shard Creator, чтобы пользоваться мной дальше, "
        f"тебе надо подписаться на наш канал!"
    )


def channel_gate_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if CHANNEL_URL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Подписаться",
                    url=CHANNEL_URL,
                    style=BTN_SUCCESS,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Проверить подписку",
                callback_data=CB_CHANNEL_GATE_CHECK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_channel_gate_screen(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id,
        channel_gate_screen_html(),
        reply_markup=channel_gate_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _member_is_subscribed(status: ChatMemberStatus) -> bool:
    return status in (
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
    )


async def user_is_channel_subscriber(bot: Bot, user_id: int) -> tuple[bool, str | None]:
    """
    Проверка подписки через getChatMember.
    Возвращает (подписан, текст_ошибки_для_пользователя).
    """
    chat_id = resolve_channel_chat_id()
    if chat_id is None:
        return True, None
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramForbiddenError:
        logger.error(
            "channel gate: bot cannot read channel %s — добавь бота админом в канал",
            chat_id,
        )
        return False, "Не удалось проверить подписку. Напиши в /support."
    except TelegramBadRequest as exc:
        logger.warning("channel gate getChatMember failed chat=%s uid=%s: %s", chat_id, user_id, exc)
        return False, "Канал недоступен для проверки. Попробуй позже или напиши в /support."
    except Exception:
        logger.exception("channel gate getChatMember error chat=%s uid=%s", chat_id, user_id)
        return False, "Не удалось проверить подписку. Попробуй через минуту."
    if _member_is_subscribed(member.status):
        return True, None
    return False, None
