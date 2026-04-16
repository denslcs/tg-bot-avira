"""Главное меню (/start) и «Назад» на главный экран."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import SUPPORT_BOT_USERNAME
from src.keyboards.callback_data import (
    CB_CREATE_IMAGE_HUB,
    CB_CREATE_IMAGE,
    CB_MENU_ABOUT,
    CB_MENU_ABOUT_HUB,
    CB_MENU_BACK_START,
    CB_MENU_CHANNEL,
    CB_MENU_CHANNEL_HUB,
    CB_MENU_FAQ,
    CB_MENU_FAQ_HUB,
    CB_MENU_MELLSTROY,
    CB_MENU_PAY,
    CB_MENU_PAY_HUB,
    CB_MENU_PROFILE,
    CB_MENU_PROFILE_HUB,
    CB_MENU_REF,
    CB_MENU_REF_HUB,
    CB_MENU_SUPPORT,
    CB_MENU_SUPPORT_HUB,
    CB_READY_IDEAS,
    CB_READY_IDEAS_HUB,
)
from src.keyboards.styles import BTN_DANGER, BTN_PRIMARY, BTN_SUCCESS


def start_menu_keyboard(balance: int | None = None) -> InlineKeyboardMarkup:
    support_url = (
        f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_shard_creator"
        if SUPPORT_BOT_USERNAME
        else ""
    )
    support_button = (
        InlineKeyboardButton(text="💬 Поддержка", url=support_url, style=BTN_PRIMARY)
        if support_url
        else InlineKeyboardButton(text="💬 Поддержка", callback_data=CB_MENU_SUPPORT, style=BTN_PRIMARY)
    )
    # Большое главное меню на стартовом экране.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💡 Готовые идеи", callback_data=CB_READY_IDEAS, style=BTN_DANGER
                ),
            ],
            [
                InlineKeyboardButton(text="👥 Реферальная система", callback_data=CB_MENU_REF),
            ],
            [
                InlineKeyboardButton(text="💳 Оплатить", callback_data=CB_MENU_PAY, style=BTN_SUCCESS),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Что умеет бот", callback_data=CB_MENU_ABOUT),
                support_button,
            ],
            [
                InlineKeyboardButton(
                    text="Фото с Меллстройностью",
                    callback_data=CB_MENU_MELLSTROY,
                    style=BTN_DANGER,
                )
            ],
        ]
    )


def menu_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Готовые идеи", callback_data=CB_READY_IDEAS_HUB, style=BTN_DANGER
                ),
                InlineKeyboardButton(text="⭐ Подписки", callback_data=CB_MENU_PAY_HUB, style=BTN_SUCCESS),
            ],
            [
                InlineKeyboardButton(text="⭐ Поддержка", callback_data=CB_MENU_SUPPORT_HUB, style=BTN_PRIMARY),
                InlineKeyboardButton(text="⭐ Реф. система", callback_data=CB_MENU_REF_HUB),
            ],
            [
                InlineKeyboardButton(text="👤 Профиль", callback_data=CB_MENU_PROFILE_HUB, style=BTN_PRIMARY),
                InlineKeyboardButton(
                    text="🎨 Создать картинку", callback_data=CB_CREATE_IMAGE_HUB, style=BTN_PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(text="ℹ️ Что умею", callback_data=CB_MENU_ABOUT_HUB),
                InlineKeyboardButton(text="❓ FAQ", callback_data=CB_MENU_FAQ_HUB),
            ],
            [
                InlineKeyboardButton(text="📢 Канал", callback_data=CB_MENU_CHANNEL_HUB, style=BTN_SUCCESS),
                InlineKeyboardButton(text="💳 Баланс", callback_data=CB_MENU_PROFILE_HUB, style=BTN_PRIMARY),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)],
        ]
    )


def back_to_main_menu_keyboard(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)]]
    )
