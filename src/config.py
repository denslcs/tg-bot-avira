from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True, encoding="utf-8-sig")


def _must_getenv(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env var: {name}. Put it into .env")
    return value


def _parse_admin_ids(raw_value: str) -> set[int]:
    result: set[int] = set()
    for chunk in raw_value.split(","):
        text = chunk.strip()
        if not text:
            continue
        if text.isdigit():
            result.add(int(text))
    return result


def _parse_int(raw_value: str, default: int = 0) -> int:
    text = raw_value.strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


TELEGRAM_BOT_TOKEN: str = _must_getenv("TELEGRAM_BOT_TOKEN")
SUPPORT_BOT_TOKEN: str = os.getenv("SUPPORT_BOT_TOKEN", "").strip()
SUPPORT_BOT_USERNAME: str = os.getenv("SUPPORT_BOT_USERNAME", "").strip()
DB_PATH: str = os.getenv("DB_PATH", "data/bot.sqlite3").strip() or "data/bot.sqlite3"
START_CREDITS: int = int(os.getenv("START_CREDITS", "20"))
# Второе сообщение в ЛС сразу после главного экрана /start (новости, новый промпт). Пусто = не слать.
# В .env можно писать в одну строку; для переноса строк подставьте \n в тексте.
_start_ann_raw = os.getenv("START_ANNOUNCEMENT", "").strip()
START_ANNOUNCEMENT: str = _start_ann_raw.replace("\\n", "\n") if _start_ann_raw else ""
# Опционально: картинка к этому объявлению (путь от корня репозитория или абсолютный). Файл должен существовать.
_start_ann_img = os.getenv("START_ANNOUNCEMENT_IMAGE", "").strip()
START_ANNOUNCEMENT_IMAGE: Path | None = None
if _start_ann_img:
    _ann_p = Path(_start_ann_img)
    if not _ann_p.is_absolute():
        _ann_p = PROJECT_ROOT / _ann_p
    if _ann_p.is_file():
        START_ANNOUNCEMENT_IMAGE = _ann_p
ADMIN_IDS: set[int] = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "").strip()
SUPPORT_CHAT_ID: int = _parse_int(os.getenv("SUPPORT_CHAT_ID", "0"))
# Тема «Отзывы» в группе поддержки: id ветки или 0 = создать при первом отзыве и сохранить в bot_meta
SUPPORT_FEEDBACK_THREAD_ID: int = _parse_int(os.getenv("SUPPORT_FEEDBACK_THREAD_ID", "0"))

