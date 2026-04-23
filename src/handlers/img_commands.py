from __future__ import annotations

"""
Генерация изображений по тексту: OpenRouter (FLUX, Gemini) и Polza.ai (GPT Image).
"""

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config import (
    ADMIN_IDS,
    PROJECT_ROOT,
    OPENROUTER_IMAGE_ALT_COST_CREDITS,
    OPENROUTER_IMAGE_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_MODEL,
    OPENROUTER_IMAGE_GEMINI_PRO_MODEL,
    OPENROUTER_IMAGE_GEMINI_PREVIEW_COST_CREDITS,
    OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL,
    OPENROUTER_IMAGE_GPT54_IMAGE2_MODEL,
    OPENROUTER_IMAGE_MODEL,
    OPENROUTER_IMAGE_MODEL_ALT,
    POLZA_IMAGE_GPT5_IMAGE_COST_CREDITS,
    POLZA_IMAGE_GPT_IMAGE_15_COST_CREDITS,
    POLZA_IMAGE_MODEL_GPT5_IMAGE,
    POLZA_IMAGE_MODEL_GPT_IMAGE_15,
)
from src.database import (
    ImageChargeMeta,
    LastImageContext,
    add_credits_with_reason,
    add_idea_tokens,
    ensure_user,
    get_credits,
    get_daily_image_generation_usage,
    get_last_image_context,
    get_nonsub_image_quota_status,
    get_user_ready_mode,
    get_user_admin_profile,
    increment_user_generated_images_total,
    release_daily_image_generation,
    release_nonsub_image_quota_slot,
    release_nonsub_ready_idea_slot,
    save_last_image_context,
    subscription_is_active,
    take_credits_with_reason,
    try_consume_idea_token,
    try_reserve_daily_image_generation,
    try_reserve_nonsub_image_quota_slot,
    try_reserve_nonsub_ready_idea_slot,
)
from src.formatting import CREDITS_COIN_TG_HTML, HTML, esc
from src.image_gen_gate import image_generation_slot
from src.handlers.commands import (
    _is_generated_image_result_message,
    edit_or_send_nav_message,
    replace_nav_screen_in_message,
    restore_main_menu_message,
)
from src.keyboards.main_menu import menu_hub_keyboard, start_menu_keyboard
from src.keyboards.callback_data import (
    CB_BACK_IMAGE_MODELS,
    CB_CREATE_IMAGE_HUB,
    CB_CREATE_IMAGE,
    CB_IMG_CANCEL,
    CB_IMG_MODEL_SEL_PREFIX,
    CB_IMG_OK,
    CB_MENU_BACK_START,
    CB_MENU_HUB,
    CB_MENU_MELLSTROY,
    CB_READY_BEARD_SIZE_PREFIX,
    CB_READY_CAT_PREFIX,
    CB_READY_CONFIRM,
    CB_READY_IDEAS,
    CB_READY_IDEAS_HUB,
    CB_READY_NAV_PREFIX,
    CB_READY_PHOTO_BACK,
    CB_BACK_TO_READY_IDEAS,
    CB_READY_RESULT_MAIN_MENU,
    CB_REGEN,
    CB_REGEN_READY_REDO,
)
from src.keyboards.styles import BTN_DANGER, BTN_PRIMARY, BTN_SUCCESS
from src.openrouter_image import (
    OpenRouterApiError,
    is_openrouter_image_configured,
    openrouter_text_and_refs_to_image_bytes,
    openrouter_text_to_image_bytes,
)
from src.polza_image import (
    PolzaApiError,
    is_polza_configured,
    is_polza_image_model,
    polza_text_to_image_bytes,
)
from src.subscription_catalog import (
    NONSUB_IMAGE_WINDOW_DAYS,
    NONSUB_IMAGE_WINDOW_MAX,
)

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - pillow is optional at runtime
    Image = None
    ImageDraw = None
    ImageFont = None

router = Router(name="img_commands")

READY_IDEA_CATEGORIES: list[tuple[str, str]] = [
    ("memes", "Мемы"),
    ("appearance", "Внешность"),
    ("outfits", "Одежда"),
    ("locations", "Локации"),
    ("celebrities", "Знаменитости"),
    ("for_two", "Для двоих"),
    ("texts", "Тексты"),
    ("movies", "Фильмы / Сериалы"),
    ("superheroes", "Супергерои"),
    ("games", "Игры"),
    ("colors", "Цвета"),
    ("art_styles", "Арт-стили"),
    ("horror", "Хоррор"),
    ("add_photo", "Добавить фото"),
]

# document_id премиум-эмодзи для кнопок и подписей категорий «Готовые идеи».
_READY_CATEGORY_PREMIUM_IDS: dict[str, str] = {
    "memes": "5415631414370510984",
    "outfits": "5434003076849610368",
    "celebrities": "5217822164362739968",
    "texts": "5235814241927181048",
    "superheroes": "5292113682560458323",
    "colors": "5220195193923328112",
    "horror": "5332696592317160796",
    "appearance": "5348510803634960850",
    "locations": "5391032818111363540",
    "for_two": "5280816565657300091",
    "movies": "5893413965903434288",
    "games": "5319247469165433798",
    "art_styles": "5429619972529736627",
    "add_photo": "5440671202555215608",
}

# Символ-заглушка внутри <tg-emoji> (как в старых Unicode-подписях кнопок).
_READY_CATEGORY_EMOJI_FALLBACK: dict[str, str] = {
    "memes": "😂",
    "appearance": "🧔",
    "outfits": "👕",
    "locations": "🏝",
    "celebrities": "🌟",
    "for_two": "💞",
    "texts": "📝",
    "movies": "🎬",
    "superheroes": "🦸",
    "games": "🎮",
    "colors": "🎨",
    "art_styles": "🖌",
    "horror": "🌑",
    "add_photo": "📥",
}


def _ready_category_title_html(slug: str) -> str:
    """Премиум-эмодзи + название категории для HTML (подписи к превью идей)."""
    s = (slug or "").strip().lower()
    title = dict(READY_IDEA_CATEGORIES).get(s) or (slug or "—")
    eid = _READY_CATEGORY_PREMIUM_IDS.get(s)
    if not eid:
        return f"<b>{esc(title)}</b>"
    fb = _READY_CATEGORY_EMOJI_FALLBACK.get(s, "⭐")
    # Жирным только название; <tg-emoji> снаружи — так клиенты корректнее рисуют custom emoji в caption.
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji> <b>{esc(title)}</b>'

_POSTER_TEXT_READY_TITLE = "Постер с текстом"
_FLUFFY_LETTERS_TITLE = "Пушистые буквы 3D"
_PLASTER_FASHION_STUDIO_TITLE = "Fashion и гипсовые буквы"
_LUXURY_TORN_COVER_TITLE = "Luxury torn cover"
_SUPERHERO_MIRROR_TITLE = "Mirror superhero multiverse"
_BEARD_MUSTACHE_TITLE = "Густая борода + усы"
_MELLSTROY_PHOTO_TITLE = "Фото с Меллстройностью"
_RONALDO_PHOTO_TITLE = "Фото с Роналдо"
_SONY_ERICSSON_T100_TITLE = "Sony Ericsson T100"
_CHALK_ON_ASPHALT_TITLE = "Мел на асфальте"
_POLAROID_CURTAIN_TITLE = "Polaroid и занавеска"
_FANTASY_3D_GAME_TITLE = "3D заголовок фэнтези-игры"
_MMORPG_HERO_TITLE = "MMORPG: герой фэнтези"
_GTA_V_REALISM_TITLE = "GTA V: реалистичный персонаж"
_FANTASY_HEADLINE_MAX_LEN = 80
_FANTASY_COLOR_MAX_LEN = 32
_MMORPG_ALLOWED_RACES: tuple[str, ...] = (
    "Orc",
    "Undead",
    "Human",
    "Elf",
    "Gnome",
    "Goblin",
    "Worgen",
    "Draenei",
    "Troll",
    "Void Elf",
    "Nightborne",
)
_MMORPG_ALLOWED_CLASSES: tuple[str, ...] = (
    "Warrior",
    "Mage",
    "Warlock",
    "Paladin",
    "Priest",
    "Rogue",
    "Demon Hunter",
    "Death Knight",
    "Druid",
    "Hunter (Archer)",
)
_MMORPG_LAST_BUILD_BY_USER: dict[int, tuple[str, str]] = {}
# Название идеи в боте: категория «Добавить фото» → карточка с этим заголовком.
_OBJECT_IN_SCENE_TITLE = "Перемещение объекта"
_LUXURY_COVER_BRANDS: tuple[str, ...] = (
    "Gucci",
    "Fendi",
    "Prada",
    "Balenciaga",
    "Versace",
    "Chloe",
    "Hermes",
    "Dior",
    "Louis Vuitton",
)
_LUXURY_COVER_COUNTRIES: tuple[str, ...] = (
    "Italian",
    "French",
    "British",
    "American",
    "Japanese",
)
_LUXURY_COVER_COLORS: tuple[tuple[str, str], ...] = (
    ("emerald", "green"),
    ("fuchsia", "magenta"),
    ("cobalt", "blue"),
    ("crimson", "red"),
    ("amber", "yellow"),
    ("teal", "turquoise"),
    ("violet", "purple"),
)
_LUXURY_COVER_TEXTS: tuple[str, ...] = (
    "ETERNAL GLAMOUR",
    "URBAN SOPHISTICATED",
    "SUMMER LUXURY",
    "GLAMOUR UNVEILED",
    "FASHION EVOLUTION",
    "ICONIC STYLE",
    "LUXE REBORN",
    "MODERN VOGUE",
)
_SUPERHERO_POOL: tuple[tuple[str, str], ...] = (
    # DC
    ("DC", "Superman"),
    ("DC", "Batman"),
    ("DC", "Wonder Woman"),
    ("DC", "Flash"),
    ("DC", "Green Lantern (Hal Jordan)"),
    ("DC", "Aquaman"),
    ("DC", "Cyborg"),
    ("DC", "Shazam"),
    ("DC", "Martian Manhunter"),
    ("DC", "Supergirl"),
    ("DC", "Batgirl"),
    ("DC", "Nightwing"),
    ("DC", "Robin"),
    ("DC", "Green Arrow"),
    ("DC", "Black Canary"),
    ("DC", "Joker"),
    ("DC", "Lex Luthor"),
    ("DC", "Darkseid"),
    ("DC", "Reverse-Flash"),
    ("DC", "Deathstroke"),
    ("DC", "Bane"),
    ("DC", "Poison Ivy"),
    ("DC", "Mr. Freeze"),
    ("DC", "Two-Face"),
    ("DC", "Scarecrow"),
    ("DC", "Black Manta"),
    ("DC", "Sinestro"),
    ("DC", "Brainiac"),
    ("DC", "Doomsday"),
    # Marvel
    ("Marvel", "Spider-Man"),
    ("Marvel", "Iron Man"),
    ("Marvel", "Captain America"),
    ("Marvel", "Thor"),
    ("Marvel", "Hulk"),
    ("Marvel", "Black Widow"),
    ("Marvel", "Hawkeye"),
    ("Marvel", "Doctor Strange"),
    ("Marvel", "Wolverine"),
    ("Marvel", "Storm"),
    ("Marvel", "Cyclops"),
    ("Marvel", "Jean Grey"),
    ("Marvel", "Professor X"),
    ("Marvel", "Black Panther"),
    ("Marvel", "Captain Marvel"),
    ("Marvel", "Thanos"),
    ("Marvel", "Loki"),
    ("Marvel", "Magneto"),
    ("Marvel", "Venom"),
    ("Marvel", "Green Goblin"),
    ("Marvel", "Doctor Octopus"),
    ("Marvel", "Red Skull"),
    ("Marvel", "Apocalypse"),
    ("Marvel", "Juggernaut"),
    ("Marvel", "Sabretooth"),
    ("Marvel", "Mysterio"),
    ("Marvel", "Kraven"),
    ("Marvel", "Electro"),
    ("Marvel", "Sandman"),
    ("Marvel", "Rhino"),
    # The Boys
    ("The Boys", "Starlight"),
    ("The Boys", "Queen Maeve"),
    ("The Boys", "Homelander"),
    ("The Boys", "Stormfront"),
    ("The Boys", "The Deep"),
    ("The Boys", "A-Train"),
    ("The Boys", "Black Noir"),
)
# Последний (universe, hero) для идеи «Mirror superhero» — чтобы не повторять костюм два раза подряд.
_SUPERHERO_LAST_BY_USER: dict[int, tuple[str, str]] = {}


def _ready_idea_needs_headline_input(title: str) -> bool:
    return (title or "").strip() in (
        _POSTER_TEXT_READY_TITLE,
        _FLUFFY_LETTERS_TITLE,
        _PLASTER_FASHION_STUDIO_TITLE,
        _CHALK_ON_ASPHALT_TITLE,
    )


def _ready_idea_needs_photo_then_text(title: str) -> bool:
    """После фото бот запрашивает текст (заголовок, ник Minecraft и т.д.)."""
    t = (title or "").strip()
    if _ready_idea_needs_headline_input(t):
        return True
    return t == "Minecraft"


def _pick_mmorpg_race_class(user_id: int) -> tuple[str, str]:
    """Pick race+class, avoiding immediate repeat for same user."""
    all_pairs = [(r, c) for r in _MMORPG_ALLOWED_RACES for c in _MMORPG_ALLOWED_CLASSES]
    if not all_pairs:
        return ("Human", "Warrior")
    prev = _MMORPG_LAST_BUILD_BY_USER.get(int(user_id))
    if prev is not None and len(all_pairs) > 1:
        candidates = [pair for pair in all_pairs if pair != prev]
    else:
        candidates = all_pairs
    picked = random.choice(candidates)
    _MMORPG_LAST_BUILD_BY_USER[int(user_id)] = picked
    return picked


def _pick_luxury_cover_vars() -> tuple[str, str, str, str, str]:
    """Runtime randomization for luxury torn-cover idea."""
    brand = random.choice(_LUXURY_COVER_BRANDS)
    country = random.choice(_LUXURY_COVER_COUNTRIES)
    color_name, color_tone = random.choice(_LUXURY_COVER_COLORS)
    text = random.choice(_LUXURY_COVER_TEXTS)
    return brand, country, color_name, color_tone, text


def _pick_superhero_vars(user_id: int) -> tuple[str, str]:
    """Случайная пара вселенная + герой; тот же костюм не два раза подряд (3-й запуск может снова попасть в прошлого героя)."""
    pool = list(_SUPERHERO_POOL)
    if not pool:
        return ("Marvel", "Spider-Man")
    prev = _SUPERHERO_LAST_BY_USER.get(int(user_id))
    if prev is not None and len(pool) > 1:
        candidates = [p for p in pool if p != prev]
        if not candidates:
            candidates = pool
    else:
        candidates = pool
    picked = random.choice(candidates)
    _SUPERHERO_LAST_BY_USER[int(user_id)] = picked
    return picked


def _ready_idea_requirement_line(*, title: str, photos_required: int) -> str:
    """Одна строка под описанием идеи: что нужно для запуска."""
    pr = int(photos_required)
    if pr == 0:
        return "Нужно для запуска: только текст"
    if _ready_idea_needs_photo_then_text(title):
        if pr == 2:
            return "Нужно для запуска: 2 фото и текст"
        return "Нужно для запуска: 1 фото и текст"
    if pr == 2:
        return "Нужно для запуска: 2 фото"
    return "Нужно для запуска: 1 фото"


_READY_SINGLE_PERSON_HINT_EXCLUDED_TITLES = {
    "На отдыхе в Италии",
    "Polaroid и занавеска",
    "Чёрный студийный",
    "Бордовый кино-портрет",
    "Постер с текстом",
    "GTA Vice City",
    _GTA_V_REALISM_TITLE,
    "Двойная экспозиция: вороны",
    "Sony Ericsson T100",
    "Мел на асфальте",
}


def _ready_idea_recommendation_line(*, title: str, photos_required: int) -> str:
    """Рекомендация к карточке идеи для более стабильного результата."""
    t = (title or "").strip()
    if not t or t in _READY_SINGLE_PERSON_HINT_EXCLUDED_TITLES:
        return ""
    if int(photos_required) <= 0:
        return ""
    return (
        '<blockquote><i><tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> Рекомендуется фото, где в кадре только один человек — '
        "так результат обычно получается точнее и качественнее.</i></blockquote>"
    )


