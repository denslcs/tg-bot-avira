import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest

from src.config import ADMIN_IDS, MAX_SUPPORT_DRAFT_TOTAL_CHARS, SUPPORT_CHAT_ID, SUPPORT_FEEDBACK_THREAD_ID
from src.keyboards.styles import BTN_DANGER, BTN_PRIMARY, BTN_SUCCESS
from src.database import (
    close_ticket,
    count_generated_images_total,
    get_meta,
    get_open_ticket_by_id,
    get_open_ticket_by_thread,
    get_ticket_detail_by_id,
    get_user_admin_profile,
    mark_first_reply_to_user,
    record_support_rating,
    set_meta,
    subscription_is_active,
    update_ticket_thread,
)
from src.support_topic_naming import topic_title
from src.support_state import (
    admin_outbox_append,
    admin_outbox_join,
    admin_outbox_len,
    append_support_draft,
    clear_admin_ticket_flow,
    clear_feedback_preview,
    clear_feedback_session,
    clear_support_draft,
    get_admin_control_message,
    get_draft_ticket_id,
    get_feedback_preview,
    get_support_draft,
    in_feedback_await_text,
    in_feedback_preview,
    in_support_draft,
    pop_feedback_await_text,
    pop_admin_chunk,
    register_admin_chunk,
    schedule_support_draft_timers,
    set_admin_control_message,
    set_feedback_preview,
    start_support_draft,
    start_feedback_await_text,
)

router = Router(name="support_messages")
logger = logging.getLogger(__name__)

MAX_FEEDBACK_CHARS = 3500
_META_FEEDBACK_THREAD = "support_feedback_thread_id"


def _days_in_main_bot(created_at: str | None) -> int:
    text = (created_at or "").strip()
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


async def _ensure_feedback_thread_id(bot) -> int | None:
    if SUPPORT_CHAT_ID == 0:
        return None
    raw = await get_meta(_META_FEEDBACK_THREAD)
    if raw and str(raw).strip().isdigit():
        return int(str(raw).strip())
    if SUPPORT_FEEDBACK_THREAD_ID > 0:
        await set_meta(_META_FEEDBACK_THREAD, str(SUPPORT_FEEDBACK_THREAD_ID))
        return SUPPORT_FEEDBACK_THREAD_ID
    try:
        topic = await bot.create_forum_topic(chat_id=SUPPORT_CHAT_ID, name="Отзывы (анонимно)")
        tid = topic.message_thread_id
        await set_meta(_META_FEEDBACK_THREAD, str(tid))
        return tid
    except Exception:
        logger.exception("create feedback forum topic failed")
        return None


async def _post_anonymous_feedback(bot, ticket_id: int, score: int, feedback_text: str | None) -> None:
    thread_id = await _ensure_feedback_thread_id(bot)
    if thread_id is None:
        return
    body = (feedback_text or "").strip()
    fb_block = body if body else "— без текстового отзыва"
    text = (
        "Анонимный отзыв\n"
        f"Оценка: {score}/5\n"
        f"Тикет: #{ticket_id}\n\n"
        f"{fb_block}"
    )
    try:
        await bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            message_thread_id=thread_id,
            text=text[:4000],
        )
    except Exception:
        logger.exception("post anonymous feedback failed")


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
                InlineKeyboardButton(
                    text=str(i), callback_data=f"tickrate:{ticket_id}:{i}", style=BTN_PRIMARY
                )
                for i in range(1, 6)
            ],
            [
                InlineKeyboardButton(text="В другой раз", callback_data=f"rate_later:{ticket_id}"),
            ],
        ]
    )


def _feedback_offer_keyboard(ticket_id: int, score: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Написать отзыв",
                    callback_data=f"fb_write:{ticket_id}:{score}",
                    style=BTN_PRIMARY,
                ),
                InlineKeyboardButton(
                    text="В другой раз",
                    callback_data=f"fb_skip:{ticket_id}:{score}",
                ),
            ],
        ]
    )


def _feedback_confirm_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить", callback_data=f"fb_send:{ticket_id}"),
                InlineKeyboardButton(text="Изменить", callback_data=f"fb_edit:{ticket_id}"),
            ],
            [
                InlineKeyboardButton(text="Отмена", callback_data=f"fb_cancel:{ticket_id}"),
            ],
        ]
    )


