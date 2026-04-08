from __future__ import annotations

"""
Генерация изображений по тексту: OpenRouter (FLUX, Gemini) и Polza.ai (GPT Image).
"""

import logging
from io import BytesIO
from pathlib import Path

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
    OPENROUTER_IMAGE_ALT_COST_CREDITS,
    OPENROUTER_IMAGE_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_MODEL,
    OPENROUTER_IMAGE_GEMINI_PRO_MODEL,
    OPENROUTER_IMAGE_GEMINI_PREVIEW_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL,
    OPENROUTER_IMAGE_MODEL,
    OPENROUTER_IMAGE_MODEL_ALT,
    OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS,
    POLZA_IMAGE_GPT5_IMAGE_COST_CREDITS,
    POLZA_IMAGE_GPT_IMAGE_15_COST_CREDITS,
    POLZA_IMAGE_MODEL_GPT5_IMAGE,
    POLZA_IMAGE_MODEL_GPT_IMAGE_15,
)
from src.database import (
    ImageChargeMeta,
    add_credits,
    add_idea_tokens,
    ensure_user,
    get_credits,
    get_daily_image_generation_usage,
    get_last_image_context,
    get_nonsub_image_quota_status,
    get_user_admin_profile,
    release_daily_image_generation,
    release_nonsub_image_quota_slot,
    release_nonsub_ready_idea_slot,
    save_last_image_context,
    subscription_is_active,
    take_credits,
    try_consume_idea_token,
    try_reserve_daily_image_generation,
    try_reserve_nonsub_image_quota_slot,
    try_reserve_nonsub_ready_idea_slot,
)
from src.formatting import HTML, esc
from src.handlers.commands import edit_or_send_nav_message, restore_main_menu_message
from src.keyboards.callback_data import (
    CB_BACK_IMAGE_MODELS,
    CB_CREATE_IMAGE,
    CB_IMG_CANCEL,
    CB_IMG_MODEL_SEL_PREFIX,
    CB_IMG_OK,
    CB_MENU_BACK_START,
    CB_READY_CAT_PREFIX,
    CB_READY_CONFIRM,
    CB_READY_IDEAS,
    CB_READY_NAV_PREFIX,
    CB_READY_PHOTO_BACK,
    CB_REGEN,
)
from src.keyboards.styles import BTN_DANGER, BTN_PRIMARY, BTN_SUCCESS
from src.openrouter_image import (
    OpenRouterApiError,
    format_openrouter_image_user_error,
    is_openrouter_image_configured,
    openrouter_text_and_refs_to_image_bytes,
    openrouter_text_to_image_bytes,
)
from src.polza_image import (
    PolzaApiError,
    format_polza_image_user_error,
    is_polza_configured,
    is_polza_image_model,
    polza_text_to_image_bytes,
)
from src.subscription_catalog import (
    NONSUB_IMAGE_WINDOW_DAYS,
    NONSUB_IMAGE_WINDOW_MAX,
)

router = Router(name="img_commands")

READY_IDEA_CATEGORIES: list[tuple[str, str]] = [
    ("trends", "🔥 Тренды"),
    ("outfits", "👕 Одежда"),
    ("locations", "🏝 Локации"),
    ("celebrities", "🌟 Знаменитости"),
    ("for_two", "💞 Для двоих"),
    ("texts", "📝 Тексты"),
    ("movies", "🎬 Фильмы"),
    ("games", "🎮 Игры"),
    ("colors", "🎨 Цвета"),
    ("add_photo", "📥 Добавить фото"),
]

