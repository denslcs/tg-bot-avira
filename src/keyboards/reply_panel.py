from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def quick_panel_keyboard(balance: int | None = None, mode_label: str = "Medium") -> ReplyKeyboardMarkup:
    prof_btn = (
        f"👤 Профиль: {balance}"
        if isinstance(balance, int) and balance >= 0
        else "👤 Профиль"
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=prof_btn), KeyboardButton(text="🖥 Меню")],
            [KeyboardButton(text="🫂 Реф. система"), KeyboardButton(text=f"🎛 Режим: {mode_label}")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши запрос или выбери кнопку ниже…",
    )

