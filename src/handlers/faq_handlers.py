import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import OPENROUTER_IMAGE_COST_CREDITS
from src.formatting import (
    CREDITS_COIN_TG_HTML,
    CREDITS_PREMIUM_EMOJI_ID,
    HTML,
    PROFILE_SUBSCRIPTION_LABEL_TG_HTML,
    PROFILE_VALID_UNTIL_LABEL_TG_HTML,
    esc,
    html_escape_preserve_tg_emoji,
)
from src.handlers.commands import edit_or_send_nav_message
from src.keyboards.callback_data import CB_MENU_BACK_START
from src.keyboards.styles import BTN_PRIMARY

router = Router(name="faq")

_TG_EMOJI_TAG_RE = re.compile(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", re.DOTALL)


def _faq_button_label(title: str) -> str:
    """Подпись кнопки без HTML: премиум-эмодзи заменяем на 🪙."""
    return _TG_EMOJI_TAG_RE.sub("🪙", title)[:64]


# ID премиум-эмодзи для inline-кнопок (тот же набор, что в главном меню / профиле).
_READY_IDEAS_PREMIUM_EMOJI_ID = "5422439311196834318"
_SUPPORT_CHAT_PREMIUM_EMOJI_ID = "5443038326535759644"
_FAQ_WARNING_PREMIUM_EMOJI_ID = "5447644880824181073"
_SUBSCRIPTION_DIAMOND_PREMIUM_EMOJI_ID = "5427168083074628963"
_GIFT_BONUS_PREMIUM_EMOJI_ID = "5203996991054432397"
_FAQ_LIST_PREMIUM_EMOJI_ID = "5314504236132747481"

_READY_IDEA_HEAD_TG = f'<tg-emoji emoji-id="{_READY_IDEAS_PREMIUM_EMOJI_ID}">💡</tg-emoji>'
_GIFT_HEAD_TG = f'<tg-emoji emoji-id="{_GIFT_BONUS_PREMIUM_EMOJI_ID}">🎁</tg-emoji>'
_SUPPORT_HEAD_TG = f'<tg-emoji emoji-id="{_SUPPORT_CHAT_PREMIUM_EMOJI_ID}">💬</tg-emoji>'
_WARN_HEAD_TG = f'<tg-emoji emoji-id="{_FAQ_WARNING_PREMIUM_EMOJI_ID}">⚠️</tg-emoji>'

_READY_LIMITS_FAQ_BODY = (
    f"{_READY_IDEA_HEAD_TG} «Готовые идеи» — отдельный режим от обычной генерации.\n"
    f"Стоимость зависит от режима и подписки: fast / medium / premium, в диапазоне 15–65 {CREDITS_COIN_TG_HTML}.\n\n"
    f"{PROFILE_SUBSCRIPTION_LABEL_TG_HTML} С подпиской: лимита по «Готовым идеям» нет.\n"
    f"{PROFILE_VALID_UNTIL_LABEL_TG_HTML} Без подписки: 1 запуск за цикл, новый цикл через 30 суток после исчерпания.\n"
    f"{_GIFT_HEAD_TG} Рефералка /ref: без подписки — +1 бонусный запуск за каждых 2 друзей; "
    f"с подпиской за тех же условий — +10 {CREDITS_COIN_TG_HTML} кредитов вместо запуска."
)

_CREDITS_FAQ_BODY = (
    "Кредиты тратятся только на генерацию изображений.\n\n"
    f"• «Создать картинку» — {OPENROUTER_IMAGE_COST_CREDITS} кредитов за 1 генерацию.\n"
    f"• «Готовые идеи» — от 15 до 65 кредитов (по режиму и подписке).\n"
    f"• Обычные сообщения и навигация {CREDITS_COIN_TG_HTML} кредиты не списывают.\n\n"
    "Профиль и лимиты смотри в /profile."
)

_FAQ: list[tuple[str, str, str]] = [
    (
        "credits",
        f"Как работают {CREDITS_COIN_TG_HTML} кредиты?",
        _CREDITS_FAQ_BODY,
    ),
    (
        "ready_limits",
        f"{_READY_IDEA_HEAD_TG} Готовые идеи: лимиты",
        _READY_LIMITS_FAQ_BODY,
    ),
    (
        "support",
        f"{_SUPPORT_HEAD_TG} Как написать в поддержку?",
        f"{_SUPPORT_HEAD_TG} Нажми /support — откроется бот поддержки.\n"
        "Если поддержка не подключена, бот подскажет что делать.",
    ),
    (
        "spam",
        f"{_WARN_HEAD_TG} Бот не отвечает / режет сообщения",
        f"{_WARN_HEAD_TG} Сработал антиспам.\n"
        "Не отправляй много одинаковых или слишком частых сообщений подряд.\n"
        "Подожди немного и отправь один нормальный запрос.",
    ),
    (
        "sub",
        f"{PROFILE_SUBSCRIPTION_LABEL_TG_HTML} Что за подписка?",
        f"Подписка действует 30 дней и даёт бонусные {CREDITS_COIN_TG_HTML} кредиты при покупке.\n"
        "Пока подписка активна, лимиты без подписки не применяются.\n"
        "Статус и срок — в /profile. Продление — через «Оплатить».",
    ),
]

_FAQ_BUTTON_ICON: dict[str, str] = {
    "credits": CREDITS_PREMIUM_EMOJI_ID,
    "ready_limits": _READY_IDEAS_PREMIUM_EMOJI_ID,
    "support": _SUPPORT_CHAT_PREMIUM_EMOJI_ID,
    "spam": _FAQ_WARNING_PREMIUM_EMOJI_ID,
    "sub": _SUBSCRIPTION_DIAMOND_PREMIUM_EMOJI_ID,
}

_FAQ_BUTTON_TEXT: dict[str, str] = {
    "credits": "Как работают кредиты?",
    "ready_limits": "Готовые идеи: лимиты",
    "support": "Как написать в поддержку?",
    "spam": "Бот не отвечает / режет сообщения",
    "sub": "Что за подписка?",
}


def _faq_keyboard(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (slug, title, _) in enumerate(_FAQ):
        icon_id = _FAQ_BUTTON_ICON.get(slug)
        btn_text = _FAQ_BUTTON_TEXT.get(slug) or _faq_button_label(title)
        row.append(
            InlineKeyboardButton(
                text=btn_text[:64],
                callback_data=f"faq:{i}",
                style=BTN_PRIMARY,
                **({"icon_custom_emoji_id": icon_id} if icon_id else {}),
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=back_callback,
                icon_custom_emoji_id="5256247952564825322",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _faq_back_callback_from_message(message: Message | None) -> str:
    if not message or not message.reply_markup or not message.reply_markup.inline_keyboard:
        return CB_MENU_BACK_START
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if (getattr(btn, "text", "") or "").strip() in ("Назад", "🔙 Назад", "⬅️ Назад") and getattr(btn, "callback_data", None):
                return str(btn.callback_data)
    return CB_MENU_BACK_START


@router.message(Command("faq"))
async def cmd_faq(message: Message) -> None:
    await message.answer(
        f'<b><tg-emoji emoji-id="{_FAQ_LIST_PREMIUM_EMOJI_ID}">📋</tg-emoji> Частые вопросы</b>\n'
        "<blockquote><i>Выбери тему — пришлю короткий ответ.</i></blockquote>",
        reply_markup=_faq_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data.startswith("faq:"))
async def faq_callback(callback: CallbackQuery) -> None:
    if not callback.data:
        return
    try:
        idx = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректно.", show_alert=True)
        return
    if idx < 0 or idx >= len(_FAQ):
        await callback.answer("Нет такого раздела.", show_alert=True)
        return
    _, title, body = _FAQ[idx]
    text = (
        f"<b>{html_escape_preserve_tg_emoji(title)}</b>\n\n"
        f"<blockquote>{html_escape_preserve_tg_emoji(body)}</blockquote>"
    )
    await callback.answer()
    if callback.message:
        back_callback = _faq_back_callback_from_message(callback.message)
        await edit_or_send_nav_message(
            callback.message,
            text=text,
            reply_markup=_faq_keyboard(back_callback=back_callback),
            parse_mode=HTML,
        )
