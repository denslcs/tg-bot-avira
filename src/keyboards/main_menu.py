"""Главное меню (/start) и «Назад» на главный экран."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.keyboards.callback_data import (
    CB_CREATE_IMAGE,
    CB_MENU_ABOUT,
    CB_MENU_BACK_START,
    CB_MENU_PAY,
    CB_MENU_PROFILE,
    CB_MENU_REF,
    CB_MENU_SUPPORT,
)
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS


def start_menu_keyboard() -> InlineKeyboardMarkup:
    # Сверху — зелёные (success), середина — синие (primary), снизу — нейтральные (без style).
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Реферальная система", callback_data=CB_MENU_REF, style=BTN_SUCCESS
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Профиль", callback_data=CB_MENU_PROFILE, style=BTN_SUCCESS
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎨 Создать картинку", callback_data=CB_CREATE_IMAGE, style=BTN_PRIMARY
                ),
                InlineKeyboardButton(text="💳 Оплатить", callback_data=CB_MENU_PAY, style=BTN_PRIMARY),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Что умеет бот", callback_data=CB_MENU_ABOUT),
                InlineKeyboardButton(text="💬 Поддержка", callback_data=CB_MENU_SUPPORT),
            ],
        ]
    )


def back_to_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]]
    )