# title, preview, prompt, photos_required
READY_IDEA_ITEMS: dict[str, list[tuple[str, str, str, int]]] = {
    "trends": [
        (
            "Неон-стрит",
            "Городской вечер, мокрый асфальт, яркий неон и киношный свет.",
            "Create a cinematic urban portrait with wet asphalt reflections, neon signs, and dynamic bokeh. Keep subject identity natural and realistic.",
            1,
        ),
        (
            "Luxury minimal",
            "Чистый luxury-кадр с мягким контрастом и дорогой фактурой.",
            "Create a premium fashion portrait in luxury minimal style: clean composition, soft high-end lighting, natural skin texture, elegant color grading.",
            1,
        ),
    ],
    "outfits": [
        (
            "Smart casual look",
            "Собрать аккуратный smart casual образ и сохранить естественность лица.",
            "Generate a smart casual fashion portrait. Keep facial identity, realistic body proportions, and clean editorial framing.",
            1,
        ),
    ],
    "locations": [
        (
            "Европейская улица",
            "Перенос в атмосферную европейскую локацию с естественным освещением.",
            "Place the subject in a picturesque European street scene at golden hour with realistic shadows and cohesive perspective.",
            1,
        ),
        (
            "На отдыхе в Италии",
            "Кинематографичный кадр на белой яхте у побережья Амальфи, мягкий закатный свет.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the only source of facial identity. Keep face and hair unchanged and realistic: same facial structure, skin texture, age, and expression. No face swap artifacts, no beautification, no plastic skin. Create a cinematic medium-wide portrait (not close-up): a young man sits relaxed on a white luxury yacht near the Italian Amalfi coastline at sunset. Outfit: elegant unbuttoned blue shirt and white shorts. Scene details: stainless steel railing, cream seat cushion, refined leather details, visible coastal city in background. Lighting: warm natural sunset with strong realistic reflections and contrast, detailed shadows, and a natural aesthetic afternoon tone. Final image should feel realistic, clean, high-detail, and polished.",
            1,
        ),
        (
            "Бекрумс VHS",
            "VHS-кадр в Backrooms: пользователь в движении, эффект записи и широкий угол.",
            "CRITICAL IDENTITY LOCK: keep the user face realistic and recognizable. Create a found-footage VHS style frame in Backrooms atmosphere: long yellow empty corridors, fluorescent ceiling lights, liminal uncanny mood, low-fi analog noise and tape artifacts. The user looks directly into the camera and is caught in dynamic playful movement (slight goofy pose / expressive motion), not static. Wardrobe rule: if input shows only head/portrait, dress the user in a yellow utility jumpsuit; if input is full-body, keep the user's original outfit. Camera rule: slight fisheye look (about 120-degree field of view), camera tilted and positioned a little lower than the user angle. Add on-screen VHS timestamp/date overlay in the lower-left corner (recording-like style). Keep the result photorealistic while preserving authentic VHS degradation.",
            1,
        ),
    ],
    "celebrities": [
        (
            "Переговоры с Путиным",
            "Пользователь сидит в кабинете Путина на официальных переговорах.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the user's face 100% unchanged and realistic: same facial structure, skin texture, age, and expression. Create a photorealistic formal negotiation scene inside Vladimir Putin's office: Vladimir Putin and the user are seated at a negotiation table facing each other in a calm diplomatic meeting setup. Wardrobe requirement: the user must wear a formal official business suit (classic dark suit, white shirt, tie). Preserve natural human proportions, realistic skin texture, authentic office lighting, detailed interior, clean composition, and professional documentary photo style.",
            1,
        ),
        (
            "Победа над Мухаммадом Али на ринге",
            "Реалистичный кадр боксерского боя: пользователь победитель, Мухаммад Али проигравший.",
            "Create a highly photorealistic boxing match result scene inspired by a real sports photo. IMPORTANT REFERENCE MAPPING: image #1 is user identity reference, image #2 is Muhammad Ali identity reference. CRITICAL IDENTITY LOCK FOR BOTH: preserve Muhammad Ali and user faces from their references with high fidelity (same facial structure, eyes, nose, lips, skin texture, and age). Do not replace Muhammad Ali with another person and do not distort either face. Keep both faces clearly visible and recognizable. Final moment: the user is the winner and Muhammad Ali is the loser. Composition should look like an authentic post-fight ring photo with a referee between fighters raising the user's hand. Arena environment must feel premium and massive: an enormous sold-out stadium packed with thousands of cheering spectators, mostly dark surroundings, and powerful cinematic spotlights/floodlights cutting through the darkness and focusing on the ring like a world-title mega event. Add realistic light beams, subtle haze, dramatic contrast, and elite pay-per-view broadcast atmosphere. No country flags, no national symbols, no flag patches on outfits. Keep natural body proportions, realistic gloves and uniforms, documentary sports photography style, and clean high-detail realism.",
            1,
        ),
    ],
    "for_two": [
        (
            "Для влюбленных: рыцарь и дама",
            "Романтическая сцена на закате: рыцарь (фото 1) и женщина (фото 2).",
            "IMPORTANT REFERENCE MAPPING: image #1 is the knight identity (male), image #2 is the woman identity (female). CRITICAL IDENTITY LOCK FOR BOTH: preserve both faces with high fidelity (facial structure, eyes, nose, lips, skin texture, age) and keep them clearly recognizable. Create a romantic portrait scene with a horse, a medieval knight, and sunset atmosphere. The knight must be based on image #1, without helmet, with visible face. The woman must be based on image #2, in a flowing dress and veil, with visible face. Knight armor should be highly detailed, richly decorated, realistic polished metal. Cinematic fantasy mood, warm golden lighting, volumetric fog, pastel haze. Photorealistic, highly detailed, sharp focus, realistic skin texture, shot on ARRI Alexa, 85mm lens, high resolution.",
            2,
        ),
    ],
    "texts": [
        (
            "Постер с текстом",
            "Вертикальный постер: главный герой + крупный читаемый заголовок.",
            "Create a vertical poster with the subject as the hero. Add readable headline text 'YOUR TEXT HERE' in modern typography and keep composition balanced.",
            1,
        ),
    ],
    "movies": [
        (
            "Кто ты из Вестероса",
            "Кинематографичный 3D-образ в стиле Game of Thrones: выбери дом Вестероса под внешность пользователя.",
            "Use the uploaded character from the image as the identity reference and place this person into the final scene. Create an ultra-realistic 3D close-up render of the character standing front-facing. Shoot from a low camera angle so the character dominates the frame. Background should be blurred and misty, with cinematic bokeh, light bloom, and soft shadows. Visual quality requirements: exceptional detail, fine skin texture, clearly defined hair roots, strong cinematic lighting, full 3D depth feeling, premium CG texture quality as if made by top-tier 3D artists. Aspect ratio 3:4, high resolution. Do NOT alter face features or hair — keep them 100% unchanged. RANDOM HOUSE RULE: randomly choose exactly one house from this list only: Stark, Lannister, Targaryen, Baratheon. Then style outfit, heraldic details, and color palette strictly according to the selected house.",
            1,
        ),
        (
            "Персонаж из Аватара",
            "Пользователь как герой мира Avatar: кинематографичный кадр в стиле фильма.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the only identity reference. Keep face structure, age, skin texture, and hairstyle recognizable and realistic. Transform the user into a highly detailed, photorealistic Avatar-universe character (Na'vi aesthetics, blue skin, cinematic tribal costume design, premium textures). GENDER ADAPTATION RULE: infer presentation from the user photo and choose matching character styling automatically. If the user appears male, use a Jake-inspired warrior costume and masculine silhouette. If the user appears female, use a Neytiri-inspired warrior costume and feminine silhouette. Keep the final result respectful, realistic, and coherent. Camera and mood: slightly low upward-facing angle, dramatic cinematic lighting, high contrast, deep saturated blue background, warm highlights on one side of the face and soft velvety shadows on the other. No props, no extra accessories. Emphasize detailed costume materials, realistic skin texture, controlled color grading, and an editorial close portrait feeling.",
            1,
        ),
    ],
    "games": [
        (
            "Фотка в эндер мире",
            "Последняя фотка перед битвой с драконом в Minecraft (высокое качество).",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the face 100% unchanged and realistic: same facial structure, eyes, nose, lips, skin texture, age, and expression. No face swap artifacts, no beautification, no cartoonization, no pixelated face, no extra facial hair. PROPORTION RULE: Keep natural human head-to-body proportions. Head must not look oversized; keep it slightly stylized but close to realistic proportions, with shoulders/torso visibly dominant in volume. Create a high-quality Minecraft End dimension scene: the user is sitting on top of an obsidian block at the edge of a cliff, looking directly at the camera. Camera angle: top-down, slightly tilted perspective from above. Outfit requirement: the user must wear Minecraft-inspired diamond armor on torso and legs (diamond chestplate + diamond leggings), integrated naturally with the scene. Add the user's Telegram nickname above the head in Minecraft-style yellow text with a dark outline. In the background, an Ender Dragon is flying in the sky. Keep the End-world atmosphere (obsidian, void-like depth, dramatic ambient light), with cinematic composition, sharp details, clean textures, and natural lighting integration on the user. Apply End-themed lighting on the user as well: purple-black ambient glow and subtle violet shadows on skin, armor, and clothing, so the user color grading matches the End environment naturally. Final output must look coherent, polished, and artifact-free.",
            1,
        ),
        (
            "Clash Royale элитные варвары",
            "Выпала возможность прочувствовать себя в в шкуре элитного варвара.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the face 100% unchanged and realistic: same facial structure, eyes, nose, lips, skin texture, age, and expression. No face swap artifacts, no beautification, no cartoon face, no plastic skin, no added beard or mustache. REFERENCE COMPOSITION RULE: Use the provided Clash Royale reference image as layout/composition anchor. Replace ONLY the FRONT (right-side, closest to camera) elite barbarian with the user. Keep the back barbarian as the second character in the scene. POSE RULE: user and the second barbarian should stand close in a friendly side-by-side hug pose, with each character placing one arm over the other's shoulders (mutual arm-over-shoulder). Keep full-body framing of both characters, same arena perspective from the reference, and same armor style (golden horned helmet, wristbands, barbarian belt/skirt, barefoot). Arena details: red carpet, bridge/towers, battle atmosphere, warm cinematic lighting, slight depth of field, clean textures, high detail, natural seamless face integration.",
            1,
        ),
    ],
    "colors": [
        (
            "Purple pulse",
            "Фиолетово-розовая палитра, мягкий glow и чистая кожа.",
            "Generate a portrait with purple-magenta color palette, soft glow effects, and natural skin details.",
            1,
        ),
    ],
    "add_photo": [
        (
            "Коллаж из двух фото",
            "Объединить 2 фото: лицо с первого, стиль/ракурс со второго.",
            "Create one final portrait. Keep face identity from photo #1. Use style, color, and atmosphere inspired by photo #2. Result should be coherent and photorealistic.",
            2,
        ),
    ],
}

