"""Генерация изображений через Polza.ai Media API (GPT Image и др.)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config import (
    POLZAAI_API_BASE,
    POLZAAI_API_KEY,
    POLZA_IMAGE_INPUT_RESOLUTION,
    POLZA_IMAGE_MODEL_IDS,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 2.0
POLL_MAX_ATTEMPTS = 90


class PolzaApiError(RuntimeError):
    """Ответ Polza с HTTP ≥ 400 или status failed."""

    def __init__(self, message: str, *, http_status: int = 0) -> None:
        super().__init__(message)
        self.http_status = http_status


def is_polza_configured() -> bool:
    return bool(POLZAAI_API_KEY)


def is_polza_image_model(model: str) -> bool:
    return (model or "").strip() in POLZA_IMAGE_MODEL_IDS


# Не раскрываем пользователю сбои биллинга/квот на стороне Polza.ai.
_IMAGE_GEN_TRY_LATER = (
    "Сейчас не получилось сгенерировать картинку. Попробуй позже или повтори запрос чуть позже."
)


def _polza_error_looks_like_provider_billing(exc: PolzaApiError) -> bool:
    if exc.http_status == 402:
        return True
    t = str(exc).lower()
    hints = (
        "402",
        "payment",
        "insufficient",
        "balance",
        "billing",
        "quota",
        "not enough",
        "funds",
        "квот",
        "баланс",
        "оплат",
        "недостаточно",
        "лимит",
        "credit limit",
    )
    return any(h in t for h in hints)


def format_polza_image_user_error(exc: BaseException) -> str:
    if isinstance(exc, PolzaApiError):
        if _polza_error_looks_like_provider_billing(exc):
            return _IMAGE_GEN_TRY_LATER
        msg = str(exc) or ""
        if "polzaai_api_key" in msg.lower():
            return (
                "Ошибка доступа к генерации картинок. Администратору: проверь ключ "
                "<code>POLZAAI_API_KEY</code> в <code>.env</code>."
            )
        if exc.http_status >= 500:
            return _IMAGE_GEN_TRY_LATER
        if exc.http_status == 429:
            return "Сервис генерации перегружен или сработал лимит. Попробуй через минуту."
        return msg or _IMAGE_GEN_TRY_LATER
    if isinstance(exc, TimeoutError):
        return "Генерация через Polza.ai слишком долгая. Попробуй ещё раз позже."
    return "Не удалось получить картинку через Polza.ai. Попробуй позже или смени модель."


def _media_url() -> str:
    base = (POLZAAI_API_BASE or "https://polza.ai/api").strip().rstrip("/")
    return f"{base}/v1/media"


def _extract_result_url(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        u = data.get("url")
        if u:
            return str(u).strip()
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"]).strip()
    return ""


def _error_message(payload: dict[str, Any]) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    if err:
        return str(err)
    return payload.get("content") or "Неизвестная ошибка Polza.ai"


async def polza_text_to_image_bytes(
    prompt: str,
    *,
    model: str,
    user_id: int | None = None,
) -> bytes:
    """
    Текст → PNG/JPEG bytes. POST /v1/media, при pending — опрос GET /v1/media/{id}.
    aspect_ratio 1:1; image_resolution — только если POLZA_IMAGE_INPUT_RESOLUTION задан (GPT Image его не поддерживает).
    Поле user — Telegram user_id для учёта у Polza; доступ по тарифу проверяется в боте до вызова.
    """
    if not POLZAAI_API_KEY:
        raise PolzaApiError("Не задан POLZAAI_API_KEY")
    m = (model or "").strip()
    if m not in POLZA_IMAGE_MODEL_IDS:
        raise PolzaApiError(f"Модель не поддерживается через Polza: {m}")

    url = _media_url()
    headers = {
        "Authorization": f"Bearer {POLZAAI_API_KEY}",
        "Content-Type": "application/json",
    }
    inp: dict[str, Any] = {
        "prompt": (prompt or "").strip(),
        "aspect_ratio": "1:1",
    }
    if POLZA_IMAGE_INPUT_RESOLUTION:
        inp["image_resolution"] = POLZA_IMAGE_INPUT_RESOLUTION

    body: dict[str, Any] = {
        "model": m,
        "input": inp,
    }
    if user_id is not None:
        body["user"] = str(user_id)

    timeout = httpx.Timeout(200.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        try:
            payload = resp.json()
        except Exception:
            resp.raise_for_status()
            raise PolzaApiError(f"HTTP {resp.status_code}: некорректный JSON") from None

        if resp.status_code >= 400:
            msg = _error_message(payload) if isinstance(payload, dict) else resp.text[:300]
            raise PolzaApiError(msg or f"HTTP {resp.status_code}", http_status=resp.status_code)

        if not isinstance(payload, dict):
            raise PolzaApiError("Неожиданный ответ Polza.ai")

        media_id = str(payload.get("id") or "").strip()
        status = str(payload.get("status") or "").lower()

        if status == "completed":
            img_url = _extract_result_url(payload)
            if not img_url:
                raise PolzaApiError("В ответе Polza нет URL изображения")
            return await _download_bytes(client, img_url)

        if status == "failed":
            raise PolzaApiError(_error_message(payload))

        if not media_id:
            raise PolzaApiError("Polza не вернула id задачи")

        # pending / processing — опрос
        status_url = f"{url}/{media_id}"
        for attempt in range(POLL_MAX_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_SEC)
            r2 = await client.get(status_url, headers=headers)
            try:
                p2 = r2.json()
            except Exception:
                logger.warning("Polza poll: bad JSON attempt=%s", attempt)
                continue
            if not isinstance(p2, dict):
                continue
            st = str(p2.get("status") or "").lower()
            if st == "completed":
                img_url = _extract_result_url(p2)
                if not img_url:
                    raise PolzaApiError("Задача завершена, но URL изображения пуст")
                return await _download_bytes(client, img_url)
            if st == "failed":
                raise PolzaApiError(_error_message(p2))
            if st in ("cancelled",):
                raise PolzaApiError("Генерация отменена")

        raise TimeoutError("Polza.ai: превышено время ожидания")


async def _download_bytes(client: httpx.AsyncClient, image_url: str) -> bytes:
    r = await client.get(image_url, follow_redirects=True)
    r.raise_for_status()
    data = r.content
    if not data:
        raise PolzaApiError("Пустой файл изображения по URL")
    return data
