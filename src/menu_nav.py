"""Редактирование одного сообщения меню (без копления постов в чате)."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

from src.formatting import HTML

logger = logging.getLogger(__name__)


def clip_caption_html(html: str, *, limit: int = 1024) -> str:
    if len(html) <= limit:
        return html
    return html[: max(0, limit - 1)] + "…"


async def edit_menu_photo_caption(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    image: FSInputFile,
) -> None:
    cap = clip_caption_html(caption)
    try:
        await message.edit_media(
            InputMediaPhoto(media=image, caption=cap, parse_mode=HTML),
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "message is not modified" in msg:
            return
        raise


async def edit_menu_caption_keep_photo(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    """Обновить подпись и клавиатуру, не меняя файл фото (надёжнее смены медиа на миниатюру)."""
    cap = clip_caption_html(caption)
    try:
        await message.edit_caption(caption=cap, reply_markup=reply_markup, parse_mode=HTML)
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "message is not modified" in msg:
            return
        raise


async def edit_menu_plain_text(
    message: Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    cap = clip_caption_html(text)
    try:
        await message.edit_text(cap, reply_markup=reply_markup, parse_mode=HTML)
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "message is not modified" in msg:
            return
        raise


async def replace_menu_screen(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    banner_path: Path | None = None,
) -> None:
    """
    Заменить экран меню.
    Сообщение с фото: если задан banner_path — смена картинки (edit_media); иначе только подпись и кнопки
    (тот же файл фото — Telegram часто отклоняет смену меди на крошечный PNG).
    Текстовое сообщение — edit_text.
    """
    cap = clip_caption_html(caption)
    try:
        if message.photo:
            if banner_path is not None and banner_path.is_file():
                await edit_menu_photo_caption(
                    message,
                    caption=cap,
                    reply_markup=reply_markup,
                    image=FSInputFile(banner_path),
                )
            else:
                await edit_menu_caption_keep_photo(
                    message, caption=cap, reply_markup=reply_markup
                )
            return
        if message.text is not None:
            await edit_menu_plain_text(message, text=cap, reply_markup=reply_markup)
            return
        await message.edit_caption(caption=cap, reply_markup=reply_markup, parse_mode=HTML)
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "message is not modified" in msg:
            return
        logger.warning("replace_menu_screen: telegram error, %s", e)
        raise