# title, preview, prompt, photos_required
READY_IDEA_ITEMS: dict[str, list[tuple[str, str, str, int]]] = {
    "memes": [
        (
            "Absolute Cinema",
            "Культовый студийный портрет в духе «Absolute Cinema»: ч/б, мощный взгляд, руки вверх и винтажная плёночная фактура.",
            "Create a studio portrait in iconic \"Absolute Cinema\" meme style. IMPORTANT REFERENCE MAPPING: image #1 is the USER identity reference; image #2 is the Absolute Cinema style/composition pose reference. CRITICAL IDENTITY LOCK: preserve the exact person from image #1 (face structure, age cues, skin texture, hairstyle/hairline, expression character). Replace identity in the reference composition with the user while keeping the recognizable Absolute Cinema setup. OUTFIT RULE (mandatory): the person must ALWAYS wear a formal suit jacket in the final image (classic dark tailored suit look). This is mandatory even if image #1 is face-only / head-only / tight portrait. If body/clothes are missing in input, infer a realistic full upper body in suit with natural neck/shoulder transition. POSE RULE (mandatory): seated on a sofa/couch exactly like the reference mood, frontal composition, both hands raised at shoulder level (\"hands up\" gesture), calm stoic expression, no pose drift. PROPORTION LOCK (very important): realistic human anatomy and perspective — normal head-to-shoulders ratio, natural neck thickness, believable torso width, and full-size adult hands with correct palm/finger length. Do not enlarge the head or shrink hands. Keep arm length, shoulder width, elbow position, and wrist size physically plausible for a seated person. STYLE: dramatic monochrome (black-and-white) studio chiaroscuro, sculpted key light + rich shadow depth, subtle analog film grain and mild vintage texture, clean symmetrical framing. TYPOGRAPHY: bold white all-caps text at the bottom in two lines exactly: line 1 \"ABSOLUTE\", line 2 \"CINEMA\"; centered, meme-like proportion, no extra text. RESULT: photorealistic, iconic, reaction-meme-ready image with consistent posture and proportional anatomy. NEGATIVE: no suit, casual clothes/hoodie/t-shirt, standing pose, wrong hand pose, wrong couch setup, cartoon look, painterly style, color image, big-head small-hands distortion, warped anatomy, extra fingers, wrong face identity, misspelled text, watermark, logos, extra captions.",
            1,
        ),
    ],
    "appearance": [
        (
            "Густая борода + усы",
            "Добавь густую объёмную бороду и усы, естественно подогнанные под твой цвет волос.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of identity. Preserve exact face geometry, skin texture, age cues, hairline, and recognizable likeness — no face replacement, no beauty retouch drift. TASK: add a dense, full, well-shaped beard with a connected thick moustache (barber-level grooming), while keeping realism and natural follicle detail. COLOR MATCH RULE (mandatory): beard and moustache color must be automatically matched to the user's natural hair color from the reference (including undertone and darkness level), with subtle natural variation at roots/tips; no random black/orange tint if it does not match hair. If scalp hair is partially visible, infer closest realistic match from brows and hair roots. BLEND RULE: facial hair must emerge naturally from skin with correct growth direction, density gradients, and believable transitions on cheeks, jawline, chin, and upper lip. Keep pores and skin detail visible; avoid painted-on look. STYLE: photorealistic portrait, clean lighting, sharp detail, modern editorial/barbershop quality. UNISEX: if the person presentation is masculine, apply full beard + moustache as requested; if feminine or ambiguous, still keep identity unchanged and apply a tasteful dense beard/moustache transformation in the same realistic style without caricature. NEGATIVE: fake pasted beard, wrong beard color, patchy low-density stubble, cartoon beard, plastic skin, warped jawline, extra facial features, text overlays, watermark.",
            1,
        ),
        (
            "Смена пола",
            "Трансформация внешности: мужчина → женский образ, женщина → мужской образ, с сохранением узнаваемости.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY identity source. Preserve recognizable core likeness (eye spacing, nose base, mouth shape, jaw geometry, skin texture, age cues) so the transformed result is still clearly the same person. GENDER-SWAP RULE (strict): if the reference appears male-presenting, transform into a realistic female-presenting version; if the reference appears female-presenting, transform into a realistic male-presenting version. TARGET LOOK WHEN MALE -> FEMALE: longer natural-looking hair (medium or long), cleaner softer facial lines, no beard/moustache, refined skin texture, expressive but realistic eyes, harmonious feminine styling while keeping identity. TARGET LOOK WHEN FEMALE -> MALE: shorter haircut, natural stubble/light beard shadow, stronger and slightly rougher facial structure cues (jawline/brow), masculine styling while keeping identity. STYLE: photorealistic portrait, realistic anatomy, coherent lighting, no caricature, no exaggerated filters. Keep wardrobe neutral and plausible for the transformed presentation, without costume-like stereotypes. NEGATIVE: different person, over-airbrushed plastic skin, cartoon/anime style, face warp, extreme beauty filter, grotesque distortion, extra facial features, text overlays, watermark.",
            1,
        ),
    ],
    "outfits": [
        (
            "Красивый костюм с букетом",
            "Букет полевых цветов, луг и небо, и ты в красивом костюме.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve face structure, skin texture, age, hair — realistic and recognizable; no face swap, no plastic skin. If the reference is face-only or head-and-shoulders: infer full body proportions consistent with the face. If full body is visible: keep body type coherent. EXPRESSION: neutral, serious, direct eye contact with camera. OUTFIT (unisex): refined stone-beige linen summer tailoring — male-presenting: slim-fit blazer and trousers, crisp white shirt, top button open, no tie; female-presenting: same palette — tailored beige blazer with trousers or skirt, or an elegant minimal dress in stone beige / cream, equally editorial. Relaxed textured linen look. PROP: large dense wildflower bouquet held with both hands at waist height — white peonies or roses, tall blue delphinium stalks, baby's breath, dried grasses, long green stems visible. POSE: standing upright, centered, full body in frame. CAMERA: low angle from knee height looking upward; subject reads tall against open sky; meadow fills lower frame. SETTING: vast wildflower field (white, yellow, purple blooms), flat arid plain, distant brown rolling hills, bright blue sky with wispy clouds. Bright natural daylight, soft shadows. STYLE: fashion editorial, serene mood. TECH: Kodak Portra 400 feel, slight natural grain optional, 35mm wide lens, f/5.6, subject sharp, high resolution. NEGATIVE: oversaturated, HDR, heavy retouching, plastic skin, warped proportions, lens flare, vignette, digital artifacting, Telegram username text, watermark.",
            1,
        ),
        (
            "Gucci editorial",
            "High-fashion: Gucci-эстетика, смелые принты, драматичный свет, кадр как обложка Vogue.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve facial structure, skin texture, age, hair — recognizable and natural; no face replacement, no plastic doll skin. INPUT CROP: If face-only or head-and-shoulders, infer full body with proportions matching the face; if full body is shown, keep silhouette coherent. High-fashion editorial inspired by Alessandro Michele–era maximal Gucci runway mood — NOT an official ad; do not reproduce exact logos, GG monograms, or trademark prints; use original dense floral, geometric, and jewel-tone prints in that spirit. UNISEX: adapt layering and silhouette to apparent gender from the reference while keeping the same maximalist eclectic mix. LOOK: fierce, intense gaze; optional oversized square gradient-tint sunglasses; silk headscarf tied under chin; cream shirt with bold original floral print; dark velvet waistcoat; structured jacket with bold geometric diamond/grid pattern draped on shoulders; chunky pearl statement necklace. SETTING: minimalist grey studio, soft directional light and clean shadows; optional velvet armchair, white plinth with open large-format book or magazine (generic artwork, no readable logos). Vogue-style composition, cover framing. LIGHTING: dramatic directional light, cinematic shadows, subtle rim; glossy editorial skin with real pores (no waxy CGI). CAMERA: confident pose, ultra-sharp eyes, shallow depth of field, rich cohesive color grading. TECH: ultra-detailed, very high resolution; avoid fake HDR halos. NEGATIVE: readable brand logos, watermark, Telegram username text, oversmoothed skin, warped limbs, extra fingers, cluttered background, cheap CGI.",
            1,
        ),
        (
            _LUXURY_TORN_COVER_TITLE,
            "Будто ты уже на обложке модного журнала: дерзкий визуал, люксовый вайб и кадр, который хочется сохранить в галерею.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve exact face geometry, skin texture, age cues, and hairline — no face replacement. Build a premium fashion magazine cover composition with a torn-paper collage effect. Main concept: one person appears as a layered collage where torn paper strips reveal multiple aligned fragments of the same face (eyes / mid-face / lips), with realistic paper fibers and irregular ripped edges like editorial cutout design. Tear layout must look like the provided references: broad horizontal/diagonal ripped bands crossing the portrait, plus one dominant portrait panel and additional close-up fragments, all of the SAME person. UNISEX WITH PRESENTATION LOCK: adapt styling to apparent gender from the reference — male-presenting subject in clearly masculine fashion silhouette; female-presenting subject in clearly feminine fashion silhouette; if ambiguous, use a balanced neutral editorial silhouette. WARDROBE VARIATION RULE: for each generation, vary the outfit pieces while staying luxury editorial (for example denim set, fitted jacket, skirt/trousers, structured top, layered accessories, bag), not the same fixed outfit every time. COLOR HARMONY RULE: wardrobe and accessories must harmonize with the selected background color family (Color2_theme), using matching or complementary tones while keeping a premium fashion palette. FRAMING RULE (strict): subject must be visible not lower than knees (knee-up / three-quarter framing or higher), never tiny full-body in the distance. Background: vibrant monochrome studio backdrop matching the selected cover color family. Layout rules: bold title text on the left side, one luxury brand label at top right, VOGUE + country caption at bottom left. Do NOT add any QR code or barcode anywhere. Keep composition clean and premium, like a real glossy cover. Photorealistic 8K studio quality, glossy print feel, crisp details, controlled highlights, no clutter. STRICT CLEAN OUTPUT: no watermark, no platform logo, no app signature, no generated-by stamp, no random corner marks. NEGATIVE: cartoon style, plastic skin, wrong face, extra people, unreadable typography noise, watermark, QR code, barcode.",
            1,
        ),
    ],
    "locations": [
        (
            "Ростомер",
            "Сверхреалистичный кадр как при полицейском оформлении",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve exactly: facial structure, eyes, nose, lips, skin texture, age, hair. Do NOT replace or beautify the face; no face-swap artifacts. EXPRESSION: the subject must look serious, stern, and neutral — typical police booking photo (no smile, no relaxed or playful look). Only adjust expression toward stern while keeping the same person recognizable. INPUT CROP RULE: The reference may be face-only, head-and-shoulders, or full body. If the crop is tight (face or head-and-shoulders): realistically infer the full body needed for a standard booking layout — match apparent gender, age, and build suggested by the face; coherent proportions. If the reference is full body: preserve body type and integrate into the scene. SCENE: Hyperrealistic police booking / arrest documentation photography. Single image split into two panels: front view | back view of the same person; both standing straight, neutral posture, facing a height-measurement wall with black horizontal lines and height marks. Harsh overhead lighting, documentary crime-photography aesthetic, muted neutral color palette, high contrast, clear forensic sharpness. Realistic skin texture, visible pores, natural body hair where appropriate; no glam retouch. When the body is inferred or shown: lean athletic build may be used if consistent with the face; optional dense alphanumeric tattoos on chest, ribs, arms, and back as part of the booking-sheet visual trope — never obscure the face. TECHNICAL: 35mm lens character, high resolution, no motion blur, no depth-of-field softness, no artistic filters or beauty filters. NEGATIVE: European vacation street, yacht, golden-hour romance, soft glamour lighting, illustration, cartoon.",
            1,
        ),
        (
            "На отдыхе в Италии",
            "Кинематографичный кадр на белой яхте у побережья Амальфи, мягкий закатный свет.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the only source of identity. Keep face and hair unchanged and realistic: same facial structure, skin texture, age, and expression. No face swap artifacts, no beautification, no plastic skin. BODY PROPORTION LOCK (strict): always keep anatomically correct and natural full-body proportions (head-to-body ratio, shoulders, torso, arms, hands, legs, knees, feet), with no stretched limbs, oversized hands, tiny legs, or distorted perspective. SKIN MATCH LOCK (strict): for all visible body parts (arms, forearms, hands, legs, knees, feet, neck), match the user's real skin tone, undertone, texture, age cues, freckles/moles, and natural tan level from the reference; avoid mismatch between face and body. If user looks like a young male in reference, preserve youthful male skin details consistently across face and body. GENDER / OUTFIT RULE (unisex): infer apparent gender presentation from the reference. If female-presenting — elegant Mediterranean summer yacht look (e.g. light linen dress, blouse with tailored shorts/skirt, or refined resort co-ord); palette: cream, white, soft blue, citrus accents. If male-presenting — navy or slate linen shirt worn partly unbuttoned with rolled sleeves, white linen shorts, Riviera style. If ambiguous — neutral refined resort wear matching face and build. POSE & ENERGY (mandatory): avoid a stiff catalog pose or blank stare into the lens — show a candid, living vacation beat: soft laugh, hair moving in the breeze, turning toward the coast, lifting or sipping a drink, adjusting sunglasses or a hat, relaxed hand gestures, leaning back with one arm along the rail, or mid-conversation body language; gaze may be three-quarter, toward the horizon/sea, or briefly at the camera as if a friend took the shot — not a passport photo or frozen mannequin. SCENE AESTHETIC (target look): cinematic medium-wide shot (not close-up); subject on cream leather yacht seating with piping (lounging naturally, not rigid), polished chrome railings; calm sea with a bright golden sun-path reflection on the water; steep Amalfi-style hillside behind with pastel Mediterranean buildings; golden-hour sky (warm orange-gold) with soft rim light on the subject. Composition: subject centered, horizon in upper third. Lighting: warm sunset, natural specular highlights on water, realistic contrast. Final: luxurious peaceful summer vibe, photorealistic, high detail, polished.",
            1,
        ),
        (
            "Самолёт бизнес-класс",
            "Живой кадр в бизнес-джете: тёплый свет, спокойная уверенность, атмосфера дорогого перелёта без постановки.",
            "CRITICAL IDENTITY LOCK: Use uploaded photo #1 as the ONLY identity source. Preserve the same person with high fidelity: facial structure, skin texture, age cues, hairline, and expression style. STRICT PHOTOREALISM ONLY: candid iPhone-style private-jet cabin photo, no AI-art look, no illustration, no stylization. UNISEX MANDATE (strict): build the scene so it works equally for any gender presentation; no masculine-only or feminine-only assumptions. Keep pose, framing, and styling universal, and adapt fit/details naturally from the reference person only. Scene: natural unposed moment inside a premium private business jet during golden hour. Perspective should feel like a real handheld social-media story frame, slight authentic imperfection allowed (micro blur / grain), but identity remains clear. Subject: the same person reclining naturally in a cream leather diamond-stitched seat, one leg crossed over the other, one hand holding a low glass with ice (or resting naturally on the armrest), the other resting on lap. Gaze relaxed and confident toward camera with subtle, unforced expression. Wardrobe (UNISEX): clean premium dark look adapted to apparent presentation from the reference (e.g., fitted black crewneck tee or dark minimal top, dark tailored jeans or trousers, clean white sneakers). Optional accessories: subtle chain or minimal watch/jewelry, kept realistic and not gender-stereotyped. Cabin details: polished dark wood side table, open laptop, stack of documents/folder, brown leather duffel on nearby seat, oval jet windows with warm sunlight beams creating defined highlights on leather, wood, metal, glass, and skin. Lighting: strong natural late-afternoon sun rays from windows, high-contrast but plausible; no fake neon. Camera feel: iPhone 14 Pro candid documentary look, native processing, minimal edit, raw-file vibe. Mood: wealthy but understated, honest, grainy realism, like shot by a friend. NEGATIVE: gender-stereotyped styling forced against the reference, text overlays, logos/watermarks, cartoon look, beauty retouch, plastic skin, uncanny face, extra fingers/limbs, fake CGI cabin, over-stylized cinematic VFX.",
            1,
        ),
        (
            "Бекрумс",
            "VHS-кадр в Backrooms.",
            "CRITICAL IDENTITY LOCK: keep the user face realistic and recognizable. Create a found-footage VHS style frame in Backrooms Level-0 atmosphere: endless yellow-beige wallpaper with subtle repeating pattern, low-pile tan carpet, long empty corridors, sickly fluorescent ceiling panels, liminal uncanny mood, low-fi analog noise, scan lines, mild chromatic aberration, tape artifacts. The user looks toward the camera, dynamic mid-motion (found-footage), slight playful energy — not a stiff studio pose. WARDROBE (unisex): if input is head-only or tight portrait, dress the body in a bright yellow industrial coveralls / boiler suit (same garment for any gender); if full-body reference is visible, keep the user's original outfit. Camera: slight fisheye (~120° FOV), tilted, camera slightly below eye level. Add white VHS date/time stamp overlay in the lower-left corner (1990s camcorder style). Keep photorealistic subject integration with authentic VHS degradation.",
            1,
        ),
    ],
    "celebrities": [
        (
            _RONALDO_PHOTO_TITLE,
            "Динамичный кадр матча с Роналдо на поле — 9:16, кинематографичный спорт.",
            "CRITICAL IDENTITY LOCK: use the face from the uploaded image only — photoreal, recognizable; do not warp the user's face or blend it with Cristiano Ronaldo. "
            "UNISEX KIT (mandatory): infer gender presentation from the upload. "
            "Male-presenting: standard men's Real Madrid home kit with normal pro football shorts. "
            "Female-presenting: authentic women's Real Madrid home kit — same white jersey, Emirates FLY BETTER sponsor, number 9, captain's armband, "
            "with women's football shorts (or fitted athletic/cycling-style shorts) and boots; natural female athletic proportions. "
            "If ambiguous: neutral professional kit that fits the body naturally. "
            "A realistic action photo of a soccer match featuring the user (use face from uploaded image). "
            "The person is wearing a white Real Madrid FC home jersey with the Emirates FLY BETTER sponsor, number 9, and the captain's armband. "
            "They are running to tackle the ball from Cristiano Ronaldo, who is playing for Al Nassr on a grass field. "
            "Cristiano Ronaldo is wearing a yellow and blue Al Nassr jersey with the KAFD sponsor. "
            "The background is a stadium full of blurred spectators. "
            "The style is cinematic sports photography with motion blur effects and sunlight on the field. "
            "The photo should be highly realistic, a professional sports photo. The photo ratio should be 9:16 portrait. "
            "Keep Cristiano Ronaldo clearly recognizable and realistic. "
            "NEGATIVE: watermark, random text overlays, wrong kit colors, identity swap, cartoon look, distorted user face.",
            1,
        ),
        (
            _MELLSTROY_PHOTO_TITLE,
            "Попал на скрытую тусовку к Мелу.",
            "CRITICAL IDENTITY LOCK: image #1 (uploaded by user) is the ONLY source of the user's identity. Keep face structure, skin texture, age cues, hairstyle, and facial expression recognizable with high-fidelity detail. USER FACE IDENTITY LOCK (ABSOLUTE): preserve the user's exact identity from image #1 with no changes to core facial geometry: jawline, cheekbones, eye shape/spacing, nose shape/length, lip shape, forehead proportions, eyebrow structure, skin tone undertone, and age markers must remain the same person. No beautification, no face enhancement that changes identity, no 'lookalike' replacement. USER FACE QUALITY LOCK (strict): prioritize the user face above all elements; render it sharp, natural, high-detail, with realistic skin texture and clean eyes; avoid blur, over-smoothing, plastic skin, or stylized retouch. REFERENCE ORDER LOCK (strict): image #1 = user, image #2 = Mellstroy, image #3 = cat, image #4 = Foga. Never swap or reinterpret this mapping. COMPOSITION (strict): photorealistic scene where the user and Mellstroy stand side by side in one frame; both must be visible not lower than knees (knee-up or fuller), centered, natural perspective, no pose replacement. The user is holding the cat from reference image #3 in their hands (natural size and pose). Add Foga from reference image #4 peeking from behind a corner/wall edge in the background. FOGA LOCK (strict): do NOT redesign or stylize Foga — keep the same appearance and proportions as in reference image #4. MELLSTROY FACE LOCK (ABSOLUTE): never alter Mellstroy's face in any way; no face replacement, no face mixing, no morphing, no beautification, no age shift, no expression rewrite, no random substitute person — Mellstroy's face must stay exactly as in reference image #2. GLOBAL FACE CONSISTENCY LOCK: everyone keeps their own face only; no identity transfer between people, no blending one person's facial features into another. APARTMENT ENVIRONMENT LOCK: keep the same premium apartment vibe (luxury modern suite style) and improve interior realism/detailing: refined warm ambient lighting, realistic lamps, curtains, textured walls, polished wood/metal surfaces, coherent depth and perspective, and clean high-end decor without clutter. CAMERA LOOK: shot as if captured on iPhone 14 Pro, realistic smartphone optics, natural dynamic range, subtle handheld realism, crisp detail, and authentic mobile photo processing. Keep all subjects coherent in one realistic environment with natural perspective, shadows, and lighting. UNISEX: adapt user's clothing fit to their gender presentation from image #1. NEGATIVE: user face drift, user lookalike instead of exact identity, changed jawline, changed eye shape, changed nose shape, face swap errors, wrong reference mapping, random man instead of Mellstroy, identity mix, face morph, low-detail face, blurry face, over-smoothed skin, cropped bodies below knees, extra people, cartoon style, changed Foga face, watermark, text overlays.",
            1,
        ),
        (
            "Переговоры с Путиным",
            "Ты сидишь в кабинете Путина на официальных переговорах.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the user's face 100% unchanged and realistic: same facial structure, skin texture, age, and expression. Create a photorealistic formal negotiation scene inside Vladimir Putin's office: Vladimir Putin and the user are seated at a negotiation table facing each other in a calm diplomatic meeting setup. Wardrobe requirement: the user must wear a formal official business suit (classic dark suit, white shirt, tie). Preserve natural human proportions, realistic skin texture, authentic office lighting, detailed interior, clean composition, and professional documentary photo style.",
            1,
        ),
        (
            "Победа над Мухаммадом Али на ринге",
            "Выпал шанс прославиться на ринге, как Мухаммад Али, но ты его выиграл...",
            "Create a highly photorealistic boxing match result scene inspired by a real sports photo. IMPORTANT REFERENCE MAPPING: image #1 is the ONLY attached reference — the USER identity (face, skin, age, hair). There is NO second reference image: synthesize Muhammad Ali from this text only — lean heavyweight build, 1960s–70s iconic boxer look, short hair, expressive face, realistic sweat and glove marks; photoreal recognizable likeness, not a random extra. CRITICAL USER IDENTITY LOCK (strict): preserve the user's face from image #1 with maximum fidelity — same facial structure, eyes, nose, lips, skin texture, age; no warping, no caricature, no face blend with the opponent, no beauty-filter drift. CRITICAL OPPONENT LOCK: Muhammad Ali must look like himself (era-accurate), clearly distinct from the user, both faces fully visible. Final moment: the user is the winner and Muhammad Ali is the loser. Composition should look like an authentic post-fight ring photo with a referee between fighters raising the user's hand. Arena environment must feel premium and massive: an enormous sold-out stadium packed with thousands of cheering spectators, mostly dark surroundings, and powerful cinematic spotlights/floodlights cutting through the darkness and focusing on the ring like a world-title mega event. Add realistic light beams, subtle haze, dramatic contrast, and elite pay-per-view broadcast atmosphere. No country flags, no national symbols, no flag patches on outfits. Keep natural body proportions, realistic gloves and uniforms, documentary sports photography style, and clean high-detail realism.",
            1,
        ),
        (
            "UFC: лицом к лицу с Макгрегором",
            "Промо как перед боем: ты и Конор в профиль нос к носу, Дана чуть сзади, баннер UFC и софиты — напряжение до щелчка.",
            "Cinematic ultra-high-quality photograph of an intense face-off stare-down at a major UFC-style promo event. STRICT PROFILE COMPOSITION: powerful intimate close-up, both subjects in perfect side profile at eye level, 50mm portrait-lens perspective, natural perspective, hyperrealistic 8K, subtle cinematic film grain, shallow depth of field emphasizing the eye-to-eye confrontation. LEFT PROFILE (viewer's left): the USER from the uploaded photo — CRITICAL IDENTITY LOCK: preserve all unique facial traits, bone structure, skin texture, skull shape, and hair from the reference; unshakable concentration and stern resolve; stylish premium black crewneck sweater. RIGHT PROFILE (viewer's right): Conor McGregor — photoreal recognizable likeness: signature fade haircut, thick groomed reddish-brown beard; iconic purple check-pattern suit jacket and white dress shirt; equally tense challenging expression, eyes locked on the opponent. PROXIMITY: faces maximally close, noses almost touching at the horizontal center of frame; the narrow gap between faces is the sharpest focal plane. BACKGROUND MEDIATOR: slightly behind and between them, Dana White partially visible, concerned attentive expression, softened by shallow depth of field. SETTING: realistic detailed but softly blurred large UFC-style promo banner with readable generic event text including \"UFC\", \"FIGHT NIGHT\", \"LAS VEGAS\"; distant silhouettes of broadcast cameras and studio equipment. LIGHTING: dramatic high-contrast studio/key lights sculpting cheekbones and emotion on both subjects; deeply cinematic look; highly detailed skin pores and subtle sweat beads. NEGATIVE: frontal faces, cartoon, plastic skin, wrong user face, extra people in focus, watermark, mangled logos as unreadable blobs.",
            1,
        ),
    ],
    "for_two": [
        (
            "Для влюбленных: рыцарь и дама",
            "Романтическая сцена на закате.",
            "IMPORTANT REFERENCE MAPPING: image #1 is the knight identity (male), image #2 is the woman identity (female). CRITICAL IDENTITY LOCK FOR BOTH: preserve both faces with high fidelity (facial structure, eyes, nose, lips, skin texture, age) and keep them clearly recognizable. HAIR LOCK FOR BOTH: if hair is visible in the reference photos, preserve each person's hairstyle, hairline, hair length, and natural hair color (do not replace with generic fantasy hair). Create a romantic cinematic portrait: open grassy field at golden hour, tall dry grass, warm golden mist and soft tree line in the distance. A brown horse with a white blaze stands between and slightly behind the couple, dark leather saddle and bridle. The knight (image #1) wears polished steel plate armor with breastplate, pauldrons, gauntlets, chainmail visible at edges — no helmet, face clearly visible. The lady (image #2) wears a long flowing cream medieval dress with embroidered trim and a sheer veil catching the light. The couple holds hands at center frame (his armored hand and her bare hand). Knight armor: highly detailed, realistic polished metal, leather straps. Mood: warm directional sunlight, soft glow on veil and armor reflections, shallow depth of field, background softly blurred. Cinematic fantasy romance, photorealistic, sharp focus on subjects, ARRI Alexa look, 85mm lens, high resolution.",
            2,
        ),
        (
            "Love is…",
            "Вкладыш жвачки Love is.",
            "IMPORTANT REFERENCE MAPPING: image #1 is the man identity, image #2 is the woman identity. CRITICAL IDENTITY LOCK FOR BOTH: preserve both faces with high fidelity (facial structure, eyes, nose, lips, skin texture, age) and keep them clearly recognizable. Create a Love is gum wrapper insert using both uploaded faces. On the illustrated card: classic hand-drawn Love is strip style — soft lines, soft warm pastel palette, light blue wash background behind the couple scene; thin black border around the artwork. Top left on the card: logo text \"love is…\" in the iconic bold style. Top right: two small red hearts. Bottom: a short touching funny Russian caption in handwritten cursive feel, authentic Love is tone. Flat lay: the card rests on a weathered light wooden table; scattered translucent red heart-shaped gummy candies; one or two sticks of pink bubble gum on white paper wrappers at corners. Photorealistic still-life of the table scene; the cartoon illustration ON the card shows the couple matching image #1 (man) and image #2 (woman) in a cozy everyday romantic moment.",
            2,
        ),
        (
            _POLAROID_CURTAIN_TITLE,
            "Два фото любых «вдвоём» — как на старом Polaroid: мягкая вспышка в темноте, лёгкая размытость, за спинами белая занавеска. Порядок снимков не важен.",
            "IMPORTANT REFERENCE MAPPING: image #1 is the first subject reference, image #2 is the second subject reference — two people, two animals, or a mixed pair; upload order is arbitrary (first vs second photo does not assign roles beyond #1/#2 mapping). CRITICAL IDENTITY LOCK FOR BOTH: preserve each subject faithfully — for humans: facial structure, skin texture, age, hair, expression; for animals: species, markings, fur pattern, pose cues — both must stay clearly recognizable. OUTPUT STYLE: authentic Polaroid instant-film snapshot look — casual ordinary photo, no sharp dominant prop or staged object as the hero of the frame; natural two-subject composition. LIGHTING: soft even illumination across the entire frame, like a compact camera flash in a dark room — flash falloff spreading gently over the whole image, not a harsh spotlight; subtle overall softness / mild blur consistent with small-format instant film and slight motion or vintage lens character. BACKGROUND: replace everything behind both subjects with a plain white curtain / white drape — seamless, neutral, no busy details; subjects separated from curtain by natural distance. MOOD: intimate simple snapshot of two subjects together. NEGATIVE: swapped identities, beauty-face replacement on humans, crisp HDR studio backdrop, extra subjects, readable text overlays, heavy vignette that hides faces.",
            2,
        ),
    ],
    "texts": [
        (
            _POSTER_TEXT_READY_TITLE,
            "Вертикальный постер: ты в кадре + надпись в твоём стиле. После фото введи текст заголовка — подстроим цвет и фактуру под картинку.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity for the main subject. Preserve face structure, skin texture, age, eyes, and expression — integrate the person naturally into a stylized high-end 3D/CG poster (NOT a flat template). ART DIRECTION: vertical poster composition, hero subject from reference, cinematic lighting, smooth detailed shading, atmospheric depth, designer splash-screen quality. Environment: rich moody palette with strong accent lights (e.g. emissive glow blobs, rim light, neon-tinged highlights) that can inform the headline treatment. The exact headline string and typography integration rules are specified in the reference hint below — follow them exactly. NEGATIVE: flat clipart, cheap social-media template, tiny illegible text, random watermark, wrong face, beauty-plastic skin.",
            1,
        ),
        (
            _FLUFFY_LETTERS_TITLE,
            "Гигантские пушистые 3D-буквы с мордочками и ты впереди в таком же костюме. Сначала фото, потом слово или короткая фраза для букв.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of the person's facial identity. Preserve the face 100% photorealistic: same facial structure, skin texture, eyes, age, and expression — no stylized skin, no replacement face, no fur mask covering the face. The fuzzy costume must leave the real human face fully visible (open face / face opening in the hood), only the body is in plush fur. SCENE QUALITY: premium 3D CGI, soft cinematic studio lighting, tactile high-detail fur strands, Pixar/DreamWorks-style polish. FOREGROUND: the same person from the reference, full body in frame, wearing a full-body fluffy mascot suit whose color and fur texture visually match the giant letters behind (one cohesive whimsical palette). POSE (mandatory): star pose / jumping-jack — arms raised high and spread wide, legs spread wide, feet on the ground, playful energy, centered, camera at full-body height. BACKGROUND: a large horizontal row of volumetric 3D letterforms. Exact spelling and letter styling are specified in the reference hint below — follow them exactly. NEGATIVE: 2D flat text, human face covered by fur, wrong identity, extra random people, watermark, readable UI.",
            1,
        ),
        (
            _PLASTER_FASHION_STUDIO_TITLE,
            "Премиум fashion в чистом светлом зале: ты в дорогом костюме у массивных объёмных букв из матового гипса — как съёмка для обложки. Потом придумаешь слово или короткую фразу, и она станет скульптурной надписью рядом с тобой; при желании — два яруса текста, верх и низ.",
            "CRITICAL — FACIAL IDENTITY: The uploaded photo is the ONLY source for the model's appearance. Transfer face without alteration: preserve sex, age, bone structure, skin texture, hair, and all individual traits; no face replacement, no beautification drift. UNISEX WARDROBE: elegant tailored trouser suit (pantsuit) — if male-presenting, classic slim-fit suit with trousers; if female-presenting, refined women's pantsuit or equivalent elegant tailored suit; if ambiguous, neutral premium tailoring that matches the face. MAIN COMPOSITION: stylish subject in relaxed pose, shoulder casually leaning against massive freestanding 3D letter sculptures; thoughtful, slightly mysterious expression; body organically integrated into the scene, subtle sense of pressing into the textured plaster surface of the letterforms. TYPOGRAPHY (mandatory): volumetric words built from matte gray plaster — exact spelling, language, line breaks, and capitalization are specified ONLY in the runtime reference hint below from the user's typed text (not fixed placeholder words). Letters are separate physical objects standing in space (NOT wall-mounted), massive block constructions; if two lines are requested, use two stacked tiers overlapping as a layered installation; soft natural cast shadows. TECH: ultra-detailed photorealistic photography, masterpiece image quality, tack-sharp eye focus on the model's face, professional color grading, top-tier studio lighting, 8K feel. DEPTH: shallow depth of field — face in sharp focus; plaster word sculpture slightly softer but text must remain fully legible. AESTHETIC: premium fashion-editorial minimalism, clean background, luxury modern magazine cover mood, contemporary fashion photography. NEGATIVE: wrong face, different age or gender than reference, flat 2D text stickers, letters fused into a flat wall, illegible text, watermark, cluttered background, website URLs or brand watermarks on the letters.",
            1,
        ),
        (
            _FANTASY_3D_GAME_TITLE,
            "Название, от которого хочется нажать «Играть»: громкий логотип, магия в кадре и настроение настоящего трейлера.",
            "Cinematic fantasy PC/console game title / key-art generator. Ultra-bold three-dimensional logotype with sharp aggressive beveled edges; letters feel magically forged from stone or metal; glowing cracks where inner light leaks; floating magical runes and spark particles harmonized with the PRIMARY color theme. Centered composition — the headline dominates; atmospheric blurred background (no busy readable props stealing focus). NO subtitle lines, NO extra UI text, NO watermark — only the main title lettering as the hero subject. Typography: heavy tactile 3D volume, premium game branding quality. Style families supported: dark fantasy void, epic heroic gold, bright emerald adventure, mystic arcane cyan/blue — the model must choose MOOD and WORLD_STYLE that fit the user's HEADLINE tone plus the BASE COLOR family (see runtime hint). NEGATIVE: flat 2D text sticker, thin fonts, subtitle clutter, mockup frames, studio watermark, illegible tiny letters.",
            0,
        ),
    ],
    "movies": [
        (
            "Хоумлендер и Бутч",
            "Селфи с двумя героями сериала прямо во время съёмок.",
            "Ultra-realistic 9:16 iPhone selfie photo, wide-angle front camera, taken by me, behind-the-scenes movie set photography style, natural daylight, cinematic atmosphere, high dynamic range, true-to-life colors. Use the attached photo as the exact face reference for ME: same facial features, skin texture, haircut, proportions, no beautification. Location: behind-the-scenes set inspired by a major superhero production, film cameras, lights, green screen, crew members, trailers in background. Scene: friendly backstage selfie during filming. We are standing close together and taking a selfie: • On my left — Homelander, in his signature superhero suit, blonde hair, natural makeup/skin look, confident expression. • In the middle — Me, holding the phone, smiling confidently. • On my right — Butcher, rugged look with stubble, dark coat/jacket in his signature style. We are laughing and smiling naturally, friendly and relaxed. Clothing style: • Homelander: full superhero suit, realistic texture, no mask. • Butcher: dark trench coat / jacket, gritty realistic styling. • Me: infer from my reference photo — if male-presenting, elegant classic suit, white shirt, dark trousers, leather shoes; if female-presenting, stylish feminine look suitable for set (e.g. blouse or shirt with skirt or tailored trousers, optional light jacket), natural and coherent with my face reference. Body language: • Me holding the iPhone with one hand • Homelander leaning slightly toward me • Butcher smiling and posing casually • Natural selfie posture. Photography style: shot on iPhone 15 Pro Max, HDR, 4K quality, realistic skin texture, soft shadows, shallow depth of field, no filters, no AI artifacts. Background: camera rigs, lighting stands, directors chairs, crew walking, cables, monitors showing scenes, blurred film set environment. Mood: friendly Hollywood backstage moment, fun teamwork, nostalgic superhero vibe, authentic and emotional. Negative prompt: no blur, no distortion, no extra fingers, no cartoon style, no plastic skin, no fake lighting, no wrong faces, no masks.",
            1,
        ),
        (
            "Game of Thrones",
            "Модель сама определит какой дом Вестероса тебе подходит больше!.",
            "Use the uploaded character from the image as the identity reference and place this person into the final scene. Create an ultra-realistic 3D close-up render of the character standing front-facing. Shoot from a low camera angle so the character dominates the frame. Background should be blurred and misty, with cinematic bokeh, light bloom, and soft shadows. Visual quality requirements: exceptional detail, fine skin texture, clearly defined hair roots, strong cinematic lighting, full 3D depth feeling, premium CG texture quality as if made by top-tier 3D artists. Aspect ratio 3:4, high resolution. Do NOT alter face features or hair — keep them 100% unchanged. RANDOM HOUSE RULE: randomly choose exactly one house from this list only: Stark, Lannister, Targaryen, Baratheon. Then style outfit, heraldic details, and color palette strictly according to the selected house.",
            1,
        ),
        (
            "Avatar",
            "Как бы ты выглядел будучи одним из народа Нави.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the only identity reference. Keep face structure, age, skin texture, and hairstyle recognizable and realistic. Transform the user into a highly detailed, photorealistic Avatar-universe character (Na'vi aesthetics, blue skin, cinematic tribal costume design, premium textures). GENDER ADAPTATION RULE: infer presentation from the user photo and choose matching character styling automatically. If the user appears male, use a Jake-inspired warrior costume and masculine silhouette. If the user appears female, use a Neytiri-inspired warrior costume and feminine silhouette. Keep the final result respectful, realistic, and coherent. Camera and mood: slightly low upward-facing angle, dramatic cinematic lighting, high contrast, deep saturated blue background, warm highlights on one side of the face and soft velvety shadows on the other. No props, no extra accessories. Emphasize detailed costume materials, realistic skin texture, controlled color grading, and an editorial close portrait feeling.",
            1,
        ),
    ],
    "superheroes": [
        (
            _SUPERHERO_MIRROR_TITLE,
            "Один эффектный супергеройский кадр — как постер к фильму, где главный герой это ты.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY identity source. Preserve exact face geometry, skin texture, age cues, hairline, and recognizable likeness — no face replacement. Create ONE single highly detailed photorealistic mirror-selfie frame (not a collage), showing the same person in a premium superhero-inspired look. UNISEX MANDATE (strict): adapt body fit, armor tailoring, makeup intensity, and silhouette to the person's presentation from the reference; no gender stereotypes, no caricature. SUPERHERO COSTUME RULE: use one selected hero-inspired suit with high-fidelity practical textures (fabric weave, armor plates, seams, utility belt, boots, gloves, cape if applicable), realistic wear and lighting response; no toy/plastic cosplay look. SCENE: modern apartment/bedroom interior, coherent realistic background and reflections. CAMERA FEEL: believable smartphone mirror selfie, natural hand/phone placement, coherent perspective, soft indoor daylight mixed with ambient room lighting. FRAMING: one strong hero shot with subject large in frame (at least knee-up), clear facial detail priority, premium editorial composition. QUALITY PRIORITY: maximize face fidelity and detail on the user (eyes, skin texture, likeness), then costume materials and lighting realism. STRICT CLEAN OUTPUT: no watermark, no platform logo, no app signature, no generated-by stamp, no random corner marks. NEGATIVE: collage/multi-panel layout, tiny distant subject, blurred face, wrong identity, cartoon/anime style, malformed limbs, unreadable text overlays, watermark.",
            1,
        ),
    ],
    "games": [
        (
            "Minecraft",
            "Последняя фотка перед битвой с драконом в Эндер мире.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the face 100% unchanged and realistic: same facial structure, eyes, nose, lips, skin texture, age, and expression. No face swap artifacts, no beautification, no cartoonization, no pixelated face, no extra facial hair. PROPORTION RULE: Keep natural human head-to-body proportions. Head must not look oversized; keep it slightly stylized but close to realistic proportions, with shoulders/torso visibly dominant in volume. Create a high-quality Minecraft End dimension scene: the user is sitting on top of an obsidian block at the edge of a cliff, looking directly at the camera. Camera angle: top-down, slightly tilted perspective from above. Outfit requirement: the user must wear Minecraft-inspired diamond armor on torso and legs (diamond chestplate + diamond leggings), integrated naturally with the scene. In the background, an Ender Dragon is flying in the sky. Keep the End-world atmosphere (obsidian, void-like depth, dramatic ambient light), with cinematic composition, sharp details, clean textures, and natural lighting integration on the user. Apply End-themed lighting on the user as well: purple-black ambient glow and subtle violet shadows on skin, armor, and clothing, so the user color grading matches the End environment naturally. Final output must look coherent, polished, and artifact-free.",
            1,
        ),
        (
            "Clash Royale",
            "Выпала возможность прочувствовать себя в шкуре элитного варвара.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Keep the face 100% unchanged and realistic: same facial structure, eyes, nose, lips, skin texture, age, and expression. No face swap artifacts, no beautification, no cartoon face, no plastic skin, no added beard or mustache. COMPOSITION: side-by-side full-body shot on a red carpet stone bridge toward a castle arena; warm sunset sky, banners, Clash Royale battle atmosphere. LEFT character: keep the official in-game Clash Royale Barbarian (stylized 3D game look) unchanged. RIGHT character: the user in photorealistic elite barbarian gear — golden horned helmet, spiked wristbands, brown kilt with red belt, barefoot — matching the pose and lighting. POSE: friendly mutual arm-over-shoulder hug with the game barbarian. Same perspective, full-body framing, warm cinematic lighting, slight depth of field, clean textures, natural seamless face integration on the user only.",
            1,
        ),
        (
            "GTA Vice City",
            "Погрузись в криминальный мир городка Vice City.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve face shape, hair, skin tone, and age; convert the person into a GTA Vice City (2002) RenderWare-era playable-character look while keeping likeness readable on the low-poly face. HEAD-ONLY OR TIGHT FACE CROP RULE: If the input shows only the head or a close portrait with no visible body, you MUST invent a full-body Vice City NPC: proportional PS2-era body, simple rig, tropical/1980s Miami casual outfit (shirt, shorts, or era-typical streetwear). Place the character in a clear outdoor Vice City inspired location — palm-lined boulevard, pastel Art Deco buildings, ocean or bay in the distance, Vice City color grading (warm sunset orange–purple haze OR soft neon pink/cyan night accents). Do NOT leave a floating head; the final frame must show the full character in environment. If the input already shows full body, convert the whole figure to the same Vice City mesh style and still set the scene in a classic Vice City street or beachfront. VICE CITY RENDER SPECS: authentic 2002 look — mid-to-low poly geometry, visible edges, flat shading, low-resolution textures, no ray tracing, no modern global illumination, no depth of field, no motion blur, no cinematic bloom. Lighting: match Vice City mood but keep readable PS2-era simplicity. STRICT: no HUD, minimap, health bars, subtitles, or on-screen UI. FORBIDDEN: GTA V realism, ultra HD skin, generic modern Miami stock photo, random unrelated cities. Negative: random Vice City street pasted from real photos, invented HUD, extra random NPCs as main subject, GTA 5 graphics, bokeh, cinematic grading.",
            1,
        ),
        (
            _GTA_V_REALISM_TITLE,
            "Твой кадр в стиле GTA V: поза и композиция сохраняются, а сцена становится игровым реализмом.",
            "CRITICAL IDENTITY LOCK: uploaded photo #1 is the ONLY source of identity. Preserve the same person with high fidelity (face structure, skin texture, age cues, hairline, expression character). Convert the subject into a realistic GTA V in-game character model (Los Santos / San Andreas vibe), not cartoon and not low-poly retro. POSE & COMPOSITION LOCK (strict): keep the same body posture, arm/hand positions, head tilt, gaze direction, and camera angle from the source photo as closely as possible. If any body parts are cropped or missing, reconstruct them naturally while preserving the same upper-body pose and perspective continuity. UNISEX MANDATE (strict): adapt outfit fit and styling to the person's presentation from the reference without gender stereotypes; result must work equally for any gender. LOCATION RULE (conditional): if the input already shows a full-body person in a recognizable real location, preserve that same location and framing, only converting the whole scene into GTA V-style realistic game rendering. If the input is tight/portrait-only or lacks a clear location, synthesize a coherent GTA V environment automatically (street, boulevard, parking lot, rooftop, alley, or beach promenade in Los Santos style) with physically plausible perspective and lighting. RENDER STYLE: GTA V-era realistic game materials — clean topology, believable cloth folds and stitching, natural skin shading, realistic hair cards/strands, subtle wear on clothing, grounded daylight or urban ambient lighting. Keep the output as a single coherent in-engine-like frame with no HUD/UI. NEGATIVE: gender-stereotyped forced look, wrong identity, pose drift, different camera angle, random cinematic bokeh, anime/cartoon style, Vice City 2002 low-poly style, floating head, malformed hands, extra limbs, text overlays, logos, watermark, HUD/minimap/subtitles.",
            1,
        ),
        (
            _MMORPG_HERO_TITLE,
            "Погрузись в мир MMORPG: тёмное фэнтези, эпический герой и дух большой RPG — как в лучших кинематографичных трейлерах.",
            "Premium MMORPG / RPG hero portrait — STRICT PHOTOREAL WARCRAFT LOOK (not cinematic trailer, not game-shader): final result must look like a realistic dark-fantasy photo, as if captured with a real camera in real light, while keeping full Warcraft universe canon for race/class/armor/location. NOT like pre-rendered Blizzard cinematic frame, NOT like in-game engine render, character select screen, splash art, or stylized illustration. Avoid clean \"cartoon CG\" look: no plastic skin, no toy-like metal, no flat gradient lighting, no overly smooth faces. Target: grounded realism with physically plausible lighting, realistic skin pores and microtexture, natural hair strands, believable metal wear (scratches, edge wear, micro-scratches), leather grain, fabric weave, and coherent reflections. CRITICAL IDENTITY LOCK (highest priority): image #1 is the ONLY face/body identity source. The OUTPUT must still read unmistakably as THIS SAME PERSON — even when transformed into Warcraft race traits. Preserve eye spacing/shape, brow-to-eye relationship, nose structure, mouth/lip silhouette, jaw/chin geometry, cheek volume, and age cues; apply race anatomy as a layer, not a replacement face. Do NOT replace with another person; no beautification drift; no generic NPC face template. UNISEX / PRESENTATION: infer apparent gender presentation from the reference and match armor silhouette/proportions accordingly (no default male/female stereotype set). "
            "RANDOM BUILD (mandatory — pick ONE internally consistent set; do not label text on image): "
            "(1) RACE — RANDOM PICK exactly ONE race from this World of Warcraft list only: Orc, Undead, Human, Elf, Gnome, Goblin, Worgen, Draenei, Troll, Void Elf, Nightborne. Do not invent or use races outside this list. Apply race styling as a veneer: tusks/ears/horns/skin tone must conform to the user's facial geometry from image #1, not erase it. "
            "(2) CLASS — RANDOM PICK exactly ONE class from this World of Warcraft list only: Warrior, Mage, Warlock, Paladin, Priest, Rogue, Demon Hunter, Death Knight, Druid, Hunter (Archer). Do not invent or use classes outside this list. Class must be instantly readable from silhouette + gear language + VFX accents. "
            "(3) LOCATION — RANDOM PICK exactly ONE canonical Warcraft-world location mood and commit to it: burning battlefield with embers, frozen citadel approach, moonlit forest shrine, plague-torn gothic city, arcane observatory tower, stormy cliffside fortress, torch-lit throne hall, or desert titan ruins. Keep location language strictly Warcraft-like in architecture, materials, symbols, and atmosphere. Use real atmospheric depth (fog/haze/particles) and physically coherent light interaction with armor. "
            "ARMOR QUALITY MANDATE (very important): avoid generic repetitive armor. Build a unique class- and race-specific Warcraft-grade set with complex layered construction: distinct silhouette, asymmetrical hero pieces, sculpted pauldrons, engraved cuirass, articulated gauntlets, belts/trophies/talismans, cloth+metal+leather mixing, believable wear/micro-scratches, and high material separation. Armor, weapon/focus, and details must read as authentic Warcraft class fantasy (not generic fantasy). Each generation must produce clearly different armor language across classes (e.g., Paladin holy ornate plate vs Rogue segmented leather vs Warlock cursed runic armor). Weapon/focus must match class fantasy and quality tier. "
            "CAMERA / FRAMING RULE (mandatory): realistic camera portrait, natural lens behavior, grounded contrast and color. Subject framing must be either full-body OR from knees-up (American shot). Never crop tighter than knees (no waist-up, chest-up, or close-up only). Keep the character dominant and environment readable. Magic/VFX must feel physically grounded (light interacts with smoke, fog, surfaces) and not like flat neon stickers. "
            "RENDER RULES: realistic key/rim lighting, volumetric atmosphere, grounded glow effects (runes/fel/frost/holy), no over-neon arcade, no cinematic trailer stylization. Image #2 (if present) is Warcraft armor/world quality reference only — do not copy identity/face/body from image #2. "
            "NEGATIVE: cinematic trailer frame look, generic same-looking armor, low-detail primitive armor, mobile-RPG icon style, in-game character-select shader, real-time game engine look, Fortnite/Overwatch-style clean stylization, chibi proportions, exaggerated toon shading, cartoon/anime/painterly styles, flat background with no location story, plastic toy materials, wax skin, chunky hair clumps, floating head only, crop above knees, extra people, readable logos/UI/HUD, watermark text, duplicate faces, wrong identity.",
            1,
        ),
    ],
    "colors": [
        (
            "Оранжевый",
            "Оранжевая фотка с элементами одежды и аксессуаров оранжевого цвета.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY face reference — preserve 100% facial identity: same facial structure, skin texture, age, hairline; no face swap, no different person. UNISEX / LOOK: bold monochromatic orange fashion portrait — same accessory recipe for any gender; infer fit and hair from the reference (natural beard/hair length respected). Framing: tight head-and-shoulders or upper chest, editorial fashion. Wardrobe: bright ribbed knit orange beanie; rectangular translucent orange-framed sunglasses with orange-tinted lenses; textured orange wool coat or blazer with visible lapel; crisp white ribbed turtleneck as contrast under the coat. Background: solid vivid saturated orange wall, seamless, matching the outfit palette. Lighting: soft, even, flattering; emphasize fabric texture and real skin. Mood: cool, direct gaze at camera; modern high-saturation editorial. NEGATIVE: Telegram username text, watermark, plastic skin, beauty blur, wrong face.",
            1,
        ),
        (
            "Чёрный студийный",
            "Черный фон черная фотка, ничего больше.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the only identity source. Preserve exact facial details and natural skin texture — no beauty retouching, no plastic skin, no face replacement. UNISEX: same lighting and wardrobe rules for any gender — dark minimal top (black crew-neck tee, ribbed collar, or equivalent dark simple shirt/jacket) matching apparent build from the reference. Black-and-white studio portrait, tight frontal head-and-shoulders, direct gaze at camera, calm confident mood. Single directional key light (Rembrandt / chiaroscuro): strong contrast, characteristic light triangle on cheek, deep shadows on the opposite side of the face, visible skin texture (pores, natural detail). Background: solid deep black, seamless. Hyperdetailed skin, tack-sharp eyes, shallow depth of field. Premium monochrome studio look, Leica 90mm character (do not render camera, logos, or UI). NEGATIVE: color, soft flat lighting, glam retouch, text overlay, Telegram nickname.",
            1,
        ),
        (
            "Бордовый кино-портрет",
            "Вертикальный кадр: резкий киносвет, контраст, чуть снизу вверх — подбородок и шея, фон — густой тёмно-красный против светлой кожи и тёмной одежды.",
            "CRITICAL IDENTITY LOCK: Use the uploaded user photo as the ONLY source of facial identity — same face shape, features, skin texture, age, hair; no face replacement, no different person. UNISEX: dark minimal clothing (e.g. black or charcoal top, jacket, or tailored dark piece) suited to apparent gender from the reference; keep silhouette elegant and cohesive with the face. COMPOSITION: vertical portrait orientation, editorial fashion / cinema poster quality. LIGHTING: sharp cinematic lighting with strong directional keys and intense contrast — sculpted cheekbones, defined jaw, rich shadow falloff; premium studio or motion-picture still look. CAMERA: slightly low angle looking upward to emphasize jawline and neck, imposing yet refined, sculptural elegance; subject may hold a calm confident gaze toward camera or slightly past lens. BACKGROUND: saturated deep dark red / burgundy seamless or gradient — bold color contrast against lighter natural skin tones and dark wardrobe. ASPECT: portrait ~3:4 or 9:16 feel. NEGATIVE: flat lighting, pastel flat bg, wrong face, beauty-plastic skin, watermark, text, props stealing focus.",
            1,
        ),
    ],
    "art_styles": [
        (
            _SONY_ERICSSON_T100_TITLE,
            "Легендарная T100: одно фото — твоё лицо в 1-бит на зелёном ЖК, сверху строка «добро пожаловать», как на старой кнопочной трубке.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Face shape, features, expression, and distinctive traits must match the reference with maximum fidelity while being converted into retro 1-bit pixel-art on the phone screen — same person, readable likeness at ultra-low resolution. SCENE: Classic candybar mobile phone Sony Ericsson T100 (early-2000s design), photorealistic plastic body and keypad partially visible; main focus on the small LCD screen. SCREEN SPECS (visual simulation): monochrome green LCD palette typical of that era; logical content as if 84×48 pixels — black-and-white / 1-bit style pixel illustration of the character on the display, clearly visible square pixel grid, sharp contrast, no smooth gradients inside the portrait (dithering allowed). On-screen portrait: pixel-art head/face centered like classic phone wallpapers. TOP OF SCREEN TEXT: classic Sony Ericsson UI style banner — narrow black band at top of LCD with bitmap/pixel font; Cyrillic greeting exactly: «добро пожаловать» (legible, fits the narrow display). OVERALL IMAGE: 9:16 vertical composition, nostalgic early-2000s mobile aesthetic, medium framing on the handset, shallow depth of field acceptable on the phone edges. STYLE: ultra-low-res look on the LCD area only; the phone casing may be rendered with realistic detail. MOOD: nostalgic \"old school\" mobile phone wallpaper. NEGATIVE: wrong face, different person, high-res face inside the LCD, modern smartphone, color photo inside the LCD, illegible gibberish instead of the greeting, watermark, UI clutter beyond the one greeting line and the portrait.",
            1,
        ),
        (
            _CHALK_ON_ASPHALT_TITLE,
            "Сначала фото — из него портрет мелом; потом своя надпись разноцветными буквами над рисунком. Лужицы, снег у края, вид сверху как с телефона.",
            "Photorealistic scene. CRITICAL IDENTITY LOCK: The chalk artwork on the ground must faithfully reproduce the people and the full composition from the uploaded reference photo — same faces, poses, body language, and framing; every person must stay clearly recognizable; translate the entire scene into colored street-chalk technique only (no photorealistic humans standing on the asphalt — only the drawing). LARGE chalk illustration on old cracked asphalt: soft slightly smudged lines like real sidewalk chalk; dusty matte chalk colors with authentic chalky texture; light smudge marks and chalk dust around the figures. SURFACE: weathered rough asphalt with visible cracks, small stones, grit; puddles of water, mud, and remnants of wet snow along the frame edges. PROPS: real pieces of colored chalk lying next to the drawing — red, yellow, blue, pink, and white; scattered chalk dust and finger smudges on the pavement. HANDWRITTEN QUOTE on the asphalt directly above the drawing: multicolor chalk by hand (childlike uneven street lettering). The exact characters, line breaks, language, and wording are specified ONLY in the runtime reference hint from the user's typed text — no fixed placeholder quote. CAMERA: from above at a slight angle, as if someone photographs the ground with a phone; natural daylight, soft overcast sky, slightly cool color temperature. ATMOSPHERE: ordinary urban courtyard; high detail on asphalt and chalk; authentic street-art feeling. NEGATIVE: wrong faces, different people than reference, illegible or substituted quote text, photoreal people instead of chalk drawing, watermark, clean studio floor.",
            1,
        ),
    ],
    "horror": [
        (
            "Ступени у огня",
            "Ночь, широкие ступени особняка, рваный белый наряд, грязь и кровь на лице, сигарета, жёсткая вспышка — дом догорает в темноте позади.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve facial structure, skin texture, age, hair, and overall likeness — no face replacement, no different person. UNISEX WARDROBE from reference: if female-presenting — torn, dirty white wedding dress, heavily stained; if male-presenting — torn dirty white formal wedding suit / tuxedo remnants (jacket, shirt) with the same distressed staining; if ambiguous — torn white ceremonial outfit that still reads clearly as post-wedding formal wear. POSE: subject sitting on wide stone steps of a luxurious mansion at night, relaxed but exhausted, slightly slouched; holding a cigarette, visible smoke drifting in cold night air (adult subject). FACE: messy hair; face smeared with dirt, ash, and blood stains; raw natural skin; tired but calm expression with a hint of dark satisfaction. BACKGROUND: burning mansion behind them, mostly lost in deep darkness; faint fire glow and small flames on the ruined building; light smoke and ash in the air. COMPOSITION: centered subject, medium full shot, slightly low angle, shallow depth of field, cinematic framing, portrait ~3:4 feel. LIGHTING: harsh direct on-camera flash on the subject, strong frontal flash, overexposed highlights on skin and clothing, sharp hard shadows behind, deep black shadows around, minimal ambient, slight falloff into darkness, subtle fire glow as secondary rim light. STYLE: ultra realistic, film still, 35mm photography, raw flash photography, high detail, visible skin texture, pores, imperfections, slight noise, high-ISO character; no over-stylization. MOOD: dark humor, survival, relief after chaos, eerie calm, unsettling post-violence silence. NEGATIVE: cartoon, anime, watermark, text overlay, plastic skin, beauty blur, extra limbs, duplicate faces.",
            1,
        ),
        (
            "Двойная экспозиция: вороны",
            "Double exposure: ты в руинах колокольни, вороны взмывают в ночное небо, жёсткая вспышка и абсолютная тьма вокруг.",
            "Aspect ratio 3:4. CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of identity — preserve exact facial structure, skin texture, age cues, hairline, and recognizable likeness; no face replacement and no beautification drift. UNISEX MANDATE (strict): style must work for any gender presentation; no feminine/masculine stereotype lock. Outfit: light summer clothing adapted from the reference person (e.g., light dress, shirt, or lightweight top) with natural fit and realistic fabric behavior. CONCEPT: double exposure portrait where the subject is overlaid with silhouettes of crows bursting upward into the night sky. SCENE: subject stands among old bell-tower ruins, head slightly tilted back, scarf or neck cloth slipped down to neck level, hair blown by wind. LIGHTING: hard direct on-camera flash from the front; background is absolute blackness. EFFECTS: crows cast jagged shadows and motion blur streaks across face and body; heavy underexposure with crushed shadows, while light clothing reads as a vivid color accent spot. LOOK: film grain, heavy vignette, high contrast. CAMERA / FRAMING: frontal waist-up portrait, old iPhone snapshot character, raw imperfect flash rendering. STYLE TAGS: fashion photography, arthouse, cinematic realism. NEGATIVE: cartoon/anime/painterly style, smooth beauty skin, CGI-clean look, extra limbs/faces, text overlays, logos, watermark.",
            1,
        ),
        (
            "Найденная фотка: тоннель",
            "Жуткий raw-кадр из мокрого тоннеля: ты на переднем плане, а в воде за спиной — пугающее существо как практический грим.",
            "CRITICAL IDENTITY LOCK: The uploaded user photo is the ONLY source of facial identity. Preserve exact facial structure, skin texture, age cues, hairline, and expression character — no face replacement, no beauty retouch, no identity drift. This request generates an image in a disturbing horror style, resembling a rough \"found photograph\" shot at night on a cheap early-2000s camera. ALL creature elements must be practical effects only (latex masks/prosthetics/practical makeup/suit performer), not CGI, not clean 3D render, not VFX creature design. COMPOSITION: vertical portrait frame, subject in foreground (mid shot), wet hair strands, damp skin, direct tired stare; subtle panic/fatigue in expression. UNISEX STYLING: soaked dark T-shirt or dark minimal top, no gender-specific costume cliches, natural fit adapted from the reference. LOCATION: dirty underground drainage/sewer tunnel with shallow reflective water, stained concrete walls, claustrophobic depth, low visibility. BACKGROUND THREAT: one humanoid practical-FX creature emerging from water several meters behind the subject; partially out of focus but clearly threatening. LIGHTING: harsh on-camera flash, strong reflections on wet skin and puddles, hard shadows, deep black falloff. IMAGE QUALITY: gritty early-digital snapshot feel — visible noise, slight compression artifacts, mild color cast, imperfect exposure. MOOD: accidental capture of immediate danger, raw and unsettling realism. NEGATIVE: polished cinematic beauty look, fantasy armor, cartoon/anime, painterly style, obvious CGI monster, perfect studio setup, text overlays, watermark, duplicate faces, extra limbs/fingers.",
            1,
        ),
    ],
    "add_photo": [
        (
            _OBJECT_IN_SCENE_TITLE,
            "Сначала фото объекта, потом фото места — бот вставит первое во второе.",
            "COMPOSITE TASK — SINGLE OUTPUT PHOTO. Photorealistic integration only, never side-by-side collage. "
            "Image #1 = source subject (object, vehicle, furniture, product, animal, or person). "
            "Image #2 = destination scene where this subject must naturally exist. "
            "Extract only the subject from #1 conceptually and place it into #2; never paste the whole frame from #1. "
            "REALISM LOCK (strict): insertion must look physically real, as if shot in one camera frame — not a pasted sticker. "
            "Match camera perspective, lens distortion, scale, horizon, and contact plane with #2; preserve believable distance to nearby objects. "
            "Lighting lock: match key/fill/back light direction, intensity, color temperature, shadow softness, and reflections according to #2. "
            "Add realistic contact shadows, ambient occlusion, grounding reflections, and subtle bounce light where needed. "
            "Material lock: preserve true textures (metal/glass/fabric/skin/fur) and adapt them to scene lighting without plastic/flat look. "
            "Edge lock: clean natural edges with fine hair/fur detail and depth-of-field coherence; no halos, no cutout borders. "
            "If source subject is a person: FACE IDENTITY LOCK (absolute) — do not change face, age, facial structure, skin texture, or hairstyle; keep the exact same person recognizable. "
            "If source is non-human object: preserve geometry, proportions, branding/details, and realistic wear. "
            "FORBIDDEN: sticker-like overlay, split screen, before/after panels, visible rectangular crop from #1, duplicate UI, watermark, text overlays.",
            2,
        ),
    ],
}

