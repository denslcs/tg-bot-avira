from __future__ import annotations

from aiogram import F, Router
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
)
from src.database import (
    add_credits,
    ensure_user,
    get_credits,
    get_last_image_context,
    get_monthly_image_generation_usage,
    get_user_admin_profile,
    release_monthly_image_generation,
    save_last_image_context,
    subscription_is_active,
    take_credits,
    try_reserve_monthly_image_generation,
)
from src.formatting import HTML, esc
from src.gemini_image import edit_image_png, generate_image_png, is_gemini_configured

router = Router(name="img_commands")

# Отображаемые имена моделей (в кнопках и подписи к результату)
MODEL_NANO_DISPLAY = "🍌 Nano Banana"
MODEL_NANO2_DISPLAY = "🍌🍌 Nano Banana 2"

_GEMINI_MISSING_TEXT = (
    "<b>Генерация картинок выключена</b> — нет ключа <code>GEMINI_API_KEY</code>.\n\n"
    "<blockquote><i>Создай ключ в Google AI Studio и добавь в <code>.env</code> на сервере:</i>\n"
    "<code>GEMINI_API_KEY=твой_ключ</code></blockquote>\n"
    "Перезапусти бота (<code>systemctl restart</code>). Шаблон — в <code>.env.example</code>."
)

CB_CREATE_IMAGE = "menu:create_image"
CB_MENU_BACK_START = "menu:back_start"
CB_BACK_IMAGE_MODELS = "img:back_models"
CB_GEN_TEXT = "img:mode:text"
CB_GEN_EDIT = "img:mode:edit"
CB_PICK_NANO = "img:pick_nano"
CB_PICK_NANO_2 = "img:pick_nano2"
CB_READY_IDEAS = "menu:ready_ideas"
CB_APPLY_READY_PREFIX = "img:idea:"
CB_REGEN = "img:regen"

_BACK_MAIN = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]
_BACK_MODELS = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_IMAGE_MODELS)]


def _gemini_missing_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_BACK_MAIN])


class ImageGenState(StatesGroup):
    waiting_mode = State()
    waiting_prompt = State()
    waiting_photo_for_edit = State()
    waiting_photo_for_idea = State()


def image_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🍌 Nano Banana — {GEMINI_NANO_COST_CREDITS} кредит",
                    callback_data=CB_PICK_NANO,
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🍌🍌 Nano Banana 2 — {GEMINI_IMAGE_COST_CREDITS} кредита",
                    callback_data=CB_PICK_NANO_2,
                )
            ],
            _BACK_MAIN,
        ]
    )


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Сгенерировать картинку текстом", callback_data=CB_GEN_TEXT)],
            [InlineKeyboardButton(text="🖼 Изменить картинку (фото + текст)", callback_data=CB_GEN_EDIT)],
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


async def _prepare_image_charge_and_monthly_slot(
    message: Message,
    *,
    user_id: int,
    is_admin: bool,
    charge: bool,
    cost: int,
) -> bool:
    if not is_admin:
        used0, limit0 = await get_monthly_image_generation_usage(user_id)
        if used0 >= limit0:
            await message.answer(
                "<b>Лимит месяца</b>\n"
                f"<blockquote><i>Генераций картинок:</i> <b>{esc(used0)}/{esc(limit0)}</b> (UTC). "
                "Оформи подписку в <code>/start</code> или дождись нового месяца.</blockquote>",
                parse_mode=HTML,
            )
            return False
    if charge:
        ok = await take_credits(user_id, cost)
        if not ok:
            balance = await get_credits(user_id)
            await message.answer(
                f"<blockquote><i>Недостаточно кредитов.</i> Нужно <b>{esc(cost)}</b>, у тебя <b>{esc(balance)}</b>.</blockquote>",
                parse_mode=HTML,
            )
            return False
    if not is_admin:
        if not await try_reserve_monthly_image_generation(user_id):
            used, limit = await get_monthly_image_generation_usage(user_id)
            if charge:
                await add_credits(user_id, cost)
            await message.answer(
                "<b>Лимит занят</b>\n"
                f"<blockquote><i>Параллельный запрос.</i> Сейчас: <b>{esc(used)}/{esc(limit)}</b>. "
                "Попробуй позже или оформи подписку.</blockquote>",
                parse_mode=HTML,
            )
            return False
    return True


