from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import OPENROUTER_IMAGE_COST_CREDITS, OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS
from src.formatting import HTML, esc
from src.keyboards.callback_data import CB_MENU_BACK_START
from src.menu_nav import replace_menu_screen
from src.keyboards.styles import BTN_PRIMARY

router = Router(name="faq")

_CREDITS_FAQ_BODY = (
    "• Обычное сообщение в личке (не команда, не режим описания картинки) — бот ответит приветствием и покажет меню; "
        "кредиты за это не списываются.\n"
    f"• Картинка по своему описанию («Создать картинку») — {OPENROUTER_IMAGE_COST_CREDITS} кредитов за генерацию.\n"
    f"• Картинка по готовому промпту из раздела «Готовые идеи» — {OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS} кредитов за генерацию.\n"
    "• Без подписки дополнительно действует лимит: не больше 3 генераций за 30 суток по UTC, "
    "даже если кредитов много — дальше нужна подписка или ожидание сброса окна.\n"
    "Баланс и лимиты: /profile. Очистить историю диалога: /newchat."
)

_FAQ: list[tuple[str, str, str]] = [
    (
        "credits",
        "Как работают кредиты?",
        _CREDITS_FAQ_BODY,
    ),
    (
        "support",
        "Как написать в поддержку?",
        "Команда /support в этом боте — откроется чат поддержки с тикетами. "
        "Если бот не подключён к поддержке, в ответе будет подсказка для администратора.",
    ),
    (
        "spam",
        "Бот не отвечает / режет сообщения",
        "Подряд один и тот же текст может попасть под антиспам: бот попросит переформулировать или подождать. "
        "Есть ограничение частоты сообщений в минуту — не отправляй десятки строк подряд.",
    ),
    (
        "sub",
        "Что за подписка?",
        "Подписка на 30 дней: при оплате на баланс начисляются бонусы в кредитах (тарифы в /start → Оплатить: "
        "Stars, карта и др., если настроено).\n"
        "Пока подписка активна: текст в личке без списания кредитов; картинки — по балансу, без лимита «3 за 30 дней». "
        "Срок и тариф смотри в /profile. Продлить — снова через меню оплаты.",
    ),
]


def _faq_keyboard() -> InlineKeyboardMarkup:
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
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    if callback.message:
        try:
            if callback.message.photo:
                await replace_menu_screen(
                    callback.message,
                    caption=text,
                    reply_markup=_faq_keyboard(),
                    banner_path=None,
                )
            else:
                await callback.message.edit_text(text, reply_markup=_faq_keyboard(), parse_mode=HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=_faq_keyboard(), parse_mode=HTML)
    await callback.answer()
