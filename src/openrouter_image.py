"""Генерация изображений через OpenRouter (например FLUX.2 Klein)."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from src.config import (
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY,
    OPENROUTER_IMAGE_MODEL,
)

logger = logging.getLogger(__name__)


def is_openrouter_image_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def format_openrouter_image_user_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "401" in text or "invalid" in text and "key" in text:
        return (
            "Ошибка ключа OpenRouter: проверь <code>OPENROUTER_API_KEY</code> в .env "
            "(ключ без лишних пробелов и кавычек)."
        )
    if "402" in text or "payment" in text or "credits" in text:
        return (
            "На балансе OpenRouter не хватает кредитов. Пополни счёт в "
            "<a href=\"https://openrouter.ai\">openrouter.ai</a>."
        )
    if "429" in text or "rate" in text:
        return "OpenRouter перегружен или лимит запросов. Попробуй через минуту."
    return "Не удалось сгенерировать картинку через OpenRouter. Попробуй позже или смени формулировку."


def _data_url_to_bytes(data_url: str) -> bytes:
    if not data_url.startswith("data:"):
        raise ValueError("Ожидался data URL с изображением")
    comma = data_url.find(",")
    if comma == -1:
        raise ValueError("Некорректный data URL")
    raw = data_url[comma + 1 :].strip()
    return base64.b64decode(raw, validate=False)


def _extract_first_image_bytes(payload: dict[str, Any]) -> bytes:
    choices = payload.get("choices") or []
    if not choices:
        err = payload.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else None
        raise RuntimeError(msg or "Пустой ответ OpenRouter (нет choices)")
    message = (choices[0] or {}).get("message") or {}
    images = message.get("images")
    if not images:
        raise RuntimeError("В ответе нет изображения (проверь modalities: image и модель)")
    first = images[0]
    if isinstance(first, dict):
        url_obj = first.get("image_url")
        if isinstance(url_obj, dict):
            url = url_obj.get("url") or ""
        else:
            url = str(url_obj or "")
    else:
        url = str(first)
    if not url:
        raise RuntimeError("Пустая ссылка на изображение в ответе")
    return _data_url_to_bytes(url)


async def openrouter_text_to_image_bytes(prompt: str, *, model: str | None = None) -> bytes:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Не задан OPENROUTER_API_KEY")
    m = (model or OPENROUTER_IMAGE_MODEL).strip() or OPENROUTER_IMAGE_MODEL
    url = f"{OPENROUTER_API_BASE}/chat/completions"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
    if OPENROUTER_APP_TITLE:
        headers["X-Title"] = OPENROUTER_APP_TITLE
    body = {
        "model": m,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image"],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        resp = await client.post(url, headers=headers, json=body)
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        raise RuntimeError(resp.text[:500] if resp.text else "Некорректный JSON") from None
    if resp.status_code >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        msg = ""
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("metadata") or "")
        logger.warning("OpenRouter error status=%s body=%s", resp.status_code, data)
        raise RuntimeError(msg or f"HTTP {resp.status_code}")
    if not isinstance(data, dict):
        raise RuntimeError("Неожиданный формат ответа")
    return _extract_first_image_bytes(data)