# Доп. изображения для API (extra_refs). Превью листинга — отдельные *_LISTING_IMAGE / _ready_idea_listing_photo_path.
_READY_IDEA_STATIC_REF_BY_TITLE: dict[str, str | list[str]] = {
    "Absolute Cinema": str(
        PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "absolute_cinema_preview.png"
    ),
    _MMORPG_HERO_TITLE: str(
        PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "mmorpg_hero_custom_preview.png"
    ),
    _MELLSTROY_PHOTO_TITLE: [
        str(PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "mellstroy_ref.png"),
        str(PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "mellstroy_cat_ref.png"),
        str(PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "foga_ref.png"),
    ],
}

# Превью для листания идей в Telegram (_ready_idea_listing_photo_path → подпись к сообщению).
# Не передаются в openrouter_text_and_refs_to_image_bytes и не попадают в промпт как референс.
_MINECRAFT_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "minecraft_preview.png"
_CLASH_ROYALE_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "clash_royale_preview.png"
_GTA_VICE_CITY_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "gta_vice_city_preview.png"
_GAME_OF_THRONES_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "game_of_thrones_preview.png"
_AVATAR_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "avatar_preview.png"
_PUTIN_NEGOTIATIONS_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "putin_negotiations_preview.png"
_MUHAMMAD_ALI_VICTORY_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "muhammad_ali_victory_preview.png"
_HOMELANDER_BUTCHER_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "homelander_butcher_preview.png"
_ROSTOMER_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "rostomer_preview.png"
_ITALY_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "italy_preview.png"
_BACKROOMS_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "backrooms_preview.png"
_ORANGE_COLOR_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "orange_color_preview.png"
_BLACK_STUDIO_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "black_studio_preview.png"
_SUIT_BOUQUET_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "suit_bouquet_preview.png"
_GUCCI_EDITORIAL_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "gucci_editorial_preview.png"
_KNIGHT_LADY_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "knight_lady_preview.png"
_LOVE_IS_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "love_is_preview.png"
_POSTER_TEXT_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "poster_with_text_preview.png"
_FLUFFY_LETTERS_READY_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "fluffy_letters_preview.png"
_SONY_ERICSSON_T100_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "sony_ericsson_t100_preview.png"
_CHALK_ASPHALT_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "chalk_asphalt_preview.png"
_STUPENI_U_OGNYA_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "stupeni_u_ognya_preview.png"
_UFC_MCGREGOR_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "ufc_mcgregor_preview.png"
_FANTASY_GAME_TITLE_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "fantasy_game_title_preview.png"
_POLAROID_CURTAIN_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "polaroid_curtain_preview.png"
_BURGUNDY_CINEMA_PORTRAIT_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "burgundy_cinema_portrait_preview.png"
_PLASTER_FASHION_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "plaster_fashion_preview.png"
_CROW_DOUBLE_EXPOSURE_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "crow_double_exposure_preview.png"
_GTA_V_REALISM_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "gta_v_realism_preview.png"
_TUNNEL_FOUND_PHOTO_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "tunnel_found_photo_preview.png"
_BUSINESS_JET_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "business_jet_preview.png"
_ABSOLUTE_CINEMA_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "absolute_cinema_preview.png"
_BEARD_MUSTACHE_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "beard_mustache_preview.png"
_GENDER_SWAP_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "gender_swap_preview.png"
_MMORPG_HERO_CUSTOM_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "mmorpg_hero_custom_preview.png"
_LUXURY_TORN_COVER_LISTING_IMAGE = (
    PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "luxury_torn_cover_preview.png"
)
_SUPERHERO_MIRROR_LISTING_IMAGE = (
    PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "superhero_mirror_multiverse_preview.png"
)
_RONALDO_PHOTO_LISTING_IMAGE = (
    PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "ronaldo_preview.png"
)
_MELLSTROY_PHOTO_LISTING_IMAGE = (
    PROJECT_ROOT / "assets" / "ready_ideas" / "custom" / "mellstroy_preview.png"
)
# Категория «Добавить фото» → «Перемещение объекта»; только UI, не в API.
_OBJECT_IN_SCENE_LISTING_IMAGE = PROJECT_ROOT / "assets" / "ready_ideas" / "add_photo_object_in_scene_preview.png"


