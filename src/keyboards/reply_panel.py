from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def quick_panel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📋 Меню")],
            [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="👥 Реф. система")],
            [KeyboardButton(text="📊 История бюджета")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши запрос или выбери кнопку ниже…",
    )

