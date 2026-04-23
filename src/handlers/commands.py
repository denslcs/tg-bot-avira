"""
Команды и главное меню: /start, профиль, рефералка, справка, часть админ-команд в ЛС.
Клавиатура старта: src/keyboards/main_menu.py.
"""

import logging
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
)

from src.config import (
    ADMIN_IDS,
    CHANNEL_URL,
    PROJECT_ROOT,
    START_ANNOUNCEMENT,
    START_ANNOUNCEMENT_IMAGE,
    SUPPORT_BOT_USERNAME,
)
from src.antispam_state import reset_user_spam
from src.handlers.global_errors import USER_GENERIC_ERROR
from src.private_rate_limit import reset_private_rate
from src.database import (
    add_credits_with_reason,
    apply_referral,
    clear_dialog_messages,
    count_generated_images_total,
    ensure_user,
    get_budget_history_recent,
    get_credits,
    get_daily_image_generation_usage,
    get_nonsub_image_quota_status,
    get_referral_count,
    get_referral_subscription_bonus_total,
    get_referral_paid_count,
    get_user_admin_profile,
    get_user_ready_mode,
    subscription_is_active,
    set_user_ready_mode,
    take_credits_with_reason,
)
from src.subscription_catalog import PLANS
from src.formatting import (
    CREDITS_COIN_TG_HTML,
    HTML,
    PROFILE_AVATAR_TG_HTML,
    PROFILE_GENERATED_IMAGES_LABEL_TG_HTML,
    PROFILE_SUBSCRIPTION_LABEL_TG_HTML,
    PROFILE_VALID_UNTIL_LABEL_TG_HTML,
    all_plans_premium_line_html,
    esc,
    format_subscription_ends_at,
    plan_subscription_title_html,
)
from src.keyboards.callback_data import (
    CB_IMG_OK,
    CB_MENU_ABOUT,
    CB_MENU_ABOUT_HUB,
    CB_MENU_BACK_START,
    CB_MENU_BUDGET_HUB,
    CB_MENU_CHANNEL,
    CB_MENU_CHANNEL_HUB,
    CB_MENU_FAQ,
    CB_MENU_FAQ_HUB,
    CB_MENU_HUB,
    CB_MENU_PROFILE,
    CB_MENU_PROFILE_HUB,
    CB_MENU_REF,
    CB_MENU_REF_HUB,
    CB_MENU_REF_LEGACY,
    CB_MENU_SUPPORT,
    CB_MENU_SUPPORT_HUB,
    CB_BACK_TO_READY_IDEAS,
    CB_READY_RESULT_MAIN_MENU,
    CB_REGEN,
    CB_REGEN_READY_REDO,
    CB_READY_MODE_LEGACY_PREFIX,
    CB_READY_MODE_PREFIX,
)
from src.keyboards.main_menu import back_to_main_menu_keyboard, menu_hub_keyboard, start_menu_keyboard
from src.keyboards.reply_panel import quick_panel_keyboard
from src.keyboards.styles import BTN_PRIMARY, BTN_SUCCESS

router = Router(name="commands")

_BACK_TO_MENU_ROW = [
    InlineKeyboardButton(
        text="Назад",
        callback_data=CB_MENU_BACK_START,
        icon_custom_emoji_id="5256247952564825322",
    )
]


def _back_row(back_callback: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text="Назад",
            callback_data=back_callback,
            icon_custom_emoji_id="5256247952564825322",
        )
    ]


# Невидимый символ — только чтобы обновить reply-клавиатуру с актуальным балансом.
_QUICK_PANEL_STUB = "\u200b"

_READY_MODE_LABEL_BY_ID: dict[str, str] = {
    "fast": "Fast",
    "medium": "Medium",
    "premium": "Premium",
}


def _ready_mode_label(mode: str | None) -> str:
    return _READY_MODE_LABEL_BY_ID.get((mode or "").strip().lower(), "Medium")


_READY_MODE_IDS: frozenset[str] = frozenset({"fast", "medium", "premium"})

# Клиенты Telegram часто добавляют U+FE0F к эмодзи в тексте кнопки (🎛️ vs 🎛) — фильтры без нормализации не срабатывают.
_U_FE0F = "\ufe0f"


def _panel_plain_text(text: str | None) -> str:
    return (text or "").replace(_U_FE0F, "").strip()


def _is_quick_panel_ready_mode_hint(text: str | None) -> bool:
    t = _panel_plain_text(text)
    return t.startswith("🎛") and "Режим:" in t[:40]


def _is_quick_panel_menu_button(text: str | None) -> bool:
    return _panel_plain_text(text) in ("🖥 Меню", "📋 Меню")


def _is_quick_panel_speed_mode_row(text: str | None) -> bool:
    return _panel_plain_text(text).lower() in ("⚡ fast", "🚀 medium", "💎 premium")


def _is_quick_panel_profile_button(text: str | None) -> bool:
    t = _panel_plain_text(text)
    return t.startswith("👤 Профиль") or t.startswith("🐷 Баланс") or t.startswith("💰 Баланс")


def _is_quick_panel_ref_button(text: str | None) -> bool:
    return _panel_plain_text(text) in ("🫂 Реф. система", "👥 Реф. система", "🎥 Реф. система")


def _read_ready_mode_picker_generation(data: dict) -> int | None:
    v = data.get("_ready_mode_picker_gen")
    if v is None:
        return None
    try:
        g = int(v)
    except (TypeError, ValueError):
        return None
    return g if g > 0 else None


def _parse_ready_mode_panel_callback(data: str | None) -> tuple[str | None, int | None]:
    """(suffix, gen) для ``menu:ready_mode:`` — либо ``{gen}:{mode}``, либо устаревший ``{mode}``."""
    if not data or not data.startswith(CB_READY_MODE_PREFIX):
        return None, None
    tail = data[len(CB_READY_MODE_PREFIX) :].strip()
    if not tail:
        return None, None
    if ":" in tail:
        gen_s, _, rest = tail.partition(":")
        rest = rest.strip().lower()
        if rest not in _READY_MODE_IDS:
            return None, None
        try:
            return rest, int(gen_s)
        except ValueError:
            return None, None
    t = tail.lower()
    if t in _READY_MODE_IDS:
        return t, None
    return None, None