def _admin_chunk_keyboard(ticket_id: int, chunk_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data=f"adm_snd:{ticket_id}:{chunk_id}",
                    style=BTN_SUCCESS,
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"adm_can:{ticket_id}:{chunk_id}",
                    style=BTN_DANGER,
                ),
            ]
        ]
    )


def _admin_finish_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Закончить ответ",
                    callback_data=f"adm_fin:{ticket_id}",
                    style=BTN_SUCCESS,
                ),
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

    if in_feedback_preview(user_id):
        await message.answer(
            "Сначала нажми одну из кнопок под черновиком отзыва: "
            "«Отправить», «Изменить» или «Отмена»."
        )
        return

    if in_feedback_await_text(user_id):
        if len(text) > MAX_FEEDBACK_CHARS:
            await message.answer(
                f"Слишком длинно (максимум {MAX_FEEDBACK_CHARS} символов). Сократи и отправь снова."
            )
            return
        pair = pop_feedback_await_text(user_id)
        if not pair:
            return
        ticket_id, score = pair
        set_feedback_preview(user_id, ticket_id, score, text)
        await message.answer(
            "Проверь текст отзыва ниже. Можно отправить, переписать или отменить.\n\n"
            f"━━━\n{text}\n━━━",
            reply_markup=_feedback_confirm_keyboard(ticket_id),
        )
        return

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
        profile = await get_user_admin_profile(user_id)
        sub_till = "—"
        sub_line = "не активна"
        days_in_bot = 0
        generated_total = await count_generated_images_total(user_id)
        if profile:
            days_in_bot = _days_in_main_bot(profile.created_at)
            sub_till = profile.subscription_ends_at or "—"
            if subscription_is_active(profile.subscription_ends_at):
                plan = (profile.subscription_plan or "").strip()
                sub_line = f"активна ({plan})" if plan else "активна"
        payload = (
            f"[Тикет #{ticket.ticket_id}] Сообщение от пользователя\n"
            f"user_id: {user_id}\n"
            f"username: {username}\n"
            f"подписка/тариф: {sub_line}\n"
            f"действует до (UTC): {sub_till}\n"
            f"дней в основном боте: {days_in_bot}\n"
            f"сгенерировано изображений: {generated_total}\n\n"
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

    if not append_support_draft(user_id, text):
        await message.answer(
            f"Слишком длинное описание (не больше {MAX_SUPPORT_DRAFT_TOTAL_CHARS} символов всего). "
            "Сократи текст или отправь: готово"
        )
        return
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
            "Потом по желанию можно оставить анонимный отзыв — или нажать «В другой раз» и закрыть без оценки.",
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


@router.callback_query(F.data.startswith("rate_later:"))
async def rate_later_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    ticket_id = int(parts[1])
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != callback.from_user.id:
        await callback.answer("Это не твой тикет.", show_alert=True)
        return
    clear_feedback_session(callback.from_user.id)
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
            "Ок, тикет закрыт без оценки.\n"
            "Если позже захочешь поделиться мнением — об этом можно написать в новом обращении.",
            reply_markup=None,
        )
    await callback.answer("Закрыто.")


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
    if score <= 3:
        offer = (
            f"Оценка: {score}/5.\n"
            "Напиши, пожалуйста, что не устроило — так мы сможем исправиться.\n"
            "Или отложи отзыв."
        )
    else:
        offer = (
            f"Оценка: {score}/5 — спасибо!\n"
            "Хочешь оставить короткий отзыв о поддержке? Или пропусти."
        )
    if callback.message:
        await callback.message.edit_text(
            offer,
            reply_markup=_feedback_offer_keyboard(ticket_id, score),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("fb_skip:"))
