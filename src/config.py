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

