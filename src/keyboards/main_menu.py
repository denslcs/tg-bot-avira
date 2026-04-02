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
    CB_READY_IDEAS,
)


def start_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎨 Создать картинку", callback_data=CB_CREATE_IMAGE),
                InlineKeyboardButton(text="💡 Готовые идеи", callback_data=CB_READY_IDEAS),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Что умеет бот", callback_data=CB_MENU_ABOUT),
                InlineKeyboardButton(text="👥 Реферальная система", callback_data=CB_MENU_REF),
            ],
            [
                InlineKeyboardButton(text="👤 Профиль", callback_data=CB_MENU_PROFILE),
            ],
            [
                InlineKeyboardButton(text="💳 Оплатить", callback_data=CB_MENU_PAY),
                InlineKeyboardButton(text="💬 Поддержка", callback_data=CB_MENU_SUPPORT),
            ],
        ]
    )


def back_to_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]]
    )
