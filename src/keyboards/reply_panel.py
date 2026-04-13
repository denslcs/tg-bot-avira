from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def quick_panel_keyboard(balance: int | None = None) -> ReplyKeyboardMarkup:
    bal_btn = (
        f"💰 Баланс: {balance}"
        if isinstance(balance, int) and balance >= 0
        else "💰 Баланс"
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=bal_btn), KeyboardButton(text="📋 Меню")],
            [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="👥 Реф. система")],
            [KeyboardButton(text="📊 История бюджета")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши запрос или выбери кнопку ниже…",
    )

