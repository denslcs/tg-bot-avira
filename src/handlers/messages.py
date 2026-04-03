from aiogram import F, Router
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.antispam_state import check_spam_private_message
from src.config import (
    ADMIN_IDS,
    MAIN_BOT_RELAY_SUPPORT_TOPICS,
    MAX_SUPPORT_DRAFT_TOTAL_CHARS,
    MAX_USER_MESSAGE_CHARS,
    SUPPORT_CHAT_ID,
)
from src.database import (
    add_dialog_message,
    ensure_user,
    get_open_ticket_by_id,
    get_open_ticket_by_thread,
)
from src.keyboards.main_menu import start_menu_keyboard
from src.private_rate_limit import check_private_message_rate
from src.support_state import (
    append_support_draft,
    clear_support_draft,
    get_draft_ticket_id,
    get_support_draft,
    in_support_draft,
)


router = Router(name="messages")

@router.message(F.chat.id == SUPPORT_CHAT_ID, F.message_thread_id)
async def support_topic_admin_reply(message: Message) -> None:
    if not MAIN_BOT_RELAY_SUPPORT_TOPICS:
        return
    if not message.from_user:
        return
    if message.from_user.id not in ADMIN_IDS:
        return
    answer_text = (message.text or "").strip()
    if not answer_text or answer_text.startswith("/"):
        return
    if not message.message_thread_id:
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        return
    await message.bot.send_message(
        chat_id=ticket.user_id,
        text=f"Ответ поддержки Avira:\n{answer_text}",
    )


@router.message()
async def any_message(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    if message.chat.type != "private":
        return

    if message.content_type == ContentType.SUCCESSFUL_PAYMENT:
        return

    fsm_state = await state.get_state()
    if fsm_state is not None and str(fsm_state).startswith("ImageGenState"):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("Я пока понимаю только текст 🙂 (позже добавим фото/видео).")
        return
    if text.startswith("/"):
        return

    user_id = message.from_user.id
    await ensure_user(user_id, message.from_user.username)

    if len(text) > MAX_USER_MESSAGE_CHARS:
        await message.answer(
            f"Слишком длинное сообщение (максимум {MAX_USER_MESSAGE_CHARS} символов). "
            "Сократи текст и отправь снова."
        )
        return

    if in_support_draft(user_id):
        if text.lower() == "готово":
            support_text = get_support_draft(user_id).strip()
            ticket_id = get_draft_ticket_id(user_id)
            if not support_text:
                await message.answer(
                    "Ты еще не описал проблему.\n"
                    "Сначала напиши проблему, потом отправь: готово"
                )
                return
            if not ticket_id:
                await message.answer("Тикет не найден. Нажми /support заново.")
                clear_support_draft(user_id)
                return
            ticket_row = await get_open_ticket_by_id(ticket_id)
            if not ticket_row:
                await message.answer("Тикет уже закрыт. Нажми /support для нового.")
                clear_support_draft(user_id)
                return
            sender = (
                f"[Тикет #{ticket_id}] Новое сообщение в поддержке\n"
                f"user_id: {message.from_user.id}\n"
                f"username: {ticket_row.username}\n"
                f"name: {message.from_user.full_name}\n\n"
                f"Текст:\n{support_text}"
            )
            await message.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=ticket_row.thread_id,
                text=sender,
            )
            clear_support_draft(user_id)
            await message.answer("Отправил в поддержку. Твой диалог открыт, скоро ответим.")
            return

        if not append_support_draft(user_id, text):
            await message.answer(
                f"Слишком длинное описание (не больше {MAX_SUPPORT_DRAFT_TOTAL_CHARS} символов всего). "
                "Сократи текст или отправь: готово"
            )
            return
        await message.answer(
            "Добавил в заявку.\n"
            "Если нужно, напиши еще детали.\n"
            "Когда закончишь, отправь: готово"
        )
        return

    is_admin = user_id in ADMIN_IDS
    if not is_admin:
        rate_blocked, rate_msg = check_private_message_rate(user_id)
        if rate_blocked:
            await message.answer(rate_msg or "")
            return
        blocked, spam_reply = check_spam_private_message(user_id, text)
        if blocked:
            if spam_reply:
                await message.answer(spam_reply)
            return

    try:
        await add_dialog_message(user_id, "user", text)
        reply_text = (
            "Привет! Я Avira ✨\n\n"
            "Выбери, что хочешь: картинки, оплата, профиль и остальное — в меню ниже или команда /start."
        )
        await add_dialog_message(user_id, "assistant", reply_text)
    except Exception:
        raise

    await message.answer(reply_text, reply_markup=start_menu_keyboard())