def _start_listing_banner_path() -> Path | None:
    """Единое превью раздела «Готовые идеи»: список категорий и фолбэк для идеи без своего превью.

    Файл: assets/start/ready_ideas_preview.png (одна картинка; баннер главного меню — другой файл).
    """
    p = PROJECT_ROOT / "assets" / "start" / "ready_ideas_preview.png"
    return p if p.is_file() else None


def _ready_ideas_category_hub_photo_paths() -> list[Path]:
    """Одно фото для экрана категорий — тот же путь, что и _start_listing_banner_path."""
    p = _start_listing_banner_path()
    return [p] if p else []


async def _purge_prior_ready_hub_ui(
    bot,
    chat_id: int,
    anchor: Message | None,
    prior_data: dict,
) -> None:
    """Удаляет альбом категорий и/или якорное сообщение перед новым UI (без дублей в чате)."""
    ids = list(prior_data.get("_ready_category_album_ids") or [])
    seen: set[int] = set()
    for mid in ids:
        if mid in seen:
            continue
        seen.add(mid)
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            logging.debug("purge hub album mid=%s", mid, exc_info=True)
    if anchor is not None and anchor.message_id not in seen:
        try:
            await anchor.delete()
        except Exception:
            logging.debug("purge hub anchor", exc_info=True)


async def _purge_ready_category_album_messages_only(bot, chat_id: int, prior_data: dict) -> None:
    """Только старые id альбома «готовых идей»; якорное сообщение (меню) не трогаем — его можно отредактировать."""
    ids = list(prior_data.get("_ready_category_album_ids") or [])
    seen: set[int] = set()
    for mid in ids:
        if mid in seen:
            continue
        seen.add(mid)
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            logging.debug("purge ready album only mid=%s", mid, exc_info=True)


async def _send_ready_hub_messages(
    bot,
    chat_id: int,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    paths: list[Path],
) -> tuple[Message, list[int]]:
    """Одно фото с подписью и inline-клавиатурой (экран категорий «Готовые идеи»)."""
    ok = [p for p in paths if p.is_file()]
    if not ok:
        m = await bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=HTML)
        return m, [m.message_id]
    m = await bot.send_photo(
        chat_id,
        FSInputFile(ok[0]),
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=HTML,
    )
    return m, [m.message_id]


def _ready_idea_listing_photo_path(title: str) -> Path | None:
    """Файл картинки для карточки просмотра готовой идеи в чате (иллюстрация «как может выглядеть результат»).

    Используется только в UI (_edit_ready_nav_message). Не является референсом для генерации:
    в API уходят фото пользователя и, для части идей, байты из _READY_IDEA_STATIC_REF_BY_TITLE.
    При отсутствии своего превью подставляется ready_ideas_preview.png через _ready_categories_listing_photo().
    """
    t = title.strip()
    if t == "Minecraft" and _MINECRAFT_READY_LISTING_IMAGE.is_file():
        return _MINECRAFT_READY_LISTING_IMAGE
    if t == "Clash Royale" and _CLASH_ROYALE_READY_LISTING_IMAGE.is_file():
        return _CLASH_ROYALE_READY_LISTING_IMAGE
    if t == "GTA Vice City" and _GTA_VICE_CITY_READY_LISTING_IMAGE.is_file():
        return _GTA_VICE_CITY_READY_LISTING_IMAGE
    if t == "Game of Thrones" and _GAME_OF_THRONES_READY_LISTING_IMAGE.is_file():
        return _GAME_OF_THRONES_READY_LISTING_IMAGE
    if t == "Avatar" and _AVATAR_READY_LISTING_IMAGE.is_file():
        return _AVATAR_READY_LISTING_IMAGE
    if t == "Переговоры с Путиным" and _PUTIN_NEGOTIATIONS_READY_LISTING_IMAGE.is_file():
        return _PUTIN_NEGOTIATIONS_READY_LISTING_IMAGE
    if t == "Победа над Мухаммадом Али на ринге" and _MUHAMMAD_ALI_VICTORY_READY_LISTING_IMAGE.is_file():
        return _MUHAMMAD_ALI_VICTORY_READY_LISTING_IMAGE
    if t == "UFC: лицом к лицу с Макгрегором" and _UFC_MCGREGOR_LISTING_IMAGE.is_file():
        return _UFC_MCGREGOR_LISTING_IMAGE
    if t == "Хоумлендер и Бутч" and _HOMELANDER_BUTCHER_READY_LISTING_IMAGE.is_file():
        return _HOMELANDER_BUTCHER_READY_LISTING_IMAGE
    if t == "Ростомер" and _ROSTOMER_READY_LISTING_IMAGE.is_file():
        return _ROSTOMER_READY_LISTING_IMAGE
    if t == "На отдыхе в Италии" and _ITALY_READY_LISTING_IMAGE.is_file():
        return _ITALY_READY_LISTING_IMAGE
    if t == "Бекрумс" and _BACKROOMS_READY_LISTING_IMAGE.is_file():
        return _BACKROOMS_READY_LISTING_IMAGE
    if t == "Оранжевый" and _ORANGE_COLOR_READY_LISTING_IMAGE.is_file():
        return _ORANGE_COLOR_READY_LISTING_IMAGE
    if t == "Чёрный студийный" and _BLACK_STUDIO_READY_LISTING_IMAGE.is_file():
        return _BLACK_STUDIO_READY_LISTING_IMAGE
    if t == "Бордовый кино-портрет" and _BURGUNDY_CINEMA_PORTRAIT_LISTING_IMAGE.is_file():
        return _BURGUNDY_CINEMA_PORTRAIT_LISTING_IMAGE
    if t == _MMORPG_HERO_TITLE and _MMORPG_HERO_CUSTOM_LISTING_IMAGE.is_file():
        return _MMORPG_HERO_CUSTOM_LISTING_IMAGE
    if t == _LUXURY_TORN_COVER_TITLE and _LUXURY_TORN_COVER_LISTING_IMAGE.is_file():
        return _LUXURY_TORN_COVER_LISTING_IMAGE
    if t == _SUPERHERO_MIRROR_TITLE and _SUPERHERO_MIRROR_LISTING_IMAGE.is_file():
        return _SUPERHERO_MIRROR_LISTING_IMAGE
    if t == _RONALDO_PHOTO_TITLE and _RONALDO_PHOTO_LISTING_IMAGE.is_file():
        return _RONALDO_PHOTO_LISTING_IMAGE
    if t == _MELLSTROY_PHOTO_TITLE and _MELLSTROY_PHOTO_LISTING_IMAGE.is_file():
        return _MELLSTROY_PHOTO_LISTING_IMAGE
    if t == "Красивый костюм с букетом" and _SUIT_BOUQUET_READY_LISTING_IMAGE.is_file():
        return _SUIT_BOUQUET_READY_LISTING_IMAGE
    if t == "Gucci editorial" and _GUCCI_EDITORIAL_READY_LISTING_IMAGE.is_file():
        return _GUCCI_EDITORIAL_READY_LISTING_IMAGE
    if t == "Для влюбленных: рыцарь и дама" and _KNIGHT_LADY_READY_LISTING_IMAGE.is_file():
        return _KNIGHT_LADY_READY_LISTING_IMAGE
    if t == "Love is…" and _LOVE_IS_READY_LISTING_IMAGE.is_file():
        return _LOVE_IS_READY_LISTING_IMAGE
    if t == _POLAROID_CURTAIN_TITLE and _POLAROID_CURTAIN_LISTING_IMAGE.is_file():
        return _POLAROID_CURTAIN_LISTING_IMAGE
    if t == _FANTASY_3D_GAME_TITLE and _FANTASY_GAME_TITLE_LISTING_IMAGE.is_file():
        return _FANTASY_GAME_TITLE_LISTING_IMAGE
    if t == _POSTER_TEXT_READY_TITLE and _POSTER_TEXT_READY_LISTING_IMAGE.is_file():
        return _POSTER_TEXT_READY_LISTING_IMAGE
    if t == _FLUFFY_LETTERS_TITLE and _FLUFFY_LETTERS_READY_LISTING_IMAGE.is_file():
        return _FLUFFY_LETTERS_READY_LISTING_IMAGE
    if t == _PLASTER_FASHION_STUDIO_TITLE and _PLASTER_FASHION_LISTING_IMAGE.is_file():
        return _PLASTER_FASHION_LISTING_IMAGE
    if t == _SONY_ERICSSON_T100_TITLE and _SONY_ERICSSON_T100_LISTING_IMAGE.is_file():
        return _SONY_ERICSSON_T100_LISTING_IMAGE
    if t == _CHALK_ON_ASPHALT_TITLE and _CHALK_ASPHALT_LISTING_IMAGE.is_file():
        return _CHALK_ASPHALT_LISTING_IMAGE
    if t == "Ступени у огня" and _STUPENI_U_OGNYA_LISTING_IMAGE.is_file():
        return _STUPENI_U_OGNYA_LISTING_IMAGE
    if t == "Двойная экспозиция: вороны" and _CROW_DOUBLE_EXPOSURE_LISTING_IMAGE.is_file():
        return _CROW_DOUBLE_EXPOSURE_LISTING_IMAGE
    if t == _GTA_V_REALISM_TITLE and _GTA_V_REALISM_LISTING_IMAGE.is_file():
        return _GTA_V_REALISM_LISTING_IMAGE
    if t == "Найденная фотка: тоннель" and _TUNNEL_FOUND_PHOTO_LISTING_IMAGE.is_file():
        return _TUNNEL_FOUND_PHOTO_LISTING_IMAGE
    if t == "Самолёт бизнес-класс" and _BUSINESS_JET_LISTING_IMAGE.is_file():
        return _BUSINESS_JET_LISTING_IMAGE
    if t == "Absolute Cinema" and _ABSOLUTE_CINEMA_LISTING_IMAGE.is_file():
        return _ABSOLUTE_CINEMA_LISTING_IMAGE
    if t == "Густая борода + усы" and _BEARD_MUSTACHE_LISTING_IMAGE.is_file():
        return _BEARD_MUSTACHE_LISTING_IMAGE
    if t == "Смена пола" and _GENDER_SWAP_LISTING_IMAGE.is_file():
        return _GENDER_SWAP_LISTING_IMAGE
    if t == _OBJECT_IN_SCENE_TITLE and _OBJECT_IN_SCENE_LISTING_IMAGE.is_file():
        return _OBJECT_IN_SCENE_LISTING_IMAGE
    return _start_listing_banner_path()


