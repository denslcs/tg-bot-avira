from __future__ import annotations

"""
Генерация изображений по тексту через OpenRouter (FLUX). Без Gemini/Qwen и без правки по фото.
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
    OPENROUTER_IMAGE_COST_CREDITS,
    OPENROUTER_IMAGE_MODEL,
)
from src.database import (
    ImageChargeMeta,
    add_credits,
    ensure_user,
    get_credits,
    get_daily_image_generation_usage,
    get_last_image_context,
    get_nonsub_image_quota_status,
    get_user_admin_profile,
    release_daily_image_generation,
    release_nonsub_image_quota_slot,
    save_last_image_context,
    subscription_is_active,
    take_credits,
    try_reserve_daily_image_generation,
    try_reserve_nonsub_image_quota_slot,
)
from src.formatting import HTML, esc
from src.keyboards.callback_data import (
    CB_APPLY_READY_PREFIX,
    CB_BACK_IMAGE_MODELS,
    CB_CREATE_IMAGE,
    CB_IMG_CANCEL,
    CB_MENU_BACK_START,
    CB_READY_IDEAS,
    CB_REGEN,
)
from src.keyboards.main_menu import start_menu_keyboard
from src.keyboards.styles import BTN_DANGER, BTN_PRIMARY, BTN_SUCCESS
from src.openrouter_image import (
    OpenRouterApiError,
    format_openrouter_image_user_error,
    is_openrouter_image_configured,
    openrouter_text_to_image_bytes,
)
from src.subscription_catalog import NONSUB_IMAGE_WINDOW_DAYS, NONSUB_IMAGE_WINDOW_MAX

router = Router(name="img_commands")

# Готовые промпты: (текст на кнопке, полный текст для генерации). Пока пусто — дополни сам.
# Пример: ("🥤 Коллаж напитков", "coca cola and pepsi bottles, studio photo...")
READY_IDEAS: list[tuple[str, str]] = []

# Подпись для внутреннего контекста «Ещё раз» (пользователю не показываем).
_IMAGE_CONTEXT_LABEL = "text2img"

_IMAGE_GEN_MISSING_TEXT = (
    "<b>Генерация картинок выключена.</b>\n\n"
    "<blockquote>Администратору: задай <code>OPENROUTER_API_KEY</code> и при необходимости "
    "<code>OPENROUTER_IMAGE_MODEL</code> в <code>.env</code> (см. .env.example).</blockquote>"
)

_BACK_MAIN = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]


def _waiting_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💡 Готовые идеи", callback_data=CB_READY_IDEAS, style=BTN_SUCCESS
                ),
                InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=CB_IMG_CANCEL, style=BTN_DANGER
                ),
            ],
        ]
    )


def _missing_config_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_BACK_MAIN])


class ImageGenState(StatesGroup):
    waiting_prompt = State()


async def _prepare_image_charge_and_daily_slot(
    message: Message,
    *,
    user_id: int,
    is_admin: bool,
    charge: bool,
    cost: int,
    usage_kind: str,
) -> tuple[bool, ImageChargeMeta | None]:
    meta = ImageChargeMeta()
    profile = await get_user_admin_profile(user_id) if not is_admin else None
    has_active_sub = bool(profile and subscription_is_active(profile.subscription_ends_at))

    if is_admin:
        return True, meta

    if not has_active_sub:
        if not await try_reserve_nonsub_image_quota_slot(user_id):
            await message.answer(
                "<b>Лимит без подписки</b>\n"
                f"<blockquote>За <b>{NONSUB_IMAGE_WINDOW_DAYS}</b> дней (UTC) доступно не более "
                f"<b>{NONSUB_IMAGE_WINDOW_MAX}</b> генераций картинок — даже при большом балансе кредитов. "
                "Оформи подписку в <code>/start</code> → <b>Оплатить</b> или дождись сброса окна.</blockquote>",
                parse_mode=HTML,
            )
            return False, None
        meta.nonsub_quota_reserved = True
        if charge:
            ok = await take_credits(user_id, cost)
            if not ok:
                await release_nonsub_image_quota_slot(user_id)
                balance = await get_credits(user_id)
                await message.answer(
                    f"<blockquote><i>Недостаточно кредитов.</i> Нужно <b>{esc(cost)}</b>, у тебя <b>{esc(balance)}</b>."
                    "</blockquote>",
                    parse_mode=HTML,
                )
                return False, None
            meta.credit_charged = True
        return True, meta

    if charge:
        ok = await take_credits(user_id, cost)
        if not ok:
            balance = await get_credits(user_id)
            extra = (
                "\n<blockquote><i>Подписка активна, но кредиты закончились.</i> "
                "Можно пополнить в <code>/start</code> → <b>Оплатить</b> (пакеты бонусов) "
                "или пригласить друзей по <code>/ref</code>.</blockquote>"
            )
            await message.answer(
                f"<blockquote><i>Недостаточно кредитов.</i> Нужно <b>{esc(cost)}</b>, у тебя <b>{esc(balance)}</b>.</blockquote>"
                f"{extra}",
                parse_mode=HTML,
            )
            return False, None
        meta.credit_charged = True

    if not await try_reserve_daily_image_generation(user_id, usage_kind):
        used, limit = await get_daily_image_generation_usage(user_id, usage_kind)
        if meta.credit_charged:
            await add_credits(user_id, cost)
        await message.answer(
            "<b>Лимит занят</b>\n"
            f"<blockquote><i>Параллельный запрос.</i> Сегодня (UTC): <b>{esc(used)}/{esc(limit)}</b>. "
            "Попробуй позже.</blockquote>",
            parse_mode=HTML,
        )
        return False, None
    meta.daily_reserved = True
    return True, meta


def ready_ideas_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (title, _) in enumerate(READY_IDEAS):
        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:64],
                    callback_data=f"{CB_APPLY_READY_PREFIX}{idx}",
                    style=BTN_PRIMARY,
                )
            ]
        )
    rows.append(_BACK_MAIN)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _regen_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Ещё раз", callback_data=CB_REGEN, style=BTN_SUCCESS)],
            _BACK_MAIN,
        ],
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


async def _send_result_photo_with_regen(
    message: Message,
    state: FSMContext | None,
    *,
    user_id: int,
    image_bytes: bytes,
    filename: str,
    prompt: str,
    model: str,
    cost: int,
    is_admin: bool,
    charge: bool,
    deducted_credits: bool,
    served_from_cache: bool = False,
) -> None:
    await save_last_image_context(
        user_id, "text", prompt, model, cost, _IMAGE_CONTEXT_LABEL, None
    )
    if is_admin:
        day_note = ""
    else:
        q = await get_nonsub_image_quota_status(user_id)
        if q:
            u, lim = q
            day_note = (
                f"\n<blockquote><i>Генераций без подписки за {NONSUB_IMAGE_WINDOW_DAYS} дней (UTC):</i> "
                f"<b>{esc(u)}/{esc(lim)}</b>.</blockquote>"
            )
        else:
            day_note = ""
    if is_admin:
        cache_note = ""
        if served_from_cache:
            cache_note = "\n<i>Кэш: тот же промпт+модель — файл с диска, запрос к API не уходил.</i>"
        caption = f"<b>Готово ✅</b>\n<i>Режим админа — кредиты не списывались.</i>{cache_note}"
    else:
        balance = await get_credits(user_id)
        spent = ""
        if charge and deducted_credits:
            cw = _credits_word(cost)
            spent = f"Списано: <b>{esc(cost)}</b> {cw}.\n"
        caption = (
            f"<b>Готово ✅</b>\n"
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
    cost: int,
    usage_kind: str = "self",
    use_image_cache: bool = True,
) -> None:
    await ensure_user(user_id, username)
    if not is_openrouter_image_configured():
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    is_admin = user_id in ADMIN_IDS
    charge = not is_admin
    prep = await _prepare_image_charge_and_daily_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost, usage_kind=usage_kind
    )
    ok, meta = prep
    if not ok or meta is None:
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        image_bytes, from_cache = await openrouter_text_to_image_bytes(
            prompt, model=model, use_cache=use_image_cache
        )
    except Exception as exc:
        if isinstance(exc, OpenRouterApiError):
            logging.warning(
                "OpenRouter отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
        else:
            logging.exception("Image text generation failed user_id=%s", user_id)
        if meta.daily_reserved:
            await release_daily_image_generation(user_id, usage_kind)
        if meta.credit_charged:
            await add_credits(user_id, cost)
        if meta.nonsub_quota_reserved:
            await release_nonsub_image_quota_slot(user_id)
        err = format_openrouter_image_user_error(exc)
        await wait_msg.edit_text(err, parse_mode=HTML, disable_web_page_preview=True)
        return
    await wait_msg.delete()
    await _send_result_photo_with_regen(
        message,
        state,
        user_id=user_id,
        image_bytes=image_bytes,
        filename="image.png",
        prompt=prompt,
        model=model,
        cost=cost,
        is_admin=is_admin,
        charge=charge,
        deducted_credits=meta.credit_charged,
        served_from_cache=from_cache,
    )


async def _start_image_flow(message: Message, state: FSMContext, user_id: int, username: str | None) -> None:
    if not is_openrouter_image_configured():
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    await ensure_user(user_id, username)
    await state.clear()
    await state.update_data(
        selected_model=OPENROUTER_IMAGE_MODEL,
        selected_cost=OPENROUTER_IMAGE_COST_CREDITS,
    )
    await state.set_state(ImageGenState.waiting_prompt)
    await message.answer(
        "<b>🎨 Картинка по описанию</b>\n"
        "<blockquote><i>Напиши одним сообщением, что должно быть на картинке.</i></blockquote>",
        reply_markup=_waiting_prompt_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_IMG_CANCEL)
async def cancel_image_flow(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        "<blockquote><i>Ок, отменили генерацию. Выбери действие в меню ниже.</i></blockquote>",
        reply_markup=start_menu_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_BACK_IMAGE_MODELS)
async def back_to_image_flow(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    await _start_image_flow(callback.message, state, callback.from_user.id, callback.from_user.username)


async def _send_ready_ideas_screen(message: Message, state: FSMContext, user_id: int, username: str | None) -> None:
    await state.clear()
    if not is_openrouter_image_configured():
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    await ensure_user(user_id, username)
    if READY_IDEAS:
        sub = (
            "<blockquote><i>Нажми вариант — сразу запустится генерация по готовому тексту.</i></blockquote>"
        )
    else:
        sub = (
            "<blockquote><i>Пока нет заготовок — список добавит администратор. "
            "Можно описать картинку вручную: «Создать картинку».</i></blockquote>"
        )
    await message.answer(
        f"<b>💡 Готовые идеи</b>\n{sub}",
        reply_markup=ready_ideas_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_READY_IDEAS)
async def open_ready_ideas(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    await _send_ready_ideas_screen(
        callback.message, state, callback.from_user.id, callback.from_user.username
    )


@router.message(Command("ideas"))
async def cmd_ready_ideas(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await _send_ready_ideas_screen(message, state, message.from_user.id, message.from_user.username)


@router.callback_query(F.data.startswith(CB_APPLY_READY_PREFIX))
async def apply_ready_idea(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if not READY_IDEAS:
        await callback.answer("Список готовых идей пока пуст.", show_alert=True)
        return
    if not is_openrouter_image_configured():
        await callback.answer()
        await callback.message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    try:
        idx = int(callback.data.replace(CB_APPLY_READY_PREFIX, ""))
        _, prompt = READY_IDEAS[idx]
    except (ValueError, IndexError):
        await callback.answer("Некорректный вариант.", show_alert=True)
        return
    prompt = (prompt or "").strip()
    if not prompt:
        await callback.answer("Пустой промпт в этой идее.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id
    await ensure_user(user_id, callback.from_user.username)
    await _execute_text_generation(
        callback.message,
        None,
        user_id=user_id,
        username=callback.from_user.username,
        prompt=prompt,
        model=OPENROUTER_IMAGE_MODEL,
        cost=OPENROUTER_IMAGE_COST_CREDITS,
        usage_kind="self",
        use_image_cache=True,
    )


@router.callback_query(F.data == CB_CREATE_IMAGE)
async def open_image_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    await _start_image_flow(callback.message, state, callback.from_user.id, callback.from_user.username)


@router.message(ImageGenState.waiting_prompt, ~F.text)
async def wrong_type_waiting_prompt(message: Message) -> None:
    await message.answer(
        "Нужен текстовый промпт: напиши описание картинки одним сообщением.",
        reply_markup=_waiting_prompt_keyboard(),
    )


@router.message(ImageGenState.waiting_prompt)
async def create_image_from_prompt(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer(
            "Нужен текстовый промпт. Попробуй еще раз.",
            reply_markup=_waiting_prompt_keyboard(),
        )
        return
    if prompt.startswith("/"):
        return

    user_id = message.from_user.id
    data = await state.get_data()
    model = str(data.get("selected_model") or OPENROUTER_IMAGE_MODEL)
    cost = int(data.get("selected_cost") or OPENROUTER_IMAGE_COST_CREDITS)
    await _execute_text_generation(
        message,
        state,
        user_id=user_id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        cost=cost,
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
    if ctx.kind != "text":
        await callback.answer("Этот режим больше не поддерживается. Создай картинку текстом.", show_alert=True)
        return
    await callback.answer()
    await _execute_text_generation(
        callback.message,
        None,
        user_id=user_id,
        username=callback.from_user.username,
        prompt=ctx.prompt,
        model=ctx.model,
        cost=ctx.cost,
        usage_kind="self",
        use_image_cache=False,
    )