# Если основной бот добавлен в админ-группу с топиками: по умолчанию НЕ дублируем ответы
# из тем в личку (это делает support-бот). Включи 1 только если нужен старый режим одного бота.
MAIN_BOT_RELAY_SUPPORT_TOPICS: bool = os.getenv("MAIN_BOT_RELAY_SUPPORT_TOPICS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# SLA: через сколько часов без первого ответа пользователю считать тикет «просроченным» (подсказки и фоновые алерты)
SLA_WARNING_HOURS: float = float(os.getenv("SLA_WARNING_HOURS", "4"))
# Интервал между SLA-напоминаниями в общий чат форума (без topic). Часы по умолчанию 8; либо явно минуты (приоритетнее).
_sla_min_raw = os.getenv("SLA_ALERT_INTERVAL_MINUTES", "").strip()
if _sla_min_raw:
    SLA_ALERT_INTERVAL_MINUTES = max(60, _parse_int(_sla_min_raw, 480))
else:
    SLA_ALERT_INTERVAL_MINUTES = max(
        60,
        int(float(os.getenv("SLA_ALERT_INTERVAL_HOURS", "8")) * 60),
    )

# Еженедельная сводка: час UTC (0–23) и день недели (0=пн)
WEEKLY_REPORT_HOUR_UTC: int = min(23, max(0, _parse_int(os.getenv("WEEKLY_REPORT_HOUR_UTC", "9"), 9)))
WEEKLY_REPORT_WEEKDAY: int = min(6, max(0, _parse_int(os.getenv("WEEKLY_REPORT_WEEKDAY", "0"), 0)))

# Личка основного бота: длина сообщения и черновика поддержки; антифлуд (сообщений за ~минуту)
MAX_USER_MESSAGE_CHARS: int = max(500, min(32000, _parse_int(os.getenv("MAX_USER_MESSAGE_CHARS", "1100"), 1100)))
MAX_SUPPORT_DRAFT_TOTAL_CHARS: int = max(2000, min(64000, _parse_int(os.getenv("MAX_SUPPORT_DRAFT_TOTAL_CHARS", "16000"), 16000)))
PRIVATE_MESSAGES_PER_MINUTE: int = max(5, min(120, _parse_int(os.getenv("PRIVATE_MESSAGES_PER_MINUTE", "30"), 30)))

# Генерация картинок: только OpenRouter (см. OPENROUTER_*).
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_API_BASE: str = (
    os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1").strip().rstrip("/")
    or "https://openrouter.ai/api/v1"
)
OPENROUTER_IMAGE_MODEL: str = (
    os.getenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/flux.2-klein-4b").strip()
    or "black-forest-labs/flux.2-klein-4b"
)
OPENROUTER_IMAGE_COST_CREDITS: int = max(1, _parse_int(os.getenv("OPENROUTER_IMAGE_COST_CREDITS", "3"), 3))
# Фиксированная цена «Готовых идей» (не из .env).
OPENROUTER_IMAGE_READY_IDEAS_COST_CREDITS: int = 30
# Вторая модель для подписчиков (панель выбора). Должна быть с output=image на OpenRouter.
# Пусто — только базовая модель, без панели. Не языковые модели вроде qwen/... — они не рисуют картинки.
OPENROUTER_IMAGE_MODEL_ALT: str = os.getenv(
    "OPENROUTER_IMAGE_MODEL_ALT", "black-forest-labs/flux.2-pro"
).strip()
OPENROUTER_IMAGE_ALT_COST_CREDITS: int = max(
    1, _parse_int(os.getenv("OPENROUTER_IMAGE_ALT_COST_CREDITS", "20"), 20)
)
OPENROUTER_IMAGE_GEMINI_MODEL: str = (
    os.getenv("OPENROUTER_IMAGE_GEMINI_MODEL", "google/gemini-2.5-flash-image").strip()
    or "google/gemini-2.5-flash-image"
)
OPENROUTER_IMAGE_GEMINI_COST_CREDITS: int = max(
    1, _parse_int(os.getenv("OPENROUTER_IMAGE_GEMINI_COST_CREDITS", "8"), 8)
)
# «Nano Banana 2» в панели Galaxy / Universe (см. _model_choices_for_subscription_plan).
OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL: str = (
    os.getenv("OPENROUTER_IMAGE_GEMINI_PREVIEW_MODEL", "google/gemini-3.1-flash-image-preview").strip()
    or "google/gemini-3.1-flash-image-preview"
)
OPENROUTER_IMAGE_GEMINI_PREVIEW_COST_CREDITS: int = max(
    1, _parse_int(os.getenv("OPENROUTER_IMAGE_GEMINI_PREVIEW_COST_CREDITS", "15"), 15)
)
# Опциональная Pro-модель для ready-флоу с референсами (если задана — приоритетнее Banana 2).
OPENROUTER_IMAGE_GEMINI_PRO_MODEL: str = os.getenv("OPENROUTER_IMAGE_GEMINI_PRO_MODEL", "").strip()
# В API уходит aspect_ratio 1:1 (~1024×1024, ~1 Мп по доке OpenRouter).
# Значение вроде «1K» у FLUX на OpenRouter может давать больше мегапикселей и цену ~2.5× к тарифу «$ за Мп».
# Пустой OPENROUTER_IMAGE_OUTPUT_SIZE — не передаём image_size (только 1:1), ближе к одному мегапикселю в биллинге.
OPENROUTER_IMAGE_OUTPUT_SIZE: str = os.getenv("OPENROUTER_IMAGE_OUTPUT_SIZE", "").strip()
# Кэш сгенерированных картинок на диске (ключ: модель + нормализованный промпт). 0 = выкл.
_openrouter_cache_raw = os.getenv("OPENROUTER_IMAGE_CACHE", "1").strip().lower()
OPENROUTER_IMAGE_CACHE_ENABLED: bool = _openrouter_cache_raw not in ("0", "false", "no", "off")
_cdir = os.getenv("OPENROUTER_IMAGE_CACHE_DIR", "data/image_cache_openrouter").strip()
OPENROUTER_IMAGE_CACHE_DIR: Path = PROJECT_ROOT / (_cdir or "data/image_cache_openrouter")
# Опционально для статистики OpenRouter (см. документацию)
OPENROUTER_HTTP_REFERER: str = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "Tg_bot_AVIRA").strip() or "Tg_bot_AVIRA"

