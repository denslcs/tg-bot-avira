from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest

from src.config import ADMIN_IDS, SUPPORT_CHAT_ID
from src.database import (
    close_ticket,
    get_open_ticket_by_id,
    get_open_ticket_by_thread,
    get_ticket_detail_by_id,
    mark_first_reply_to_user,
    record_support_rating,
    update_ticket_thread,
)
from src.support_topic_naming import topic_title
from src.support_state import (
    admin_outbox_append,
    admin_outbox_join,
    admin_outbox_len,
    append_support_draft,
    clear_admin_ticket_flow,
    clear_support_draft,
    get_admin_control_message,
    get_draft_ticket_id,
    get_support_draft,
    in_support_draft,
    pop_admin_chunk,
    register_admin_chunk,
    schedule_support_draft_timers,
    set_admin_control_message,
    start_support_draft,
)


router = Router(name="support_messages")


def _resolution_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"ticket_res:{ticket_id}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"ticket_res:{ticket_id}:no"),
            ]
        ]
    )


def _rating_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=str(i), callback_data=f"tickrate:{ticket_id}:{i}")
                for i in range(1, 6)
            ]
        ]
    )


def _admin_chunk_keyboard(ticket_id: int, chunk_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить", callback_data=f"adm_snd:{ticket_id}:{chunk_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"adm_can:{ticket_id}:{chunk_id}"),
            ]
        ]
    )


def _admin_finish_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Закончить ответ", callback_data=f"adm_fin:{ticket_id}"),
            ]
        ]
    )


async def _upsert_finish_panel(bot, ticket_id: int, thread_id: int) -> None:
    n = admin_outbox_len(ticket_id)
    text = (
        f"В очереди на отправку пользователю: {n} частей.\n"
        "Пока не нажал «Закончить ответ», можно писать в теме ещё сообщения — у каждого будут свои «Отправить» / «Отмена».\n"
        "Когда готово — «Закончить ответ»: пользователь получит текст и вопрос «решён ли вопрос»."
    )
    kb = _admin_finish_keyboard(ticket_id)
    ctrl = get_admin_control_message(ticket_id)
    if ctrl:
        chat_id, msg_id = ctrl
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    sent = await bot.send_message(
        chat_id=SUPPORT_CHAT_ID,
        message_thread_id=thread_id,
        text=text,
        reply_markup=kb,
    )
    set_admin_control_message(ticket_id, sent.chat.id, sent.message_id)


