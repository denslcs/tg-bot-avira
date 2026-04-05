from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.config import OPENROUTER_IMAGE_COST_CREDITS, OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS
from src.formatting import HTML, esc
from src.handlers.commands import delete_nav_source_message
from src.keyboards.callback_data import CB_MENU_BACK_START
from src.keyboards.styles import BTN_PRIMARY

router = Router(name="faq")

_READY_LIMITS_FAQ_BODY = (
    "Раздел «Готовые идеи» считается отдельно от обычных картинок («Создать картинку»).\n\n"
    "За каждую генерацию из «Готовых идей» всегда списываются кредиты по тарифу. "
    "Отдельно учитывается лимит или токен: либо списывается бесплатный слот по правилам тарифа/без подписки, "
    "либо — если слотов уже нет — один токен готовых идей. Токен не заменяет кредиты.\n\n"
    "Дневной лимит по подписке (Nova, SuperNova, Galaxy — сколько генераций в сутки, смотри /profile): "
    "календарные сутки по московскому времени (МСК), сброс в 00:00 МСК. "
    "Пока дневной лимит не исчерпан, токены не тратятся. "
    "После исчерпания дневного лимита каждая следующая генерация дополнительно тратит один токен "
    "(кредиты списываются в любом случае).\n\n"
    "У тарифов Starter и Universe отдельного дневного лимита по «готовым идеям» нет — токены копятся на балансе "
    "и пригодятся при смене тарифа или без подписки.\n\n"
    "Без подписки: отдельные циклы для картинок (до 3 за цикл) и для «готовых идей» (1 за цикл). "
    "Когда все слоты цикла израсходованы, новый цикл откроется ровно через 30 суток от момента исчерпания "
    "(то же время суток по UTC). Сверх слота — токены или подписка; кредиты за генерацию всё равно нужны."
)

_CREDITS_FAQ_BODY = (
    "• Обычное сообщение в личке (не команда, не режим описания картинки) — бот ответит приветствием и покажет меню; "
        "кредиты за это не списываются.\n"
    f"• Картинка по своему описанию («Создать картинку») — {OPENROUTER_IMAGE_COST_CREDITS} кредитов за генерацию.\n"
    f"• Картинка из раздела «Готовые идеи» — {OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS} кредитов за генерацию; "
    "лимит дня и токены — в FAQ «Готовые идеи: лимит и токены» и в /profile.\n"
    "• Без подписки лимит на картинки: цикл до 3 генераций; после полного исчерпания следующий цикл через 30 суток "
    "от момента исчерпания (UTC). Кредиты лимит не обходят — см. /profile и FAQ про готовые идеи.\n"
    "Баланс и лимиты: /profile. Очистить историю диалога: /newchat."
)

_FAQ: list[tuple[str, str, str]] = [
    (
        "credits",
        "Как работают кредиты?",
        _CREDITS_FAQ_BODY,
    ),
    (
        "ready_limits",
        "Готовые идеи: лимит и токены",
        _READY_LIMITS_FAQ_BODY,
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
        "Пока подписка активна: текст в личке без списания кредитов; картинки — по балансу, без лимита цикла как без подписки. "
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
    await callback.answer()
    if callback.message:
        chat_id = callback.message.chat.id
        await delete_nav_source_message(callback.message)
        await callback.bot.send_message(
            chat_id,
            text,
            reply_markup=_faq_keyboard(),
            parse_mode=HTML,
        )