async def feedback_skip_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id, score = int(parts[1]), int(parts[2])
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != callback.from_user.id:
        await callback.answer("Это не твой тикет.", show_alert=True)
        return
    uid = callback.from_user.id
    clear_feedback_session(uid)
    await record_support_rating(ticket_id, uid, score, None)
    await _post_anonymous_feedback(callback.bot, ticket_id, score, None)
    await close_ticket(ticket_id)
    await _close_ticket_after_rating(
        callback.bot,
        ticket_id,
        ticket.thread_id,
        ticket.username,
        uid,
    )
    if callback.message:
        await callback.message.edit_text(
            f"Спасибо за оценку {score}/5! Тикет закрыт ✅\n"
            "Текстового отзыва нет — мы всё равно учли оценку (анонимно для команды).",
            reply_markup=None,
        )
    await callback.answer("Готово.")


@router.callback_query(F.data.startswith("fb_write:"))
async def feedback_write_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    ticket_id, score = int(parts[1]), int(parts[2])
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != callback.from_user.id:
        await callback.answer("Это не твой тикет.", show_alert=True)
        return
    start_feedback_await_text(callback.from_user.id, ticket_id, score)
    if callback.message:
        await callback.message.edit_text(
            "Напиши отзыв одним сообщением в этот чат (не нужно писать «готово»).\n"
            "После отправки появятся кнопки «Отправить», «Изменить», «Отмена».",
            reply_markup=None,
        )
    await callback.answer("Жду текст.")


@router.callback_query(F.data.startswith("fb_send:"))
async def feedback_send_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    ticket_id = int(parts[1])
    uid = callback.from_user.id
    prev = get_feedback_preview(uid)
    if not prev or prev[0] != ticket_id:
        await callback.answer("Черновик устарел. Начни оценку заново, если тикет ещё открыт.", show_alert=True)
        return
    tid, score, body = prev
    ticket = await get_open_ticket_by_id(tid)
    if not ticket:
        clear_feedback_session(uid)
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != uid:
        await callback.answer("Ошибка.", show_alert=True)
        return
    clear_feedback_preview(uid)
    await record_support_rating(tid, uid, score, body.strip())
    await _post_anonymous_feedback(callback.bot, tid, score, body)
    await close_ticket(tid)
    await _close_ticket_after_rating(
        callback.bot,
        tid,
        ticket.thread_id,
        ticket.username,
        uid,
    )
    if callback.message:
        await callback.message.edit_text(
            f"Спасибо! Отзыв и оценка {score}/5 сохранены. Тикет закрыт ✅",
            reply_markup=None,
        )
    await callback.answer("Спасибо!")


@router.callback_query(F.data.startswith("fb_edit:"))
async def feedback_edit_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    ticket_id = int(parts[1])
    uid = callback.from_user.id
    prev = get_feedback_preview(uid)
    if not prev or prev[0] != ticket_id:
        await callback.answer("Нет черновика для правки.", show_alert=True)
        return
    _, score, _old = prev
    clear_feedback_preview(uid)
    start_feedback_await_text(uid, ticket_id, score)
    if callback.message:
        await callback.message.edit_text(
            "Ок, напиши новый текст отзыва одним сообщением.",
            reply_markup=None,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("fb_cancel:"))
async def feedback_cancel_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user:
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    ticket_id = int(parts[1])
    uid = callback.from_user.id
    prev = get_feedback_preview(uid)
    if not prev or prev[0] != ticket_id:
        await callback.answer("Нет активного черновика.", show_alert=True)
        return
    _tid, score, _body = prev
    ticket = await get_open_ticket_by_id(ticket_id)
    if not ticket:
        clear_feedback_session(uid)
        await callback.answer("Тикет уже закрыт.", show_alert=True)
        return
    if ticket.user_id != uid:
        await callback.answer("Ошибка.", show_alert=True)
        return
    clear_feedback_session(uid)
    await record_support_rating(ticket_id, uid, score, None)
    await _post_anonymous_feedback(callback.bot, ticket_id, score, None)
    await close_ticket(ticket_id)
    await _close_ticket_after_rating(
        callback.bot,
        ticket_id,
        ticket.thread_id,
        ticket.username,
        uid,
    )
    if callback.message:
        await callback.message.edit_text(
            f"Оценка {score}/5 сохранена без текстового отзыва. Тикет закрыт ✅",
            reply_markup=None,
        )
    await callback.answer("Готово.")
