"""Главное меню (/start) и «Назад» на главный экран."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import CHANNEL_URL, SUPPORT_BOT_USERNAME
from src.keyboards.callback_data import (
    CB_CREATE_IMAGE_HUB,
    CB_CREATE_IMAGE,
    CB_MENU_ABOUT,
    CB_MENU_ABOUT_HUB,
    CB_MENU_BACK_START,
    CB_MENU_BUDGET_HUB,
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
        InlineKeyboardButton(
            text="Поддержка",
            url=support_url,
            style=BTN_PRIMARY,
            icon_custom_emoji_id="5443038326535759644",
        )
        if support_url
        else InlineKeyboardButton(
            text="Поддержка",
            callback_data=CB_MENU_SUPPORT,
            style=BTN_PRIMARY,
            icon_custom_emoji_id="5443038326535759644",
        )
    )
    # Большое главное меню на стартовом экране.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готовые идеи",
                    callback_data=CB_READY_IDEAS,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="5422439311196834318",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Оплатить",
                    callback_data=CB_MENU_PAY,
                    style=BTN_SUCCESS,
                    icon_custom_emoji_id="5312361253610475399",
                ),
            ],
            [
                support_button,
            ],
            [
                InlineKeyboardButton(
                    text="Реферальная система",
                    callback_data=CB_MENU_REF,
                    icon_custom_emoji_id="5391320026869408028",
                ),
                InlineKeyboardButton(
                    text="Что умеет бот",
                    callback_data=CB_MENU_ABOUT,
                    icon_custom_emoji_id="5330522514231684724",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔥 ФОТО С МЕЛЛСТРОЙНОСТЬЮ 🔥",
                    callback_data=CB_MENU_MELLSTROY,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="5389038097860144794",
                )
            ],
        ]
    )


def menu_hub_keyboard() -> InlineKeyboardMarkup:
    channel_button = (
        InlineKeyboardButton(
            text="Канал",
            url=CHANNEL_URL,
            style=BTN_SUCCESS,
            icon_custom_emoji_id="5388632425314140043",
        )
        if CHANNEL_URL
        else InlineKeyboardButton(
            text="Канал",
            callback_data=CB_MENU_CHANNEL_HUB,
            style=BTN_SUCCESS,
            icon_custom_emoji_id="5388632425314140043",
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готовые идеи",
                    callback_data=CB_READY_IDEAS_HUB,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="5422439311196834318",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Подписки",
                    callback_data=CB_MENU_PAY_HUB,
                    style=BTN_SUCCESS,
                    icon_custom_emoji_id="5312361253610475399",
                ),
                channel_button,
            ],
            [
                InlineKeyboardButton(
                    text="Поддержка",
                    callback_data=CB_MENU_SUPPORT_HUB,
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5443038326535759644",
                ),
                InlineKeyboardButton(
                    text="Профиль",
                    callback_data=CB_MENU_PROFILE_HUB,
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5325971446625758812",
                ),
                InlineKeyboardButton(
                    text="🎨 Создать картинку", callback_data=CB_CREATE_IMAGE_HUB, style=BTN_PRIMARY
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Баланс",
                    callback_data=CB_MENU_PROFILE_HUB,
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5312123810638483121",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Реф. система",
                    callback_data=CB_MENU_REF_HUB,
                    icon_custom_emoji_id="5391320026869408028",
                ),
                InlineKeyboardButton(
                    text="Что умею",
                    callback_data=CB_MENU_ABOUT_HUB,
                    icon_custom_emoji_id="5330522514231684724",
                ),
                InlineKeyboardButton(
                    text="FAQ",
                    callback_data=CB_MENU_FAQ_HUB,
                    icon_custom_emoji_id="5314504236132747481",
                ),
            ],
            [
                InlineKeyboardButton(text="📊 История бюджета", callback_data=CB_MENU_BUDGET_HUB),
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=CB_MENU_BACK_START,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ],
        ]
    )


def back_to_main_menu_keyboard(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=back_callback,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ]
        ]
    )
