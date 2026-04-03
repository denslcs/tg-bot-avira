"""Редактирование одного сообщения меню (без копления постов в чате)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

logger = logging.getLogger(__name__)

# Компактное изображение вместо баннера на внутренних экранах (caption до 1024 симв.).
_SUBMENU_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A/sAAUBSQHwHpK8WQAAAABJRU5ErkJggg=="
)


def clip_caption_html(html: str, *, limit: int = 1024) -> str:
    if len(html) <= limit:
        return html
    return html[: max(0, limit - 1)] + "…"


async def edit_menu_photo_caption(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    image: FSInputFile | BufferedInputFile,
) -> None:
    cap = clip_caption_html(caption)
    try:
        await message.edit_media(
            InputMediaPhoto(media=image, caption=cap, parse_mode="HTML"),
            reply_markup=reply_markup,
        )
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
        await message.edit_text(cap, reply_markup=reply_markup, parse_mode="HTML")
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
    Заменить содержимое: для сообщения с фото — edit_media; для текста — edit_text.
    banner_path: файл фото для верхней части; None — минимальный placeholder под длинный caption.
    """
    cap = clip_caption_html(caption)
    try:
        if message.photo:
            if banner_path is not None and banner_path.is_file():
                img: FSInputFile | BufferedInputFile = FSInputFile(banner_path)
            else:
                img = BufferedInputFile(_SUBMENU_PLACEHOLDER_PNG, filename="nav.png")
            await edit_menu_photo_caption(message, caption=cap, reply_markup=reply_markup, image=img)
            return
        if message.text is not None:
            await edit_menu_plain_text(message, text=cap, reply_markup=reply_markup)
            return
        await message.edit_caption(caption=cap, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        msg = (e.message or "").lower()
        if "message is not modified" in msg:
            return
        logger.warning("replace_menu_screen: telegram error, %s", e)
        raise
