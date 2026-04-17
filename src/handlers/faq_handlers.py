from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import OPENROUTER_IMAGE_COST_CREDITS, OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS
from src.formatting import HTML, esc
from src.handlers.commands import edit_or_send_nav_message
from src.keyboards.callback_data import CB_MENU_BACK_START
from src.keyboards.styles import BTN_PRIMARY

router = Router(name="faq")

_READY_LIMITS_FAQ_BODY = (
    "«Готовые идеи» — отдельный режим от обычной генерации.\n"
    f"За запуск списывается {OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS} кредитов.\n\n"
    "С подпиской: лимита по «Готовым идеям» нет.\n"
    "Без подписки: 1 запуск за цикл, новый цикл через 30 суток после исчерпания.\n"
    "Бонус: +1 запуск за каждых 2 друзей по /ref."
)

_CREDITS_FAQ_BODY = (
    "Кредиты тратятся только на генерацию изображений.\n\n"
    f"• «Создать картинку» — {OPENROUTER_IMAGE_COST_CREDITS} кредитов за 1 генерацию.\n"
    f"• «Готовые идеи» — {OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS} кредитов за 1 генерацию.\n"
    "• Обычные сообщения и навигация кредиты не списывают.\n\n"
    "Баланс и лимиты смотри в /profile."
)

_FAQ: list[tuple[str, str, str]] = [
    (
        "credits",
        "Как работают кредиты?",
        _CREDITS_FAQ_BODY,
    ),
    (
        "ready_limits",
        "Готовые идеи: лимиты",
        _READY_LIMITS_FAQ_BODY,
    ),
    (
        "support",
        "Как написать в поддержку?",
        "Нажми /support — откроется бот поддержки.\n"
        "Если поддержка не подключена, бот подскажет что делать.",
    ),
    (
        "spam",
        "Бот не отвечает / режет сообщения",
        "Сработал антиспам.\n"
        "Не отправляй много одинаковых или слишком частых сообщений подряд.\n"
        "Подожди немного и отправь один нормальный запрос.",
    ),
    (
        "sub",
        "Что за подписка?",
        "Подписка действует 30 дней и даёт бонусные кредиты при покупке.\n"
        "Пока подписка активна, лимиты без подписки не применяются.\n"
        "Статус и срок — в /profile. Продление — через «Оплатить».",
    ),
]


def _faq_keyboard(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (slug, title, _) in enumerate(_FAQ):
        row.append(
            InlineKeyboardButton(text=title[:30], callback_data=f"faq:{i}", style=BTN_PRIMARY)
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _faq_back_callback_from_message(message: Message | None) -> str:
    if not message or not message.reply_markup or not message.reply_markup.inline_keyboard:
        return CB_MENU_BACK_START
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if (getattr(btn, "text", "") or "").strip() == "⬅️ Назад" and getattr(btn, "callback_data", None):
                return str(btn.callback_data)
    return CB_MENU_BACK_START


@router.message(Command("faq"))
async def cmd_faq(message: Message) -> None:
    await message.answer(
        "<b>Частые вопросы</b>\n"
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
    text = f"<b>{esc(title)}</b>\n\n<blockquote>{esc(body)}</blockquote>"
    await callback.answer()
    if callback.message:
        back_callback = _faq_back_callback_from_message(callback.message)
        await edit_or_send_nav_message(
            callback.message,
            text=text,
            reply_markup=_faq_keyboard(back_callback=back_callback),
            parse_mode=HTML,
        )