def _regen_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Ещё раз", callback_data=CB_REGEN)],
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
    profile,
) -> None:
    await save_last_image_context(
        user_id, kind, prompt, model, cost, model_name, photo_file_id
    )
    used_m, limit_m = await get_monthly_image_generation_usage(user_id)
    month_note = "" if is_admin else f"\n<blockquote><i>Месяц (UTC):</i> {esc(used_m)}/{esc(limit_m)} генераций.</blockquote>"
    mn = esc(model_name)
    if is_admin:
        caption = f"<b>Готово ✅</b>\n<b>ИИ:</b> {mn}\n<i>Режим админа — кредиты не списывались.</i>"
    elif not charge and profile is not None:
        balance = await get_credits(user_id)
        caption = (
            f"<b>Готово ✅</b>\n<b>ИИ:</b> {mn}\n"
            f"<blockquote><i>💰 Баланс:</i> <b>{esc(balance)}</b></blockquote>\n"
            "<i>Кредиты за картинку не списывались (активна подписка).</i>"
            f"{month_note}"
        )
    else:
        balance = await get_credits(user_id)
        cw = _credits_word(cost)
        caption = (
            f"<b>Готово ✅</b>\n<b>ИИ:</b> {mn}\n"
            f"Списано: <b>{esc(cost)}</b> {cw}.\n"
            f"<blockquote><i>💰 Баланс:</i> <b>{esc(balance)}</b></blockquote>{month_note}"
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
) -> None:
    await ensure_user(user_id, username)
    if not is_gemini_configured():
        await message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    is_admin = user_id in ADMIN_IDS
    profile = await get_user_admin_profile(user_id) if not is_admin else None
    charge = not is_admin and (
        profile is None or not subscription_is_active(profile.subscription_ends_at)
    )
    if not await _prepare_image_charge_and_monthly_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost
    ):
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        image_bytes = await generate_image_png(prompt, model=model)
    except Exception as exc:
        if not is_admin:
            await release_monthly_image_generation(user_id)
        if charge:
            await add_credits(user_id, cost)
        await wait_msg.edit_text(f"Ошибка генерации: {exc}")
        return
    await wait_msg.delete()
    await _send_result_photo_with_regen(
        message,
        state,
        user_id=user_id,
        image_bytes=image_bytes,
        filename="image.png",
        kind="text",
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        photo_file_id=None,
        is_admin=is_admin,
        charge=charge,
        profile=profile,
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
) -> None:
    await ensure_user(user_id, username)
    if not is_gemini_configured():
        await message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    is_admin = user_id in ADMIN_IDS
    profile = await get_user_admin_profile(user_id) if not is_admin else None
    charge = not is_admin and (
        profile is None or not subscription_is_active(profile.subscription_ends_at)
    )
    if not await _prepare_image_charge_and_monthly_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost
    ):
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        image_bytes_src = await message.bot.download(source_file_id)
        source_bytes = image_bytes_src.read()
        image_bytes = await edit_image_png(source_bytes, prompt, model=model)
    except Exception as exc:
        if not is_admin:
            await release_monthly_image_generation(user_id)
        if charge:
            await add_credits(user_id, cost)
        await wait_msg.edit_text(f"Ошибка генерации: {exc}")
        return
    await wait_msg.delete()
    await _send_result_photo_with_regen(
        message,
        state,
        user_id=user_id,
        image_bytes=image_bytes,
        filename="edited.png",
        kind="edit",
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
        photo_file_id=source_file_id,
        is_admin=is_admin,
        charge=charge,
        profile=profile,
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
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CB_APPLY_READY_PREFIX}{idx}")])
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
    if not is_gemini_configured():
        await callback.answer()
        await callback.message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.clear()
    await callback.message.answer(
        "<b>Выбери модель ИИ</b>\n<blockquote><i>🍌 — линейка Nano Banana.</i></blockquote>",
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
    if not is_gemini_configured():
        await callback.answer()
        await callback.message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.clear()
    await callback.message.answer(
        "<b>Выбери модель ИИ</b>\n<blockquote><i>🍌 — линейка Nano Banana.</i></blockquote>",
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
        await callback.message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=GEMINI_NANO_MODEL,
        selected_name=MODEL_NANO_DISPLAY,
        selected_cost=GEMINI_NANO_COST_CREDITS,
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
        await callback.message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
        return
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await state.update_data(
        selected_model=GEMINI_IMAGE_MODEL,
        selected_name=MODEL_NANO2_DISPLAY,
        selected_cost=GEMINI_IMAGE_COST_CREDITS,
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
    await state.set_state(ImageGenState.waiting_photo_for_edit)
    await callback.message.answer(
        "<blockquote><i>Одно сообщение:</i> фото + описание в <b>подписи</b> к фото.</blockquote>",
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


@router.callback_query(F.data.startswith(CB_APPLY_READY_PREFIX))
async def apply_ready_idea(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    if not is_gemini_configured():
        await callback.answer()
        await callback.message.answer(_GEMINI_MISSING_TEXT, reply_markup=_gemini_missing_kb(), parse_mode=HTML)
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
        selected_cost=GEMINI_IMAGE_COST_CREDITS,
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
async def wrong_type_waiting_photo_edit(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "Нужно фото с подписью в одном сообщении. "
        "Отправь сжатое фото (не файлом) и опиши правку в подписи к фото."
    )


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
    await _execute_text_generation(
        message,
        state,
        user_id=user_id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        model_name=model_name,
        cost=cost,
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
        await _execute_text_generation(
            callback.message,
            None,
            user_id=user_id,
            username=callback.from_user.username,
            prompt=ctx.prompt,
            model=ctx.model,
            model_name=ctx.model_name,
            cost=ctx.cost,
        )
    else:
        if not ctx.photo_file_id:
            await callback.message.answer(
                "Исходное фото недоступно (истекло у Telegram или сессия пустая). "
                "Отправь фото с подписью снова через «Создать картинку»."
            )
            return
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
        )


@router.message(ImageGenState.waiting_photo_for_edit, F.photo)
async def create_image_edit_from_photo(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.photo:
        return
    prompt = (message.caption or "").strip()
    if not prompt:
        await message.answer("Добавьте описание в подпись к фото и отправьте снова.")
        return
    await _generate_from_photo_with_prompt(message, state, prompt)


@router.message(ImageGenState.waiting_photo_for_idea, F.photo)
async def create_image_from_ready_prompt(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.photo:
        return
    data = await state.get_data()
    prompt = str(data.get("ready_prompt") or "").strip()
    if not prompt:
        await message.answer("Промпт не найден. Нажмите 'Готовые идеи' снова.")
        await state.clear()
        return
    await _generate_from_photo_with_prompt(message, state, prompt)


async def _generate_from_photo_with_prompt(message: Message, state: FSMContext, prompt: str) -> None:
    if not message.from_user or not message.photo:
        return
    user_id = message.from_user.id
    source_file_id = message.photo[-1].file_id
    data = await state.get_data()
    model = str(data.get("selected_model") or GEMINI_IMAGE_MODEL)
    model_name = str(data.get("selected_name") or MODEL_NANO2_DISPLAY)
    cost = int(data.get("selected_cost") or GEMINI_IMAGE_COST_CREDITS)
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
    )

