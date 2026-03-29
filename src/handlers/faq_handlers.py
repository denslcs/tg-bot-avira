from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router(name="faq")

_FAQ: list[tuple[str, str, str]] = [
    (
        "credits",
        "Как работают кредиты?",
        "Один текстовый запрос к боту в личке списывает 1 кредит (кроме админов).\n"
        "Баланс: /profile\n"
        "История диалога можно очистить: /newchat",
    ),
    (
        "support",
        "Как написать в поддержку?",
        "Открой отдельный чат поддержки: команда /support в этом боте.\n"
        "Там тикеты и ответы команды.",
    ),
    (
        "spam",
        "Бот не отвечает / режет сообщения",
        "Если много раз подряд отправляешь один и тот же текст, сработает защита от спама "
        "и кратковременная пауза. Переформулируй вопрос или подожди немного.",
    ),
    (
        "sub",
        "Что за подписка?",
        "Это по сути месячная привилегия: пока подписка активна, у тебя расширенные возможности по сравнению "
        "с бесплатным режимом.\n"
        "Планируется доступ к нескольким моделям ИИ, больше бесплатных промптов, выгоднее по кредитам "
        "и лимитам (например, дневным). Точные цифры появятся в боте, когда подключим модели и тарифы.\n"
        "Срок подписки можно посмотреть в /profile; продление пока через команду админа.",
    ),
]


def _faq_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (slug, title, _) in enumerate(_FAQ):
        row.append(InlineKeyboardButton(text=title[:30], callback_data=f"faq:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("faq"))
async def cmd_faq(message: Message) -> None:
    await message.answer(
        "Выбери тему — пришлю короткий ответ:",
        reply_markup=_faq_keyboard(),
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
    text = f"{title}\n\n{body}"
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=_faq_keyboard())
        except Exception:
            await callback.message.answer(text)
    await callback.answer()
