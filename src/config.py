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

# Лимит сообщений в личке основного бота в сутки (UTC) для пользователей без подписки. 0 = выкл.
FREE_DAILY_MESSAGE_LIMIT: int = _parse_int(os.getenv("FREE_DAILY_MESSAGE_LIMIT", "0"), 0)

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
OPENROUTER_IMAGE_COST_CREDITS: int = max(1, _parse_int(os.getenv("OPENROUTER_IMAGE_COST_CREDITS", "5"), 5))
# Соотношение сторон для OpenRouter image_config: "1:1" → 1024×1024 (документация OpenRouter). Пусто — не передавать.
OPENROUTER_IMAGE_ASPECT_RATIO: str = os.getenv("OPENROUTER_IMAGE_ASPECT_RATIO", "1:1").strip()
# Кэш сгенерированных картинок на диске (ключ: модель + нормализованный промпт). 0 = выкл.
_openrouter_cache_raw = os.getenv("OPENROUTER_IMAGE_CACHE", "1").strip().lower()
OPENROUTER_IMAGE_CACHE_ENABLED: bool = _openrouter_cache_raw not in ("0", "false", "no", "off")
_cdir = os.getenv("OPENROUTER_IMAGE_CACHE_DIR", "data/image_cache_openrouter").strip()
OPENROUTER_IMAGE_CACHE_DIR: Path = PROJECT_ROOT / (_cdir or "data/image_cache_openrouter")
# Опционально для статистики OpenRouter (см. документацию)
OPENROUTER_HTTP_REFERER: str = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "Tg_bot_AVIRA").strip() or "Tg_bot_AVIRA"

# Оплата подписки: внешние ссылки (YooKassa / Stripe / crypto-касса). Пусто — бот предложит поддержку.
PAY_URL_CARD_RU: str = os.getenv("PAY_URL_CARD_RU", "").strip()
PAY_URL_CARD_INTL: str = os.getenv("PAY_URL_CARD_INTL", "").strip()
PAY_URL_CRYPTO: str = os.getenv("PAY_URL_CRYPTO", "").strip()