_READY_IDEA_STATIC_REF_BY_TITLE: dict[str, str] = {
    "Победа над Мухаммадом Али на ринге": r"C:\Users\puma1\.cursor\projects\c-Users-puma1-Tg-bot-AVIRA\assets\c__Users_puma1_AppData_Roaming_Cursor_User_workspaceStorage_30e373e7c0bd4c0e8bda9500b3b60435_images_114b8c4714b8b9b1196d51ad8d72a-1b94cd0d-73ba-44de-b3da-08a08fade423.png",
}

# Подпись для внутреннего контекста «Ещё раз» (пользователю не показываем).
_IMAGE_CONTEXT_LABEL = "text2img"

# Подсказки для пользователя: в каких стилях модель обычно сильна (без сравнения с другими).
_STYLE_HINT_KLEIN = (
    "Удачно смотрится в простом реализме, пейзажах, натюрмортах и спокойных сценах с одной главной идеей."
)
_STYLE_HINT_PRO = (
    "Хорошо ложится на фотореализм, портреты, людей в среде, интерьеры, архитектуру "
    "и предметные снимки «как с камеры»."
)
_STYLE_HINT_GEMINI = (
    "Сильна в иллюстрациях, ярких образах, сказочных и фантазийных сценах, обложках "
    "и картинках «к краткой истории»."
)
_STYLE_HINT_GEMINI_PREVIEW = (
    "Хорошо подходит, когда нужен выразительный, необычный кадр и смелая визуальная подача одной и той же задумки."
)
_STYLE_HINT_GPT_IMG_15 = (
    "Уместна для схем, слайдов, простых макетов и картинок, где важно много условий в тексте и понятная композиция."
)
_STYLE_HINT_GPT5_IMG = (
    "Хороша для насыщенных сцен по длинному описанию: богатый по деталям и внимательный к тексту запроса результат."
)

_WAITING_PROMPT_HTML = (
    "<b>🎨 Картинка по описанию</b>\n"
    "<blockquote><i>Напиши одним сообщением, что должно быть на картинке.</i></blockquote>"
)

_IMAGE_GEN_MISSING_TEXT = (
    "<b>Генерация картинок выключена.</b>\n\n"
    "<blockquote>Администратору: задай <code>OPENROUTER_API_KEY</code> и при необходимости "
    "<code>OPENROUTER_IMAGE_MODEL</code> в <code>.env</code> (см. .env.example).</blockquote>"
)

_POLZA_MISSING_TEXT = (
    "<b>Модель GPT Image (Polza.ai) недоступна.</b>\n\n"
    "<blockquote>Администратору: задай <code>POLZAAI_API_KEY</code> в <code>.env</code> "
    "(см. .env.example).</blockquote>"
)

# После успешной генерации не удаляем служебное сообщение — только обновляем текст.
_GEN_STATUS_DONE_TEXT = (
    "<i>✅ Генерация завершена.</i> Результат — в следующем сообщении."
)

_BACK_MAIN = [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]


async def _finalize_generation_status_message(wait_msg: Message) -> None:
    try:
        await wait_msg.edit_text(
            _GEN_STATUS_DONE_TEXT,
            parse_mode=HTML,
            reply_markup=None,
        )
    except Exception:
        logging.debug("finalize generation status message failed", exc_info=True)


def _waiting_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
    choosing_model = State()
    waiting_prompt = State()
    ready_choosing_category = State()
    ready_browsing_idea = State()
    ready_waiting_photos = State()
    ready_waiting_confirm = State()