# Polza.ai — GPT Image (Media API); модели из POLZA_IMAGE_MODEL_IDS — Galaxy / Universe (см. img_commands).
POLZAAI_API_KEY: str = os.getenv("POLZAAI_API_KEY", "").strip()
POLZAAI_API_BASE: str = (
    os.getenv("POLZAAI_API_BASE", "https://polza.ai/api").strip().rstrip("/") or "https://polza.ai/api"
)
POLZA_IMAGE_MODEL_GPT_IMAGE_15: str = (
    os.getenv("POLZA_IMAGE_MODEL_GPT_IMAGE_15", "openai/gpt-image-1.5").strip() or "openai/gpt-image-1.5"
)
POLZA_IMAGE_MODEL_GPT5_IMAGE: str = (
    os.getenv("POLZA_IMAGE_MODEL_GPT5_IMAGE", "openai/gpt-5-image").strip() or "openai/gpt-5-image"
)
POLZA_IMAGE_GPT_IMAGE_15_COST_CREDITS: int = max(
    1, _parse_int(os.getenv("POLZA_IMAGE_GPT_IMAGE_15_COST_CREDITS", "10"), 10)
)
POLZA_IMAGE_GPT5_IMAGE_COST_CREDITS: int = max(
    1, _parse_int(os.getenv("POLZA_IMAGE_GPT5_IMAGE_COST_CREDITS", "12"), 12)
)
POLZA_IMAGE_MODEL_IDS: frozenset[str] = frozenset(
    {POLZA_IMAGE_MODEL_GPT_IMAGE_15, POLZA_IMAGE_MODEL_GPT5_IMAGE}
)
# Polza Media: openai/gpt-image-* на Polza не принимают input.image_resolution (ошибка API).
# Задай значение только если документация модели явно поддерживает поле; иначе оставь пусто.
_polza_res = os.getenv("POLZA_IMAGE_INPUT_RESOLUTION", "").strip()
POLZA_IMAGE_INPUT_RESOLUTION: str | None = _polza_res if _polza_res else None

# Оплата подписки: внешние ссылки (YooKassa / Stripe / crypto-касса). Пусто — бот предложит поддержку.
PAY_URL_CARD_RU: str = os.getenv("PAY_URL_CARD_RU", "").strip()
PAY_URL_CARD_INTL: str = os.getenv("PAY_URL_CARD_INTL", "").strip()
PAY_URL_CRYPTO: str = os.getenv("PAY_URL_CRYPTO", "").strip()

# Уведомления о покупках (⭐ Stars) в админ-чат с топиками: id супергруппы и id ветки на тариф / бонусы.
# Бот должен быть участником чата и иметь право писать в темы. 0 = не слать.
ADMIN_SALES_NOTIFY_CHAT_ID: int = _parse_int(os.getenv("ADMIN_SALES_NOTIFY_CHAT_ID", "0"), 0)
ADMIN_SALES_THREAD_STARTER: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_STARTER", "0"), 0)
ADMIN_SALES_THREAD_NOVA: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_NOVA", "0"), 0)
ADMIN_SALES_THREAD_SUPERNOVA: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_SUPERNOVA", "0"), 0)
ADMIN_SALES_THREAD_GALAXY: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_GALAXY", "0"), 0)
ADMIN_SALES_THREAD_UNIVERSE: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_UNIVERSE", "0"), 0)
ADMIN_SALES_THREAD_BONUS_PACKS: int = _parse_int(os.getenv("ADMIN_SALES_THREAD_BONUS_PACKS", "0"), 0)
