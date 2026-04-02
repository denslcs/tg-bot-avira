from __future__ import annotations

"""
Генерация и правка изображений: клавиатуры выбора режима/модели, FSM, хендлеры callback и сообщений.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config import (
    ADMIN_IDS,
    GEMINI_IMAGE_COST_CREDITS,
    GEMINI_IMAGE_MODEL,
    GEMINI_NANO_COST_CREDITS,
    GEMINI_NANO_MODEL,
    IMAGE_READY_IDEAS_COST_CREDITS,
    MAX_USER_MESSAGE_CHARS,
    OPENROUTER_IMAGE_COST_CREDITS,
    OPENROUTER_IMAGE_MODEL,
    QWEN_IMAGE_COST_CREDITS,
    QWEN_IMAGE_EDIT_MODEL,
    QWEN_IMAGE_MODEL,
)
from src.database import (
    add_credits,
    ensure_user,
    get_credits,
    get_daily_image_generation_usage,
    get_last_image_context,
    get_user_admin_profile,
    release_daily_image_generation,
    save_last_image_context,
    subscription_is_active,
    take_credits,
    try_reserve_daily_image_generation,
)
from src.formatting import HTML, esc
from src.gemini_image import (
    edit_image_png,
    format_gemini_user_error,
    generate_image_png,
    is_gemini_configured,
)
from src.openrouter_image import (
    OpenRouterApiError,
    format_openrouter_image_user_error,
    is_openrouter_image_configured,
    openrouter_text_to_image_bytes,
)
from src.qwen_image import (
    format_qwen_image_user_error,
    is_qwen_image_configured,
    qwen_edit_image_bytes,
    qwen_text_to_image_bytes,
)
from src.keyboards.callback_data import (
    CB_APPLY_READY_PREFIX,
    CB_BACK_IMAGE_MODELS,
    CB_CREATE_IMAGE,
    CB_GEN_EDIT,
    CB_GEN_TEXT,
    CB_MENU_BACK_START,
    CB_PICK_NANO,
    CB_PICK_NANO_2,
    CB_PICK_FLUX,
    CB_PICK_QWEN,
    CB_READY_IDEAS,
    CB_REGEN,
)
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS
from src.subscription_catalog import UNLIMITED_DAILY_IMAGE_GENERATIONS

router = Router(name="img_commands")

# Отображаемые имена моделей (в кнопках и подписи к результату)
MODEL_NANO_DISPLAY = "🍌 Nano Banana"
MODEL_NANO2_DISPLAY = "🍌🍌 Nano Banana 2"
MODEL_QWEN_DISPLAY = "🧠 Wan 2.7 Image"
MODEL_FLUX_DISPLAY = "🌲 FLUX Klein 4B"

_IMAGE_GEN_MISSING_TEXT = (
    "<b>Генерация картинок выключена.</b>\n\n"
    "<blockquote><i>Google Gemini:</i> <code>GEMINI_API_KEY</code>, "
    "<code>IMAGE_GEN_BACKEND=gemini</code> (по умолчанию).</blockquote>\n"
    "<blockquote><i>Wan 2.7 (текст→картинка и правка фото):</i> "
    "<code>IMAGE_GEN_BACKEND=qwen</code> и <code>DASHSCOPE_API_KEY</code>.</blockquote>\n"
    "<blockquote><i>OpenRouter (FLUX, текст→картинка):</i> <code>OPENROUTER_API_KEY</code>.</blockquote>\n"
    "Для Wan-правки задай <code>QWEN_IMAGE_EDIT_MODEL</code> (см. .env.example)."
)


def _text_to_image_configured() -> bool:
    return (
        is_gemini_configured()
        or is_qwen_image_configured()
        or is_openrouter_image_configured()
    )


def _text_to_image_configured_for(backend: str) -> bool:
    if backend == "qwen":
        return is_qwen_image_configured()
    if backend == "openrouter":
        return is_openrouter_image_configured()
    return is_gemini_configured()


def _edit_flow_configured() -> bool:
    return is_gemini_configured() or is_qwen_image_configured()


def _edit_flow_configured_for(backend: str) -> bool:
    """Правка по фото и готовые идеи: Gemini или Qwen-Image-Edit."""
    if backend == "qwen":
        return is_qwen_image_configured()
    return is_gemini_configured()

_BACK_MAIN = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]
_BACK_MODELS = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_IMAGE_MODELS)]


def _gemini_missing_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_BACK_MAIN])


async def _can_use_gemini_models(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    profile = await get_user_admin_profile(user_id)
    return bool(profile and subscription_is_active(profile.subscription_ends_at))


class ImageGenState(StatesGroup):
    waiting_mode = State()
    waiting_prompt = State()
    waiting_photo_for_edit = State()
    waiting_edit_text_after_photo = State()
    waiting_edit_photo_after_text = State()
    waiting_photo_for_idea = State()


def image_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🍌 Nano Banana — {GEMINI_NANO_COST_CREDITS} кредитов",
                    callback_data=CB_PICK_NANO,
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🍌🍌 Nano Banana 2 — {GEMINI_IMAGE_COST_CREDITS} кредитов",
                    callback_data=CB_PICK_NANO_2,
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🧠 Wan 2.7 — {QWEN_IMAGE_COST_CREDITS} кредитов",
                    callback_data=CB_PICK_QWEN,
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🌲 FLUX Klein — {OPENROUTER_IMAGE_COST_CREDITS} кредитов",
                    callback_data=CB_PICK_FLUX,
                    style=BTN_PRIMARY,
                )
            ],
            _BACK_MAIN,
        ]
    )


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Сгенерировать картинку текстом",
                    callback_data=CB_GEN_TEXT,
                    style=BTN_PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Изменить картинку (фото + текст)",
                    callback_data=CB_GEN_EDIT,
                    style=BTN_SUCCESS,
                )
            ],
            _BACK_MODELS,
        ]
    )


READY_IDEAS: list[tuple[str, str]] = [
    ("◼ Улучшить фото", "Сделай фото более четким, детализированным, с мягким студийным светом и естественным цветом кожи."),
    ("◾ Добавить макияж", "Сделай аккуратный естественный макияж: ровный тон, легкий контур, выразительные ресницы, натуральные губы."),
    ("◆ Сменить одежду", "Смени одежду на стильный современный образ, сохрани позу, лицо и фон максимально естественными."),
    ("◇ Новая локация", "Перенеси человека в новую реалистичную локацию, сохрани лицо и пропорции, аккуратно впиши свет и перспективу."),
    ("▦ Дизайн/стиль", "Стилизуй фото в cinematic-стиле с красивой цветокоррекцией, глубоким контрастом и чистой детализацией."),
]


async def _prepare_image_charge_and_daily_slot(
    message: Message,
    *,
    user_id: int,
    is_admin: bool,
    charge: bool,
    cost: int,
    usage_kind: str,
) -> bool:
    profile = await get_user_admin_profile(user_id) if not is_admin else None
    has_active_sub = bool(profile and subscription_is_active(profile.subscription_ends_at))
    if not is_admin:
        used0, limit0 = await get_daily_image_generation_usage(user_id, usage_kind)
        if used0 >= limit0:
            kind_label = "готовые промпты" if usage_kind == "ready" else "свои генерации"
            cta = (
                "\n<blockquote><i>Если понравилось и хочешь продолжать без дневных ограничений — "
                "оформи подписку в</i> <code>/start</code> <i>→ Оплатить.</i></blockquote>"
                if not has_active_sub
                else ""
            )
            await message.answer(
                "<b>Лимит на сегодня</b>\n"
                f"<blockquote><i>{esc(kind_label)}:</i> <b>{esc(used0)}/{esc(limit0)}</b> (UTC сутки). "
                "Дождись нового дня.</blockquote>"
                f"{cta}",
                parse_mode=HTML,
            )
            return False
    if charge:
        ok = await take_credits(user_id, cost)
        if not ok:
            balance = await get_credits(user_id)
            if has_active_sub:
                extra = (
                    "\n<blockquote><i>Подписка активна, но кредиты закончились.</i> "
                    "Можно пополнить их в <code>/start</code> → <b>Оплатить</b> (пакеты бонусов) "
                    "или пригласить друзей по ссылке из <code>/ref</code>.</blockquote>"
                )
            else:
                extra = (
                    "\n<blockquote><i>Можно оформить подписку в</i> <code>/start</code> "
                    "<i>или пригласить друзей по ссылке из</i> <code>/ref</code>.</blockquote>"
                )
            await message.answer(
                f"<blockquote><i>Недостаточно кредитов.</i> Нужно <b>{esc(cost)}</b>, у тебя <b>{esc(balance)}</b>.</blockquote>"
                f"{extra}",
                parse_mode=HTML,
            )
            return False
    if not is_admin:
        if not await try_reserve_daily_image_generation(user_id, usage_kind):
            used, limit = await get_daily_image_generation_usage(user_id, usage_kind)
            kind_label = "готовые промпты" if usage_kind == "ready" else "свои генерации"
            if charge:
                await add_credits(user_id, cost)
            await message.answer(
                "<b>Лимит занят</b>\n"
                f"<blockquote><i>Параллельный запрос.</i> {esc(kind_label)}: <b>{esc(used)}/{esc(limit)}</b> "
                "(UTC сутки). Попробуй позже или оформи подписку.</blockquote>",
                parse_mode=HTML,
            )
            return False
    return True


def _regen_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Ещё раз", callback_data=CB_REGEN, style=BTN_SUCCESS)],
            _BACK_MAIN,
        ],
    )


async def _send_result_photo_with_regen(
    message: Message,
    state: FSMContext | None,
    *,
    user_id: int,
    image_bytes: bytes,
    filename: str,
    kind: str,
    prompt: str,
    model: str,
    model_name: str,
    cost: int,
    photo_file_id: str | None,
    is_admin: bool,
    charge: bool,
    usage_kind: str,
) -> None:
    await save_last_image_context(
        user_id, kind, prompt, model, cost, model_name, photo_file_id
    )
    used_d, limit_d = await get_daily_image_generation_usage(user_id, usage_kind)
    if is_admin or limit_d >= UNLIMITED_DAILY_IMAGE_GENERATIONS:
        day_note = ""
    else:
        kind_label = "готовые промпты" if usage_kind == "ready" else "свои генерации"
        day_note = f"\n<blockquote><i>Сегодня (UTC):</i> {esc(kind_label)} {esc(used_d)}/{esc(limit_d)}.</blockquote>"
    mn = esc(model_name)
    if is_admin:
        caption = f"<b>Готово ✅</b>\n<b>ИИ:</b> {mn}\n<i>Режим админа — кредиты не списывались.</i>"
    else:
        balance = await get_credits(user_id)
        spent = ""
        if charge:
            cw = _credits_word(cost)
            spent = f"Списано: <b>{esc(cost)}</b> {cw}.\n"
        caption = (
            f"<b>Готово ✅</b>\n<b>ИИ:</b> {mn}\n"
            f"{spent}"
            f"<blockquote><i>💰 Баланс:</i> <b>{esc(balance)}</b></blockquote>{day_note}"
        )
    await message.answer_photo(
        photo=BufferedInputFile(image_bytes, filename=filename),
        caption=caption,
        reply_markup=_regen_keyboard(),
        parse_mode=HTML,
    )
    if state is not None:
        await state.clear()


async def _execute_text_generation(
    message: Message,
    state: FSMContext | None,
    *,
    user_id: int,
    username: str | None,
    prompt: str,
    model: str,
    model_name: str,
    cost: int,
    backend: str,
    usage_kind: str = "self",
) -> None:
    await ensure_user(user_id, username)
    if not _text_to_image_configured_for(backend):
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    is_admin = user_id in ADMIN_IDS
    charge = not is_admin
    if not await _prepare_image_charge_and_daily_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost, usage_kind=usage_kind
    ):
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        if backend == "qwen":
            image_bytes = await qwen_text_to_image_bytes(prompt)
        elif backend == "openrouter":
            image_bytes = await openrouter_text_to_image_bytes(prompt, model=model)
        else:
            image_bytes = await generate_image_png(prompt, model=model)
    except Exception as exc:
        if isinstance(exc, OpenRouterApiError):
            logging.warning(
                "OpenRouter отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
        else:
            logging.exception(
                "Image text generation failed user_id=%s backend=%s",
                user_id,
                backend,
            )
        if not is_admin:
            await release_daily_image_generation(user_id, usage_kind)
        if charge:
            await add_credits(user_id, cost)
        if backend == "qwen":
            err = format_qwen_image_user_error(exc)
        elif backend == "openrouter":
            err = format_openrouter_image_user_error(exc)
        else:
            err = format_gemini_user_error(exc)
        await wait_msg.edit_text(
            err,
            parse_mode=HTML if backend in ("gemini", "openrouter") else None,
            disable_web_page_preview=True,
        )
        return
    await wait_msg.delete()
    if backend == "qwen":
        caption_model_name = f"Wan 2.7 ({QWEN_IMAGE_MODEL})"
    elif backend == "openrouter":
        caption_model_name = model_name
    else:
        caption_model_name = model_name
    await _send_result_photo_with_regen(
        message,
        state,
        user_id=user_id,
        image_bytes=image_bytes,
        filename="image.png",
        kind="text",
        prompt=prompt,
        model=model,
        model_name=caption_model_name,
        cost=cost,
        photo_file_id=None,
        is_admin=is_admin,
        charge=charge,
        usage_kind=usage_kind,
    )


async def _execute_edit_generation(
    message: Message,
    state: FSMContext | None,
    *,
    user_id: int,
    username: str | None,
    prompt: str,
    model: str,
    model_name: str,
    cost: int,
    source_file_id: str,
    backend: str,
    usage_kind: str = "self",
) -> None:
    if len(prompt) > MAX_USER_MESSAGE_CHARS:
        await message.answer(
            f"Слишком длинный текст в подписи (максимум {MAX_USER_MESSAGE_CHARS} символов). "
            "Сократи описание и отправь снова."
        )
        return
    await ensure_user(user_id, username)
    if not _edit_flow_configured_for(backend):
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    is_admin = user_id in ADMIN_IDS
    charge = not is_admin
    if not await _prepare_image_charge_and_daily_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost, usage_kind=usage_kind
    ):
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        image_bytes_src = await message.bot.download(source_file_id)
        source_bytes = image_bytes_src.read()
        if backend == "qwen":
            image_bytes = await qwen_edit_image_bytes(source_bytes, prompt)
        else:
            image_bytes = await edit_image_png(source_bytes, prompt, model=model)
    except Exception as exc:
        logging.exception(
            "Image edit failed user_id=%s backend=%s",
            user_id,
            backend,
        )
        if not is_admin:
            await release_daily_image_generation(user_id, usage_kind)
        if charge:
            await add_credits(user_id, cost)
        err = (
            format_qwen_image_user_error(exc)
            if backend == "qwen"
            else format_gemini_user_error(exc)
        )
        await wait_msg.edit_text(
            err,
            parse_mode=HTML if backend == "gemini" else None,
            disable_web_page_preview=True,
        )
        return
    await wait_msg.delete()
    caption_edit_name = (
        f"Wan 2.7 Edit ({QWEN_IMAGE_EDIT_MODEL})" if backend == "qwen" else model_name
    )
    await _send_result_photo_with_regen(
        message,
        state,
        user_id=user_id,
        image_bytes=image_bytes,
        filename="edited.png",
        kind="edit",
        prompt=prompt,
        model=model,
        model_name=caption_edit_name,
        cost=cost,
        photo_file_id=source_file_id,
        is_admin=is_admin,
        charge=charge,
        usage_kind=usage_kind,
    )


def _credits_word(n: int) -> str:
    n = abs(int(n)) % 100
    n1 = n % 10
    if 10 < n < 20:
        return "кредитов"
    if n1 == 1:
        return "кредит"
    if n1 in (2, 3, 4):
        return "кредита"
    return "кредитов"


def ready_ideas_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (title, _) in enumerate(READY_IDEAS):
        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:30],
                    callback_data=f"{CB_APPLY_READY_PREFIX}{idx}",
                    style=BTN_PRIMARY,
                )
            ]
        )
    rows.append(_BACK_MAIN)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == CB_BACK_IMAGE_MODELS)
async def back_to_image_models(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not _text_to_image_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.clear()
    await callback.message.answer(
        "<b>Выбери модель ИИ</b>\n<blockquote><i>Gemini, Wan 2.7, OpenRouter (FLUX).</i></blockquote>",
        reply_markup=image_menu_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data == CB_CREATE_IMAGE)
async def open_image_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not _text_to_image_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.clear()
    await callback.message.answer(
        "<b>Выбери модель ИИ</b>\n<blockquote><i>Gemini, Wan 2.7, OpenRouter (FLUX).</i></blockquote>",
        reply_markup=image_menu_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data == CB_PICK_NANO)
async def pick_nano(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not is_gemini_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    if not await _can_use_gemini_models(callback.from_user.id):
        await callback.answer()
        await callback.message.answer(
            "<b>Gemini доступен только с активной подпиской.</b>\n"
            "<blockquote><i>Без подписки используй Wan 2.7 (кнопка в меню моделей), "
            "или оформи подписку в</i> <code>/start</code> <i>→ Оплатить.</i></blockquote>",
            parse_mode=HTML,
        )
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=GEMINI_NANO_MODEL,
        selected_name=MODEL_NANO_DISPLAY,
        selected_cost=GEMINI_NANO_COST_CREDITS,
        selected_backend="gemini",
    )
    await state.set_state(ImageGenState.waiting_mode)
    await callback.message.answer(
        "<b>Выбери режим</b>",
        reply_markup=mode_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data == CB_PICK_NANO_2)
async def pick_nano_2(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not is_gemini_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    if not await _can_use_gemini_models(callback.from_user.id):
        await callback.answer()
        await callback.message.answer(
            "<b>Gemini доступен только с активной подпиской.</b>\n"
            "<blockquote><i>Без подписки используй Wan 2.7 (кнопка в меню моделей), "
            "или оформи подписку в</i> <code>/start</code> <i>→ Оплатить.</i></blockquote>",
            parse_mode=HTML,
        )
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=GEMINI_IMAGE_MODEL,
        selected_name=MODEL_NANO2_DISPLAY,
        selected_cost=GEMINI_IMAGE_COST_CREDITS,
        selected_backend="gemini",
    )
    await state.set_state(ImageGenState.waiting_mode)
    await callback.message.answer(
        "<b>Выбери режим</b>",
        reply_markup=mode_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(F.data == CB_PICK_FLUX)
async def pick_flux(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not is_openrouter_image_configured():
        await callback.answer(
            "Нет ключа: добавь OPENROUTER_API_KEY в .env и перезапусти бота.",
            show_alert=True,
        )
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    # Сразу отвечаем на callback — иначе в Telegram «вечные часики» при ошибке ниже.
    await callback.answer()
    await state.clear()
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=OPENROUTER_IMAGE_MODEL,
        selected_name=MODEL_FLUX_DISPLAY,
        selected_cost=OPENROUTER_IMAGE_COST_CREDITS,
        selected_backend="openrouter",
    )
    await state.set_state(ImageGenState.waiting_mode)
    await callback.message.answer(
        "<b>Выбери режим</b>\n<blockquote><i>FLUX Klein — только генерация по тексту. "
        "Правка фото — через Wan или Gemini.</i></blockquote>",
        reply_markup=mode_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_PICK_QWEN)
async def pick_qwen(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not is_qwen_image_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=QWEN_IMAGE_MODEL,
        selected_name=MODEL_QWEN_DISPLAY,
        selected_cost=QWEN_IMAGE_COST_CREDITS,
        selected_backend="qwen",
    )
    await state.set_state(ImageGenState.waiting_mode)
    await callback.message.answer(
        "<b>Выбери режим</b>",
        reply_markup=mode_keyboard(),
        parse_mode=HTML,
    )
    await callback.answer()


@router.callback_query(ImageGenState.waiting_mode, F.data == CB_GEN_TEXT)
async def mode_text(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ImageGenState.waiting_prompt)
    await callback.message.answer(
        "<blockquote><i>Напиши текстом, что должно быть на картинке.</i></blockquote>",
        parse_mode=HTML,
    )


@router.callback_query(ImageGenState.waiting_mode, F.data == CB_GEN_EDIT)
async def mode_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    backend = str(data.get("selected_backend") or "gemini")
    if backend == "openrouter":
        await callback.message.answer(
            "<b>Режим «фото + текст» недоступен для FLUX Klein.</b>\n"
            "<blockquote><i>Выбери Wan 2.7 или Gemini в меню моделей — там есть правка по фото.</i></blockquote>",
            parse_mode=HTML,
        )
        return
    if not _edit_flow_configured_for(backend):
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await state.set_state(ImageGenState.waiting_photo_for_edit)
    await callback.message.answer(
        "<blockquote><i>Можно одним сообщением:</i> фото + описание в подписи.\n"
        "<i>Или двумя:</i> сначала фото, потом текст (или наоборот).</blockquote>",
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_READY_IDEAS)
async def open_ready_ideas(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "<b>Готовые идеи</b>\n<blockquote><i>Выбери промпт — потом отправь фото без текста.</i></blockquote>",
        reply_markup=ready_ideas_keyboard(),
        parse_mode=HTML,
    )


@router.message(Command("ideas"))
async def cmd_ready_ideas(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await ensure_user(message.from_user.id, message.from_user.username)
    await state.clear()
    await message.answer(
        "<b>Готовые идеи</b>\n<blockquote><i>Выбери промпт — потом отправь фото без текста.</i></blockquote>",
        reply_markup=ready_ideas_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data.startswith(CB_APPLY_READY_PREFIX))
async def apply_ready_idea(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if not _edit_flow_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    try:
        idx = int(callback.data.replace(CB_APPLY_READY_PREFIX, ""))
        title, prompt = READY_IDEAS[idx]
    except Exception:
        await callback.answer("Некорректный промпт", show_alert=True)
        return
    await callback.answer()
    await state.update_data(
        selected_model=GEMINI_IMAGE_MODEL,
        selected_name=MODEL_NANO2_DISPLAY,
        selected_cost=IMAGE_READY_IDEAS_COST_CREDITS,
        selected_backend="gemini",
        ready_prompt=prompt,
        ready_title=title,
    )
    await state.set_state(ImageGenState.waiting_photo_for_idea)
    await callback.message.answer(
        "<b>Промпт выбран.</b>\n"
        "<blockquote><i>Отправь фото без текста — сработает выбранная идея.</i></blockquote>",
        parse_mode=HTML,
    )


@router.message(ImageGenState.waiting_mode)
async def remind_pick_mode(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "<blockquote><i>Выбери режим кнопками ниже.</i></blockquote>",
        reply_markup=mode_keyboard(),
        parse_mode=HTML,
    )


@router.message(ImageGenState.waiting_prompt, ~F.text)
async def wrong_type_waiting_prompt(message: Message) -> None:
    await message.answer("Нужен текстовый промпт: напиши описание картинки одним сообщением.")


@router.message(ImageGenState.waiting_photo_for_edit, ~F.photo)
async def wrong_type_waiting_photo_edit(message: Message, state: FSMContext) -> None:
    if message.text and message.text.startswith("/"):
        return
    text = (message.text or "").strip()
    if text:
        await state.update_data(pending_edit_prompt=text)
        await state.set_state(ImageGenState.waiting_edit_photo_after_text)
        await message.answer(
            "Текст получил ✅ Теперь отправь фото, которое нужно изменить."
        )
        return
    await message.answer(
        "Нужно фото для правки. Отправь фото (с подписью или отдельно)."
    )


@router.message(ImageGenState.waiting_edit_photo_after_text, ~F.photo)
async def wrong_type_waiting_edit_photo_after_text(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Жду фото для правки. Текст уже сохранён.")


@router.message(ImageGenState.waiting_edit_text_after_photo, ~F.text)
async def wrong_type_waiting_edit_text_after_photo(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Жду текст с описанием правки. Фото уже сохранено.")


@router.message(ImageGenState.waiting_photo_for_idea, ~F.photo)
async def wrong_type_waiting_photo_idea(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Отправь фото без текста — сработает выбранный готовый промпт.")


@router.message(ImageGenState.waiting_prompt)
async def create_image_from_prompt(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Нужен текстовый промпт. Попробуй еще раз.")
        return

    user_id = message.from_user.id
    data = await state.get_data()
    model = str(data.get("selected_model") or GEMINI_IMAGE_MODEL)
    model_name = str(data.get("selected_name") or MODEL_NANO2_DISPLAY)
    cost = int(data.get("selected_cost") or GEMINI_IMAGE_COST_CREDITS)
    backend = str(data.get("selected_backend") or "gemini")
    await _execute_text_generation(
        message,
        state,
        user_id=user_id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        backend=backend,
        usage_kind="self",
    )


@router.callback_query(F.data == CB_REGEN)
async def regenerate_same(callback: CallbackQuery, _state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    user_id = callback.from_user.id
    ctx = await get_last_image_context(user_id)
    if not ctx:
        await callback.answer(
            "Нет сохранённого запроса. Сначала сгенерируй картинку.",
            show_alert=True,
        )
        return
    await callback.answer()
    if ctx.kind == "text":
        mn = ctx.model_name or ""
        if "Wan 2.7" in mn:
            backend = "qwen"
        elif "FLUX" in mn:
            backend = "openrouter"
        else:
            backend = "gemini"
        await _execute_text_generation(
            callback.message,
            None,
            user_id=user_id,
            username=callback.from_user.username,
            prompt=ctx.prompt,
            model=ctx.model,
            model_name=ctx.model_name,
            cost=ctx.cost,
            backend=backend,
            usage_kind="self",
        )
    else:
        if not ctx.photo_file_id:
            await callback.message.answer(
                "Исходное фото недоступно (истекло у Telegram или сессия пустая). "
                "Отправь фото с подписью снова через «Создать картинку»."
            )
            return
        backend = "qwen" if "Wan 2.7" in (ctx.model_name or "") else "gemini"
        await _execute_edit_generation(
            callback.message,
            None,
            user_id=user_id,
            username=callback.from_user.username,
            prompt=ctx.prompt,
            model=ctx.model,
            model_name=ctx.model_name,
            cost=ctx.cost,
            source_file_id=ctx.photo_file_id,
            backend=backend,
            usage_kind="ready" if ctx.cost == IMAGE_READY_IDEAS_COST_CREDITS else "self",
        )


@router.message(ImageGenState.waiting_photo_for_edit, F.photo)
async def create_image_edit_from_photo(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.photo:
        return
    prompt = (message.caption or "").strip()
    if not prompt:
        await state.update_data(pending_edit_photo_file_id=message.photo[-1].file_id)
        await state.set_state(ImageGenState.waiting_edit_text_after_photo)
        await message.answer("Фото получил ✅ Теперь пришли текст: что изменить на фото.")
        return
    await _generate_from_photo_with_prompt(message, state, prompt, usage_kind="self")


@router.message(ImageGenState.waiting_edit_text_after_photo, F.text)
async def create_image_edit_after_photo_then_text(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Нужен текст с описанием правки.")
        return
    data = await state.get_data()
    source_file_id = str(data.get("pending_edit_photo_file_id") or "").strip()
    if not source_file_id:
        await message.answer("Фото не найдено. Отправь фото снова.")
        await state.set_state(ImageGenState.waiting_photo_for_edit)
        return
    model = str(data.get("selected_model") or GEMINI_IMAGE_MODEL)
    model_name = str(data.get("selected_name") or MODEL_NANO2_DISPLAY)
    cost = int(data.get("selected_cost") or GEMINI_IMAGE_COST_CREDITS)
    backend = str(data.get("selected_backend") or "gemini")
    await _execute_edit_generation(
        message,
        state,
        user_id=message.from_user.id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        source_file_id=source_file_id,
        backend=backend,
        usage_kind="self",
    )


@router.message(ImageGenState.waiting_edit_photo_after_text, F.photo)
async def create_image_edit_after_text_then_photo(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.photo:
        return
    data = await state.get_data()
    prompt = str(data.get("pending_edit_prompt") or "").strip()
    if not prompt:
        await message.answer("Текст правки не найден. Напиши описание заново.")
        await state.set_state(ImageGenState.waiting_photo_for_edit)
        return
    model = str(data.get("selected_model") or GEMINI_IMAGE_MODEL)
    model_name = str(data.get("selected_name") or MODEL_NANO2_DISPLAY)
    cost = int(data.get("selected_cost") or GEMINI_IMAGE_COST_CREDITS)
    backend = str(data.get("selected_backend") or "gemini")
    await _execute_edit_generation(
        message,
        state,
        user_id=message.from_user.id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        source_file_id=message.photo[-1].file_id,
        backend=backend,
        usage_kind="self",
    )


@router.message(ImageGenState.waiting_photo_for_idea, F.photo)
async def create_image_from_ready_prompt(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.photo:
        return
    if (message.caption or "").strip():
        await message.answer("Для готового промпта отправь только фото без текста в подписи.")
        return
    data = await state.get_data()
    prompt = str(data.get("ready_prompt") or "").strip()
    if not prompt:
        await message.answer("Промпт не найден. Нажмите 'Готовые идеи' снова.")
        await state.clear()
        return
    await _generate_from_photo_with_prompt(message, state, prompt, usage_kind="ready")


async def _generate_from_photo_with_prompt(
    message: Message, state: FSMContext, prompt: str, usage_kind: str
) -> None:
    if not message.from_user or not message.photo:
        return
    user_id = message.from_user.id
    source_file_id = message.photo[-1].file_id
    data = await state.get_data()
    model = str(data.get("selected_model") or GEMINI_IMAGE_MODEL)
    model_name = str(data.get("selected_name") or MODEL_NANO2_DISPLAY)
    cost = int(data.get("selected_cost") or GEMINI_IMAGE_COST_CREDITS)
    backend = str(data.get("selected_backend") or "gemini")
    await _execute_edit_generation(
        message,
        state,
        user_id=user_id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        source_file_id=source_file_id,
        backend=backend,
        usage_kind=usage_kind,
    )