def _dedupe_model_choices(items: list[tuple[str, str, int, str]]) -> list[tuple[str, str, int, str]]:
    """Один id модели — одна кнопка (если в .env Klein и Pro совпали)."""
    seen: set[str] = set()
    out: list[tuple[str, str, int, str]] = []
    for label, mid, cost, hint in items:
        key = (mid or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((label, mid, cost, hint))
    return out


def _model_pick_caption_html(*, for_admin: bool, choices: list[tuple[str, str, int, str]]) -> str:
    intro = (
        "Режим администратора — доступны все модели. Ниже кратко, в каких стилях каждая из них обычно сильна."
        if for_admin
        else "Тариф в профиле определяет список моделей. Ниже — подсказки по стилю. Выбери вариант и опиши картинку."
    )
    lines = [
        "<b>Выбор модели ИИ</b>",
        f"<blockquote><i>{esc(intro)}</i></blockquote>",
    ]
    for label, _mid, cost, hint in choices:
        lines.append(f"<b>{esc(label)}</b> · {esc(cost)} кр.")
        lines.append(f"<blockquote><i>{esc(hint)}</i></blockquote>")
    return "\n".join(lines)


def _model_choices_for_subscription_plan(plan_id: str) -> list[tuple[str, str, int, str]]:
    """
    Подпись кнопки, id модели (OpenRouter или Polza), стоимость в кредитах, подсказка по стилю.
    Nova: только Klein 4B.
    SuperNova: Klein 4B + Nano Banana.
    Galaxy: Klein 4B + Nano Banana + GPT Image 1.5 (Polza).
    Universe / Starter: полный набор (как Universe). Starter — пробный 3 дня, одна покупка.
    Неизвестный plan_id: как Universe.
    Без подписки панель не используется.
    """
    klein_id = (OPENROUTER_IMAGE_MODEL or "").strip() or "black-forest-labs/flux.2-klein-4b"
    pro_id = (OPENROUTER_IMAGE_MODEL_ALT or "").strip() or "black-forest-labs/flux.2-pro"
    gemini_id = (OPENROUTER_IMAGE_GEMINI_MODEL or "").strip() or "google/gemini-2.5-flash-image"
    preview_id = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or "google/gemini-3.1-flash-image-preview"

    klein = ("⚡ FLUX Klein 4B", klein_id, OPENROUTER_IMAGE_COST_CREDITS, _STYLE_HINT_KLEIN)
    pro = ("🎨 FLUX Pro", pro_id, OPENROUTER_IMAGE_ALT_COST_CREDITS, _STYLE_HINT_PRO)
    gemini = ("🍌 Nano Banana", gemini_id, OPENROUTER_IMAGE_GEMINI_COST_CREDITS, _STYLE_HINT_GEMINI)
    gemini_preview = (
        "🍌 Nano Banana 2",
        preview_id,
        OPENROUTER_IMAGE_GEMINI_PREVIEW_COST_CREDITS,
        _STYLE_HINT_GEMINI_PREVIEW,
    )
    gpt_img_15 = (
        "🖼 GPT Image 1.5",
        POLZA_IMAGE_MODEL_GPT_IMAGE_15,
        POLZA_IMAGE_GPT_IMAGE_15_COST_CREDITS,
        _STYLE_HINT_GPT_IMG_15,
    )
    gpt5_img = (
        "🖼 GPT‑5 Image",
        POLZA_IMAGE_MODEL_GPT5_IMAGE,
        POLZA_IMAGE_GPT5_IMAGE_COST_CREDITS,
        _STYLE_HINT_GPT5_IMG,
    )

    p = (plan_id or "").strip().lower()
    if p == "nova":
        return _dedupe_model_choices([klein])
    if p == "supernova":
        return _dedupe_model_choices([klein, gemini])
    if p == "galaxy":
        return _dedupe_model_choices([klein, gemini, gpt_img_15])
    # starter, universe и неизвестный plan — полная матрица
    return _dedupe_model_choices([klein, gemini, gpt_img_15, pro, gemini_preview, gpt5_img])


async def _effective_image_model_and_cost(user_id: int, requested_model: str) -> tuple[str, int]:
    """Модель и цена согласно текущей подписке (без подписки — только Klein)."""
    profile = await get_user_admin_profile(user_id)
    has_sub = bool(profile and subscription_is_active(profile.subscription_ends_at))
    if not has_sub:
        return OPENROUTER_IMAGE_MODEL.strip(), OPENROUTER_IMAGE_COST_CREDITS
    plan_id = (profile.subscription_plan or "").strip().lower() if profile else ""
    choices = _model_choices_for_subscription_plan(plan_id)
    want = (requested_model or "").strip()
    for _lb, mid, cst, _hint in choices:
        if mid.strip() == want:
            return mid.strip(), cst
    if choices:
        mid0, cst0 = choices[0][1], choices[0][2]
        return mid0.strip(), cst0
    return OPENROUTER_IMAGE_MODEL.strip(), OPENROUTER_IMAGE_COST_CREDITS


def _subscriber_model_pick_keyboard(choices: list[tuple[str, str, int, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i, (label, _mid, cost, _hint) in enumerate(choices):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{label} · {cost} кр."[:64],
                    callback_data=f"{CB_IMG_MODEL_SEL_PREFIX}{i}",
                    style=BTN_PRIMARY,
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    is_ready = usage_kind == "ready"

    if is_ready and not has_active_sub:
        if await try_reserve_nonsub_ready_idea_slot(user_id):
            meta.nonsub_ready_reserved = True
        elif await try_consume_idea_token(user_id):
            meta.idea_token_consumed = True
        else:
            await message.answer(
                "<b>Готовые идеи без подписки</b>\n"
                f"<blockquote>Бесплатный слот в цикле уже использован — следующий будет через <b>{NONSUB_IMAGE_WINDOW_DAYS}</b> суток "
                "от момента исчерпания (то же время суток по UTC). Оформи подписку в <code>/start</code> → <b>Оплатить</b> "
                "или получи дополнительный запуск: <b>+1 за каждых 2 приглашённых друзей</b> по <code>/ref</code>.</blockquote>",
                parse_mode=HTML,
            )
            return False, None
        if charge:
            ok = await take_credits(user_id, cost)
            if not ok:
                if meta.nonsub_ready_reserved:
                    await release_nonsub_ready_idea_slot(user_id)
                elif meta.idea_token_consumed:
                    await add_idea_tokens(user_id, 1)
                balance = await get_credits(user_id)
                await message.answer(
                    f"<blockquote><i>Недостаточно кредитов.</i> Нужно <b>{esc(cost)}</b>, у тебя <b>{esc(balance)}</b>."
                    "</blockquote>",
                    parse_mode=HTML,
                )
                return False, None
            meta.credit_charged = True
        return True, meta

    if is_ready and has_active_sub:
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
        return True, meta

    if not has_active_sub:
        if not await try_reserve_nonsub_image_quota_slot(user_id):
            await message.answer(
                "<b>Лимит без подписки</b>\n"
                f"<blockquote>В цикле доступно не более <b>{NONSUB_IMAGE_WINDOW_MAX}</b> генераций картинок; после полного исчерпания "
                f"следующий цикл — через <b>{NONSUB_IMAGE_WINDOW_DAYS}</b> суток от этого момента (то же время суток по UTC). "
                "Кредиты лимит не обходят. Оформи подписку в <code>/start</code> → <b>Оплатить</b> или дождись обновления цикла.</blockquote>",
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
            f"<blockquote><i>Параллельный запрос.</i> Сегодня (МСК): <b>{esc(used)}/{esc(limit)}</b>. "
            "Попробуй позже.</blockquote>",
            parse_mode=HTML,
        )
        return False, None
    meta.daily_reserved = True
    return True, meta


def _ready_categories_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for slug, title in READY_IDEA_CATEGORIES:
        pair.append(
            InlineKeyboardButton(
                text=title[:64],
                callback_data=f"{CB_READY_CAT_PREFIX}{slug}",
                style=BTN_PRIMARY,
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ideas_for_category(category: str) -> list[tuple[str, str, str, int]]:
    return READY_IDEA_ITEMS.get((category or "").strip().lower(), [])


def _ready_browser_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    prev_i = (index - 1) % total
    next_i = (index + 1) % total
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_READY_NAV_PREFIX}prev:{prev_i}",
                    style=BTN_PRIMARY,
                ),
                InlineKeyboardButton(
                    text="✅ Выбрать",
                    callback_data=f"{CB_READY_NAV_PREFIX}pick:{index}",
                    style=BTN_SUCCESS,
                ),
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_READY_NAV_PREFIX}next:{next_i}",
                    style=BTN_PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Категории",
                    callback_data=f"{CB_READY_NAV_PREFIX}back_cats",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU_BACK_START)],
        ]
    )


def _ready_wait_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К идеям", callback_data=CB_READY_PHOTO_BACK)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_IMG_CANCEL, style=BTN_DANGER)],
        ]
    )


def _ready_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_READY_CONFIRM, style=BTN_SUCCESS)],
            [InlineKeyboardButton(text="↩️ Назад к фото", callback_data=CB_READY_PHOTO_BACK)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_IMG_CANCEL, style=BTN_DANGER)],
        ]
    )


def _ready_category_caption() -> str:
    return (
        "<b>💡 Готовые идеи</b>\n"
        "<blockquote><i>Выбери направление. Затем листай варианты, нажми «Выбрать», "
        "загрузи фото и подтверди запуск.</i></blockquote>"
    )