def _ready_mode_picker_normalize(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    return m if m in _READY_MODE_IDS else "medium"


async def _ready_idea_cost_lazy(user_id: int, mode: str) -> int:
    from src.handlers import img_commands as img

    m = _ready_mode_picker_normalize(mode)
    return await img._ready_idea_cost_for_user_mode(user_id, m)


def _ready_mode_activation_html(mode: str) -> str:
    from src.handlers import img_commands as img

    label = _ready_mode_label(mode)
    human = img._ready_mode_model_human(mode)
    return (
        f"<b>{esc(label)}</b> активирован.\n"
        f"<i>Генерирует:</i> <b>{esc(human)}</b>"
    )


def _ready_mode_selected_line(mode: str) -> str:
    m = _ready_mode_picker_normalize(mode)
    if m == "fast":
        return "⚡ Fast"
    if m == "medium":
        return "🚀 Medium"
    return "💎 Premium"


def _ready_mode_picker_body_html(*, balance: int, mode: str, cost: int) -> str:
    m = _ready_mode_picker_normalize(mode)
    sel = _ready_mode_selected_line(m)
    return (
        "<b>🎛 Выбери режим генерации</b>\n"
        f"Баланс: <b>{esc(str(balance))}</b> {CREDITS_COIN_TG_HTML}\n"
        f"Выбрано: <b>{sel}</b>\n"
        f"Стоимость за генерацию: <b>{esc(str(cost))}</b> {CREDITS_COIN_TG_HTML}"
    )


def _ready_mode_picker_markup(current_mode: str, *, gen: int) -> InlineKeyboardMarkup:
    cur = _ready_mode_picker_normalize(current_mode)
    row: list[InlineKeyboardButton] = []
    for mode_id, label in (
        ("fast", "⚡ Fast"),
        ("medium", "🚀 Medium"),
        ("premium", "💎 Premium"),
    ):
        selected = mode_id == cur
        text = f"{label} ✅" if selected else label
        kw: dict = {
            "text": text[:64],
            "callback_data": f"{CB_READY_MODE_PREFIX}{gen}:{mode_id}",
        }
        if selected:
            kw["style"] = BTN_PRIMARY
        row.append(InlineKeyboardButton(**kw))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def _send_ready_mode_picker(message: Message, user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    old_mid = data.get("_ready_mode_picker_message_id")
    old_chat = data.get("_ready_mode_picker_chat_id")
    if isinstance(old_mid, int) and isinstance(old_chat, int):
        try:
            await message.bot.edit_message_reply_markup(old_chat, old_mid, reply_markup=None)
        except TelegramBadRequest:
            logging.debug("strip old ready mode picker keyboard", exc_info=True)
    prev = _read_ready_mode_picker_generation(data)
    gen = (prev or 0) + 1
    mode = await get_user_ready_mode(user_id)
    m = _ready_mode_picker_normalize(mode)
    balance = await get_credits(user_id)
    cost = await _ready_idea_cost_lazy(user_id, m)
    body = _ready_mode_picker_body_html(balance=balance, mode=m, cost=cost)
    try:
        sent = await message.answer(
            body, parse_mode=HTML, reply_markup=_ready_mode_picker_markup(m, gen=gen)
        )
    except TelegramBadRequest:
        logging.warning("ready mode picker: answer with inline keyboard failed", exc_info=True)
        await message.answer(
            "<b>Не удалось показать выбор режима.</b>\n"
            "<blockquote><i>Попробуй ещё раз или нажми «🖥 Меню».</i></blockquote>",
            parse_mode=HTML,
        )
        return
    except Exception:
        logging.exception("ready mode picker: unexpected error")
        await message.answer(USER_GENERIC_ERROR)
        return
    await state.update_data(
        _ready_mode_picker_gen=gen,
        _ready_mode_picker_message_id=sent.message_id,
        _ready_mode_picker_chat_id=sent.chat.id,
    )


async def _refresh_quick_panel(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        balance = await get_credits(user_id)
        mode = await get_user_ready_mode(user_id)
        await bot.send_message(
            chat_id,
            _QUICK_PANEL_STUB,
            reply_markup=quick_panel_keyboard(balance, mode_label=_ready_mode_label(mode)),
            disable_notification=True,
        )
    except Exception:
        logging.warning("quick panel refresh failed (reply-клавиатура могла не обновиться)", exc_info=True)


async def _sync_ready_browsing_after_mode_change(bot: Bot, state: FSMContext, user_id: int) -> None:
    try:
        from src.handlers import img_commands as img

        await img.refresh_ready_browsing_anchor(bot, user_id=user_id, state=state)
    except Exception:
        logging.debug("sync ready browsing anchor after mode change failed", exc_info=True)


def _budget_source_label(source: str) -> str:
    labels = {
        "credit_add": "Начисление",
        "credit_spend": "Списание",
        "admin_add": "Админ начислил",
        "admin_take": "Админ списал",
        "image_generate": "Генерация изображения",
        "ready_idea_generate": "Готовая идея",
        "subscription_bonus": "Бонус подписки",
        "bonus_pack": "Бонус-пакет",
        "subscription_purchase": "Покупка подписки",
        "referral_subscription_bonus": "Реф-бонус за подписку друга",
        "referral_inviter_bonus": "Реф-бонус за приглашение",
        "referral_pair_sub_bonus": "Реф-бонус за 2 приглашения (с подпиской)",
        "referral_invitee_welcome": "Приветственный реф-бонус",
    }
    return labels.get(source, source or "Операция")


def _main_screen_text(balance: int, bonus_note: str = "") -> str:
    bonus_html = esc(bonus_note) if bonus_note else ""
    return (
        '<b><tg-emoji emoji-id="5463297803235113601">✨</tg-emoji> Добро пожаловать в Shard Creator</b>\n'
        "<i>Создание и изменение фото в пару кликов.</i>\n\n"
        '<b><tg-emoji emoji-id="5258203794772085854">⚡️</tg-emoji> Быстрый старт:</b>\n'
        '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Открой <b>«<tg-emoji emoji-id="5282843764451195532">🖥</tg-emoji> Меню»</b> — там все разделы: идеи, подписки, FAQ и рефералка.\n'
        f'<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Нажми <b>«{PROFILE_AVATAR_TG_HTML} Профиль»</b> внизу — увидишь '
        f"{CREDITS_COIN_TG_HTML} кредиты, статистику и лимиты.\n"
        '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Загляни в <b>«<tg-emoji emoji-id="5330522514231684724">🌟</tg-emoji> Что умеет бот»</b> — там коротко и понятно, как использовать все возможности.\n\n'
        '<blockquote><b><tg-emoji emoji-id="5422439311196834318">💡</tg-emoji> Подсказка:</b> <i>чем точнее задача в одном сообщении, тем лучше и быстрее итоговая генерация.</i></blockquote>\n\n'
        "<blockquote><i>Продолжая работу с ботом, ты подтверждаешь согласие на обработку персональных данных.</i>"
        f"{bonus_html}</blockquote>"
    )


def _days_in_bot(created_at: str) -> int:
    text = (created_at or "").strip()
    if not text:
        return 0
    candidates = (text.replace("Z", "+00:00"), text)
    dt: datetime | None = None
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            break
        except ValueError:
            continue
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _start_banner_path() -> Path | None:
    """Баннер только для главного экрана (/start, restore меню). Файл: assets/start/main_menu_banner.png"""
    p = PROJECT_ROOT / "assets" / "start" / "main_menu_banner.png"
    return p if p.is_file() else None


def _is_generated_image_result_message(message: Message) -> bool:
    """Сообщение с готовой картинкой из генерации — не редактировать и не удалять."""
    if not message.photo:
        return False
    cap = message.caption or ""
    if "Картинка сохранена" in cap:
        return True
    if "Made in Shard Creator" in cap or "Shard Creator" in cap:
        return True
    if "Готово" in cap and "✔️" in cap:
        return True
    if "Списано:" in cap and ("Баланс" in cap or "кредит" in cap.lower()):
        return True
    kb = message.reply_markup
    if kb and kb.inline_keyboard:
        for row in kb.inline_keyboard:
            for btn in row:
                cd = getattr(btn, "callback_data", None)
                if cd in (
                    CB_REGEN,
                    CB_REGEN_READY_REDO,
                    CB_BACK_TO_READY_IDEAS,
                    CB_READY_RESULT_MAIN_MENU,
                    CB_IMG_OK,
                    "img:save",
                ):
                    return True
    return False


async def replace_nav_screen_in_message(
    message: Message,
    *,
    caption_html: str,
    reply_markup: InlineKeyboardMarkup | None,
    new_media_path: Path | None = None,
) -> bool:
    """Заменить экран в том же сообщении: edit_media → edit_caption → edit_text. Без delete+send.

    Не трогает сообщения с результатом генерации. Если задан ``new_media_path`` и он есть на диске:
    для **фото** — только ``edit_media`` (новая картинка + подпись). При сбое **не** правим только
    подпись на старом фото — иначе «чужое» превью остаётся на месте.

    Для **текста** нельзя подставить файл через Bot API — возвращаем False; вызывающий шлёт фото
    отдельно (часто delete + send_photo), иначе подпись обновилась бы без нужной картинки.

    Без ``new_media_path`` у фото — ``edit_caption``; у текста — ``edit_text``.
    """
    if _is_generated_image_result_message(message):
        return False
    parse_mode = HTML
    if new_media_path is not None and new_media_path.is_file():
        if message.photo:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(new_media_path),
                        caption=caption_html,
                        parse_mode=parse_mode,
                    ),
                    reply_markup=reply_markup,
                )
                return True
            except Exception:
                logging.debug("replace_nav_screen_in_message: edit_media failed", exc_info=True)
            return False
        return False
    if message.photo:
        try:
            await message.edit_caption(
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return True
        except Exception:
            logging.debug("replace_nav_screen_in_message: edit_caption (no new file) failed", exc_info=True)
    try:
        await message.edit_text(
            caption_html,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return True
    except Exception:
        logging.debug("replace_nav_screen_in_message: edit_text failed", exc_info=True)
        return False


async def edit_or_send_nav_message(
    message: Message | None,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = HTML,
    disable_web_page_preview: bool = False,
) -> Message | None:
    """
    Навигация: текстовые сообщения — edit_text.

    Сообщение с результатом генерации (картинка) не редактируем — шлём новый текст.

    Баннер главного меню и другие UI-фото без результата генерации: при тексте
    подписи ≤1024 — edit_caption на том же сообщении (картинка не отделяется от меню).
    Иначе — отдельное текстовое сообщение и снятие клавиатуры с фото (редкий случай).
    """
    if message is None:
        return None

    if _is_generated_image_result_message(message):
        try:
            return await message.bot.send_message(
                message.chat.id,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception:
            logging.exception("edit_or_send_nav_message: send after result photo failed")
            return None

    if message.photo:
        if not _is_generated_image_result_message(message) and len(text) <= 1024:
            try:
                return await message.edit_caption(
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            except Exception:
                logging.debug(
                    "edit_or_send_nav_message: edit_caption failed, fallback below",
                    exc_info=True,
                )
        try:
            sent = await message.bot.send_message(
                message.chat.id,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                logging.debug(
                    "edit_or_send_nav_message: could not strip keyboard from old photo",
                    exc_info=True,
                )
            return sent
        except Exception:
            logging.exception("edit_or_send_nav_message: send for photo message failed")
            return None

    try:
        return await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception:
        logging.debug("edit_or_send_nav_message: edit_text failed, fallback to send", exc_info=True)

    try:
        return await message.bot.send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception:
        logging.exception("edit_or_send_nav_message: send fallback failed")
        return None


async def send_main_menu_screen(
    bot: Bot,
    chat_id: int,
    user_id: int,
    username: str | None,
) -> None:
    """Главный экран как после /start: баланс в тексте, меню, при наличии — фото-баннер."""
    await ensure_user(user_id, username)
    balance = await get_credits(user_id)
    ready_mode = await get_user_ready_mode(user_id)
    text = _main_screen_text(balance, "")
    kb = start_menu_keyboard(balance)
    banner = _start_banner_path()
    if banner:
        await bot.send_photo(
            chat_id,
            photo=FSInputFile(banner),
            caption=text,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=HTML)


async def restore_main_menu_message(message: Message, user_id: int, username: str | None) -> None:
    """Вернуть главный экран: баннер остаётся тем же сообщением (подпись + клавиатура), без дубля текста."""
    try:
        await ensure_user(user_id, username)
        balance = await get_credits(user_id)
        text = _main_screen_text(balance, "")
        kb = start_menu_keyboard(balance)
        banner = _start_banner_path()

        if message.photo and not _is_generated_image_result_message(message):
            # После готовых идей на том же сообщении может быть превью Minecraft и т.д. —
            # только edit_caption не меняет картинку; возвращаем стартовый баннер.
            if banner and banner.is_file():
                try:
                    await message.edit_media(
                        media=InputMediaPhoto(
                            media=FSInputFile(banner),
                            caption=text,
                            parse_mode=HTML,
                        ),
                        reply_markup=kb,
                    )
                    return
                except Exception:
                    logging.debug("restore_main_menu_message: edit_media failed", exc_info=True)
            # Если стартового баннера нет, не перезаписываем подпись текущего превью:
            # оставляем фото «на месте», снимаем с него клавиатуру и отправляем меню отдельно.
            if not banner:
                try:
                    await message.edit_reply_markup(reply_markup=None)
                except Exception:
                    logging.debug("restore_main_menu_message: strip keyboard failed", exc_info=True)
                await message.bot.send_message(
                    message.chat.id,
                    text,
                    reply_markup=kb,
                    parse_mode=HTML,
                )
                return
            try:
                await message.edit_caption(caption=text, reply_markup=kb, parse_mode=HTML)
                return
            except Exception:
                logging.debug("restore_main_menu_message: edit_caption failed", exc_info=True)

        if message.photo and _is_generated_image_result_message(message):
            await send_main_menu_screen(message.bot, message.chat.id, user_id, username)
            return

        if banner and not message.photo:
            # Не удаляем UI-сообщения: только заменяем/переотправляем при необходимости.
            edited = await edit_or_send_nav_message(message, text=text, reply_markup=kb, parse_mode=HTML)
            if edited is not None:
                return
            await send_main_menu_screen(message.bot, message.chat.id, user_id, username)
            return

        edited = await edit_or_send_nav_message(message, text=text, reply_markup=kb, parse_mode=HTML)
        if edited is not None:
            return
        await send_main_menu_screen(message.bot, message.chat.id, user_id, username)
    finally:
        await _refresh_quick_panel(message.bot, message.chat.id, user_id)


def _parse_ref_start_arg(args: str | None) -> int | None:
    """Аргумент команды /start (диплинк t.me/bot?start=ref_<id>)."""
    if not args:
        return None
    rest = args.strip()
    if not rest:
        return None
    first = rest.split()[0]
    payload = first[4:] if first.startswith("ref_") else first
    if payload.isdigit():
        return int(payload)
    return None


def _parse_ref_payload(raw_text: str) -> int | None:
    """Fallback: полный текст сообщения, если args недоступен."""
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return _parse_ref_start_arg(parts[1])


@router.message(Command("start", ignore_mention=True))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject) -> None:
    if not message.from_user:
        return
    # Если пользователь запускает /start в середине image-flow, гасим старую карточку,
    # чтобы она не оставалась «активной» с кнопками.
    try:
        st = await state.get_state()
        data = await state.get_data()
        anchor_chat = data.get("_img_flow_anchor_chat_id")
        anchor_msg = data.get("_img_flow_anchor_message_id")
        if st and anchor_chat and anchor_msg:
            try:
                await message.bot.edit_message_text(
                    "<i>Сеанс генерации закрыт. Открыто новое главное меню.</i>",
                    chat_id=int(anchor_chat),
                    message_id=int(anchor_msg),
                    parse_mode=HTML,
                    reply_markup=None,
                )
            except Exception:
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=int(anchor_chat),
                        message_id=int(anchor_msg),
                        reply_markup=None,
                    )
                except Exception:
                    logging.debug("cmd_start: could not neutralize previous image-flow card", exc_info=True)
    except Exception:
        logging.debug("cmd_start: flow anchor pre-check failed", exc_info=True)

    await state.clear()
    user_id = message.from_user.id
    await ensure_user(user_id, message.from_user.username)
    raw = (message.text or message.caption or "").strip()
    referrer_id = _parse_ref_start_arg(command.args)
    if referrer_id is None and raw:
        referrer_id = _parse_ref_payload(raw)
    if referrer_id is None and raw and ("ref_" in raw or raw.split(maxsplit=1)[-1].strip().isdigit()):
        logging.warning(
            "referral: не распарсили диплинк raw=%r command.args=%r",
            raw,
            command.args,
        )
    bonus_note = ""
    if referrer_id:
        # Пригласитель должен быть в БД, иначе apply_referral тихо вернёт False
        await ensure_user(referrer_id, None)
        applied = await apply_referral(invitee_user_id=user_id, inviter_user_id=referrer_id)
        if applied:
            bonus_note = "\n🎉 Реферальный бонус: тебе +10 кредитов."
            logging.info("referral applied: invitee=%s inviter=%s", user_id, referrer_id)
    balance = await get_credits(user_id)
    ready_mode = await get_user_ready_mode(user_id)

    text = _main_screen_text(balance, bonus_note)
    kb = start_menu_keyboard(balance)
    banner = _start_banner_path()
    if banner:
        await message.answer_photo(
            FSInputFile(banner),
            caption=text,
            reply_markup=kb,
            parse_mode=HTML,
        )
    else:
        await message.answer(text, reply_markup=kb, parse_mode=HTML)
    await message.answer(
        "Панель быстрого доступа включена ⤵️",
        reply_markup=quick_panel_keyboard(balance, mode_label=_ready_mode_label(ready_mode)),
    )

    ann_text = START_ANNOUNCEMENT.strip() if START_ANNOUNCEMENT else ""
    if START_ANNOUNCEMENT_IMAGE:
        cap = ann_text[:1024] if ann_text else None
        await message.answer_photo(FSInputFile(START_ANNOUNCEMENT_IMAGE), caption=cap)
    elif ann_text:
        await message.answer(ann_text[:4096])


@router.message(F.text.func(_is_quick_panel_profile_button))
async def quick_panel_profile(message: Message) -> None:
    if not message.from_user:
        return
    await send_profile_card(message, message.from_user.id, message.from_user.username)


@router.message(F.text.func(_is_quick_panel_menu_button))
async def quick_panel_menu(message: Message) -> None:
    bal: int | None = None
    if message.from_user:
        bal = await get_credits(message.from_user.id)
    await message.answer(
        '<b><tg-emoji emoji-id="5282843764451195532">🖥</tg-emoji> Главное меню</b>\n<blockquote><i>Выбери нужный раздел.</i></blockquote>',
        reply_markup=menu_hub_keyboard(bal),
        parse_mode=HTML,
    )


@router.message(F.text == "💬 Поддержка")
async def quick_panel_support(message: Message) -> None:
    await cmd_support(message)


@router.message(F.text.func(_is_quick_panel_ref_button))
async def quick_panel_ref(message: Message) -> None:
    if not message.from_user:
        return
    await deliver_referral_screen(message.bot, message.from_user.id, message.from_user.username, message)


@router.message(F.text.func(_is_quick_panel_speed_mode_row))
async def quick_panel_ready_mode_select(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    text = (message.text or "").strip().lower()
    target = "medium"
    if "fast" in text:
        target = "fast"
    elif "premium" in text:
        target = "premium"
    mode = await set_user_ready_mode(message.from_user.id, target)
    logging.info(
        "ready_mode/set_from_reply user_id=%s target=%s applied=%s",
        message.from_user.id,
        target,
        mode,
    )
    await _sync_ready_browsing_after_mode_change(message.bot, state, message.from_user.id)
    balance = await get_credits(message.from_user.id)
    await message.answer(
        _ready_mode_activation_html(mode),
        parse_mode=HTML,
        reply_markup=quick_panel_keyboard(balance, mode_label=_ready_mode_label(mode)),
    )


@router.message(F.text.func(_is_quick_panel_ready_mode_hint))
async def quick_panel_ready_mode_hint(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await _send_ready_mode_picker(message, message.from_user.id, state)


@router.callback_query(F.data.startswith(CB_READY_MODE_LEGACY_PREFIX))
async def ready_mode_legacy_inline(callback: CallbackQuery, state: FSMContext) -> None:
    """Старые ``img:idea_mode:*`` на карточках в чате — применяем режим в БД, не перерисовываем сообщение (это карточка идеи)."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    suffix = (callback.data or "")[len(CB_READY_MODE_LEGACY_PREFIX) :].strip().lower()
    if suffix not in _READY_MODE_IDS:
        await callback.answer("Неверный режим", show_alert=True)
        return
    mode = await set_user_ready_mode(callback.from_user.id, suffix)
    logging.info(
        "ready_mode/set_from_legacy_inline user_id=%s target=%s applied=%s",
        callback.from_user.id,
        suffix,
        mode,
    )
    await callback.answer("Сохранено")
    await _refresh_quick_panel(callback.bot, callback.message.chat.id, callback.from_user.id)
    await _sync_ready_browsing_after_mode_change(callback.bot, state, callback.from_user.id)
    await callback.message.answer(_ready_mode_activation_html(mode), parse_mode=HTML)


@router.callback_query(F.data.startswith(CB_READY_MODE_PREFIX))
async def quick_panel_ready_mode_inline(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    suffix, tap_gen = _parse_ready_mode_panel_callback(callback.data)
    if suffix is None or suffix not in _READY_MODE_IDS:
        await callback.answer("Неверный режим", show_alert=True)
        return
    data = await state.get_data()
    stored_gen = _read_ready_mode_picker_generation(data)
    stored_mid = data.get("_ready_mode_picker_message_id")
    stored_chat = data.get("_ready_mode_picker_chat_id")
    cur = callback.message
    mid_ok = isinstance(stored_mid, int) and isinstance(stored_chat, int)
    if stored_gen is not None:
        if (
            tap_gen is None
            or tap_gen != stored_gen
            or not mid_ok
            or cur.message_id != stored_mid
            or cur.chat.id != stored_chat
        ):
            logging.info(
                "ready_mode/ignore_stale_picker user_id=%s tap_gen=%s stored_gen=%s msg=%s stored_msg=%s",
                callback.from_user.id,
                tap_gen,
                stored_gen,
                cur.message_id,
                stored_mid,
            )
            await callback.answer(
                "Это старое меню. Нажми «🎛 Режим» и выбери режим в последнем сообщении.",
                show_alert=False,
            )
            return
    elif tap_gen is not None:
        await callback.answer(
            "Открой выбор режима заново через «🎛 Режим».",
            show_alert=False,
        )
        return
    panel_gen = stored_gen if stored_gen is not None else 1
    mode = await set_user_ready_mode(callback.from_user.id, suffix)
    logging.info(
        "ready_mode/set_from_inline user_id=%s target=%s applied=%s gen=%s",
        callback.from_user.id,
        suffix,
        mode,
        panel_gen,
    )
    balance = await get_credits(callback.from_user.id)
    cost = await _ready_idea_cost_lazy(callback.from_user.id, mode)
    body = _ready_mode_picker_body_html(balance=balance, mode=mode, cost=cost)
    kb = _ready_mode_picker_markup(mode, gen=panel_gen)
    msg = callback.message
    fallback_used = False
    try:
        await msg.edit_text(body, parse_mode=HTML, reply_markup=kb)
    except TelegramBadRequest:
        try:
            await msg.edit_caption(caption=body, parse_mode=HTML, reply_markup=kb)
        except TelegramBadRequest:
            fallback_used = True
            new_gen = panel_gen + 1
            kb_new = _ready_mode_picker_markup(mode, gen=new_gen)
            sent = await msg.answer(body, parse_mode=HTML, reply_markup=kb_new)
            await state.update_data(
                _ready_mode_picker_gen=new_gen,
                _ready_mode_picker_message_id=sent.message_id,
                _ready_mode_picker_chat_id=sent.chat.id,
            )
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                logging.debug("strip picker after fallback send", exc_info=True)
    if not fallback_used:
        await state.update_data(
            _ready_mode_picker_gen=panel_gen,
            _ready_mode_picker_message_id=cur.message_id,
            _ready_mode_picker_chat_id=cur.chat.id,
        )
    await callback.answer()
    await _refresh_quick_panel(callback.bot, msg.chat.id, callback.from_user.id)
    await _sync_ready_browsing_after_mode_change(callback.bot, state, callback.from_user.id)
    await msg.answer(_ready_mode_activation_html(mode), parse_mode=HTML)


@router.message((F.text == "История бюджета") | (F.text == "📊 История бюджета"))
async def quick_panel_budget_history(message: Message) -> None:
    await _send_budget_history(message, back_callback=CB_MENU_BACK_START)


async def _send_budget_history(message: Message, *, back_callback: str) -> None:
    if not message.from_user:
        return
    rows = await get_budget_history_recent(message.from_user.id, days=7, limit=20)
    if not rows:
        await message.answer(
            '<b><tg-emoji emoji-id="6057406808086023473">📉</tg-emoji> История бюджета (7 дней)</b>\n'
            "<blockquote><i>Пока нет записей за последнюю неделю.</i></blockquote>",
            parse_mode=HTML,
            reply_markup=back_to_main_menu_keyboard(back_callback),
        )
        return
    lines = [
        '<b><tg-emoji emoji-id="6057406808086023473">📉</tg-emoji> История бюджета (7 дней)</b>',
        "<blockquote>",
    ]
    for item in rows:
        sign = "+" if item.delta > 0 else ""
        delta_text = f"{sign}{item.delta}" if item.delta else "0"
        details = f" — {esc(item.details)}" if item.details else ""
        lines.append(
            f"• <b>{esc(delta_text)}</b> кр. · <i>{esc(_budget_source_label(item.source))}</i>{details}"
        )
    lines.append("</blockquote>")
    await message.answer(
        "\n".join(lines),
        parse_mode=HTML,
        reply_markup=back_to_main_menu_keyboard(back_callback),
    )


@router.callback_query(F.data == CB_MENU_BUDGET_HUB)
async def menu_budget_hub(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    await _send_budget_history(callback.message, back_callback=CB_MENU_HUB)


@router.callback_query(F.data == CB_MENU_BACK_START)
async def menu_back_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await state.clear()
    user_id = callback.from_user.id
    await callback.answer()
    await restore_main_menu_message(callback.message, user_id, callback.from_user.username)


@router.callback_query(F.data == CB_MENU_HUB)
async def menu_hub(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    bal: int | None = None
    if callback.from_user:
        bal = await get_credits(callback.from_user.id)
    await edit_or_send_nav_message(
        callback.message,
        text="<b>📋 Главное меню</b>\n<blockquote><i>Выбери нужный раздел.</i></blockquote>",
        reply_markup=menu_hub_keyboard(bal),
        parse_mode=HTML,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📌 <b>Что доступно</b>\n\n"
        "🏠 <code>/start</code> — <i>главное меню, баланс и картинки</i>\n"
        "❓ <code>/help</code> — <i>этот список</i>\n"
        "💳 <code>/pay</code> — <i>подписка и оплата</i>\n"
        "👤 <code>/profile</code> — <i>статус аккаунта и подписки</i>\n"
        "🫂 <code>/ref</code> — <i>реферальная система</i>\n"
        '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji> <code>/ideas</code> — <i>готовые идеи для картинок</i>\n'
        "📋 <code>/faq</code> — <i>частые вопросы</i>\n"
        "🔄 <code>/newchat</code> или <code>/clear</code> — <i>очистить память диалога</i>\n"
        "💬 <code>/support</code> — <i>обращение в поддержку</i>\n"
        '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> <code>/resolved</code> — <i>закрыть тикет (в боте поддержки)</i>\n'
        "🆔 <code>/myid</code> — <i>твой Telegram ID</i>\n\n"
        "<blockquote>🎨 Картинки — через кнопки в <code>/start</code>.</blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.callback_query((F.data == CB_MENU_ABOUT) | (F.data == CB_MENU_ABOUT_HUB))
async def menu_about(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_ABOUT_HUB else CB_MENU_BACK_START
    text = (
        "<b>Что умеет бот</b>\n"
        "<blockquote>"
        "• Собирать кадр по твоему описанию — от бытового до киношного.\n"
        "• В «Готовых идеях» можно окунуться в атмосферу игр и фэнтези, попробовать MMORPG-героя, "
        "сериалы и кино, хоррор и «найденную плёнку», fashion и студийные портреты.\n"
        "• Поставить тебя рядом с узнаваемыми образами и сценами — промо, ринг, переговоры, то, что уже есть в подборке.\n"
        "• Перенести в любую локацию: от бизнес-джета и Амальфи до тоннеля, бэкрумов или ночного города — без съёмочной группы.\n"
        "• Подобрать сцену под твой запрос: одно фото, два референса или фото плюс текст — всё подсказано в карточке идеи."
        "</blockquote>"
    )
    await edit_or_send_nav_message(
        callback.message,
        text=text,
        reply_markup=back_to_main_menu_keyboard(back_callback),
        parse_mode=HTML,
    )


@router.callback_query((F.data == CB_MENU_FAQ) | (F.data == CB_MENU_FAQ_HUB))
async def menu_faq(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_FAQ_HUB else CB_MENU_BACK_START
    from src.handlers.faq_handlers import _faq_keyboard

    await edit_or_send_nav_message(
        callback.message,
        text='<b><tg-emoji emoji-id="5314504236132747481">⁉️</tg-emoji> Частые вопросы</b>\n<blockquote><i>Выбери тему — пришлю короткий ответ.</i></blockquote>',
        reply_markup=_faq_keyboard(back_callback=back_callback),
        parse_mode=HTML,
    )


@router.callback_query((F.data == CB_MENU_CHANNEL) | (F.data == CB_MENU_CHANNEL_HUB))
async def menu_channel(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_CHANNEL_HUB else CB_MENU_BACK_START
    if not CHANNEL_URL:
        await edit_or_send_nav_message(
            callback.message,
            text=(
                "<b>📢 Канал</b>\n"
                "<blockquote><i>Ссылка пока не добавлена. Заполни</i> "
                "<code>CHANNEL_URL</code> <i>в .env, и кнопка откроет канал.</i></blockquote>"
            ),
            reply_markup=back_to_main_menu_keyboard(back_callback),
            parse_mode=HTML,
        )
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Открыть канал", url=CHANNEL_URL, style=BTN_PRIMARY)],
            _back_row(back_callback),
        ]
    )
    await edit_or_send_nav_message(
        callback.message,
        text="<b>📢 Канал</b>\n<blockquote><i>Нажми кнопку ниже, чтобы перейти в канал.</i></blockquote>",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


@router.callback_query((F.data == CB_MENU_SUPPORT) | (F.data == CB_MENU_SUPPORT_HUB))
async def menu_support(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_SUPPORT_HUB else CB_MENU_BACK_START
    if not SUPPORT_BOT_USERNAME:
        await edit_or_send_nav_message(
            callback.message,
            text=(
                "<blockquote><i>Поддержка пока не настроена</i> "
                "(пустой <code>SUPPORT_BOT_USERNAME</code>).</blockquote>"
            ),
            reply_markup=back_to_main_menu_keyboard(back_callback),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_shard_creator"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть поддержку", url=support_url)],
            _back_row(back_callback),
        ]
    )
    await edit_or_send_nav_message(
        callback.message,
        text='<b><tg-emoji emoji-id="5443038326535759644">💬</tg-emoji> Поддержка</b>\n<i>Нажми кнопку ниже,</i> чтобы открыть чат.',
        reply_markup=keyboard,
        parse_mode=HTML,
    )


def _referral_share_url(bot_username: str | None, user_id: int) -> str:
    """Ссылка для кнопки «Пригласить»: открывает шаринг в Telegram без callback."""
    text_share = "Заходи в Shard Creator по моей ссылке 👇"
    if bot_username:
        ref_https = f"https://t.me/{bot_username}?start=ref_{user_id}"
        return "https://t.me/share/url?" + urllib.parse.urlencode(
            {"url": ref_https, "text": text_share},
            quote_via=urllib.parse.quote,
        )
    ref_plain = f"/start ref_{user_id}"
    return "https://t.me/share/url?" + urllib.parse.urlencode(
        {"text": f"{text_share}\n{ref_plain}"},
        quote_via=urllib.parse.quote,
    )


async def _build_referral_message(
    user_id: int,
    username: str | None,
    bot_username: str | None,
    *,
    back_callback: str = CB_MENU_BACK_START,
) -> tuple[str, InlineKeyboardMarkup]:
    """bot_username — из await bot.me(); у aiogram.Bot нет атрибута .username."""
    await ensure_user(user_id, username)
    try:
        invited = await get_referral_count(user_id)
    except Exception:
        logging.warning("_build_referral_message: get_referral_count failed", exc_info=True)
        invited = 0
    try:
        invited_paid = await get_referral_paid_count(user_id)
    except Exception:
        logging.warning("_build_referral_message: get_referral_paid_count failed", exc_info=True)
        invited_paid = 0
    try:
        referral_sub_bonus_total = await get_referral_subscription_bonus_total(user_id)
    except Exception:
        logging.warning("_build_referral_message: get_referral_subscription_bonus_total failed", exc_info=True)
        referral_sub_bonus_total = 0
    try:
        balance = await get_credits(user_id)
    except Exception:
        logging.warning("_build_referral_message: get_credits failed", exc_info=True)
        balance = 0
    prof = await get_user_admin_profile(user_id)
    ready_bonus_uses = int(prof.idea_tokens) if prof else 0
    ref_link = (
        f"https://t.me/{bot_username}?start=ref_{user_id}"
        if bot_username
        else f"/start ref_{user_id}"
    )
    uname_html = f"@{esc(username)}" if username else "<i>без username</i>"
    text = (
        '<b><tg-emoji emoji-id="5391320026869408028">🫂</tg-emoji> Реферальная программа</b>\n\n'
        "<blockquote>"
        f"<i>{PROFILE_AVATAR_TG_HTML} Профиль</i> {uname_html}\n"
        f'<i><tg-emoji emoji-id="5841276284155467413">🔤</tg-emoji> ID</i> <code>{esc(user_id)}</code>\n'
        f'<i><tg-emoji emoji-id="5382164415019768638">🪙</tg-emoji> Кредиты:</i> <b>{esc(balance)}</b>\n'
        f'<i><tg-emoji emoji-id="5452155223550223362">💎</tg-emoji> Бонусных запусков «Готовых идей»:</i> <b>{esc(ready_bonus_uses)}</b>\n'
        f'<i><tg-emoji emoji-id="5472239203590888751">📩</tg-emoji> Приглашения:</i> <b>{esc(invited)}</b>'
        f"\n"
        f'<i><tg-emoji emoji-id="5452155223550223362">💎</tg-emoji> Купили подписку:</i> <b>{esc(invited_paid)}</b>'
        f"\n"
        f'<i><tg-emoji emoji-id="5382164415019768638">🪙</tg-emoji> Накоплено с приглашённых:</i> '
        f"<b>{esc(invited * 20 + referral_sub_bonus_total)}</b> "
        f"<i>(база +20 за каждого: {esc(invited * 20)}; бонус 5%: {esc(referral_sub_bonus_total)})</i>"
        "</blockquote>\n\n"
        "<blockquote><i>"
        "За каждого приглашённого — <b>+20</b> кредитов тебе; новому пользователю при первом <code>/start</code> по твоей ссылке — <b>+10</b> кредитов. "
        "За каждых <b>двух</b> приглашённых: без подписки — <b>+1</b> бонусный запуск «Готовых идей»; "
        "при активной подписке — <b>+10</b> кредитов вместо запуска.\n"
        "Если приглашённый покупает подписку — тебе дополнительно начисляется <b>+5%</b> "
        "от кредитов этой подписки (именно бонус, не полная сумма)."
        "</i></blockquote>\n\n"
        "<b>🔗 Твоя ссылка</b>\n"
        f"<code>{esc(ref_link)}</code>"
    )
    share_url = _referral_share_url(bot_username, user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Пригласить", url=share_url, style=BTN_SUCCESS)],
            _back_row(back_callback),
        ]
    )
    return text, kb


async def deliver_referral_screen(
    bot: Bot,
    user_id: int,
    username: str | None,
    reply_via: Message | None,
    *,
    back_callback: str = CB_MENU_BACK_START,
) -> None:
    """Отправить экран рефералки (callback или команда /ref)."""
    me = await bot.me()
    text, kb = await _build_referral_message(
        user_id,
        username,
        me.username,
        back_callback=back_callback,
    )
    try:
        if reply_via:
            await edit_or_send_nav_message(
                reply_via,
                text=text,
                reply_markup=kb,
                parse_mode=HTML,
                disable_web_page_preview=True,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=kb,
                parse_mode=HTML,
                disable_web_page_preview=True,
            )
    except Exception:
        logging.exception("deliver_referral_screen: не удалось отправить сообщение с реферальной ссылкой")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="Не удалось показать реферальное сообщение. Нажми /start и попробуй снова.",
            )
        except Exception:
            logging.exception("deliver_referral_screen: не удалось отправить сообщение об ошибке")


@router.callback_query(
    (F.data == CB_MENU_REF) | (F.data == CB_MENU_REF_LEGACY) | (F.data == CB_MENU_REF_HUB)
)
async def menu_ref(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    # Сразу снимаем «часики» у кнопки; иначе клиент ждёт до конца отправки сообщения (до ~20 с).
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_REF_HUB else CB_MENU_BACK_START
    await deliver_referral_screen(
        callback.bot,
        callback.from_user.id,
        callback.from_user.username,
        callback.message,
        back_callback=back_callback,
    )


@router.message(Command("ref"))
async def cmd_ref(message: Message) -> None:
    if not message.from_user:
        return
    await deliver_referral_screen(message.bot, message.from_user.id, message.from_user.username, message)


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if not message.from_user:
        return

    await send_profile_card(message, message.from_user.id, message.from_user.username)


@router.callback_query((F.data == CB_MENU_PROFILE) | (F.data == CB_MENU_PROFILE_HUB))
async def menu_profile(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    await callback.answer()
    back_callback = CB_MENU_HUB if callback.data == CB_MENU_PROFILE_HUB else CB_MENU_BACK_START
    await send_profile_card(
        callback.message,
        callback.from_user.id,
        callback.from_user.username,
        edit_existing=True,
        back_callback=back_callback,
    )


async def _profile_card_html(
    user_id: int,
    username_raw: str | None,
    *,
    back_callback: str = CB_MENU_BACK_START,
) -> tuple[str, InlineKeyboardMarkup]:
    """Текст профиля и клавиатура «Назад»."""
    await ensure_user(user_id, username_raw)
    profile = await get_user_admin_profile(user_id)
    if not profile:
        missing = (
            "<blockquote><i>Профиль пока не найден. Нажми</i> <code>/start</code> <i>и попробуй снова.</i></blockquote>"
        )
        return missing, back_to_main_menu_keyboard(back_callback)
    balance = await get_credits(user_id)
    approx_images = max(0, balance // 30)
    ready_bonus_uses = int(profile.idea_tokens or 0)
    ru, rlim = await get_daily_image_generation_usage(user_id, "ready")
    username = f"@{profile.username}" if profile.username else "—"
    is_admin = user_id in ADMIN_IDS
    active_sub_real = subscription_is_active(profile.subscription_ends_at)
    # Админу доступен безлимит всегда, но оплаченный срок показываем отдельно.
    active_sub = active_sub_real or is_admin
    priority_note = ""
    if active_sub_real:
        sub_status = "активна"
        sub_till = format_subscription_ends_at(profile.subscription_ends_at)
        if is_admin:
            plan_name = plan_subscription_title_html("universe")
        elif profile.subscription_plan and profile.subscription_plan in PLANS:
            plan_name = plan_subscription_title_html(profile.subscription_plan)
        else:
            # В БД мог не быть записан тариф (старые выдачи / только срок); доступ как у Universe.
            plan_name = plan_subscription_title_html("universe")
        pid = "universe" if is_admin else (profile.subscription_plan or "").strip().lower()
        if pid in ("starter", "galaxy", "universe"):
            priority_note = (
                f"\n<i>⚡ Приоритет очереди генераций и скидка на повтор «готовой идеи» как у "
                f"{plan_subscription_title_html('universe')} (см. подсказки после картинки).</i>"
            )
    elif is_admin:
        sub_status = "админ-безлимит"
        sub_till = "—"
        plan_name = plan_subscription_title_html("universe")
        priority_note = (
            f"\n<i>⚡ Приоритет очереди генераций и скидка на повтор «готовой идеи» доступны как у "
            f"{plan_subscription_title_html('universe')}.</i>"
        )
    else:
        sub_status = "не активна"
        sub_till = (
            format_subscription_ends_at(profile.subscription_ends_at)
            if profile.subscription_ends_at
            else "—"
        )
        plan_name = "—"
    gen_total = await count_generated_images_total(user_id)
    ns_img = await get_nonsub_image_quota_status(user_id)
    # При активной подписке квота «без подписки» не считается — функция возвращает None.
    fu, flim = ns_img if ns_img is not None else (0, 0)
    ready_cycle = "без лимита" if active_sub else f"{ru}/{rlim}"
    img_cycle = "без лимита" if active_sub else f"{fu}/{flim}"
    body = (
        f"<b>{PROFILE_AVATAR_TG_HTML} Профиль</b>\n"
        "<blockquote>"
        f"<i>Ник:</i> <b>{esc(username)}</b>\n"
        f'<i><tg-emoji emoji-id="5382164415019768638">🪙</tg-emoji> Кредиты:</i> <b>{esc(balance)}</b>\n'
        f'<i><tg-emoji emoji-id="5257974976094412956">🖼</tg-emoji> Примерно доступно генераций:</i> <b>{esc(approx_images)}</b>\n'
        f"<i>🎯 Готовые идеи:</i> <b>{esc(ready_cycle)}</b>\n"
        f'<i><tg-emoji emoji-id="5258254475386167466">🖼</tg-emoji> Картинки:</i> <b>{esc(img_cycle)}</b>\n'
        f'<i><tg-emoji emoji-id="5203996991054432397">🎁</tg-emoji> Бонусные запуски (реф):</i> <b>{esc(ready_bonus_uses)}</b>\n'
        f"<i>{PROFILE_SUBSCRIPTION_LABEL_TG_HTML} Подписка:</i> <b>{esc(sub_status)}</b> · <i>{plan_name}</i>{priority_note}\n"
        f"<i>{PROFILE_VALID_UNTIL_LABEL_TG_HTML} Действует до:</i> <b>{esc(sub_till)}</b>\n"
        f"<i>{PROFILE_GENERATED_IMAGES_LABEL_TG_HTML} Сгенерировано изображений:</i> <b>{esc(gen_total)}</b>\n"
        "</blockquote>"
    )
    return body, back_to_main_menu_keyboard(back_callback)


async def send_profile_card(
    message: Message,
    user_id: int,
    username_raw: str | None,
    *,
    edit_existing: bool = False,
    back_callback: str = CB_MENU_BACK_START,
) -> None:
    text, kb = await _profile_card_html(user_id, username_raw, back_callback=back_callback)
    if user_id in ADMIN_IDS:
        text = (
            "<blockquote><b>Режим администратора</b> — "
            f"{CREDITS_COIN_TG_HTML} кредиты за генерацию изображений не списываются.</blockquote>\n"
            + text
        )
    if edit_existing:
        await edit_or_send_nav_message(message, text=text, reply_markup=kb, parse_mode=HTML)
    else:
        await message.answer(text, reply_markup=kb, parse_mode=HTML)


@router.message(Command("newchat"))
@router.message(Command("clear"))
async def cmd_newchat(message: Message) -> None:
    if not message.from_user:
        return
    await clear_dialog_messages(message.from_user.id)
    reset_user_spam(message.from_user.id)
    reset_private_rate(message.from_user.id)
    await message.answer(
        '<b>Готово <tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji></b>\n'
        "<blockquote><i>История этого диалога очищена.</i> Можно начать новую тему.</blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.message(Command("resolved"))
async def cmd_resolved_main(message: Message) -> None:
    """В основном боте тикеты ведёт support-бот — направляем пользователя туда."""
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "<blockquote><i>Чат поддержки не подключён.</i> Нужен "
            "<code>SUPPORT_BOT_USERNAME</code> в <code>.env</code>.</blockquote>",
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть поддержку и закрыть тикет", url=support_url)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await message.answer(
        "<b>Закрытие тикета</b>\n"
        "<blockquote>Тикеты ведутся в <i>отдельном боте поддержки</i>. "
        "Команду <code>/resolved</code> отправь там, где открывал обращение.</blockquote>",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    if not SUPPORT_BOT_USERNAME:
        await message.answer(
            "<blockquote><i>Поддержка не подключена</i> — проверь <code>SUPPORT_BOT_USERNAME</code> в <code>.env</code>.</blockquote>",
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=HTML,
        )
        return
    support_url = f"https://t.me/{SUPPORT_BOT_USERNAME}?start=from_shard_creator"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть чат поддержки", url=support_url, style=BTN_PRIMARY)],
            _BACK_TO_MENU_ROW,
        ]
    )
    await message.answer(
        '<b><tg-emoji emoji-id="5443038326535759644">💬</tg-emoji> Поддержка</b>\n'
        "<blockquote><i>Отдельный чат для тикетов.</i> Нажми кнопку ниже.</blockquote>",
        reply_markup=keyboard,
        parse_mode=HTML,
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    if not message.from_user:
        return
    await message.answer(
        f"<blockquote><code>{esc(message.from_user.id)}</code> — <i>твой Telegram ID</i></blockquote>",
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=HTML,
    )


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """ID группы и темы для .env (ADMIN_SALES_*): в группе/топике или подсказка в ЛС."""
    if not message.from_user:
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    chat = message.chat
    if chat.type == "private":
        o = message.forward_origin
        cid: int | None = None
        if o is not None:
            if isinstance(o, MessageOriginChat) and o.sender_chat:
                cid = o.sender_chat.id
            elif isinstance(o, MessageOriginChannel) and o.chat:
                cid = o.chat.id
        if cid is None:
            await message.answer(
                "<b>Как узнать ID для уведомлений о покупках</b>\n\n"
                "1) Добавь <b>этого же бота</b> в свою админ-группу (форум с темами).\n"
                "2) <b>ID группы</b> — напиши в группе команду <code>/chatid</code> "
                "(можно в любой теме или в «Общем»). Бот пришлёт число вида <code>-100…</code> "
                "— его клади в <code>ADMIN_SALES_NOTIFY_CHAT_ID</code>.\n"
                "3) <b>ID каждой темы</b> — зайди <i>внутрь темы</i> ("
                f"{all_plans_premium_line_html(sep=', ')}) и "
                "в этой теме снова напиши <code>/chatid</code>. Появится "
                "<code>message_thread_id</code> — его в соответствующий "
                "<code>ADMIN_SALES_THREAD_*</code> в <code>.env</code>.\n"
                "4) Повтори шаг 3 для всех тем: "
                f"{all_plans_premium_line_html(sep=', ')} и бонусы/пакеты.\n"
                "5) Перезапусти бота.\n\n"
                "<blockquote><i>Если написать <code>/chatid</code> только в личке без пересылки — "
                "показывается эта памятка. Пересланное из группы иногда даёт только chat id, "
                "без id темы — надёжнее писать <code>/chatid</code> прямо в каждой теме.</i></blockquote>",
                parse_mode=HTML,
            )
            return
        lines = [
            "<b>Пересланное из группы/канала</b>",
            f"<b>chat id:</b> <code>{cid}</code>",
        ]
        if message.message_thread_id:
            lines.append(f"<b>message_thread_id:</b> <code>{message.message_thread_id}</code>")
        else:
            lines.append(
                "<i>ID темы обычно не передаётся при пересылке — открой тему в группе и напиши там</i> <code>/chatid</code>."
            )
        await message.answer("\n".join(lines), parse_mode=HTML)
        return

    lines = [
        f"<b>Тип:</b> <code>{esc(chat.type)}</code>",
        f"<b>chat id</b> → <code>ADMIN_SALES_NOTIFY_CHAT_ID</code>:\n<code>{chat.id}</code>",
    ]
    if message.message_thread_id:
        lines.append(
            f"<b>message_thread_id</b> (эта тема) → один из <code>ADMIN_SALES_THREAD_*</code>:\n"
            f"<code>{message.message_thread_id}</code>"
        )
    else:
        lines.append(
            "<i>Топик не определён — если это форум, открой нужную <b>тему</b> и повтори <code>/chatid</code> там.</i>"
        )
    await message.answer("\n".join(lines), parse_mode=HTML)


@router.message(Command("addcredits"))
async def cmd_addcredits(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer(
            "Формат:\n"
            "/addcredits <user_id> <amount>\n\n"
            "Пример:\n"
            "/addcredits 123456789 50"
        )
        return

    target_user_id = int(parts[1])
    amount = int(parts[2])
    if amount <= 0:
        await message.answer("Количество кредитов должно быть больше 0.")
        return

    await ensure_user(target_user_id, None)
    ok = await add_credits_with_reason(
        target_user_id,
        amount,
        source="admin_add",
        details=f"/addcredits by {message.from_user.id}",
    )
    if not ok:
        await message.answer(
            f"Не удалось начислить {CREDITS_COIN_TG_HTML} кредиты.",
            parse_mode=HTML,
        )
        return

    new_balance = await get_credits(target_user_id)
    await message.answer(
        f'Готово <tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> Пользователю {target_user_id} начислено {amount} кредитов.\n'
        f"Новый баланс: {new_balance}."
    )


@router.message(Command("takecredits"))
async def cmd_takecredits(message: Message) -> None:
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("Эта команда только для администраторов.")
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer(
            "Формат:\n"
            "/takecredits <user_id> <amount>\n\n"
            "Пример:\n"
            "/takecredits 123456789 20"
        )
        return

    target_user_id = int(parts[1])
    amount = int(parts[2])
    if amount <= 0:
        await message.answer("Количество кредитов должно быть больше 0.")
        return

    await ensure_user(target_user_id, None)
    before_balance = await get_credits(target_user_id)
    if before_balance < amount:
        await message.answer(
            f"Недостаточно кредитов: у пользователя {target_user_id} сейчас {before_balance}, "
            f"запрошено списать {amount}. Списание не выполнено."
        )
        return
    ok = await take_credits_with_reason(
        target_user_id,
        amount,
        source="admin_take",
        details=f"/takecredits by {message.from_user.id}",
    )
    if not ok:
        await message.answer(
            f"Не удалось списать {CREDITS_COIN_TG_HTML} кредиты (попробуй ещё раз).",
            parse_mode=HTML,
        )
        return

    new_balance = await get_credits(target_user_id)
    await message.answer(
        f'Готово <tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji> У пользователя {target_user_id} списано {amount} кредитов.\n'
        f"Новый баланс: {new_balance}."
    )

