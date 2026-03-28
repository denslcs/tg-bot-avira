from aiogram import F, Router
from aiogram.types import Message

from src.antispam_state import check_spam_private_message
from src.config import ADMIN_IDS, MAIN_BOT_RELAY_SUPPORT_TOPICS, SUPPORT_CHAT_ID
from src.database import (
    add_dialog_message,
    ensure_user,
    get_credits,
    get_last_dialog_messages,
    get_open_ticket_by_id,
    get_open_ticket_by_thread,
    get_user_admin_profile,
    spend_one_credit,
    subscription_is_active,
)
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
async def any_message(message: Message) -> None:
    if not message.from_user:
        return
    if message.chat.type != "private":
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("Я пока понимаю только текст 🙂 (позже добавим фото/видео).")
        return
    if text.startswith("/"):
        return

    user_id = message.from_user.id
    await ensure_user(user_id, message.from_user.username)

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

        append_support_draft(user_id, text)
        await message.answer(
            "Добавил в заявку.\n"
            "Если нужно, напиши еще детали.\n"
            "Когда закончишь, отправь: готово"
        )
        return

    is_admin = user_id in ADMIN_IDS
    if not is_admin:
        blocked, spam_reply = check_spam_private_message(user_id, text)
        if blocked:
            if spam_reply:
                await message.answer(spam_reply)
            return

    if not is_admin:
        profile = await get_user_admin_profile(user_id)
        sub_free = profile is not None and subscription_is_active(profile.subscription_ends_at)
        if not sub_free:
            is_spent = await spend_one_credit(user_id)
            if not is_spent:
                balance = await get_credits(user_id)
                await message.answer(
                    "У тебя закончились кредиты 😔\n"
                    f"Текущий баланс: {balance}.\n"
                    "Скоро добавим пополнение."
                )
                return

    await add_dialog_message(user_id, "user", text)
    history = await get_last_dialog_messages(user_id, limit=10)

    # Временно вместо ИИ показываем, что память работает.
    reply_text = (
        f"Avira получила: {text}\n\n"
        f"В памяти сейчас: {len(history)} сообщений (максимум 10).\n"
        "Следующий шаг: подключим Gemini через прокси."
    )
    await add_dialog_message(user_id, "assistant", reply_text)

    if is_admin:
        await message.answer(f"{reply_text}\n\nРежим админа: безлимит кредитов.")
        return

    profile = await get_user_admin_profile(user_id)
    sub_note = ""
    if profile and subscription_is_active(profile.subscription_ends_at):
        sub_note = "\n(Запрос не списал кредит: активна подписка.)"
    balance = await get_credits(user_id)
    await message.answer(f"{reply_text}\n\nОсталось кредитов: {balance}{sub_note}")