def _ready_idea_caption(*, category_title: str, title: str, preview: str, index: int, total: int, photos_required: int) -> str:
    p_line = "2 фото" if photos_required == 2 else "1 фото"
    return (
        f"<b>{esc(category_title)}</b>\n"
        f"<blockquote><i>{esc(index + 1)}/{esc(total)}</i></blockquote>\n"
        f"<b>{esc(title)}</b>\n"
        f"<blockquote><i>{esc(preview)}</i></blockquote>\n"
        f"<i>Нужно для запуска:</i> <b>{esc(p_line)}</b>"
    )


def _regen_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ок", callback_data=CB_IMG_OK, style=BTN_SUCCESS
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Ещё раз", callback_data=CB_REGEN, style=BTN_PRIMARY
                ),
            ],
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
    usage_kind: str = "self",
) -> None:
    await save_last_image_context(
        user_id,
        "text",
        prompt,
        model,
        cost,
        _IMAGE_CONTEXT_LABEL,
        None,
        usage_kind=usage_kind,
    )
    if is_admin:
        day_note = ""
    else:
        q = await get_nonsub_image_quota_status(user_id)
        if q:
            u, lim = q
            day_note = (
                f"\n<blockquote><i>Картинки без подписки (цикл {NONSUB_IMAGE_WINDOW_MAX} шт.):</i> "
                f"<b>{esc(u)}/{esc(lim)}</b> <i>— сброс цикла через {NONSUB_IMAGE_WINDOW_DAYS} суток после исчерпания (UTC).</i></blockquote>"
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
    override_cost: int | None = None,
) -> None:
    await ensure_user(user_id, username)
    is_admin = user_id in ADMIN_IDS
    charge = not is_admin
    if not is_admin:
        model, plan_cost = await _effective_image_model_and_cost(user_id, model)
        cost = override_cost if override_cost is not None else plan_cost

    if is_polza_image_model(model):
        if not is_polza_configured():
            await message.answer(_POLZA_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
            return
    elif not is_openrouter_image_configured():
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return

    prep = await _prepare_image_charge_and_daily_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost, usage_kind=usage_kind
    )
    ok, meta = prep
    if not ok or meta is None:
        return
    wait_msg = await message.answer("Идет генерация картинки")
    try:
        if is_polza_image_model(model):
            image_bytes = await polza_text_to_image_bytes(
                prompt, model=model, user_id=user_id
            )
            from_cache = False
        else:
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
            err = format_openrouter_image_user_error(exc)
        elif isinstance(exc, PolzaApiError):
            logging.warning(
                "Polza.ai отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
            err = format_polza_image_user_error(exc)
        else:
            logging.exception("Image text generation failed user_id=%s", user_id)
            err = (
                format_polza_image_user_error(exc)
                if is_polza_image_model(model)
                else format_openrouter_image_user_error(exc)
            )
        if meta.daily_reserved:
            await release_daily_image_generation(user_id, usage_kind)
        if meta.credit_charged:
            await add_credits(user_id, cost)
        if meta.nonsub_quota_reserved:
            await release_nonsub_image_quota_slot(user_id)
        if meta.nonsub_ready_reserved:
            await release_nonsub_ready_idea_slot(user_id)
        if meta.idea_token_consumed:
            await add_idea_tokens(user_id, 1)
        await wait_msg.edit_text(err, parse_mode=HTML, disable_web_page_preview=True)
        return
    await _finalize_generation_status_message(wait_msg)
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
        usage_kind=usage_kind,
    )


