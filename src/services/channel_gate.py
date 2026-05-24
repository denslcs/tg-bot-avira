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
_TME_S_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/s/(?P<slug>[A-Za-z0-9_]{4,32})(?:/|\?|$)",
    re.IGNORECASE,
)
_INVITE_URL_RE = re.compile(r"(?:t\.me/\+|joinchat/)", re.IGNORECASE)

_resolved_channel_id_cache: int | None = None


def _normalize_channel_username(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("@"):
        return s
    return f"@{s}"


def resolve_channel_chat_id() -> str | int | None:
    """@username или числовой id канала из CHANNEL_ID / CHANNEL_URL (без API)."""
    raw = (CHANNEL_ID or "").strip()
    if raw:
        if raw.lstrip("-").isdigit():
            return int(raw)
        return _normalize_channel_username(raw)
    url = (CHANNEL_URL or "").strip()
    if not url:
        return None
    if _INVITE_URL_RE.search(url):
        logger.warning(
            "CHANNEL_URL — invite-ссылка; для проверки подписки задай CHANNEL_ID=-100… "
            "(числовой id канала, бот — админ)."
        )
        return None
    m = _TME_S_RE.search(url)
    if m:
        return f"@{m.group('slug')}"
    m = _TME_RE.search(url)
    if m:
        slug = m.group("slug")
        if slug.lower() in ("joinchat", "c", "s", "addstickers", "share"):
            return None
        return f"@{slug}"
    if url.startswith("@"):
        return url
    return None


async def resolve_channel_chat_id_for_api(bot: Bot) -> int | None:
    """
    Числовой chat_id для getChatMember.
    Публичный @username резолвится через getChat; CHANNEL_ID=-100… используется напрямую.
    """
    global _resolved_channel_id_cache
    if _resolved_channel_id_cache is not None:
        return _resolved_channel_id_cache

    static = resolve_channel_chat_id()
    if static is None:
        return None
    if isinstance(static, int):
        _resolved_channel_id_cache = static
        return static
    if isinstance(static, str) and static.lstrip("-").isdigit():
        _resolved_channel_id_cache = int(static)
        return _resolved_channel_id_cache

    username = _normalize_channel_username(str(static))
    try:
        chat = await bot.get_chat(username)
        _resolved_channel_id_cache = int(chat.id)
        logger.info(
            "channel gate: resolved %s -> chat_id=%s type=%s title=%r",
            username,
            _resolved_channel_id_cache,
            getattr(chat.type, "value", chat.type),
            chat.title,
        )
        return _resolved_channel_id_cache
    except TelegramForbiddenError:
        logger.error(
            "channel gate: bot cannot access %s — добавь бота админом канала",
            username,
        )
        return None
    except TelegramBadRequest as exc:
        logger.error(
            "channel gate: getChat failed for %s (%s). "
            "Проверь CHANNEL_ID=-100… в .env (Forward сообщения из канала в @getidsbot).",
            username,
            exc,
        )
        return None
    except Exception:
        logger.exception("channel gate: getChat failed for %s", username)
        return None


def channel_gate_active() -> bool:
    if not CHANNEL_GATE_ENABLED or not CHANNEL_URL:
        return False
    # Достаточно CHANNEL_ID или публичного username в CHANNEL_URL (не invite).
    return resolve_channel_chat_id() is not None or bool((CHANNEL_ID or "").strip())


async def channel_gate_configured_for_api(bot: Bot) -> bool:
    if not channel_gate_active():
        return False
    return await resolve_channel_chat_id_for_api(bot) is not None


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


def _bad_request_user_not_in_chat(exc: TelegramBadRequest) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "user_not_participant",
            "user is not a member",
            "not a member of",
            "participant_id_invalid",
        )
    )


def _bad_request_bot_cannot_check_channel(exc: TelegramBadRequest) -> bool:
    """Бот не добавлен в канал или не админ — типичная причина сбоя getChatMember."""
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "chat_admin_required",
            "bot is not a member",
            "not a member of the",
            "need administrator",
            "administrator rights",
            "rights in the channel",
            "member list is inaccessible",
        )
    )


async def user_is_channel_subscriber(bot: Bot, user_id: int) -> tuple[bool, str | None]:
    """
    Проверка подписки через getChatMember.
    Возвращает (подписан, текст_ошибки_для_пользователя).
    """
    chat_id = await resolve_channel_chat_id_for_api(bot)
    if chat_id is None:
        return False, (
            "Проверка подписки не настроена. Администратору: задай CHANNEL_ID=-100… "
            "в .env и добавь бота админом канала."
        )
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramForbiddenError:
        logger.error(
            "channel gate: bot cannot read channel %s — добавь бота админом в канал",
            chat_id,
        )
        return False, (
            "Бот не может проверить канал. Администратору: добавь бота админом в канал."
        )
    except TelegramBadRequest as exc:
        if _bad_request_user_not_in_chat(exc):
            return False, None
        if _bad_request_bot_cannot_check_channel(exc):
            logger.error(
                "channel gate getChatMember: bot needs admin in channel %s (uid=%s): %s",
                chat_id,
                user_id,
                exc,
            )
            return False, (
                "Бот не может проверить подписку. Администратору: добавь бота "
                "администратором Telegram-канала из CHANNEL_URL."
            )
        logger.warning(
            "channel gate getChatMember failed chat=%s uid=%s: %s",
            chat_id,
            user_id,
            exc,
        )
        return False, (
            "Канал недоступен для проверки. Администратору: добавь бота админом канала "
            "и при необходимости задай CHANNEL_ID=-100… в .env."
        )
    except Exception:
        logger.exception("channel gate getChatMember error chat=%s uid=%s", chat_id, user_id)
        return False, "Не удалось проверить подписку. Попробуй через минуту."
    if _member_is_subscribed(member.status):
        return True, None
    return False, None