async def _edit_message_progress_text(
    msg: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Текст прогресса/статуса: у фото-сообщений правим подпись, не edit_text."""
    if msg.photo:
        await msg.edit_caption(caption=text, parse_mode=HTML, reply_markup=reply_markup)
    else:
        await msg.edit_text(text, parse_mode=HTML, reply_markup=reply_markup)


async def _edit_ready_nav_message(
    message: Message,
    *,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None,
    listing_photo: Path | None,
) -> Message | None:
    """
    Смена экрана готовых идей: сначала replace (одно сообщение — превью совпадает с подписью).

    Если нужна другая картинка, а edit_media не вышел — delete + send_photo, не edit_caption на старом фото.
    """
    if listing_photo is not None and listing_photo.is_file():
        ok = await replace_nav_screen_in_message(
            message,
            caption_html=caption,
            reply_markup=reply_markup,
            new_media_path=listing_photo,
        )
        if ok:
            return message
        try:
            try:
                await message.delete()
            except Exception:
                logging.debug("_edit_ready_nav_message: delete before resend listing photo failed", exc_info=True)
            return await message.bot.send_photo(
                message.chat.id,
                photo=FSInputFile(listing_photo),
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=HTML,
            )
        except Exception:
            logging.warning("_edit_ready_nav_message: send_photo after replace failed", exc_info=True)
    return await edit_or_send_nav_message(
        message, text=caption, reply_markup=reply_markup, parse_mode=HTML
    )


def _ready_categories_listing_photo() -> Path | None:
    """Превью по умолчанию для «Готовых идей» — ready_ideas_preview.png."""
    return _start_listing_banner_path()

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
    '<i><tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Генерация завершена.</i> Результат — в следующем сообщении.'
)

# Имитация прогресса: OpenRouter/Polza не отдают реальный %, только «пока ждём».
_GEN_PROGRESS_INTERVAL_SEC = 2.0
_GEN_PROGRESS_STEP = 10
_GEN_PROGRESS_MAX_SIM = 90
_GEN_PROGRESS_FINISH_STEP_SEC = 0.12

_GEN_FAILURE_TEXT = (
    "<b>Генерация не завершена</b>\n\n"
    "<blockquote><i>Прости за сбой — картинку получить не удалось. "
    "Попробуй позже или измени запрос. Если повторяется — напиши в поддержку.</i></blockquote>"
)


async def _set_img_flow_anchor(state: FSMContext, msg: Message | None) -> None:
    if msg is None:
        return
    await state.update_data(
        _img_flow_anchor_chat_id=msg.chat.id,
        _img_flow_anchor_message_id=msg.message_id,
    )


async def _strip_ready_flow_inline_keyboards(bot, chat_id: int, data: dict, *extra_mids: int) -> None:
    """Убирает inline-клавиатуры с экрана выбора идеи, хаба и сообщений «скинь ещё фото» после загрузки фото."""
    ach = data.get("_img_flow_anchor_chat_id")
    ids: list[int] = []
    if isinstance(ach, int) and ach == chat_id:
        aid = data.get("_img_flow_anchor_message_id")
        if isinstance(aid, int):
            ids.append(aid)
    for mid in data.get("_ready_category_album_ids") or []:
        try:
            m = int(mid)
        except (TypeError, ValueError):
            continue
        if m not in ids:
            ids.append(m)
    for mid in data.get("_ready_strip_markup_message_ids") or []:
        try:
            m = int(mid)
        except (TypeError, ValueError):
            continue
        if m not in ids:
            ids.append(m)
    for m in extra_mids:
        if isinstance(m, int) and m not in ids:
            ids.append(m)
    for mid in ids:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=mid, reply_markup=None)
        except Exception:
            logging.debug("strip ready flow markup mid=%s", mid, exc_info=True)


def _gen_progress_caption(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    n = 10
    filled = n if pct >= 100 else min(n, (pct * n) // 100)
    bar = "".join("🟩" if i < filled else "⬜" for i in range(n))
    return (
        '<b><tg-emoji emoji-id="5217697679030637222">⏳</tg-emoji> Идёт создание…</b>\n\n'
        f"{bar}\n\n"
        f"<i>{pct}%</i>"
    )


async def _rollback_generation_charge(
    user_id: int,
    meta: ImageChargeMeta,
    *,
    usage_kind: str,
    cost: int,
) -> None:
    if meta.daily_reserved:
        await release_daily_image_generation(user_id, usage_kind)
    if meta.credit_charged:
        await add_credits_with_reason(user_id, cost, source="image_refund", details="refund after generation fail")
    if meta.nonsub_quota_reserved:
        await release_nonsub_image_quota_slot(user_id)
    if meta.nonsub_ready_reserved:
        await release_nonsub_ready_idea_slot(user_id)
    if meta.idea_token_consumed:
        await add_idea_tokens(user_id, 1)


async def _notify_image_generation_failure(
    wait_msg: Message | None,
    message: Message,
    state: FSMContext | None,
) -> None:
    kb = start_menu_keyboard()
    if state is not None:
        try:
            await state.clear()
        except Exception:
            logging.debug("state clear on gen failure", exc_info=True)
    if wait_msg is not None:
        try:
            await _edit_message_progress_text(wait_msg, _GEN_FAILURE_TEXT, reply_markup=kb)
            return
        except Exception:
            logging.debug("failure notify: edit failed, sending new message", exc_info=True)
    await message.answer(_GEN_FAILURE_TEXT, parse_mode=HTML, reply_markup=kb)


async def _await_generation_with_progress(
    wait_msg: Message,
    gen: Callable[[], Awaitable[tuple[bytes, bool]]],
    *,
    priority: bool = False,
) -> tuple[bytes, bool]:
    """Пока ждём API — полоска до 90%; после успеха — дожим до 100%, затем «готово» и фото."""
    async with image_generation_slot(priority=priority):
        task = asyncio.create_task(gen())
        p = 0
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=_GEN_PROGRESS_INTERVAL_SEC)
                break
            except asyncio.TimeoutError:
                p = min(p + _GEN_PROGRESS_STEP, _GEN_PROGRESS_MAX_SIM)
                try:
                    await _edit_message_progress_text(wait_msg, _gen_progress_caption(p))
                except Exception:
                    logging.debug("gen progress edit failed", exc_info=True)
        result = await task
        if not isinstance(result, tuple) or len(result) != 2:
            raise RuntimeError("invalid generation result")
        image_bytes, from_cache = result
        cur = p
        while cur < 100:
            cur = min(cur + _GEN_PROGRESS_STEP, 100)
            try:
                await _edit_message_progress_text(wait_msg, _gen_progress_caption(cur))
            except Exception:
                logging.debug("gen progress finish edit failed", exc_info=True)
            await asyncio.sleep(_GEN_PROGRESS_FINISH_STEP_SEC)
        await asyncio.sleep(0.22)
        return image_bytes, from_cache


async def _finalize_generation_status_message(wait_msg: Message) -> None:
    try:
        await _edit_message_progress_text(wait_msg, _GEN_STATUS_DONE_TEXT, reply_markup=None)
    except Exception:
        logging.debug("finalize generation status message failed", exc_info=True)


def _waiting_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=CB_BACK_IMAGE_MODELS,
                    icon_custom_emoji_id="5256247952564825322",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CB_IMG_CANCEL,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="6302868067407890482",
                ),
            ],
        ]
    )


def _missing_config_kb(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=back_callback,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ]
        ]
    )


class ImageGenState(StatesGroup):
    choosing_model = State()
    waiting_prompt = State()
    ready_choosing_category = State()
    ready_browsing_idea = State()
    ready_waiting_photos = State()
    ready_waiting_minecraft_nick = State()
    ready_waiting_poster_text = State()
    ready_waiting_beard_size = State()
    ready_waiting_fantasy_headline = State()
    ready_waiting_fantasy_color = State()
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


def _subscriber_model_pick_keyboard(
    choices: list[tuple[str, str, int, str]],
    back_callback: str = CB_MENU_BACK_START,
) -> InlineKeyboardMarkup:
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
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=back_callback,
                icon_custom_emoji_id="5256247952564825322",
            )
        ]
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
            ok = await take_credits_with_reason(user_id, cost, source="image_generate", details="manual prompt")
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
            ok = await take_credits_with_reason(user_id, cost, source="image_generate", details="edit mode")
            if not ok:
                balance = await get_credits(user_id)
                extra = (
                    f"\n<blockquote><i>Подписка активна, но {CREDITS_COIN_TG_HTML} кредиты закончились.</i> "
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
            ok = await take_credits_with_reason(user_id, cost, source="image_generate", details="manual prompt")
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
        ok = await take_credits_with_reason(user_id, cost, source="image_generate", details="retry generation")
        if not ok:
            balance = await get_credits(user_id)
            extra = (
                f"\n<blockquote><i>Подписка активна, но {CREDITS_COIN_TG_HTML} кредиты закончились.</i> "
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
            await add_credits_with_reason(user_id, cost, source="image_refund", details="refund after failed retry")
        await message.answer(
            "<b>Лимит занят</b>\n"
            f"<blockquote><i>Параллельный запрос.</i> Сегодня (МСК): <b>{esc(used)}/{esc(limit)}</b>. "
            "Попробуй позже.</blockquote>",
            parse_mode=HTML,
        )
        return False, None
    meta.daily_reserved = True
    return True, meta


def _ready_categories_keyboard(back_callback: str = CB_MENU_BACK_START) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for slug, title in READY_IDEA_CATEGORIES:
        icon_id = _READY_CATEGORY_PREMIUM_IDS.get(slug)
        btn_kw: dict = {
            "text": title[:64],
            "callback_data": f"{CB_READY_CAT_PREFIX}{slug}",
            "style": BTN_PRIMARY,
        }
        if icon_id:
            btn_kw["icon_custom_emoji_id"] = icon_id
        pair.append(InlineKeyboardButton(**btn_kw))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=back_callback,
                icon_custom_emoji_id="5256247952564825322",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ideas_for_category(
    category: str,
    *,
    include_hidden_start_only: bool = False,
) -> list[tuple[str, str, str, int]]:
    ideas = READY_IDEA_ITEMS.get((category or "").strip().lower(), [])
    if include_hidden_start_only:
        if (category or "").strip().lower() == "celebrities":
            return [it for it in ideas if (it[0] or "").strip() == _RONALDO_PHOTO_TITLE]
        return ideas
    # Идея доступна из старт-панели, но скрыта из общего листинга «Готовых идей».
    return [it for it in ideas if (it[0] or "").strip() != _RONALDO_PHOTO_TITLE]


def _ready_browser_keyboard(
    index: int,
    total: int,
    back_callback: str = CB_MENU_BACK_START,
    *,
    category_slug: str | None = None,
    single_shortcut_mode: bool = False,
) -> InlineKeyboardMarkup:
    """Листание карточек готовых идей. Не добавлять сюда fast/medium/premium — режим только из панели (🎛 Режим)."""
    prev_i = (index - 1) % total
    next_i = (index + 1) % total
    cs = (category_slug or "").strip().lower()
    cat_icon = _READY_CATEGORY_PREMIUM_IDS.get(cs)
    cats_btn: dict = {
        "text": "Категории",
        "callback_data": f"{CB_READY_NAV_PREFIX}back_cats",
        "style": BTN_PRIMARY,
    }
    if cat_icon:
        cats_btn["icon_custom_emoji_id"] = cat_icon
    if single_shortcut_mode:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Выбрать",
                        callback_data=f"{CB_READY_NAV_PREFIX}pick:{index}",
                        style=BTN_SUCCESS,
                        icon_custom_emoji_id="5206607081334906820",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=back_callback,
                        icon_custom_emoji_id="5256247952564825322",
                    )
                ],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u200b",
                    callback_data=f"{CB_READY_NAV_PREFIX}prev:{prev_i}",
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5258236805890710909",
                ),
                InlineKeyboardButton(
                    text="Выбрать",
                    callback_data=f"{CB_READY_NAV_PREFIX}pick:{index}",
                    style=BTN_SUCCESS,
                    icon_custom_emoji_id="5206607081334906820",
                ),
                InlineKeyboardButton(
                    text="\u200b",
                    callback_data=f"{CB_READY_NAV_PREFIX}next:{next_i}",
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5260450573768990626",
                ),
            ],
            [InlineKeyboardButton(**cats_btn)],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=back_callback,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ],
        ]
    )


def _ready_wait_photo_keyboard(
    back_text: str = "↩️ К идеям",
    back_callback: str = CB_READY_PHOTO_BACK,
) -> InlineKeyboardMarkup:
    back_btn_kwargs = {}
    if "назад" in (back_text or "").lower():
        back_btn_kwargs["icon_custom_emoji_id"] = "5256247952564825322"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=back_text, callback_data=back_callback, **back_btn_kwargs)],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CB_IMG_CANCEL,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="6302868067407890482",
                )
            ],
        ]
    )


def _ready_wait_photo_keyboard_for_state(data: dict) -> InlineKeyboardMarkup:
    # Стартовый шорткат (сейчас только «Фото с Роналдо»): не связан с листингом категорий — только выход в главное меню.
    if bool(data.get("_ready_include_hidden_start_only")):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=CB_IMG_CANCEL,
                        style=BTN_DANGER,
                        icon_custom_emoji_id="6302868067407890482",
                    )
                ],
            ]
        )
    return _ready_wait_photo_keyboard()


def _ready_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✔️ Подтвердить", callback_data=CB_READY_CONFIRM, style=BTN_SUCCESS)],
            [
                InlineKeyboardButton(
                    text="Назад к фото",
                    callback_data=CB_READY_PHOTO_BACK,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CB_IMG_CANCEL,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="6302868067407890482",
                )
            ],
        ]
    )


def _ready_beard_size_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Короткая",
                    callback_data=f"{CB_READY_BEARD_SIZE_PREFIX}short",
                    style=BTN_PRIMARY,
                ),
                InlineKeyboardButton(
                    text="Средняя",
                    callback_data=f"{CB_READY_BEARD_SIZE_PREFIX}medium",
                    style=BTN_PRIMARY,
                ),
                InlineKeyboardButton(
                    text="Длинная",
                    callback_data=f"{CB_READY_BEARD_SIZE_PREFIX}long",
                    style=BTN_PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Назад к фото",
                    callback_data=CB_READY_PHOTO_BACK,
                    icon_custom_emoji_id="5256247952564825322",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CB_IMG_CANCEL,
                    style=BTN_DANGER,
                    icon_custom_emoji_id="6302868067407890482",
                )
            ],
        ]
    )


def _ready_category_caption() -> str:
    return (
        '<b><tg-emoji emoji-id="5422439311196834318">💡</tg-emoji> Готовые идеи</b>\n'
        "Выбери категорию и идею, затем нажми «Выбрать».\n"
        "Модель для готовых идей переключается внизу: кнопка <b>«🎛 Режим»</b> на панели быстрого доступа.\n"
        "Дальше бот подскажет, что отправить: фото, текст или оба шага.\n"
        '<tg-emoji emoji-id="5330320040883411678">🗺</tg-emoji> Стоимость: <b>15–65 кр.</b> (зависит от режима и подписки).'
    )


_READY_IDEA_COST_BY_PLAN: dict[str, int] = {
    "starter": 30,
    "nova": 45,
    "supernova": 40,
    "galaxy": 35,
    "universe": 30,
}
_READY_IDEA_DEFAULT_COST: int = 45

_READY_MODE_FAST = "fast"
_READY_MODE_MEDIUM = "medium"
_READY_MODE_PREMIUM = "premium"
_READY_MODE_DEFAULT = _READY_MODE_MEDIUM
_READY_MODE_ALLOWED = {_READY_MODE_FAST, _READY_MODE_MEDIUM, _READY_MODE_PREMIUM}

_READY_MODE_MODEL_BY_ID: dict[str, str] = {
    _READY_MODE_FAST: "google/gemini-3.1-flash-image-preview",
    _READY_MODE_MEDIUM: "google/gemini-3-pro-image-preview",
    _READY_MODE_PREMIUM: "openai/gpt-5.4-image-2",
}

_READY_MODE_COST_BY_PLAN: dict[str, dict[str, int]] = {
    _READY_MODE_FAST: {"starter": 15, "universe": 15, "galaxy": 20, "supernova": 25, "nova": 30},
    _READY_MODE_MEDIUM: {"starter": 30, "universe": 30, "galaxy": 35, "supernova": 40, "nova": 45},
    _READY_MODE_PREMIUM: {"starter": 50, "universe": 50, "galaxy": 55, "supernova": 60, "nova": 65},
}

_READY_MODE_DEFAULT_COST: dict[str, int] = {
    _READY_MODE_FAST: 30,
    _READY_MODE_MEDIUM: 45,
    _READY_MODE_PREMIUM: 65,
}


def _ready_mode_normalize(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    return m if m in _READY_MODE_ALLOWED else _READY_MODE_DEFAULT


async def _live_user_ready_mode(user_id: int | None) -> str:
    """Режим готовых идей из профиля (БД), без устаревшего снимка в FSM."""
    if user_id is None:
        return _READY_MODE_DEFAULT
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return _READY_MODE_DEFAULT
    if uid <= 0:
        return _READY_MODE_DEFAULT
    return _ready_mode_normalize(await get_user_ready_mode(uid))


def _ready_mode_emoji(mode: str) -> str:
    m = _ready_mode_normalize(mode)
    return {"fast": "⚡", "medium": "🚀", "premium": "💎"}.get(m, "⚡")


def _ready_mode_line(mode: str) -> str:
    m = _ready_mode_normalize(mode)
    return f"Режим: {_ready_mode_emoji(m)} <b>{esc(m)}</b>"


def _ready_mode_model(mode: str) -> str:
    m = _ready_mode_normalize(mode)
    if m == _READY_MODE_FAST:
        return (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or _READY_MODE_MODEL_BY_ID[m]
    if m == _READY_MODE_MEDIUM:
        return (OPENROUTER_IMAGE_GEMINI_PRO_MODEL or "").strip() or _READY_MODE_MODEL_BY_ID[m]
    return (OPENROUTER_IMAGE_GPT54_IMAGE2_MODEL or "").strip() or _READY_MODE_MODEL_BY_ID[_READY_MODE_PREMIUM]


def _ready_mode_model_human(mode: str) -> str:
    m = _ready_mode_normalize(mode)
    if m == _READY_MODE_FAST:
        return "Nano Banana 2"
    if m == _READY_MODE_MEDIUM:
        return "Nano Banana Pro"
    return "Chat Gpt Image 2"


def _ready_idea_cost_for_plan_and_mode(plan_id: str | None, mode: str | None) -> int:
    m = _ready_mode_normalize(mode)
    p = (plan_id or "").strip().lower()
    return _READY_MODE_COST_BY_PLAN.get(m, {}).get(p, _READY_MODE_DEFAULT_COST[m])


def _ready_idea_cost_for_plan(plan_id: str | None) -> int:
    return _READY_IDEA_COST_BY_PLAN.get((plan_id or "").strip().lower(), _READY_IDEA_DEFAULT_COST)


async def _ready_idea_cost_for_user(user_id: int) -> int:
    return await _ready_idea_cost_for_user_mode(user_id, _READY_MODE_DEFAULT)


async def _ready_idea_cost_for_user_mode(user_id: int, mode: str | None) -> int:
    prof = await get_user_admin_profile(user_id)
    if not prof or not subscription_is_active(prof.subscription_ends_at):
        return _ready_idea_cost_for_plan_and_mode(None, mode)
    return _ready_idea_cost_for_plan_and_mode(prof.subscription_plan, mode)


def _ready_generation_cost_html(cost: int | None = None) -> str:
    shown = int(cost) if cost is not None else _READY_IDEA_DEFAULT_COST
    return f'<tg-emoji emoji-id="5330320040883411678">🗺</tg-emoji> Стоимость генерации: <b>{esc(shown)} кр.</b>'


def _ready_idea_caption(
    *,
    category: str,
    title: str,
    preview: str,
    index: int,
    total: int,
    photos_required: int,
    cost: int,
    mode: str,
    show_category_title: bool = True,
) -> str:
    req = _ready_idea_requirement_line(title=title, photos_required=photos_required)
    recommendation = _ready_idea_recommendation_line(title=title, photos_required=photos_required)
    recommendation_part = f"\n{recommendation}" if recommendation else ""
    category_part = f"{_ready_category_title_html(category)}\n" if show_category_title else ""
    mode_line = _ready_mode_line(mode)
    if (title or "").strip() == _RONALDO_PHOTO_TITLE:
        mode_line = "Режим: 🚀 <b>medium</b> (фиксировано для этой идеи)"
    return (
        f"{category_part}"
        f'<tg-emoji emoji-id="5397782960512444700">📌</tg-emoji> Вариант: <b>{esc(index + 1)}/{esc(total)}</b>\n'
        f"<b>{esc(title)}</b>\n"
        f"{esc(preview)}\n"
        f"{mode_line}\n"
        f"{_ready_generation_cost_html(cost)}\n"
        f"<b>{esc(req)}</b>"
        f"{recommendation_part}"
    )


def _ready_photo_upload_hint(
    *, category: str, need: int, received: int, idea_title: str | None = None
) -> str:
    """Подсказка шага загрузки фото; для части идей «для двоих» — порядок мужчина → женщина."""
    cat = (category or "").strip().lower()
    t = (idea_title or "").strip()
    req = _ready_idea_requirement_line(title=t, photos_required=need)
    is_object_in_scene = t == _OBJECT_IN_SCENE_TITLE and need == 2
    if is_object_in_scene:
        if received <= 0:
            return (
                f"<b>{esc(req)}</b>\n"
                "<b>Шаг 1 из 2 — объект</b>\n"
                "Пришли фото <b>того, что нужно перенести</b> (машина, вещь, предмет — что угодно, главное чтобы объект был понятен).\n"
                "ℹ️ Это не фон: нужен именно объект (лучше на простом фоне)."
            )
        if received == 1:
            return (
                "<b>Фото получено: 1/2</b>\n"
                "<b>Шаг 2 из 2 — место</b>\n"
                "Пришли фото <b>куда вставить</b> — целая сцена, интерьер, улица (куда объект должен попасть).\n"
                "✨ ИИ вставит объект с первого фото в эту сцену."
            )
        return "<b>Фото получено: 2/2</b>"
    is_for_two = cat == "for_two" and need == 2
    if is_for_two and t == _POLAROID_CURTAIN_TITLE:
        if received <= 0:
            return (
                f"<b>{esc(req)}</b>\n"
                "Пришли <b>первое</b> из двух фото.\n"
                "👥 Порядок не важен: можно 2 человека или, например, питомцев."
            )
        if received == 1:
            return (
                "<b>Фото получено: 1/2</b>\n"
                "Пришли <b>второе</b> фото — второй участник."
            )
        return "<b>Фото получено: 2/2</b>"
    if is_for_two:
        if received <= 0:
            return (
                f"<b>{esc(req)}</b>\n"
                '<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji> Скинь фото мужчины.\n'
                '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> Порядок важен: сначала мужчина, потом женщина.'
            )
        if received == 1:
            return '<b>Фото получено: 1/2</b>\n<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji> Теперь скинь фото женщины.'
        return "<b>Фото получено: 2/2</b>"
    if received <= 0:
        if t == "Minecraft":
            return (
                f"<b>{esc(req)}</b>\n"
                '<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji> Сначала отправь фото, затем бот попросит <b>ник</b> для надписи.'
            )
        if _ready_idea_needs_headline_input(t):
            return (
                f"<b>{esc(req)}</b>\n"
                '<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji> Сначала отправь фото, затем бот попросит <b>текст</b> для надписи.'
            )
        return f"<b>{esc(req)}</b>"
    if received < need:
        return f"Фото получено: <b>{esc(received)}/{esc(need)}</b>. Пришли ещё."
    return f"Фото получено: <b>{esc(received)}/{esc(need)}</b>."


def _is_minecraft_ready_idea(title: str, base_prompt: str = "") -> bool:
    t = (title or "").strip().lower()
    p = (base_prompt or "").strip().lower()
    return ("minecraft" in t) or ("эндер" in t) or ("minecraft" in p and "ender" in p)


_POSTER_TEXT_MAX_LEN = 48
_FLUFFY_TEXT_MAX_LEN = 40
_PLASTER_TEXT_MAX_LEN = 48
_CHALK_ASPHALT_TEXT_MAX_LEN = 120


def _ready_title_from_state_data(data: dict) -> str:
    category = str(data.get("_ready_category") or "").strip().lower()
    idx = int(data.get("_ready_index") or 0)
    include_hidden = bool(data.get("_ready_include_hidden_start_only"))
    ideas = _ideas_for_category(category, include_hidden_start_only=include_hidden)
    if ideas and 0 <= idx < len(ideas):
        return str(ideas[idx][0] or "").strip()
    return ""


def _headline_max_len_for_title(title: str) -> int:
    if (title or "").strip() == _FLUFFY_LETTERS_TITLE:
        return _FLUFFY_TEXT_MAX_LEN
    if (title or "").strip() == _FANTASY_3D_GAME_TITLE:
        return _FANTASY_HEADLINE_MAX_LEN
    if (title or "").strip() == _PLASTER_FASHION_STUDIO_TITLE:
        return _PLASTER_TEXT_MAX_LEN
    if (title or "").strip() == _CHALK_ON_ASPHALT_TITLE:
        return _CHALK_ASPHALT_TEXT_MAX_LEN
    return _POSTER_TEXT_MAX_LEN


def _has_cyrillic(s: str) -> bool:
    return any("\u0400" <= c <= "\u04ff" for c in (s or ""))


async def _user_eligible_redo_half_price(user_id: int) -> bool:
    return False


async def _image_gen_priority_from_user_id(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    prof = await get_user_admin_profile(user_id)
    if not prof or not subscription_is_active(prof.subscription_ends_at):
        return False
    plan = (prof.subscription_plan or "").strip().lower()
    return plan in ("starter", "galaxy", "universe")


async def _redo_more_button_label(user_id: int, base_cost: int) -> str:
    _ = (user_id, base_cost)
    return "Ещё раз"


async def _start_ready_redo_flow(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    ctx: LastImageContext,
) -> None:
    """Повтор той же готовой идеи: новые фото, тот же промпт (без скидок по планам)."""
    if ctx.kind != "text" or ctx.usage_kind != "ready":
        await message.answer("Повтор недоступен. Открой нужный раздел в меню.")
        return
    if not ctx.refs_file_ids:
        await message.answer("Нет данных о фото. Запусти идею из «Готовых идей» заново.")
        return
    if (ctx.ready_idea_title or "").strip() == _MELLSTROY_PHOTO_TITLE:
        await _send_ready_ideas_screen(message, state, user_id, username, edit=False)
        return
    base = int(ctx.cost)
    elig = await _user_eligible_redo_half_price(user_id)
    effective = max(1, base // 2) if elig else base
    consume = bool(elig)
    await state.clear()
    await state.update_data(
        _ready_redo_prompt=ctx.prompt,
        _ready_redo_model=ctx.model,
        _ready_redo_charge=effective,
        _ready_redo_consume_half=consume,
        _ready_redo_title=(ctx.ready_idea_title or "").strip(),
        _ready_photos=[],
        _ready_need=len(ctx.refs_file_ids),
        _ready_back_cb=CB_MENU_BACK_START,
    )
    await state.set_state(ImageGenState.ready_waiting_photos)
    await message.answer(
        "<b>Повтор готовой идеи</b>\n"
        f"<blockquote><i>Пришли снова <b>{len(ctx.refs_file_ids)}</b> фото "
        "(в том же порядке, что и в прошлый раз).</i></blockquote>"
        f"<blockquote><i>К списанию: <b>{effective}</b> кр.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=CB_BACK_TO_READY_IDEAS,
                        style=BTN_DANGER,
                        icon_custom_emoji_id="6302868067407890482",
                    )
                ]
            ]
        ),
        parse_mode=HTML,
    )


def _regen_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✔️ Ок", callback_data=CB_IMG_OK, style=BTN_SUCCESS
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Ещё раз",
                    callback_data=CB_REGEN,
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5244758760429213978",
                ),
            ],
        ],
    )


def _ready_idea_result_keyboard(
    *,
    redo_label: str,
    ready_idea_title: str | None = None,
) -> InlineKeyboardMarkup:
    second = (
        InlineKeyboardButton(
            text="В меню",
            callback_data=CB_READY_RESULT_MAIN_MENU,
            style=BTN_PRIMARY,
            icon_custom_emoji_id="5256247952564825322",
        )
        if (ready_idea_title or "").strip() == _RONALDO_PHOTO_TITLE
        else InlineKeyboardButton(
            text="💡 К готовым идеям",
            callback_data=CB_BACK_TO_READY_IDEAS,
            style=BTN_PRIMARY,
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=redo_label[:64],
                    callback_data=CB_REGEN_READY_REDO,
                    style=BTN_PRIMARY,
                    icon_custom_emoji_id="5244758760429213978",
                ),
            ],
            [second],
        ],
    )


# document_id премиум-эмодзи 🔥 — только в тексте подписи к фото (не в пикселях картинки).
_SHARD_IMAGE_BADGE_TG_EMOJI_ID = "5389038097860144794"


async def _shard_creator_brand_blockquote_html(message: Message) -> str:
    """Строка бренда в виде цитаты: премиум 🔥 + Made in Shard Creator; ссылка только на текст, не на эмодзи."""
    eid = _SHARD_IMAGE_BADGE_TG_EMOJI_ID
    emoji_html = f'<tg-emoji emoji-id="{eid}">🔥</tg-emoji>'
    inner = f"{emoji_html} Made in Shard Creator"
    try:
        me = await message.bot.get_me()
        if me and me.username:
            inner = (
                f"{emoji_html} "
                f'<a href="https://t.me/{me.username}">Made in Shard Creator</a>'
            )
    except Exception:
        logging.debug("shard_creator_brand_blockquote: get_me failed", exc_info=True)
    return f"<blockquote><i>{inner}</i></blockquote>"


async def _made_in_shard_caption(message: Message) -> str:
    """Подпись под результатом готовой идеи — только бренд-цитата."""
    return await _shard_creator_brand_blockquote_html(message)


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
    refs_file_ids: list[str] | None = None,
    ready_idea_title: str | None = None,
    mark_redo_half_after_success: bool = False,
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
        refs_file_ids=refs_file_ids,
        ready_idea_title=ready_idea_title,
    )
    brand_bq = await _shard_creator_brand_blockquote_html(message)
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
        caption = (
            "<b>Готово!</b>\n"
            "<i>сгенерировано при помощи Shard Creator</i>\n"
            f"<i>Режим админа — {CREDITS_COIN_TG_HTML} кредиты не списывались.</i>{cache_note}\n"
            f"{brand_bq}"
        )
    else:
        balance = await get_credits(user_id)
        spent = ""
        if charge and deducted_credits:
            cw = _credits_word(cost)
            spent = f'<tg-emoji emoji-id="5444856076954520455">🧾</tg-emoji> Списано: <b>{esc(cost)}</b> {cw}.\n'
        caption = (
            "<b>Готово!</b>\n"
            "<i>сгенерировано при помощи Shard Creator</i>\n"
            f"{spent}"
            f'<blockquote><i>{CREDITS_COIN_TG_HTML} кредиты:</i> <b>{esc(balance)}</b></blockquote>{day_note}\n'
            f"{brand_bq}"
        )
    # Для готовых идей: сначала отдельное фото с бренд-подписью, затем отдельное сообщение с действиями.
    if usage_kind == "ready" and refs_file_ids:
        made_caption = await _made_in_shard_caption(message)
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename=filename),
            caption=made_caption,
            parse_mode=HTML,
        )
        await increment_user_generated_images_total(user_id)
        if is_admin:
            await message.answer(
                f"👑 Режим админа: {CREDITS_COIN_TG_HTML} кредиты не списывались.",
                parse_mode=HTML,
            )
        elif charge and deducted_credits:
            balance_after = await get_credits(user_id)
            cw = _credits_word(cost)
            await message.answer(
                f'<tg-emoji emoji-id="5444856076954520455">🧾</tg-emoji> Списано: <b>{esc(cost)}</b> {cw}.\n'
                f"{CREDITS_COIN_TG_HTML} кредиты: <b>{esc(balance_after)}</b>.",
                parse_mode=HTML,
            )
        elif charge:
            balance_after = await get_credits(user_id)
            await message.answer(
                "ℹ️ Кредиты за эту генерацию не списались.\n"
                f"{CREDITS_COIN_TG_HTML} кредиты: <b>{esc(balance_after)}</b>.",
                parse_mode=HTML,
            )
        flow_data = await state.get_data() if state is not None else {}
        await _strip_ready_flow_inline_keyboards(
            message.bot, message.chat.id, flow_data, message.message_id
        )
        redo_lbl = await _redo_more_button_label(user_id, cost)
        await message.answer(
            'Готово <tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Выбери действие:',
            reply_markup=_ready_idea_result_keyboard(
                redo_label=redo_lbl,
                ready_idea_title=ready_idea_title,
            ),
            parse_mode=HTML,
        )
    else:
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename=filename),
            caption=caption,
            reply_markup=_regen_keyboard(),
            parse_mode=HTML,
        )
        await increment_user_generated_images_total(user_id)
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
    wait_msg = await message.answer(_gen_progress_caption(0), parse_mode=HTML)
    try:
        _prio = await _image_gen_priority_from_user_id(user_id)

        async def _gen_text() -> tuple[bytes, bool]:
            if is_polza_image_model(model):
                b = await polza_text_to_image_bytes(
                    prompt, model=model, user_id=user_id
                )
                return b, False
            return await openrouter_text_to_image_bytes(
                prompt, model=model, use_cache=use_image_cache
            )

        image_bytes, from_cache = await _await_generation_with_progress(
            wait_msg, _gen_text, priority=_prio
        )
    except Exception as exc:
        if isinstance(exc, OpenRouterApiError):
            logging.warning(
                "OpenRouter отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
        elif isinstance(exc, PolzaApiError):
            logging.warning(
                "Polza.ai отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
        else:
            logging.exception("Image text generation failed user_id=%s", user_id)
        await _rollback_generation_charge(
            user_id, meta, usage_kind=usage_kind, cost=cost
        )
        await _notify_image_generation_failure(wait_msg, message, state)
        return
    if not image_bytes or len(image_bytes) < 64:
        logging.warning("empty or tiny image after text gen user_id=%s", user_id)
        await _rollback_generation_charge(
            user_id, meta, usage_kind=usage_kind, cost=cost
        )
        await _notify_image_generation_failure(wait_msg, message, state)
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


def _overlay_minecraft_nick(image_bytes: bytes, username: str | None) -> bytes:
    """Надпись ника в стиле Minecraft nametag (in-game)."""
    nick = (username or "").strip().lstrip("@")
    if not nick:
        return image_bytes
    if Image is None or ImageDraw is None or ImageFont is None:
        logging.warning("Pillow is not available; skip minecraft nick overlay")
        return image_bytes
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            rgba = im.convert("RGBA")
            w, h = rgba.size
            text = nick
            # Пытаемся взять аккуратный моноширинный шрифт; если нет — fallback.
            size = max(14, min(32, w // 28))
            font = ImageFont.load_default()
            for p in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/consolab.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ):
                try:
                    font = ImageFont.truetype(p, size=size)
                    break
                except Exception:
                    continue

            draw = ImageDraw.Draw(rgba)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = max(1, bbox[2] - bbox[0])
            th = max(1, bbox[3] - bbox[1])
            pad_x = max(6, w // 180)
            pad_y = max(3, h // 320)
            box_w = tw + pad_x * 2
            box_h = th + pad_y * 2
            x = max(8, (w - box_w) // 2)
            y = max(8, int(h * 0.08))

            # Полупрозрачная черная плашка как у nametag.
            plate = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 140))
            rgba.alpha_composite(plate, (x, y))

            tx = x + pad_x
            ty = y + pad_y - 1
            # Тень + белый текст.
            draw.text((tx + 1, ty + 1), text, font=font, fill=(25, 25, 25, 255))
            draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
            out = BytesIO()
            rgba.convert("RGB").save(out, format="PNG")
            return out.getvalue()
    except Exception:
        logging.warning("minecraft nick overlay failed", exc_info=True)
        return image_bytes


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
    overlay_nick: str | None = None,
    extra_refs: list[bytes] | None = None,
    extra_refs_first: bool = False,
    strict_refs: bool = False,
    ready_idea_title: str | None = None,
    mark_redo_half_after_success: bool = False,
) -> None:
    _ = strict_refs
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
    wait_msg = await _edit_ready_nav_message(
        message,
        caption=_gen_progress_caption(0),
        reply_markup=None,
        listing_photo=_ready_categories_listing_photo(),
    )
    if wait_msg is None:
        wait_msg = await message.bot.send_message(chat_id, _gen_progress_caption(0), parse_mode=HTML)
    try:
        _prio = await _image_gen_priority_from_user_id(user_id)

        async def _gen_ready() -> tuple[bytes, bool]:
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
            if not refs:
                # Идеи без референсных фото (напр. 3D-заголовок фэнтези): только текст в промпте.
                img, cached = await openrouter_text_to_image_bytes(
                    prompt,
                    model=model,
                    use_cache=True,
                )
                return img, cached
            img = await openrouter_text_and_refs_to_image_bytes(
                prompt,
                refs=refs,
                model=model,
            )
            return img, False

        image_bytes, from_cache = await _await_generation_with_progress(
            wait_msg, _gen_ready, priority=_prio
        )
    except Exception as exc:
        if isinstance(exc, OpenRouterApiError):
            logging.warning(
                "OpenRouter refs отказ user_id=%s http=%s: %s",
                user_id,
                exc.http_status,
                exc,
            )
        else:
            logging.exception("Image refs generation failed user_id=%s", user_id)
        await _rollback_generation_charge(
            user_id, meta, usage_kind="ready", cost=cost
        )
        await _notify_image_generation_failure(wait_msg, message, state)
        return
    if not image_bytes or len(image_bytes) < 64:
        logging.warning("empty or tiny image after ready refs user_id=%s", user_id)
        await _rollback_generation_charge(
            user_id, meta, usage_kind="ready", cost=cost
        )
        await _notify_image_generation_failure(wait_msg, message, state)
        return
    if overlay_nick:
        image_bytes = _overlay_minecraft_nick(image_bytes, overlay_nick)
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
        refs_file_ids=list(refs_file_ids) if refs_file_ids else None,
        ready_idea_title=ready_idea_title,
        mark_redo_half_after_success=mark_redo_half_after_success,
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
        edited = await edit_or_send_nav_message(
            replace_message,
            text=body,
            reply_markup=_waiting_prompt_keyboard(),
            parse_mode=HTML,
        )
        await _set_img_flow_anchor(state, edited or replace_message)
        return
    sent = await bot.send_message(
        chat_id,
        body,
        reply_markup=_waiting_prompt_keyboard(),
        parse_mode=HTML,
    )
    await _set_img_flow_anchor(state, sent)


async def _show_image_model_pick(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    *,
    back_callback: str = CB_MENU_BACK_START,
) -> None:
    if not is_openrouter_image_configured():
        await edit_or_send_nav_message(
            message,
            text=_IMAGE_GEN_MISSING_TEXT,
            reply_markup=_missing_config_kb(back_callback),
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
            await _start_image_flow(
                message,
                state,
                user_id,
                username,
                replace_menu=True,
                back_callback=back_callback,
            )
            return
        plan_id = (profile.subscription_plan or "").strip().lower()
    await state.clear()
    await state.update_data(_img_back_cb=back_callback)
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
        reply_markup=_subscriber_model_pick_keyboard(choices, back_callback=back_callback),
        parse_mode=HTML,
    )


async def _start_image_flow(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    *,
    replace_menu: bool = False,
    back_callback: str = CB_MENU_BACK_START,
) -> None:
    if not is_openrouter_image_configured():
        if replace_menu:
            await edit_or_send_nav_message(
                message,
                text=_IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(back_callback),
                parse_mode=HTML,
            )
        else:
            await message.answer(
                _IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(back_callback),
                parse_mode=HTML,
            )
        return
    await ensure_user(user_id, username)
    await state.clear()
    await state.update_data(_img_back_cb=back_callback)
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


async def _restore_image_flow_parent_menu(
    callback: CallbackQuery,
    *,
    back_callback: str,
    user_id: int,
    username: str | None,
) -> None:
    """Экран до «Создать картинку»: хаб меню или главный /start (см. _img_back_cb)."""
    msg = callback.message
    if msg is None:
        return
    if back_callback == CB_MENU_HUB:
        balance = await get_credits(user_id)
        await edit_or_send_nav_message(
            msg,
            text="<b>📋 Главное меню</b>\n<blockquote><i>Выбери нужный раздел.</i></blockquote>",
            reply_markup=menu_hub_keyboard(balance),
            parse_mode=HTML,
        )
    else:
        await restore_main_menu_message(msg, user_id, username)


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
    data = await state.get_data()
    back_callback = str(data.get("_img_back_cb") or CB_MENU_BACK_START)
    if uid in ADMIN_IDS:
        await _show_image_model_pick(
            callback.message,
            state,
            uid,
            callback.from_user.username,
            back_callback=back_callback,
        )
        return
    await ensure_user(uid, callback.from_user.username)
    profile = await get_user_admin_profile(uid)
    if profile and subscription_is_active(profile.subscription_ends_at):
        await _show_image_model_pick(
            callback.message,
            state,
            uid,
            callback.from_user.username,
            back_callback=back_callback,
        )
    else:
        await state.clear()
        await _restore_image_flow_parent_menu(
            callback,
            back_callback=back_callback,
            user_id=uid,
            username=callback.from_user.username,
        )


async def _send_ready_ideas_screen(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    *,
    edit: bool = False,
    back_callback: str = CB_MENU_BACK_START,
) -> None:
    prior = await state.get_data()
    chat_id = message.chat.id
    bot = message.bot
    # Не удаляем якорь главного меню: сначала пробуем edit_media / подпись на том же сообщении.
    await _purge_ready_category_album_messages_only(bot, chat_id, prior)

    await state.clear()
    if not is_openrouter_image_configured():
        if edit:
            if _is_generated_image_result_message(message):
                await bot.send_message(
                    chat_id,
                    _IMAGE_GEN_MISSING_TEXT,
                    reply_markup=_missing_config_kb(back_callback),
                    parse_mode=HTML,
                )
            else:
                listing = _ready_categories_listing_photo()
                ok = await replace_nav_screen_in_message(
                    message,
                    caption_html=_IMAGE_GEN_MISSING_TEXT,
                    reply_markup=_missing_config_kb(back_callback),
                    new_media_path=listing if listing is not None and listing.is_file() else None,
                )
                if not ok:
                    try:
                        await message.delete()
                    except Exception:
                        logging.debug(
                            "_send_ready_ideas_screen: delete before missing-config fallback failed",
                            exc_info=True,
                        )
                    kb = _missing_config_kb(back_callback)
                    if listing is not None and listing.is_file():
                        await bot.send_photo(
                            chat_id,
                            photo=FSInputFile(listing),
                            caption=_IMAGE_GEN_MISSING_TEXT,
                            reply_markup=kb,
                            parse_mode=HTML,
                        )
                    else:
                        await bot.send_message(
                            chat_id,
                            _IMAGE_GEN_MISSING_TEXT,
                            reply_markup=kb,
                            parse_mode=HTML,
                        )
        else:
            await message.answer(_IMAGE_GEN_MISSING_TEXT, reply_markup=_missing_config_kb(), parse_mode=HTML)
        return
    await ensure_user(user_id, username)
    await state.set_state(ImageGenState.ready_choosing_category)
    cap = _ready_category_caption()
    kb = _ready_categories_keyboard(back_callback=back_callback)
    paths = _ready_ideas_category_hub_photo_paths()
    ok_paths = [p for p in paths if p.is_file()]
    listing_photo = ok_paths[0] if ok_paths else _ready_categories_listing_photo()

    if edit:
        if _is_generated_image_result_message(message):
            first, album_ids = await _send_ready_hub_messages(bot, chat_id, cap, kb, paths)
            await state.update_data(_ready_back_cb=back_callback, _ready_category_album_ids=album_ids)
            await _set_img_flow_anchor(state, first)
            return
        media_path = listing_photo if listing_photo is not None and listing_photo.is_file() else None
        ok = await replace_nav_screen_in_message(
            message,
            caption_html=cap,
            reply_markup=kb,
            new_media_path=media_path,
        )
        if ok:
            await state.update_data(
                _ready_back_cb=back_callback,
                _ready_user_id=user_id,
                _ready_mode=await get_user_ready_mode(user_id),
                _ready_category_album_ids=[message.message_id],
            )
            await _set_img_flow_anchor(state, message)
            return
        try:
            await message.delete()
        except Exception:
            logging.debug(
                "_send_ready_ideas_screen: не удалось удалить сообщение перед хабом (fallback)",
                exc_info=True,
            )

    first, album_ids = await _send_ready_hub_messages(bot, chat_id, cap, kb, paths)
    await state.update_data(
        _ready_back_cb=back_callback,
        _ready_user_id=user_id,
        _ready_mode=await get_user_ready_mode(user_id),
        _ready_category_album_ids=album_ids,
    )
    await _set_img_flow_anchor(state, first)


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
        back_callback=CB_MENU_BACK_START,
    )


@router.callback_query(F.data == CB_READY_IDEAS_HUB)
async def open_ready_ideas_from_hub(callback: CallbackQuery, state: FSMContext) -> None:
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
        back_callback=CB_MENU_HUB,
    )


@router.message(Command("ideas"))
async def cmd_ready_ideas(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await _send_ready_ideas_screen(message, state, message.from_user.id, message.from_user.username)


@router.callback_query(F.data == CB_MENU_MELLSTROY)
async def open_mellstroy_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    if not is_openrouter_image_configured():
        await _edit_ready_nav_message(
            callback.message,
            caption=_IMAGE_GEN_MISSING_TEXT,
            reply_markup=_missing_config_kb(CB_MENU_BACK_START),
            listing_photo=_ready_categories_listing_photo(),
        )
        return
    category = "celebrities"
    ideas = _ideas_for_category(category, include_hidden_start_only=True)
    target_idx = next((i for i, it in enumerate(ideas) if (it[0] or "").strip() == _RONALDO_PHOTO_TITLE), -1)
    if target_idx < 0:
        await callback.answer("Идея пока недоступна.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        _ready_back_cb=CB_MENU_BACK_START,
        _ready_user_id=callback.from_user.id,
        _ready_include_hidden_start_only=True,
    )
    await _open_ready_card(
        callback.message,
        state,
        category=category,
        index=target_idx,
        edit=True,
    )


async def _open_ready_card(
    message: Message,
    state: FSMContext,
    *,
    category: str,
    index: int,
    edit: bool,
) -> None:
    data = await state.get_data()
    chat_id = message.chat.id
    bot = message.bot
    hub_cleared = False
    if data.get("_ready_category_album_ids"):
        await _purge_prior_ready_hub_ui(bot, chat_id, message, data)
        await state.update_data(_ready_category_album_ids=[])
        edit = False
        hub_cleared = True
    back_callback = str(data.get("_ready_back_cb") or CB_MENU_BACK_START)
    include_hidden = bool(data.get("_ready_include_hidden_start_only"))
    ready_user_id = int(data.get("_ready_user_id") or 0)
    if not ready_user_id and message.from_user:
        ready_user_id = message.from_user.id
    ready_mode = await _live_user_ready_mode(ready_user_id) if ready_user_id else _READY_MODE_DEFAULT
    ready_cost = (
        await _ready_idea_cost_for_user_mode(ready_user_id, ready_mode)
        if ready_user_id > 0
        else _ready_idea_cost_for_plan_and_mode(None, ready_mode)
    )
    ideas = _ideas_for_category(category, include_hidden_start_only=include_hidden)
    if not ideas:
        empty_txt = "<b>В этой категории пока пусто.</b>\n<blockquote><i>Выбери другое направление.</i></blockquote>"
        kb = _ready_categories_keyboard(back_callback=back_callback)
        if hub_cleared:
            await bot.send_message(chat_id, empty_txt, reply_markup=kb, parse_mode=HTML)
        elif edit:
            await _edit_ready_nav_message(
                message,
                caption=empty_txt,
                reply_markup=kb,
                listing_photo=_ready_categories_listing_photo(),
            )
        else:
            await message.answer(empty_txt, reply_markup=kb, parse_mode=HTML)
        await state.set_state(ImageGenState.ready_choosing_category)
        return
    total = len(ideas)
    idx = index % total
    title, preview, _prompt, photos_required = ideas[idx]
    single_shortcut_mode = include_hidden and category == "celebrities" and (title or "").strip() == _RONALDO_PHOTO_TITLE
    cap = _ready_idea_caption(
        category=category,
        title=title,
        preview=preview,
        index=idx,
        total=total,
        photos_required=photos_required,
        cost=ready_cost,
        mode=ready_mode,
        show_category_title=not single_shortcut_mode,
    )
    await state.update_data(
        _ready_category=category,
        _ready_index=idx,
        _ready_cost=ready_cost,
        _ready_mode=ready_mode,
    )
    await state.set_state(ImageGenState.ready_browsing_idea)
    kb = _ready_browser_keyboard(
        idx,
        total,
        back_callback=back_callback,
        category_slug=category,
        single_shortcut_mode=single_shortcut_mode,
    )
    photo_path = _ready_idea_listing_photo_path(title)

    if edit and photo_path is not None and not message.photo:
        # Текст нельзя заменить на фото через edit — только delete + send или второе сообщение.
        try:
            try:
                await message.delete()
            except Exception:
                logging.debug("ready card: delete text msg before photo", exc_info=True)
            sent = await bot.send_photo(
                chat_id,
                photo=FSInputFile(photo_path),
                caption=cap,
                reply_markup=kb,
                parse_mode=HTML,
            )
            await _set_img_flow_anchor(state, sent)
            return
        except Exception:
            logging.warning("ready card send_photo failed, fallback", exc_info=True)

    if edit:
        edited = await _edit_ready_nav_message(message, caption=cap, reply_markup=kb, listing_photo=photo_path)
        await _set_img_flow_anchor(state, edited or message)
    else:
        if photo_path is not None:
            try:
                if hub_cleared:
                    sent = await bot.send_photo(
                        chat_id,
                        photo=FSInputFile(photo_path),
                        caption=cap,
                        reply_markup=kb,
                        parse_mode=HTML,
                    )
                else:
                    sent = await message.answer_photo(
                        photo=FSInputFile(photo_path),
                        caption=cap,
                        reply_markup=kb,
                        parse_mode=HTML,
                    )
                await _set_img_flow_anchor(state, sent)
                return
            except Exception:
                logging.warning("ready card answer_photo failed, fallback text", exc_info=True)
        if hub_cleared:
            sent = await bot.send_message(chat_id, cap, reply_markup=kb, parse_mode=HTML)
        else:
            sent = await message.answer(cap, reply_markup=kb, parse_mode=HTML)
        await _set_img_flow_anchor(state, sent)


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
    payload = callback.data.replace(CB_READY_NAV_PREFIX, "", 1)
    if payload == "back_cats":
        data = await state.get_data()
        back_callback = str(data.get("_ready_back_cb") or CB_MENU_BACK_START)
        await state.update_data(_ready_include_hidden_start_only=False)
        await state.set_state(ImageGenState.ready_choosing_category)
        chat_id = callback.message.chat.id
        bot = callback.message.bot
        cap = _ready_category_caption()
        kb = _ready_categories_keyboard(back_callback=back_callback)
        paths = _ready_ideas_category_hub_photo_paths()
        ok_paths = [p for p in paths if p.is_file()]
        listing = ok_paths[0] if ok_paths else None
        msg = callback.message
        if not _is_generated_image_result_message(msg):
            ok = await replace_nav_screen_in_message(
                msg,
                caption_html=cap,
                reply_markup=kb,
                new_media_path=listing,
            )
            if ok:
                await state.update_data(_ready_category_album_ids=[msg.message_id])
                await _set_img_flow_anchor(state, msg)
                await callback.answer()
                return
        try:
            await msg.delete()
        except Exception:
            logging.debug("ready back_cats: delete before hub resend failed", exc_info=True)
        first, album_ids = await _send_ready_hub_messages(bot, chat_id, cap, kb, paths)
        await state.update_data(_ready_category_album_ids=album_ids)
        await _set_img_flow_anchor(state, first)
        await callback.answer()
        return
    parts = payload.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Некорректная навигация.", show_alert=True)
        return
    action, idx_raw = parts[0], parts[1]
    data = await state.get_data()
    category = str(data.get("_ready_category") or "").strip().lower()
    include_hidden = bool(data.get("_ready_include_hidden_start_only"))
    ideas = _ideas_for_category(category, include_hidden_start_only=include_hidden)
    if not ideas:
        await callback.answer("Категория недоступна.", show_alert=True)
        return
    total = len(ideas)
    idx = int(idx_raw) % total
    current_idx = int(data.get("_ready_index") or 0) % total

    if action in ("prev", "next"):
        # При одном варианте стрелки ведут на тот же индекс — не дублировать сообщение (edit «не изменился»).
        if idx == current_idx:
            await callback.answer("В этой категории только один вариант.")
            return
        await _open_ready_card(callback.message, state, category=category, index=idx, edit=True)
        await callback.answer()
        return
    if action == "pick":
        title, _preview, _prompt, photos_required = ideas[idx]
        ready_mode = await _live_user_ready_mode(callback.from_user.id)
        ready_cost = await _ready_idea_cost_for_user_mode(callback.from_user.id, ready_mode)
        await state.update_data(
            _ready_category=category,
            _ready_index=idx,
            _ready_cost=ready_cost,
            _ready_mode=ready_mode,
            _ready_photos=[],
            _ready_need=photos_required,
            _ready_overlay_nick="",
            _ready_poster_text="",
            _ready_beard_size="",
            _ready_fantasy_color="",
            _ready_strip_markup_message_ids=[],
        )
        if photos_required == 0 and (title or "").strip() == _FANTASY_3D_GAME_TITLE:
            await state.set_state(ImageGenState.ready_waiting_fantasy_headline)
            list_ph = _ready_idea_listing_photo_path(title)
            req0 = _ready_idea_requirement_line(title=title, photos_required=0)
            data_nav = await state.get_data()
            await _edit_ready_nav_message(
                callback.message,
                caption=(
                    f'<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <b>{esc(title)}</b>\n'
                    f"{_ready_mode_line(ready_mode)}\n"
                    f"{_ready_generation_cost_html(ready_cost)}\n"
                    f"<b>{esc(req0)}</b>\n"
                    f"✍️ Пришли <b>текст заголовка</b> для логотипа (до {_FANTASY_HEADLINE_MAX_LEN} символов)."
                ),
                reply_markup=_ready_wait_photo_keyboard_for_state(data_nav),
                listing_photo=list_ph if list_ph is not None else _ready_categories_listing_photo(),
            )
            await callback.answer()
            return
        if photos_required == 0:
            await callback.answer("Эта идея пока недоступна.", show_alert=True)
            return
        await state.set_state(ImageGenState.ready_waiting_photos)
        first_hint = _ready_photo_upload_hint(
            category=category, need=photos_required, received=0, idea_title=title
        )
        data_nav = await state.get_data()
        await _edit_ready_nav_message(
            callback.message,
            caption=(
                f'<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <b>{esc(title)}</b>\n'
                f"{_ready_mode_line(ready_mode)}\n"
                f"{_ready_generation_cost_html(ready_cost)}\n"
                f"{first_hint}\n"
                '<tg-emoji emoji-id="5235837920081887219">📸</tg-emoji> Скинь фото, после загрузки появится кнопка подтверждения.'
            ),
            reply_markup=_ready_wait_photo_keyboard_for_state(data_nav),
            listing_photo=_ready_idea_listing_photo_path(title) or _ready_categories_listing_photo(),
        )
        await callback.answer()
        return
    await callback.answer("Неизвестное действие.", show_alert=True)


@router.callback_query(F.data == CB_READY_PHOTO_BACK)
async def ready_photo_back(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    uid = callback.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    if bool(data.get("_ready_include_hidden_start_only")):
        category = str(data.get("_ready_category") or "").strip().lower()
        need = int(data.get("_ready_need") or 1)
        photos = list(data.get("_ready_photos") or [])
        title = _ready_title_from_state_data(data) or _RONALDO_PHOTO_TITLE
        hint = _ready_photo_upload_hint(
            category=category,
            need=need,
            received=len(photos),
            idea_title=title,
        )
        mellstroy_note = ""
        if (title or "").strip() == _MELLSTROY_PHOTO_TITLE:
            mellstroy_note = "\n<blockquote><i>Попал на скрытую тусовку к Мелу.</i></blockquote>"
        await state.set_state(ImageGenState.ready_waiting_photos)
        await _edit_ready_nav_message(
            callback.message,
            caption=(
                f"<b>Выбрано:</b> {esc(title)}\n"
                f"{_ready_mode_line(ready_mode)}\n"
                f"{_ready_generation_cost_html(ready_cost)}\n"
                f"{hint}"
                f"{mellstroy_note}"
            ),
            reply_markup=_ready_wait_photo_keyboard_for_state(data),
            listing_photo=_ready_idea_listing_photo_path(title) or _ready_categories_listing_photo(),
        )
        return
    back_callback = str(data.get("_ready_back_cb") or CB_MENU_BACK_START)
    category = str(data.get("_ready_category") or "").strip().lower()
    idx = int(data.get("_ready_index") or 0)
    if not category:
        await _send_ready_ideas_screen(
            callback.message,
            state,
            callback.from_user.id,
            callback.from_user.username,
            edit=True,
            back_callback=back_callback,
        )
        return
    await _open_ready_card(callback.message, state, category=category, index=idx, edit=True)


@router.message(ImageGenState.ready_waiting_photos, ~F.photo)
async def ready_need_photo_hint(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ready_cost = int(data.get("_ready_cost") or _READY_IDEA_DEFAULT_COST)
    category = str(data.get("_ready_category") or "").strip().lower()
    need = int(data.get("_ready_need") or 1)
    photos = list(data.get("_ready_photos") or [])
    hint = _ready_photo_upload_hint(
        category=category,
        need=need,
        received=len(photos),
        idea_title=_ready_title_from_state_data(data),
    )
    sent = await message.answer(
        f"{hint}\nКогда соберем нужное количество, появится подтверждение.",
        reply_markup=_ready_wait_photo_keyboard_for_state(data),
        parse_mode=HTML,
    )
    extras = list(data.get("_ready_strip_markup_message_ids") or [])
    extras.append(sent.message_id)
    await state.update_data(_ready_strip_markup_message_ids=extras)


@router.message(ImageGenState.ready_waiting_photos, F.photo)
async def ready_collect_photos(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    uid = message.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    need = int(data.get("_ready_need") or 1)
    photos = list(data.get("_ready_photos") or [])
    if len(photos) >= need:
        data_full = await state.get_data()
        await _strip_ready_flow_inline_keyboards(message.bot, message.chat.id, data_full)
        await message.answer("Фото уже загружены. Нажми «Подтвердить» или «Отмена».", reply_markup=_ready_confirm_keyboard())
        return
    ph = message.photo[-1]
    if not ph.file_id:
        await message.answer("Не удалось прочитать фото, попробуй ещё раз.")
        return
    photos.append(ph.file_id)
    await state.update_data(_ready_photos=photos)
    data_after = await state.get_data()
    await _strip_ready_flow_inline_keyboards(message.bot, message.chat.id, data_after)
    if len(photos) < need:
        hint = _ready_photo_upload_hint(
            category=str(data_after.get("_ready_category") or ""),
            need=need,
            received=len(photos),
            idea_title=_ready_title_from_state_data(data_after),
        )
        sent = await message.answer(
            hint,
            reply_markup=_ready_wait_photo_keyboard_for_state(data_after),
            parse_mode=HTML,
        )
        extras = list(data_after.get("_ready_strip_markup_message_ids") or [])
        extras.append(sent.message_id)
        await state.update_data(_ready_strip_markup_message_ids=extras)
        return
    data = await state.get_data()
    if str(data.get("_ready_redo_prompt") or "").strip():
        await state.set_state(ImageGenState.ready_waiting_confirm)
        charge_show = int(data.get("_ready_redo_charge") or 0)
        await message.answer(
            "<b>Фото приняты.</b>\n"
            f"<blockquote><i>К списанию: <b>{charge_show}</b> кр. Нажми «Подтвердить», чтобы запустить тот же сценарий.</i></blockquote>",
            reply_markup=_ready_confirm_keyboard(),
            parse_mode=HTML,
        )
        return
    category = str(data.get("_ready_category") or "").strip().lower()
    idx = int(data.get("_ready_index") or 0)
    include_hidden = bool(data.get("_ready_include_hidden_start_only"))
    ideas = _ideas_for_category(category, include_hidden_start_only=include_hidden)
    title = ideas[idx][0] if ideas and 0 <= idx < len(ideas) else ""
    if _is_minecraft_ready_idea(title, ideas[idx][2] if ideas and 0 <= idx < len(ideas) else ""):
        await state.set_state(ImageGenState.ready_waiting_minecraft_nick)
        await message.answer(
            (
                f"{_ready_photo_upload_hint(category=category, need=need, received=len(photos), idea_title=title)}\n"
                "<b>Фото зафиксированы.</b>\n"
                "<blockquote><i>Теперь пришли ник для надписи над головой (до 30 символов).</i></blockquote>"
            ),
            reply_markup=_ready_wait_photo_keyboard(),
            parse_mode=HTML,
        )
        return
    if (title or "").strip() == _BEARD_MUSTACHE_TITLE:
        await state.set_state(ImageGenState.ready_waiting_beard_size)
        await message.answer(
            (
                f"{_ready_photo_upload_hint(category=category, need=need, received=len(photos), idea_title=title)}\n"
                "<b>Фото зафиксированы.</b>\n"
                "<blockquote><i>Теперь выбери размер бороды: короткая, средняя или длинная.</i></blockquote>"
            ),
            reply_markup=_ready_beard_size_keyboard(),
            parse_mode=HTML,
        )
        return
    if _ready_idea_needs_headline_input(title):
        await state.set_state(ImageGenState.ready_waiting_poster_text)
        if (title or "").strip() == _FLUFFY_LETTERS_TITLE:
            head_hint = (
                f"<blockquote><i>Теперь введи слово или короткую фразу (до {_FLUFFY_TEXT_MAX_LEN} символов, пробелы считаются) — "
                "из неё соберут пушистые 3D-буквы с мордочками. Лицо берётся только с фото выше.</i></blockquote>"
            )
        elif (title or "").strip() == _PLASTER_FASHION_STUDIO_TITLE:
            head_hint = (
                "<blockquote><i>Теперь пришли <b>текст для гипсовых букв</b> — кириллица или латиница, лаконично, как на обложке. "
                "Можно две строки через перевод строки: верхний и нижний ярус скульптуры. "
                "Слова лягут на объёмные гипсовые буквы рядом с тобой.</i></blockquote>"
            )
        elif (title or "").strip() == _CHALK_ON_ASPHALT_TITLE:
            head_hint = (
                f"<blockquote><i>Теперь пришли <b>надпись мелом</b> над портретом (до {_CHALK_ASPHALT_TEXT_MAX_LEN} символов) — кириллица или латиница. "
                "Можно несколько строк через перевод строки. Рисунок на асфальте строится по твоему фото выше.</i></blockquote>"
            )
        else:
            head_hint = (
                f"<blockquote><i>Теперь пришли текст для заголовка на постере (до {_POSTER_TEXT_MAX_LEN} символов). "
                "Он будет встроен в картинку и по цвету/настроению подстроен под сцену.</i></blockquote>"
            )
        await message.answer(
            (
                f"{_ready_photo_upload_hint(category=category, need=need, received=len(photos), idea_title=title)}\n"
                "<b>Фото зафиксированы.</b>\n"
                f"{_ready_mode_line(ready_mode)}\n"
                f"{_ready_generation_cost_html(ready_cost)}\n"
                f"{head_hint}"
            ),
            reply_markup=_ready_wait_photo_keyboard(),
            parse_mode=HTML,
        )
        return
    await state.set_state(ImageGenState.ready_waiting_confirm)
    await message.answer(
        (
            f"{_ready_photo_upload_hint(category=category, need=need, received=len(photos), idea_title=title)}\n"
            f"<b>Фото зафиксированы:</b> <b>{esc(len(photos))}</b>\n"
            f"{_ready_mode_line(ready_mode)}\n"
            f"{_ready_generation_cost_html(ready_cost)}\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        parse_mode=HTML,
    )


@router.message(ImageGenState.ready_waiting_minecraft_nick, ~F.text)
async def ready_minecraft_nick_need_text(message: Message) -> None:
    await message.answer(
        "Пришли ник текстом (до 30 символов).",
        reply_markup=_ready_wait_photo_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_poster_text, ~F.text)
async def ready_poster_text_need_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    t = _ready_title_from_state_data(data)
    mx = _headline_max_len_for_title(t)
    await message.answer(
        f"Пришли текст одним сообщением (до {mx} символов).",
        reply_markup=_ready_wait_photo_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_poster_text)
async def ready_collect_poster_text(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    uid = message.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    title = _ready_title_from_state_data(data)
    max_len = _headline_max_len_for_title(title)
    raw = message.text or ""
    if not raw.strip():
        await message.answer("Текст не должен быть пустым. Введи ещё раз.")
        return
    if len(raw) > max_len:
        await message.answer(
            f"Слишком длинно. Максимум {max_len} символов (с пробелами). Сократи и отправь снова."
        )
        return
    await state.update_data(_ready_poster_text=raw)
    await state.set_state(ImageGenState.ready_waiting_confirm)
    if title == _FLUFFY_LETTERS_TITLE:
        label = "Текст для букв"
    elif title == _PLASTER_FASHION_STUDIO_TITLE:
        label = "Текст на гипсе"
    elif title == _CHALK_ON_ASPHALT_TITLE:
        label = "Надпись мелом"
    else:
        label = "Текст заголовка"
    await message.answer(
        (
            f"<b>{esc(label)}:</b> <code>{esc(raw)}</code>\n"
            f"{_ready_mode_line(ready_mode)}\n"
            f"{_ready_generation_cost_html(ready_cost)}\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        parse_mode=HTML,
    )


@router.message(ImageGenState.ready_waiting_fantasy_headline, ~F.text)
async def ready_fantasy_headline_need_text(message: Message) -> None:
    await message.answer(
        "Пришли заголовок одним текстовым сообщением (латиница или кириллица).",
        reply_markup=_ready_wait_photo_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_fantasy_headline)
async def ready_collect_fantasy_headline(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = _ready_title_from_state_data(data)
    if (title or "").strip() != _FANTASY_3D_GAME_TITLE:
        await state.set_state(ImageGenState.ready_browsing_idea)
        await message.answer("Сначала выбери идею «3D заголовок фэнтези-игры» в готовых идеях.")
        return
    max_len = _headline_max_len_for_title(title)
    raw = message.text or ""
    if not raw.strip():
        await message.answer("Текст не должен быть пустым. Введи заголовок ещё раз.")
        return
    if len(raw) > max_len:
        await message.answer(
            f"Слишком длинно. Максимум {max_len} символов (с пробелами). Сократи и отправь снова."
        )
        return
    await state.update_data(_ready_poster_text=raw)
    await state.set_state(ImageGenState.ready_waiting_fantasy_color)
    await message.answer(
        (
            f"<b>Заголовок:</b> <code>{esc(raw)}</code>\n"
            "<blockquote><i>Теперь введи <b>базовый цвет по-русски</b> одним словом "
            "(например: <b>Синий</b>) — без узких оттенков вроде «лавандовый». "
            "Пробелы и регистр сохраним, как напишешь.</i></blockquote>"
        ),
        reply_markup=_ready_wait_photo_keyboard(),
        parse_mode=HTML,
    )


@router.message(ImageGenState.ready_waiting_fantasy_color, ~F.text)
async def ready_fantasy_color_need_text(message: Message) -> None:
    await message.answer(
        "Пришли цвет текстом по-русски — например: Синий",
        reply_markup=_ready_wait_photo_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_fantasy_color)
async def ready_collect_fantasy_color(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    color = " ".join(raw.split())
    if not color:
        await message.answer("Цвет не должен быть пустым. Например: Синий")
        return
    if len(color) > _FANTASY_COLOR_MAX_LEN:
        await message.answer(
            f"Слишком длинно — до {_FANTASY_COLOR_MAX_LEN} символов. Одно базовое слово цвета."
        )
        return
    if not _has_cyrillic(color):
        await message.answer("Введи цвет по-русски одним словом — например: Синий")
        return
    if not message.from_user:
        return
    data = await state.get_data()
    uid = message.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    headline = str(data.get("_ready_poster_text") or "")
    await state.update_data(_ready_fantasy_color=color)
    await state.set_state(ImageGenState.ready_waiting_confirm)
    await message.answer(
        (
            f"<b>Заголовок:</b> <code>{esc(headline)}</code>\n"
            f"<b>Цвет:</b> <code>{esc(color)}</code>\n"
            f"{_ready_mode_line(ready_mode)}\n"
            f"{_ready_generation_cost_html(ready_cost)}\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        parse_mode=HTML,
    )


@router.message(ImageGenState.ready_waiting_minecraft_nick)
async def ready_collect_minecraft_nick(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    nick = raw.lstrip("@").strip()
    if not nick:
        await message.answer("Ник не должен быть пустым. Введи ник (до 30 символов).")
        return
    if len(nick) > 30:
        await message.answer("Слишком длинный ник. Максимум 30 символов.")
        return
    if not message.from_user:
        return
    data = await state.get_data()
    uid = message.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    await state.update_data(_ready_overlay_nick=nick)
    await state.set_state(ImageGenState.ready_waiting_confirm)
    await message.answer(
        (
            f"<b>Ник сохранён:</b> <code>@{esc(nick)}</code>\n"
            f"{_ready_mode_line(ready_mode)}\n"
            f"{_ready_generation_cost_html(ready_cost)}\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query(F.data.startswith(CB_READY_BEARD_SIZE_PREFIX))
async def ready_pick_beard_size(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None or not callback.data:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    raw = callback.data.replace(CB_READY_BEARD_SIZE_PREFIX, "", 1).strip().lower()
    labels = {
        "short": "короткая",
        "medium": "средняя",
        "long": "длинная",
    }
    label = labels.get(raw)
    if label is None:
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    uid = callback.from_user.id
    ready_mode = await _live_user_ready_mode(uid)
    ready_cost = await _ready_idea_cost_for_user_mode(uid, ready_mode)
    await state.update_data(_ready_beard_size=raw)
    await state.set_state(ImageGenState.ready_waiting_confirm)
    await _edit_ready_nav_message(
        callback.message,
        caption=(
            f"<b>Размер бороды:</b> <code>{esc(label)}</code>\n"
            f"{_ready_mode_line(ready_mode)}\n"
            f"{_ready_generation_cost_html(ready_cost)}\n"
            "<blockquote><i>Нажми «Подтвердить», и бот запустит генерацию по выбранной идее.</i></blockquote>"
        ),
        reply_markup=_ready_confirm_keyboard(),
        listing_photo=_ready_categories_listing_photo(),
    )


# Ко всем готовым идеям с референсом лица (добавляется в _build_ready_prompt).
_READY_IDEA_UNISEX_GLOBAL = (
    "GLOBAL UNISEX / PRESENTATION: From the reference photo(s), infer apparent gender presentation for each "
    "mapped identity. Match wardrobe, hairstyle, body silhouette, and any gendered styling to that presentation "
    "(avoid defaulting to a male or female look when the face suggests otherwise). In multi-subject scenes, apply "
    "per mapped person. If ambiguous, use neutral refined styling consistent with the face and build."
)


def _build_ready_prompt(
    base_prompt: str,
    telegram_username: str | None,
    *,
    include_telegram_nick: bool = False,
    refs_hint: str | None = None,
    skip_identity_lock_footer: bool = False,
    no_reference_images: bool = False,
    style_reference_images_only: bool = False,
) -> str:
    nick = (telegram_username or "").strip()
    nick_part = (
        f"Telegram nickname to render above the head: @{nick}\n"
        if include_telegram_nick and nick
        else ""
    )
    hint_part = f"{refs_hint.strip()}\n" if refs_hint and refs_hint.strip() else ""
    if no_reference_images:
        footer = (
            "No user reference photographs — generate purely from the instructions above. "
            "Single square 1:1 key-art image, ultra high quality, no watermark, no extra UI."
        )
    elif style_reference_images_only:
        footer = (
            "Attached reference image(s) are STYLE/MOOD guides only (materials, runes, particles, lighting, atmosphere). "
            "Obey the TEXT instructions above for exact headline spelling and color — do not copy unrelated wording from the reference. "
            "Single square 1:1 key-art, ultra high quality, no watermark, no extra UI."
        )
    elif skip_identity_lock_footer:
        footer = (
            "Use both reference images as described above. "
            "If image #1 shows a person, keep face recognizable; if it shows only an object, preserve its materials and silhouette. "
            "Deliver one seamless final photograph."
        )
    else:
        footer = (
            f"{_READY_IDEA_UNISEX_GLOBAL}\n\n"
            "Use all reference images from input. Preserve facial identity and natural skin texture. "
            "Output must be clean: no watermark, no platform/app logo, no generated-by label, no random corner signature."
        )
    return (
        f"{(base_prompt or '').strip()}\n\n"
        f"{nick_part}"
        f"{hint_part}"
        f"{footer}"
    )


@router.callback_query(F.data == CB_READY_CONFIRM)
async def ready_confirm_and_generate(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    data_pre = await state.get_data()
    back_callback = str(data_pre.get("_ready_back_cb") or CB_MENU_BACK_START)
    if bool(data_pre.get("_ready_confirm_inflight")):
        await callback.answer("Запуск уже идёт…", show_alert=False)
        return
    await state.update_data(_ready_confirm_inflight=True)
    # Сразу снимаем «часики» у кнопки, чтобы клик всегда ощущался.
    await callback.answer()
    try:
        if not is_openrouter_image_configured():
            await _edit_ready_nav_message(
                callback.message,
                caption=_IMAGE_GEN_MISSING_TEXT,
                reply_markup=_missing_config_kb(),
                listing_photo=_ready_categories_listing_photo(),
            )
            return
        data = await state.get_data()
        back_callback = str(data.get("_ready_back_cb") or back_callback)
        redo_prompt = str(data.get("_ready_redo_prompt") or "").strip()
        if redo_prompt:
            photos_redo = list(data.get("_ready_photos") or [])
            need_r = int(data.get("_ready_need") or 0)
            model_r = str(data.get("_ready_redo_model") or "").strip()
            charge_r = int(data.get("_ready_redo_charge") or 0)
            consume_half = bool(data.get("_ready_redo_consume_half"))
            title_r = str(data.get("_ready_redo_title") or "").strip()
            if len(photos_redo) < need_r or need_r <= 0:
                await callback.answer("Сначала загрузи нужное число фото.", show_alert=True)
                return
            await state.clear()
            uid = callback.from_user.id
            await ensure_user(uid, callback.from_user.username)
            await _execute_ready_with_refs_generation(
                callback.message,
                state,
                user_id=uid,
                username=callback.from_user.username,
                prompt=redo_prompt,
                cost=charge_r,
                refs_file_ids=photos_redo,
                model_override=model_r if model_r else None,
                overlay_nick=None,
                extra_refs=None,
                extra_refs_first=False,
                strict_refs=False,
                ready_idea_title=title_r,
                mark_redo_half_after_success=consume_half,
            )
            return
        category = str(data.get("_ready_category") or "").strip().lower()
        include_hidden = bool(data.get("_ready_include_hidden_start_only"))
        idx_raw = data.get("_ready_index")
        try:
            idx = int(idx_raw if idx_raw is not None else 0)
        except (TypeError, ValueError):
            idx = -1
        photos = list(data.get("_ready_photos") or [])
        ideas = _ideas_for_category(category, include_hidden_start_only=include_hidden)
        if not ideas or idx < 0 or idx >= len(ideas):
            await callback.answer("Идея не найдена. Выбери заново.", show_alert=True)
            await _send_ready_ideas_screen(
                callback.message,
                state,
                callback.from_user.id,
                callback.from_user.username,
                edit=True,
                back_callback=back_callback,
            )
            return
        title, _preview, base_prompt, need = ideas[idx]
        user_id = callback.from_user.id
        ready_mode = await _live_user_ready_mode(user_id)
        is_ronaldo_ready = (title or "").strip() == _RONALDO_PHOTO_TITLE
        if len(photos) < need:
            await _edit_ready_nav_message(
                callback.message,
                caption="Сначала загрузи нужное число фото.",
                reply_markup=_ready_confirm_keyboard(),
                listing_photo=_ready_categories_listing_photo(),
            )
            return
        overlay_nick_saved = str(data.get("_ready_overlay_nick") or "").strip()
        poster_text_raw = str(data.get("_ready_poster_text") or "")
        beard_size_raw = str(data.get("_ready_beard_size") or "").strip().lower()
        fantasy_color_raw = str(data.get("_ready_fantasy_color") or "").strip()
        is_minecraft_ready = _is_minecraft_ready_idea(title, base_prompt)
        needs_headline = _ready_idea_needs_headline_input(title)
        if is_minecraft_ready and not overlay_nick_saved:
            await callback.answer("Сначала введи ник (до 30 символов).", show_alert=True)
            await state.set_state(ImageGenState.ready_waiting_minecraft_nick)
            return
        if (title or "").strip() == _FANTASY_3D_GAME_TITLE:
            if not poster_text_raw.strip():
                await callback.answer("Сначала введи текст заголовка.", show_alert=True)
                await state.set_state(ImageGenState.ready_waiting_fantasy_headline)
                return
            if not fantasy_color_raw:
                await callback.answer("Сначала введи базовый цвет по-русски (например: Синий).", show_alert=True)
                await state.set_state(ImageGenState.ready_waiting_fantasy_color)
                return
        if needs_headline and not poster_text_raw.strip():
            await callback.answer("Сначала введи текст.", show_alert=True)
            await state.set_state(ImageGenState.ready_waiting_poster_text)
            return
        if (title or "").strip() == _BEARD_MUSTACHE_TITLE and beard_size_raw not in {"short", "medium", "long"}:
            await callback.answer("Сначала выбери размер бороды.", show_alert=True)
            await state.set_state(ImageGenState.ready_waiting_beard_size)
            await _edit_ready_nav_message(
                callback.message,
                caption=(
                    "<b>Выбери размер бороды</b>\n"
                    "<blockquote><i>Выбери один вариант: короткая, средняя или длинная.</i></blockquote>"
                ),
                reply_markup=_ready_beard_size_keyboard(),
                listing_photo=_ready_categories_listing_photo(),
            )
            return
        mmorpg_pick: tuple[str, str] | None = None
        luxury_cover_pick: tuple[str, str, str, str, str] | None = None
        superhero_pick: tuple[str, str] | None = None
        if (title or "").strip() == _MMORPG_HERO_TITLE:
            mmorpg_pick = _pick_mmorpg_race_class(callback.from_user.id)
            pick_race, pick_class = mmorpg_pick
            base_prompt = (
                f"{base_prompt} "
                f"FOR THIS GENERATION (HARD LOCK): selected race = {pick_race}; selected class = {pick_class}. "
                "Do not pick or mix any other race/class in this run."
            )
        if (title or "").strip() == _LUXURY_TORN_COVER_TITLE:
            luxury_cover_pick = _pick_luxury_cover_vars()
        if (title or "").strip() == _SUPERHERO_MIRROR_TITLE:
            superhero_pick = _pick_superhero_vars(callback.from_user.id)
        # Для Minecraft-идеи ник берём из шага ввода и передаём в prompt как точный текст над головой.
        include_nick = False
        overlay_nick = None
        model_override = None
        if title in ("UFC: лицом к лицу с Макгрегором", "Ступени у огня", _MMORPG_HERO_TITLE):
            model_override = (OPENROUTER_IMAGE_GEMINI_PRO_MODEL or "").strip() or (
                "google/gemini-2.5-flash-image-pro"
            )
        elif (title or "").strip() == _FANTASY_3D_GAME_TITLE:
            # Nano Banana Pro: 3D-титул / руны; пользователь без фото, опционально статический стиль-референс в репо
            model_override = (OPENROUTER_IMAGE_GEMINI_PRO_MODEL or "").strip() or (
                "google/gemini-2.5-flash-image-pro"
            )
        elif title == "На отдыхе в Италии":
            model_override = (OPENROUTER_IMAGE_MODEL_ALT or "").strip()
        elif title in ("Оранжевый", "Чёрный студийный", "Бордовый кино-портрет"):
            model_override = (OPENROUTER_IMAGE_MODEL_ALT or "").strip() or (
                "black-forest-labs/flux.2-pro"
            )
        elif title in ("GTA Vice City", _GTA_V_REALISM_TITLE):
            # Nano Banana 2 (preview): сцена с референсом лица.
            model_override = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or (
                "google/gemini-3.1-flash-image-preview"
            )
        elif title == "Ростомер":
            # Nano Banana 2: лицо по фото + диптих/сцена; Flux Pro хуже с референсом лица.
            model_override = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or (
                "google/gemini-3.1-flash-image-preview"
            )
        elif title in (
            "Красивый костюм с букетом",
            "Gucci editorial",
            _LUXURY_TORN_COVER_TITLE,
            _SUPERHERO_MIRROR_TITLE,
        ):
            model_override = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or (
                "google/gemini-3.1-flash-image-preview"
            )
        elif title in (
            _POSTER_TEXT_READY_TITLE,
            _FLUFFY_LETTERS_TITLE,
            _PLASTER_FASHION_STUDIO_TITLE,
            _SONY_ERICSSON_T100_TITLE,
            _CHALK_ON_ASPHALT_TITLE,
        ):
            # Nano Banana 2 (preview): типографика/сцена; для постера и пушистых букв — ещё референс лица; см. .env
            model_override = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or (
                "google/gemini-3.1-flash-image-preview"
            )
        elif title == _OBJECT_IN_SCENE_TITLE:
            model_override = (OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL or "").strip() or (
                "google/gemini-3.1-flash-image-preview"
            )
        extra_refs: list[bytes] = []
        static_ref = _READY_IDEA_STATIC_REF_BY_TITLE.get(title)
        if title in ("Clash Royale", "На отдыхе в Италии", "Game of Thrones"):
            static_ref = None
        if static_ref:
            ref_paths = [static_ref] if isinstance(static_ref, str) else list(static_ref)
            for ref_path in ref_paths:
                p = Path(ref_path)
                if p.is_file():
                    try:
                        extra_refs.append(p.read_bytes())
                    except OSError:
                        logging.warning("Failed to read static ready ref: %s", ref_path)
                else:
                    logging.warning("Static ready ref is missing: %s", ref_path)
        if (title or "").strip() == _FANTASY_3D_GAME_TITLE:
            hq = json.dumps(poster_text_raw, ensure_ascii=False)
            cq = json.dumps(fantasy_color_raw, ensure_ascii=False)
            refs_hint = (
                "FANTASY KEY-ART (no attached style image): create crystalline/stone 3D lettering energy, floating runes, magical particles, "
                "dark-fantasy atmosphere matching the headline tone — purely from instructions below, not from any reference photo. "
                f"3D game TITLE logotype: reproduce EXACT characters, spaces, and capitalization: {hq}. "
                f"BASE_COLOR_RUSSIAN — one user-chosen base hue word (broad color family; exact user string): {cq}. "
                "Infer MOOD and WORLD_STYLE from headline tone + that base color. "
                "Hero subject = only the headline letterforms; no subtitles or extra copy."
            )
        else:
            refs_hint = "Reference mapping: image #1 is user identity photo."
            if title == "UFC: лицом к лицу с Макгрегором":
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity only (left profile in the final frame — face from this upload). "
                    "No second reference image: synthesize Conor McGregor likeness on the right (profile, beard, purple check suit), Dana White behind/between, "
                    "and UFC promo backdrop per base prompt text only — do not rely on an attached Conor photo."
                )
            if title == "Бордовый кино-портрет":
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity only — face, skin, age, hair from this upload. "
                    "No second reference image: match deep burgundy/maroon background, studio lighting quality, and cinematic mood "
                    "purely from the base prompt text — do not rely on an attached style reference photo."
                )
            if (title or "").strip() == _MMORPG_HERO_TITLE:
                pick_suffix = ""
                if mmorpg_pick is not None:
                    pr, pc = mmorpg_pick
                    pick_suffix = f" Forced choice for this run: race={pr}, class={pc}."
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity — face, body-type cues, and likeness (preserve this person). "
                    "Image #2 is Warcraft world/armor realism reference — use it for canon armor complexity, material richness, class silhouette variety, "
                    "and epic location mood only; do NOT copy identity/face/body from image #2."
                    f"{pick_suffix}"
                )
            if title == "Победа над Мухаммадом Али на ринге":
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity only — face and likeness from this upload. "
                    "No second reference image: render Muhammad Ali (era-accurate boxer look) from the base prompt text only."
                )
            if title == "Absolute Cinema":
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity photo (face/likeness to preserve). "
                    "Image #2 is the Absolute Cinema style/composition reference (pose, framing, monochrome mood, typography layout) — "
                    "do not copy identity from image #2."
                )
            if title == "Для влюбленных: рыцарь и дама":
                refs_hint = "Reference mapping: image #1 is knight identity photo. Image #2 is woman identity photo."
            if title == "Love is…":
                refs_hint = "Reference mapping: image #1 is the man identity photo. Image #2 is the woman identity photo."
            if title == _POLAROID_CURTAIN_TITLE:
                refs_hint = (
                    "Reference mapping: image #1 is the first uploaded subject's identity photo; image #2 is the second — "
                    "any pairing (humans or animals); order of uploads does not change roles: merge both recognizably into one Polaroid frame per base prompt."
                )
            if title == _OBJECT_IN_SCENE_TITLE:
                refs_hint = (
                    "Reference mapping: image #1 is the OBJECT or prop to place — main subject from photo #1 only, "
                    "ignore its original surroundings when building the final shot. "
                    "Image #2 is the DESTINATION environment — use this image as the base scene; "
                    "the subject from #1 must appear physically inside this scene with correct scale, lighting, and shadows."
                )
            if title == _BEARD_MUSTACHE_TITLE:
                beard_map = {
                    "short": "short (close-cropped, neat, dense short beard with connected moustache)",
                    "medium": "medium (full medium-length beard with natural volume and connected moustache)",
                    "long": "long (long dense beard with realistic weight/flow and connected moustache)",
                }
                beard_instruction = beard_map.get(
                    beard_size_raw,
                    "medium (full medium-length beard with natural volume and connected moustache)",
                )
                refs_hint = (
                    f"{refs_hint} FOR THIS GENERATION (HARD LOCK): beard_size={beard_instruction}. "
                    "Apply exactly this beard length; do not choose another size."
                )
            if title == _LUXURY_TORN_COVER_TITLE and luxury_cover_pick is not None:
                brand, country, color_name, color_tone, cover_text = luxury_cover_pick
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity photo (single person). "
                    "FOR THIS GENERATION (HARD LOCK): "
                    f"Luxury_Brand={brand}; Country={country}; Color1_hair={color_name}; "
                    f"Color2_theme={color_tone}; Text={cover_text}. "
                    "Apply these values exactly. Keep one-person torn-paper collage layout matching reference style with horizontal/diagonal ripped bands and layered close-up face slices. "
                    "Use bold left typography, brand tag at top-right, VOGUE+Country at bottom-left. "
                    "No QR code, no barcode. Subject framing must be knee-up or closer (not below knees). "
                    "Vary clothing design each run, but keep luxury editorial styling and color harmony with Color2_theme."
                )
            if title == _SUPERHERO_MIRROR_TITLE and superhero_pick is not None:
                universe, hero = superhero_pick
                refs_hint = (
                    "Reference mapping: image #1 is the USER identity photo (single person). "
                    f"FOR THIS GENERATION (HARD LOCK): Universe={universe}; Superhero={hero}. "
                    "Generate exactly ONE frame (no 2x2 collage, no multi-panel layout). "
                    "Keep the same person highly recognizable with maximum face fidelity, using a practical high-quality costume in a realistic mirror-selfie apartment setup. "
                    "Subject must be large in frame (knee-up or closer) with sharp facial detail."
                )
        if is_minecraft_ready and overlay_nick_saved:
            refs_hint = (
                f"{refs_hint} Render nickname above the head exactly once as: {overlay_nick_saved}. "
                "Use Minecraft nametag style (white text with dark shadow/background), centered above the head. "
                "Do not add any other text, usernames, HUD, subtitles, or UI."
            )
        if (title or "").strip() == _POSTER_TEXT_READY_TITLE and poster_text_raw.strip():
            quoted = json.dumps(poster_text_raw, ensure_ascii=False)
            refs_hint = (
                f"{refs_hint} TYPOGRAPHY / HEADLINE (mandatory). Render this headline EXACTLY once, same spelling and language: {quoted}. "
                "Color harmony: derive glyph fill, inner glow, outer halo, and thin highlight strokes from the strongest accent colors already present in the scene (emissive accents, rim lights, neon edges, fireflies) so the lettering matches the palette, temperature, and energy of the image. "
                "Style: heavy bold geometric sans-serif with slightly softened corners; optional mild translucency so background and VFX read faintly through letter bodies; micro-detail: hairline flowing energy trails, sparse sparkle or dust motes hugging the letter contours. "
                "Spatial integration: respect 3D depth — hair strands, glowing blobs, smoke, or foreground effects may partially occlude the text; the headline must feel embedded in the world, not pasted as a flat sticker. "
                "No extra copy, no watermark, no subtitle, no UI beyond this headline."
            )
        elif (title or "").strip() == _FLUFFY_LETTERS_TITLE and poster_text_raw.strip():
            quoted = json.dumps(poster_text_raw, ensure_ascii=False)
            refs_hint = (
                f"{refs_hint} FLUFFY 3D LETTER WORD (mandatory spelling): Spell EXACTLY this sequence of characters, same order and language, as separate giant 3D letterforms in a row: {quoted}. "
                "Each letter is a thick volumetric glyph fully covered in long soft fur/fuzz — individual strands visible, plush tactile toy/CG monster look; optional slight hue shift per letter for variety. "
                "Each letter integrates a cute goofy monster face: big cartoon eyes, thick expressive brows, mouth or grin — designer alphabet-mascot style (not gory). Soft studio lighting, subtle ground contact shadow, clean pastel or muted solid background that complements the fur colors. "
                "The person from image #1 stands IN FRONT of the letter row, closer to camera, full-body star pose as in base prompt; their fluffy suit matches the letters' fur color story. Face remains photoreal human from reference only. "
                "No extra words, no watermark, no subtitle beyond the specified letters."
            )
        elif (title or "").strip() == _PLASTER_FASHION_STUDIO_TITLE and poster_text_raw.strip():
            quoted = json.dumps(poster_text_raw, ensure_ascii=False)
            refs_hint = (
                f"{refs_hint} PLASTER TYPOGRAPHY (mandatory): Build the matte gray plaster 3D sculpture to spell EXACTLY this user text — same characters, spaces, line breaks, capitalization, and language: {quoted}. "
                "If the text contains a newline, treat it as two stacked tiers (upper line first, lower line second). If a single line, arrange as one cohesive plaster block or balanced two-line stack as fits the composition. "
                "No substitute wording, no URLs, no watermark on letters."
            )
        elif (title or "").strip() == _CHALK_ON_ASPHALT_TITLE and poster_text_raw.strip():
            quoted = json.dumps(poster_text_raw, ensure_ascii=False)
            refs_hint = (
                f"{refs_hint} CHALK QUOTE (mandatory): On the asphalt directly above the chalk portrait, letter EXACTLY this user text in multicolor sidewalk chalk — same characters, line breaks, spacing, capitalization, and language: {quoted}. "
                "Style: childlike uneven hand-drawn street lettering; pastel pink, yellow, blue, orange letters (vary hues across words/letters as in classic courtyard chalk art); must stay fully readable. "
                "The chalk portrait below must match image #1 per base prompt. No substitute wording, no extra lines of copy beyond this quote, no watermark."
            )
        is_fantasy_3d_title = (title or "").strip() == _FANTASY_3D_GAME_TITLE
        prompt = _build_ready_prompt(
            base_prompt,
            callback.from_user.username,
            include_telegram_nick=include_nick,
            refs_hint=refs_hint,
            skip_identity_lock_footer=(title.strip() == _OBJECT_IN_SCENE_TITLE),
            no_reference_images=(is_fantasy_3d_title and not extra_refs),
            style_reference_images_only=(is_fantasy_3d_title and bool(extra_refs)),
        )
        selected_ready_model = (
            (OPENROUTER_IMAGE_GEMINI_PRO_MODEL or "").strip() or "google/gemini-3-pro-image-preview"
            if is_ronaldo_ready
            else _ready_mode_model(ready_mode)
        )
        selected_ready_cost = (
            30
            if is_ronaldo_ready
            else await _ready_idea_cost_for_user_mode(user_id, ready_mode)
        )
        model_override = selected_ready_model
        await state.clear()
        await ensure_user(user_id, callback.from_user.username)
        strict_refs = True
        await _execute_ready_with_refs_generation(
            callback.message,
            state,
            user_id=user_id,
            username=callback.from_user.username,
            prompt=prompt,
            cost=selected_ready_cost,
            refs_file_ids=photos,
            model_override=model_override,
            overlay_nick=overlay_nick,
            extra_refs=extra_refs,
            extra_refs_first=False,
            strict_refs=strict_refs,
            ready_idea_title=title,
        )
    except Exception:
        logging.exception("ready_confirm_and_generate failed")
        await _edit_ready_nav_message(
            callback.message,
            caption="Ошибка запуска. Попробуй снова — открыл раздел «Готовые идеи».",
            reply_markup=None,
            listing_photo=_ready_categories_listing_photo(),
        )
        await _send_ready_ideas_screen(
            callback.message,
            state,
            callback.from_user.id,
            callback.from_user.username,
            edit=True,
            back_callback=back_callback,
        )
    finally:
        try:
            if await state.get_state() is not None:
                await state.update_data(_ready_confirm_inflight=False)
        except Exception:
            logging.debug("ready_confirm_and_generate: inflight reset failed", exc_info=True)


@router.message(ImageGenState.ready_choosing_category)
async def ready_choose_category_hint(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    back_callback = str(data.get("_ready_back_cb") or CB_MENU_BACK_START)
    await message.answer(
        "Сначала выбери категорию кнопками выше 👆",
        reply_markup=_ready_categories_keyboard(back_callback=back_callback),
    )


@router.message(ImageGenState.ready_browsing_idea)
async def ready_browse_hint(message: Message) -> None:
    await message.answer("Листай идеи стрелками и нажми «Выбрать».")


@router.message(ImageGenState.ready_waiting_confirm)
async def ready_waiting_confirm_hint(message: Message) -> None:
    await message.answer(
        "Нажми «✔️ Подтвердить» для запуска или «Отмена».",
        reply_markup=_ready_confirm_keyboard(),
    )


@router.message(ImageGenState.ready_waiting_beard_size)
async def ready_waiting_beard_size_hint(message: Message) -> None:
    await message.answer(
        "Выбери размер бороды кнопками: короткая / средняя / длинная.",
        reply_markup=_ready_beard_size_keyboard(),
    )


@router.callback_query((F.data == CB_CREATE_IMAGE) | (F.data == CB_CREATE_IMAGE_HUB))
async def open_image_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Ошибка запроса.", show_alert=True)
        return
    await callback.answer()
    uid = callback.from_user.id
    back_callback = CB_MENU_HUB if callback.data == CB_CREATE_IMAGE_HUB else CB_MENU_BACK_START
    await ensure_user(uid, callback.from_user.username)
    profile = await get_user_admin_profile(uid)
    has_sub = bool(profile and subscription_is_active(profile.subscription_ends_at))
    if uid in ADMIN_IDS or has_sub:
        await _show_image_model_pick(
            callback.message,
            state,
            uid,
            callback.from_user.username,
            back_callback=back_callback,
        )
    else:
        await _start_image_flow(
            callback.message,
            state,
            uid,
            callback.from_user.username,
            replace_menu=True,
            back_callback=back_callback,
        )


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
    """Та же модель и цена — пользователь вводит новый промпт; готовые идеи — повтор сценария или список идей."""
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
        if (ctx.ready_idea_title or "").strip() != _MELLSTROY_PHOTO_TITLE and ctx.refs_file_ids:
            await _start_ready_redo_flow(
                callback.message,
                state,
                user_id,
                callback.from_user.username,
                ctx,
            )
            return
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


@router.callback_query(F.data == CB_REGEN_READY_REDO)
async def ready_redo_from_button(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <b>Выбрано:</b> <i>Ещё раз</i>',
            parse_mode=HTML,
            reply_markup=None,
        )
    except Exception:
        logging.debug("ready_redo_from_button: mark panel failed", exc_info=True)
    ctx = await get_last_image_context(callback.from_user.id)
    if not ctx or ctx.usage_kind != "ready":
        await callback.message.answer("Нет данных для повтора. Открой «Готовые идеи» в меню.")
        return
    await _start_ready_redo_flow(
        callback.message,
        state,
        callback.from_user.id,
        callback.from_user.username,
        ctx,
    )


@router.callback_query(F.data == CB_READY_RESULT_MAIN_MENU)
async def ready_result_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Шорткат Роналдо: из результата — в главное меню (не в листинг готовых идей)."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <b>Выбрано:</b> <i>В меню</i>',
            parse_mode=HTML,
            reply_markup=None,
        )
    except Exception:
        logging.debug("ready_result_main_menu: edit failed", exc_info=True)
    await state.clear()
    await restore_main_menu_message(
        callback.message, callback.from_user.id, callback.from_user.username
    )


@router.callback_query(F.data == CB_BACK_TO_READY_IDEAS)
async def back_to_ready_ideas_from_result(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <b>Выбрано:</b> <i>К готовым идеям</i>',
            parse_mode=HTML,
            reply_markup=None,
        )
    except Exception:
        logging.debug("back_ready_ideas: edit panel failed", exc_info=True)
    await state.clear()
    await _send_ready_ideas_screen(
        callback.message,
        state,
        callback.from_user.id,
        callback.from_user.username,
        edit=False,
    )