async def _download_telegram_photo_bytes(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    if not tg_file.file_path:
        raise RuntimeError("Пустой file_path у фото")
    buf = BytesIO()
    await bot.download_file(tg_file.file_path, destination=buf)
    data = buf.getvalue()
    if not data:
        raise RuntimeError("Пустой файл фото")
    return data


async def _execute_ready_with_refs_generation(
    message: Message,
    state: FSMContext | None,
    *,
    user_id: int,
    username: str | None,
    prompt: str,
    refs_file_ids: list[str],
    cost: int,
    model_override: str | None = None,
    extra_refs: list[bytes] | None = None,
    extra_refs_first: bool = False,
    strict_refs: bool = False,
) -> None:
    await ensure_user(user_id, username)
    is_admin = user_id in ADMIN_IDS
    charge = not is_admin
    model = (model_override or "").strip()
    if not model:
        model = (OPENROUTER_IMAGE_GEMINI_PRO_MODEL or "").strip()
    if not model:
        model = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip()
    if not model:
        model = (OPENROUTER_IMAGE_GEMINI_MODEL or "").strip()
    if not model:
        model = OPENROUTER_IMAGE_MODEL
    if not is_openrouter_image_configured():
        await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return

    chat_id = message.chat.id
    prep = await _prepare_image_charge_and_daily_slot(
        message, user_id=user_id, is_admin=is_admin, charge=charge, cost=cost, usage_kind="ready"
    )
    ok, meta = prep
    if not ok or meta is None:
        return
    wait_msg = await edit_or_send_nav_message(
        message,
        text="Идет генерация картинки",
        reply_markup=None,
        parse_mode=None,
    )
    if wait_msg is None:
        wait_msg = await message.bot.send_message(chat_id, "Идет генерация картинки")
    try:
        user_refs: list[bytes] = []
        refs: list[bytes] = []
        if extra_refs and extra_refs_first:
            refs.extend(extra_refs)
        for fid in refs_file_ids:
            b = await _download_telegram_photo_bytes(message.bot, fid)
            user_refs.append(b)
            refs.append(b)
        if extra_refs and not extra_refs_first:
            refs.extend(extra_refs)
        try:
            image_bytes = await openrouter_text_and_refs_to_image_bytes(
                prompt,
                refs=refs,
                model=model,
            )
        except Exception:
            # Иногда модель/провайдер отказывает именно на сочетании "scene ref + user ref".
            # Делаем безопасный повтор с фото пользователя, чтобы сценарий не ломался.
            if (not strict_refs) and extra_refs and user_refs:
                logging.warning("Ready refs primary call failed; retrying with user refs only", exc_info=True)
                image_bytes = await openrouter_text_and_refs_to_image_bytes(
                    prompt,
                    refs=user_refs,
                    model=model,
                )
            else:
                raise
        from_cache = False
    except Exception as exc:
        if isinstance(exc, OpenRouterApiError):
            logging.warning(
                "OpenRouter refs отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
            err = format_openrouter_image_user_error(exc)
        else:
            logging.exception("Image refs generation failed user_id=%s", user_id)
            err = format_openrouter_image_user_error(exc)
        if meta.daily_reserved:
            await release_daily_image_generation(user_id, "ready")
        if meta.credit_charged:
            await add_credits(user_id, cost)
        if meta.nonsub_quota_reserved:
            await release_nonsub_image_quota_slot(user_id)
        if meta.nonsub_ready_reserved:
            await release_nonsub_ready_idea_slot(user_id)
        if meta.idea_token_consumed:
            await add_idea_tokens(user_id, 1)
        await wait_msg.edit_text(err, parse_mode=HTML, disable_web_page_preview=True)
        return
    await _finalize_generation_status_message(wait_msg)
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
        usage_kind="ready",
    )


async def _send_waiting_prompt_step(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    model: str,
    cost: int,
    replace_message: Message | None = None,
    model_style_hint: str | None = None,
) -> None:
    await state.update_data(selected_model=model, selected_cost=cost, selected_usage_kind="self")
    await state.set_state(ImageGenState.waiting_prompt)
    body = _WAITING_PROMPT_HTML
    if model_style_hint:
        body += f"\n<blockquote><i>{esc(model_style_hint)}</i></blockquote>"
    if replace_message is not None:
        await edit_or_send_nav_message(
            replace_message,
            text=body,
            reply_markup=_waiting_prompt_keyboard(),
            parse_mode=HTML,
        )
        return
    await bot.send_message(
        chat_id,
        body,
        reply_markup=_waiting_prompt_keyboard(),
        parse_mode=HTML,
    )


async def _show_image_model_pick(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
) -> None:
    if not is_openrouter_image_configured():
        await edit_or_send_nav_message(
            message,
            text=_IMAGE_GEN_MISSING_TEXT,
            reply_markup=_missing_config_kb(),
            parse_mode=HTML,
        )
        return
    await ensure_user(user_id, username)
    is_admin = user_id in ADMIN_IDS
    if is_admin:
        plan_id = "universe"
    else:
        profile = await get_user_admin_profile(user_id)
        if not profile or not subscription_is_active(profile.subscription_ends_at):
            await _start_image_flow(message, state, user_id, username, replace_menu=True)
            return
        plan_id = (profile.subscription_plan or "").strip().lower()
    await state.clear()
    choices = _model_choices_for_subscription_plan(plan_id)
    if len(choices) < 2:
        m = choices[0]
        await _send_waiting_prompt_step(
            message.bot,
            message.chat.id,
            state,
            model=m[1],
            cost=m[2],
            replace_message=message,
            model_style_hint=m[3],
        )
        return
    await state.update_data(_model_pick_plan=("__admin__" if is_admin else (plan_id or "").strip().lower()))
    await state.set_state(ImageGenState.choosing_model)
    cap = _model_pick_caption_html(for_admin=is_admin, choices=choices)
    await edit_or_send_nav_message(
        message,
        text=cap,
        reply_markup=_subscriber_model_pick_keyboard(choices),
        parse_mode=HTML,
    )


async def _start_image_flow(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    *,
    replace_menu: bool = False,
) -> None:
    if not is_openrouter_image_configured():
        if replace_menu:
            await edit_or_send_nav_message(
                message,
                text=_IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(),
                parse_mode=HTML,
            )
        else:
            await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    await ensure_user(user_id, username)
    await state.clear()
    await _send_waiting_prompt_step(
        message.bot,
        message.chat.id,
        state,
        model=OPENROUTER_IMAGE_MODEL,
        cost=OPENROUTER_IMAGE_COST_CREDITS,
        replace_message=message if replace_menu else None,
    )


@router.callback_query(F.data.startswith(CB_IMG_MODEL_SEL_PREFIX))
async def subscriber_picked_model(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    raw = callback.data[len(CB_IMG_MODEL_SEL_PREFIX) :]
    if not raw.isdigit():
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    idx = int(raw)
    uid = callback.from_user.id
    if uid in ADMIN_IDS:
        plan_id = "universe"
    else:
        profile = await get_user_admin_profile(uid)
        if not profile or not subscription_is_active(profile.subscription_ends_at):
            await callback.answer("Подписка не активна. Доступна базовая модель.", show_alert=True)
            await _start_image_flow(
                callback.message,
                state,
                uid,
                callback.from_user.username,
                replace_menu=True,
            )
            return
        plan_id = (profile.subscription_plan or "").strip().lower()
    models = _model_choices_for_subscription_plan(plan_id)
    if idx < 0 or idx >= len(models):
        await callback.answer("Нет такой модели.", show_alert=True)
        return
    _label, model_id, cost, hint = models[idx]
    await callback.answer()
    await _send_waiting_prompt_step(
        callback.bot,
        callback.message.chat.id,
        state,
        model=model_id,
        cost=cost,
        replace_message=callback.message,
        model_style_hint=hint,
    )


@router.message(ImageGenState.choosing_model)
async def remind_pick_model_or_ignore(message: Message, state: FSMContext) -> None:
    """Текст вместо кнопки на шаге выбора модели."""
    if not message.from_user:
        return
    uid = message.from_user.id
    if uid in ADMIN_IDS:
        choices = _model_choices_for_subscription_plan("universe")
        cap = _model_pick_caption_html(for_admin=True, choices=choices)
        await message.answer(
            cap,
            reply_markup=_subscriber_model_pick_keyboard(choices),
            parse_mode=HTML,
        )
        return
    profile = await get_user_admin_profile(uid)
    if not profile or not subscription_is_active(profile.subscription_ends_at):
        await state.clear()
        await message.answer(
            "<b>Подписка не активна.</b> Доступна базовая модель и лимит как без подписки — дальше опиши картинку.",
            parse_mode=HTML,
        )
        await _start_image_flow(message, state, uid, message.from_user.username)
        return
    plan_id = (profile.subscription_plan or "").strip().lower()
    await state.update_data(_model_pick_plan=plan_id)
    choices = _model_choices_for_subscription_plan(plan_id)
    cap = _model_pick_caption_html(for_admin=False, choices=choices)
    await message.answer(
        cap,
        reply_markup=_subscriber_model_pick_keyboard(choices),
        parse_mode=HTML,
    )


@router.callback_query(F.data == CB_IMG_CANCEL)
async def cancel_image_flow(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await state.clear()
    user_id = callback.from_user.id
    await callback.answer()
    await restore_main_menu_message(callback.message, user_id, callback.from_user.username)


@router.callback_query(F.data == CB_BACK_IMAGE_MODELS)
async def back_to_image_flow(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    uid = callback.from_user.id
    if uid in ADMIN_IDS:
        await _show_image_model_pick(callback.message, state, uid, callback.from_user.username)
        return
    await ensure_user(uid, callback.from_user.username)
    profile = await get_user_admin_profile(uid)
    if profile and subscription_is_active(profile.subscription_ends_at):
        await _show_image_model_pick(callback.message, state, uid, callback.from_user.username)
    else:
        await _start_image_flow(callback.message, state, uid, callback.from_user.username, replace_menu=True)


async def _send_ready_ideas_screen(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    *,
    edit: bool = False,
) -> None:
    await state.clear()
    if not is_openrouter_image_configured():
        if edit:
            await edit_or_send_nav_message(
                message,
                text=_IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(),
                parse_mode=HTML,
            )
        else:
            await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    await ensure_user(user_id, username)
    await state.set_state(ImageGenState.ready_choosing_category)
    cap = _ready_category_caption()
    kb = _ready_categories_keyboard()
    if edit:
        await edit_or_send_nav_message(message, text=cap, reply_markup=kb, parse_mode=HTML)
    else:
        await message.answer(cap, reply_markup=kb, parse_mode=HTML)


@router.callback_query(F.data == CB_READY_IDEAS)
async def open_ready_ideas(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    await _send_ready_ideas_screen(
        callback.message,
        state,
        callback.from_user.id,
        callback.from_user.username,
        edit=True,
    )


@router.message(Command("ideas"))
async def cmd_ready_ideas(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await _send_ready_ideas_screen(message, state, message.from_user.id, message.from_user.username)


async def _open_ready_card(
    message: Message,
    state: FSMContext,
    *,
    category: str,
    index: int,
    edit: bool,
) -> None:
    ideas = _ideas_for_category(category)
    if not ideas:
        if edit:
            await edit_or_send_nav_message(
                message,
                text="<b>В этой категории пока пусто.</b>\n<blockquote><i>Выбери другое направление.</i></blockquote>",
                reply_markup=_ready_categories_keyboard(),
                parse_mode=HTML,
            )
        else:
            await message.answer(
                "<b>В этой категории пока пусто.</b>\n<blockquote><i>Выбери другое направление.</i></blockquote>",
                reply_markup=_ready_categories_keyboard(),
                parse_mode=HTML,
            )
        await state.set_state(ImageGenState.ready_choosing_category)
        return
    total = len(ideas)
    idx = index % total
    title, preview, _prompt, photos_required = ideas[idx]
    cat_title = dict(READY_IDEA_CATEGORIES).get(category, category)
    cap = _ready_idea_caption(
        category_title=cat_title,
        title=title,
        preview=preview,
        index=idx,
        total=total,
        photos_required=photos_required,
    )
    await state.update_data(_ready_category=category, _ready_index=idx)
    await state.set_state(ImageGenState.ready_browsing_idea)
    kb = _ready_browser_keyboard(idx, total)
    if edit:
        await edit_or_send_nav_message(message, text=cap, reply_markup=kb, parse_mode=HTML)
    else:
        await message.answer(cap, reply_markup=kb, parse_mode=HTML)


@router.callback_query(F.data.startswith(CB_READY_CAT_PREFIX))
async def ready_pick_category(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    category = callback.data.replace(CB_READY_CAT_PREFIX, "", 1).strip().lower()
    await _open_ready_card(callback.message, state, category=category, index=0, edit=True)


@router.callback_query(F.data.startswith(CB_READY_NAV_PREFIX))
async def ready_nav_cards(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    payload = callback.data.replace(CB_READY_NAV_PREFIX, "", 1)
    if payload == "back_cats":
        await state.set_state(ImageGenState.ready_choosing_category)
        await edit_or_send_nav_message(
            callback.message,
            text=_ready_category_caption(),
            reply_markup=_ready_categories_keyboard(),
            parse_mode=HTML,
        )
        return
    parts = payload.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Некорректная навигация.", show_alert=True)
        return
    action, idx_raw = parts[0], parts[1]
    data = await state.get_data()
    category = str(data.get("_ready_category") or "").strip().lower()
    ideas = _ideas_for_category(category)
    if not ideas:
        await callback.answer("Категория недоступна.", show_alert=True)
        return
    idx = int(idx_raw) % len(ideas)
    if action in ("prev", "next"):
        await _open_ready_card(callback.message, state, category=category, index=idx, edit=True)
        return
    if action == "pick":
        title, _preview, _prompt, photos_required = ideas[idx]
        await state.update_data(_ready_category=category, _ready_index=idx, _ready_photos=[], _ready_need=photos_required)
        await state.set_state(ImageGenState.ready_waiting_photos)
        await edit_or_send_nav_message(
            callback.message,
            text=(
                f"<b>Выбрано:</b> {esc(title)}\n"
                f"<blockquote><i>Отправь {esc('2 фото' if photos_required == 2 else '1 фото')}.\n"
                "После загрузки появится кнопка подтверждения.</i></blockquote>"
            ),
            reply_markup=_ready_wait_photo_keyboard(),
            parse_mode=HTML,
        )
        return
    await callback.answer("Неизвестное действие.", show_alert=True)


@router.callback_query(F.data == CB_READY_PHOTO_BACK)
async def ready_photo_back(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    category = str(data.get("_ready_category") or "").strip().lower()
    idx = int(data.get("_ready_index") or 0)
    if not category:
        await _send_ready_ideas_screen(
            callback.message,
            state,
            callback.from_user.id,
            callback.from_user.username,
            edit=True,
        )
        return
    await _open_ready_card(callback.message, state, category=category, index=idx, edit=True)


@router.message(ImageGenState.ready_waiting_photos, ~F.photo)
async def ready_need_photo_hint(message: Message) -> None:
    await message.answer(
        "Пришли фото сообщением. Когда соберем нужное количество, появится подтверждение.",
        reply_markup=_ready_wait_photo_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_photos, F.photo)
async def ready_collect_photos(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    need = int(data.get("_ready_need") or 1)
    photos = list(data.get("_ready_photos") or [])
    if len(photos) >= need:
        await message.answer("Фото уже загружены. Нажми «Подтвердить» или «Отмена».", reply_markup=_ready_confirm_keyboard())
        return
    ph = message.photo[-1]
    if not ph.file_id:
        await message.answer("Не удалось прочитать фото, попробуй ещё раз.")
        return
    photos.append(ph.file_id)
    await state.update_data(_ready_photos=photos)
    if len(photos) < need:
        await message.answer(
            f"Фото получено: <b>{esc(len(photos))}/{esc(need)}</b>. Пришли ещё.",
            reply_markup=_ready_wait_photo_keyboard(),
            parse_mode=HTML,
        )
        return
    await state.set_state(ImageGenState.ready_waiting_confirm)
    await message.answer(
        (
            f"<b>Фото зафиксированы:</b> <b>{esc(len(photos))}</b>\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        parse_mode=HTML,
    )


def _build_ready_prompt(
    base_prompt: str,
    telegram_username: str | None,
    *,
    include_telegram_nick: bool = True,
    refs_hint: str | None = None,
) -> str:
    nick = (telegram_username or "").strip()
    nick_line = f"@{nick}" if nick else "user_without_username"
    nick_part = (
        f"Telegram nickname to render above the head: {nick_line}\n"
        if include_telegram_nick
        else ""
    )
    hint_part = f"{refs_hint.strip()}\n" if refs_hint and refs_hint.strip() else ""
    return (
        f"{(base_prompt or '').strip()}\n\n"
        f"{nick_part}"
        f"{hint_part}"
        "Use all reference images from input. Preserve facial identity and natural skin texture."
    )


@router.callback_query(F.data == CB_READY_CONFIRM)
async def ready_confirm_and_generate(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    # Сразу снимаем «часики» у кнопки, чтобы клик всегда ощущался.
    await callback.answer()
    try:
        if not is_openrouter_image_configured():
            await edit_or_send_nav_message(
                callback.message,
                text=_IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(),
                parse_mode=HTML,
            )
            return
        data = await state.get_data()
        category = str(data.get("_ready_category") or "").strip().lower()
        idx_raw = data.get("_ready_index")
        try:
            idx = int(idx_raw if idx_raw is not None else 0)
        except (TypeError, ValueError):
            idx = -1
        photos = list(data.get("_ready_photos") or [])
        ideas = _ideas_for_category(category)
        if not ideas or idx < 0 or idx >= len(ideas):
            await callback.answer("Идея не найдена. Выбери заново.", show_alert=True)
            await _send_ready_ideas_screen(
                callback.message,
                state,
                callback.from_user.id,
                callback.from_user.username,
                edit=True,
            )
            return
        title, _preview, base_prompt, need = ideas[idx]
        if len(photos) < need:
            await edit_or_send_nav_message(
                callback.message,
                text="Сначала загрузи нужное число фото.",
                reply_markup=_ready_confirm_keyboard(),
                parse_mode=None,
            )
            return
        include_nick = title == "Фотка в эндер мире"
        model_override = None
        if title == "На отдыхе в Италии":
            model_override = (OPENROUTER_IMAGE_MODEL_ALT or "").strip()
        extra_refs: list[bytes] = []
        static_ref = _READY_IDEA_STATIC_REF_BY_TITLE.get(title)
        if title in ("Clash Royale элитные варвары", "На отдыхе в Италии", "Кто ты из Вестероса"):
            static_ref = None
        if static_ref:
            p = Path(static_ref)
            if p.is_file():
                try:
                    extra_refs.append(p.read_bytes())
                except OSError:
                    logging.warning("Failed to read static ready ref: %s", static_ref)
            else:
                logging.warning("Static ready ref is missing: %s", static_ref)
        refs_hint = "Reference mapping: image #1 is user identity photo."
        if title == "Победа над Мухаммадом Али на ринге":
            refs_hint = "Reference mapping: image #1 is user identity photo. Image #2 is Muhammad Ali identity photo."
        if title == "Для влюбленных: рыцарь и дама":
            refs_hint = "Reference mapping: image #1 is knight identity photo. Image #2 is woman identity photo."
        prompt = _build_ready_prompt(
            base_prompt,
            callback.from_user.username,
            include_telegram_nick=include_nick,
            refs_hint=refs_hint,
        )
        await state.clear()
        user_id = callback.from_user.id
        await ensure_user(user_id, callback.from_user.username)
        extra_first = False
        strict_refs = False
        await _execute_ready_with_refs_generation(
            callback.message,
            state,
            user_id=user_id,
            username=callback.from_user.username,
            prompt=prompt,
            cost=OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS,
            refs_file_ids=photos,
            model_override=model_override,
            extra_refs=extra_refs,
            extra_refs_first=extra_first,
            strict_refs=strict_refs,
        )
    except Exception:
        logging.exception("ready_confirm_and_generate failed")
        await edit_or_send_nav_message(
            callback.message,
            text="Ошибка запуска. Попробуй снова — открыл раздел «Готовые идеи».",
            reply_markup=None,
            parse_mode=None,
        )
        await _send_ready_ideas_screen(
            callback.message,
            state,
            callback.from_user.id,
            callback.from_user.username,
            edit=True,
        )


@router.message(ImageGenState.ready_choosing_category)
async def ready_choose_category_hint(message: Message) -> None:
    await message.answer(
        "Сначала выбери категорию кнопками выше 👆",
        reply_markup=_ready_categories_keyboard(),
    )


@router.message(ImageGenState.ready_browsing_idea)
async def ready_browse_hint(message: Message) -> None:
    await message.answer("Листай идеи кнопками ⬅️/➡️ и нажми «✅ Выбрать».")


@router.message(ImageGenState.ready_waiting_confirm)
async def ready_waiting_confirm_hint(message: Message) -> None:
    await message.answer(
        "Нажми «✅ Подтвердить» для запуска или «❌ Отмена».",
        reply_markup=_ready_confirm_keyboard(),
    )


@router.callback_query(F.data == CB_CREATE_IMAGE)
async def open_image_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    uid = callback.from_user.id
    await ensure_user(uid, callback.from_user.username)
    profile = await get_user_admin_profile(uid)
    has_sub = bool(profile and subscription_is_active(profile.subscription_ends_at))
    if uid in ADMIN_IDS or has_sub:
        await _show_image_model_pick(
            callback.message,
            state,
            uid,
            callback.from_user.username,
        )
    else:
        await _start_image_flow(callback.message, state, uid, callback.from_user.username, replace_menu=True)


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
    uk = str(data.get("selected_usage_kind") or "self")
    if uk not in ("ready", "self"):
        uk = "self"
    await _execute_text_generation(
        message,
        state,
        user_id=user_id,
        username=message.from_user.username,
        prompt=prompt,
        model=model,
        cost=cost,
        usage_kind=uk,
    )


@router.callback_query(F.data.in_({CB_IMG_OK, "img:save"}))
async def result_ok_to_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Убрать кнопки с сообщения с картинкой, затем главное меню (фото в чате оставляем)."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logging.debug("result_ok: edit_reply_markup failed", exc_info=True)
    await restore_main_menu_message(
        callback.message,
        callback.from_user.id,
        callback.from_user.username,
    )


@router.callback_query(F.data == CB_REGEN)
async def regenerate_new_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Та же модель и цена — пользователь вводит новый промпт."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    user_id = callback.from_user.id
    ctx = await get_last_image_context(user_id)
    if not ctx:
        await callback.answer(
            "Нет данных о последней генерации. Открой «Создать картинку» в меню.",
            show_alert=True,
        )
        return
    if ctx.kind != "text":
        await callback.answer("Создай картинку через меню.", show_alert=True)
        return
    await callback.answer()
    uk = str(ctx.usage_kind or "self")
    if uk not in ("ready", "self"):
        uk = "self"
    if uk == "ready":
        await _send_ready_ideas_screen(
            callback.message,
            state,
            user_id,
            callback.from_user.username,
            edit=False,
        )
        return
    await state.update_data(
        selected_model=ctx.model,
        selected_cost=ctx.cost,
        selected_usage_kind=uk,
    )
    await state.set_state(ImageGenState.waiting_prompt)
    await callback.message.answer(
        f"{_WAITING_PROMPT_HTML}\n\n"
        "<blockquote><i>Будут использованы та же модель и стоимость. Опиши <b>новую</b> картинку.</i></blockquote>",
        reply_markup=_waiting_prompt_keyboard(),
        parse_mode=HTML,
    )

