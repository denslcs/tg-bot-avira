"""Генерация изображений через OpenRouter (например FLUX.2 Klein)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import (
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_IMAGE_ASPECT_RATIO,
    OPENROUTER_IMAGE_CACHE_DIR,
    OPENROUTER_IMAGE_CACHE_ENABLED,
    OPENROUTER_IMAGE_MODEL,
)

logger = logging.getLogger(__name__)

_cache_io_lock = asyncio.Lock()


class OpenRouterApiError(RuntimeError):
    """Ответ OpenRouter с HTTP ≥ 400 — ожидаемая ситуация для логов без traceback."""

    def __init__(self, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.http_status = http_status


def is_openrouter_image_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def format_openrouter_image_user_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "401" in text or ("invalid" in text and "key" in text):
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


def _normalize_prompt_for_cache(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _cache_key(model: str, normalized_prompt: str) -> str:
    raw = f"{model.strip()}\n{normalized_prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(key_hex: str) -> Path:
    return OPENROUTER_IMAGE_CACHE_DIR / f"{key_hex}.png"


def _read_cache_file_sync(path: Path) -> bytes | None:
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def _write_cache_file_sync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as e:
        logger.warning("OpenRouter image cache write failed: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


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
    url = ""
    if isinstance(first, dict):
        url_obj = first.get("image_url") or first.get("imageUrl")
        if isinstance(url_obj, dict):
            url = str(url_obj.get("url") or "")
        elif url_obj is not None:
            url = str(url_obj)
    else:
        url = str(first)
    if not url:
        raise RuntimeError("Пустая ссылка на изображение в ответе")
    if url.startswith("data:"):
        return _data_url_to_bytes(url)
    raise RuntimeError("Кэш и декодирование поддерживают только data URL из ответа OpenRouter")


async def openrouter_text_to_image_bytes(
    prompt: str,
    *,
    model: str | None = None,
    use_cache: bool = True,
) -> bytes:
    """
    Текст → PNG bytes. OpenRouter отдаёт картинку как data URL (base64).

    use_cache=False — всегда новый запрос к API (кнопка «Ещё раз»): кэш не читается и не пишется.
    use_cache=True — при совпадении модели и нормализованного промпта отдаются байты с диска.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Не задан OPENROUTER_API_KEY")
    m = (model or OPENROUTER_IMAGE_MODEL).strip() or OPENROUTER_IMAGE_MODEL
    norm = _normalize_prompt_for_cache(prompt)

    if use_cache and OPENROUTER_IMAGE_CACHE_ENABLED and norm:
        key = _cache_key(m, norm)
        path = _cache_path(key)
        async with _cache_io_lock:
            cached = await asyncio.to_thread(_read_cache_file_sync, path)
        if cached:
            logger.info("OpenRouter image cache hit model=%s key=%s…", m, key[:12])
            return cached

    url = f"{OPENROUTER_API_BASE}/chat/completions"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
    if OPENROUTER_APP_TITLE:
        headers["X-Title"] = OPENROUTER_APP_TITLE

    image_cfg: dict[str, str] | None = None
    if OPENROUTER_IMAGE_ASPECT_RATIO:
        image_cfg = {"aspect_ratio": OPENROUTER_IMAGE_ASPECT_RATIO}

    modalities_variants = (["image"], ["image", "text"])
    last_data: dict[str, Any] | None = None
    result: bytes | None = None

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        for mods in modalities_variants:
            body: dict[str, Any] = {
                "model": m,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": mods,
            }
            if image_cfg is not None:
                body["image_config"] = image_cfg
            resp = await client.post(url, headers=headers, json=body)
            try:
                data = resp.json()
            except Exception:
                resp.raise_for_status()
                raise RuntimeError(resp.text[:500] if resp.text else "Некорректный JSON") from None
            last_data = data if isinstance(data, dict) else None
            if resp.status_code >= 400:
                err = data.get("error") if isinstance(data, dict) else None
                msg = ""
                if isinstance(err, dict):
                    msg = str(err.get("message") or err.get("metadata") or "")
                logger.warning("OpenRouter error status=%s body=%s", resp.status_code, data)
                raise OpenRouterApiError(msg or f"HTTP {resp.status_code}", http_status=resp.status_code)
            if not isinstance(data, dict):
                raise RuntimeError("Неожиданный формат ответа")
            try:
                result = _extract_first_image_bytes(data)
                break
            except RuntimeError as e:
                if mods is modalities_variants[-1]:
                    raise
                if "нет изображения" in str(e).lower():
                    logger.info("OpenRouter: нет images с modalities=%s, пробуем следующий вариант", mods)
                    continue
                raise

    if result is None:
        raise RuntimeError(str(last_data)[:300] if last_data else "Пустой ответ OpenRouter")

    if use_cache and OPENROUTER_IMAGE_CACHE_ENABLED and norm:
        key = _cache_key(m, norm)
        path = _cache_path(key)
        async with _cache_io_lock:
            await asyncio.to_thread(_write_cache_file_sync, path, result)

    return result