@router.message(F.chat.id == SUPPORT_CHAT_ID, F.message_thread_id)
async def admin_reply_in_topic(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        return
    if message.from_user.is_bot:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    if not message.message_thread_id:
        return
    ticket = await get_open_ticket_by_thread(message.message_thread_id)
    if not ticket:
        return
    chunk_id = register_admin_chunk(ticket.ticket_id, text)
    await message.reply(
        f"Черновик ответа — часть #{chunk_id} (только эта часть).\n"
        "«Отправить» — в очередь пользователю; «Отмена» — отменить только эту часть.",
        reply_markup=_admin_chunk_keyboard(ticket.ticket_id, chunk_id),
    )


@router.callback_query(F.data.startswith("adm_snd:"))
async def admin_send_chunk(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Только для админов.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id = int(parts[1])
    chunk_id = int(parts[2])
    chunk_text = pop_admin_chunk(ticket_id, chunk_id)
    if chunk_text is None:
        await callback.answer("Эта часть уже обработана или отменена.", show_alert=True)
        return
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        clear_admin_ticket_flow(ticket_id)
        await callback.answer("Тикет закрыт.", show_alert=True)
        return
    admin_outbox_append(ticket_id, chunk_text)
    if callback.message:
        try:
            await callback.message.edit_text("Часть сохранена в очередь ✅", reply_markup=None)
        except Exception:
            pass
    await _upsert_finish_panel(callback.bot, ticket_id, ticket.thread_id)
    await callback.answer("Добавлено в очередь.")


@router.callback_query(F.data.startswith("adm_can:"))
async def admin_cancel_chunk(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Только для админов.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id = int(parts[1])
    chunk_id = int(parts[2])
    if pop_admin_chunk(ticket_id, chunk_id) is None:
        await callback.answer("Уже обработано.", show_alert=True)
        return
    if callback.message:
        try:
            await callback.message.edit_text("Эта часть отменена.", reply_markup=None)
        except Exception:
            pass
    await callback.answer("Отменено.")


@router.callback_query(F.data.startswith("adm_fin:"))
async def admin_finish_reply(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Только для админов.", show_alert=True)
        return
    ticket_id = int(callback.data.split(":")[1])
    if admin_outbox_len(ticket_id) == 0:
        await callback.answer("Очередь пуста. Сначала нажми «Отправить» у нужных частей.", show_alert=True)
        return
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        clear_admin_ticket_flow(ticket_id)
        await callback.answer("Тикет закрыт.", show_alert=True)
        return
    body = admin_outbox_join(ticket_id).strip()
    await mark_first_reply_to_user(ticket_id)
    await callback.bot.send_message(
        chat_id=ticket.user_id,
        text=f"Ответ поддержки:\n{body}",
    )
    await callback.bot.send_message(
        chat_id=ticket.user_id,
        text="Ваш вопрос решен?",
        reply_markup=_resolution_keyboard(ticket.ticket_id),
    )
    clear_admin_ticket_flow(ticket_id)
    if callback.message:
        try:
            await callback.message.edit_text(
                "Ответ отправлен пользователю ✅",
                reply_markup=None,
            )
        except Exception:
            pass
    await callback.answer("Готово.")


@router.message()
async def support_private_messages(message: Message) -> None:
    if not message.from_user:
        return
    if message.chat.type != "private":
        return

    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    user_id = message.from_user.id
    if not in_support_draft(user_id):
        await message.answer("Чтобы открыть обращение, нажми /support")
        return

    if text.lower() == "готово":
        ticket_id = get_draft_ticket_id(user_id)
        support_text = get_support_draft(user_id).strip()
        if not ticket_id or not support_text:
            await message.answer("Сначала опиши проблему, потом отправь: готово")
            return
        ticket = await get_open_ticket_by_id(ticket_id)
        if not ticket:
            clear_support_draft(user_id)
            await message.answer("Тикет уже закрыт. Нажми /support заново.")
            return

        username = f"@{message.from_user.username}" if message.from_user.username else "без_username"
        payload = (
            f"[Тикет #{ticket.ticket_id}] Сообщение от пользователя\n"
            f"user_id: {user_id}\n"
            f"username: {username}\n\n"
            f"{support_text}"
        )
        try:
            await message.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=ticket.thread_id,
                text=payload,
            )
        except TelegramBadRequest:
            topic = await message.bot.create_forum_topic(
                chat_id=SUPPORT_CHAT_ID,
                name=topic_title(ticket.ticket_id, username, "OPEN", None),
            )
            await update_ticket_thread(ticket.ticket_id, topic.message_thread_id, username)
            await message.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                message_thread_id=topic.message_thread_id,
                text=payload,
            )
        clear_support_draft(user_id)
        await message.answer("Отправили в поддержку. Ожидай ответ здесь.")
        return

    append_support_draft(user_id, text)
    await message.answer("Добавил в заявку. Когда закончишь, отправь: готово")


@router.callback_query(F.data.startswith("ticket_res:"))
async def ticket_resolution_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id = int(parts[1])
    action = parts[2]
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        await callback.answer("Тикет уже закрыт.")
        return
    if ticket.user_id != callback.from_user.id:
        await callback.answer("Это не твой тикет.", show_alert=True)
        return

    if action == "yes":
        await callback.message.edit_text(
            "Рад, что помогло 🎉\n"
            "Оцени ответ поддержки от 1 до 5 (1 — плохо, 5 — отлично).\n"
            "После выбора тикет закроется.",
            reply_markup=_rating_keyboard(ticket_id),
        )
        await callback.answer()
        return

    start_support_draft(ticket.user_id, ticket.ticket_id)
    schedule_support_draft_timers(callback.bot, ticket.user_id, ticket.ticket_id)
    await callback.message.edit_text("Понял, продолжаем. Опиши проблему еще раз и отправь: готово")
    await callback.answer("Ок, продолжаем.")


async def _close_ticket_after_rating(
    bot,
    ticket_id: int,
    thread_id: int,
    username: str,
    user_id: int,
) -> None:
    clear_admin_ticket_flow(ticket_id)
    detail = await get_ticket_detail_by_id(ticket_id)
    tname = topic_title(ticket_id, username, "CLOSED", detail.tag if detail else None)
    try:
        await bot.edit_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            name=tname,
        )
    except Exception:
        pass
    try:
        await bot.close_forum_topic(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
        )
    except Exception:
        pass
    clear_support_draft(user_id)


@router.callback_query(F.data.startswith("tickrate:"))
async def ticket_rating_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id = int(parts[1])
    try:
        score = int(parts[2])
    except ValueError:
        return
    if score < 1 or score > 5:
        await callback.answer("Оценка от 1 до 5.", show_alert=True)
        return
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != callback.from_user.id:
        await callback.answer("Это не твой тикет.", show_alert=True)
        return
    await record_support_rating(ticket_id, ticket.user_id, score)
    try:
        await callback.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=ticket.thread_id,
            text=f"Пользователь оценил тикет #{ticket_id}: {score}/5",
        )
    except Exception:
        pass
    await close_ticket(ticket_id)
    await _close_ticket_after_rating(
        callback.bot,
        ticket_id,
        ticket.thread_id,
        ticket.username,
        ticket.user_id,
    )
    if callback.message:
        await callback.message.edit_text(
            f"Спасибо за оценку! Тикет закрыт ✅ ({score}/5)",
            reply_markup=None,
        )
    await callback.answer("Спасибо!")
