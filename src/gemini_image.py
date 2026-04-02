from __future__ import annotations

import asyncio
import logging
import re
import time
from io import BytesIO

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL

logger = logging.getLogger(__name__)

_GEMINI_MAX_ATTEMPTS = 3


def is_gemini_configured() -> bool:
    return bool(GEMINI_API_KEY and GEMINI_API_KEY.strip())


def _guess_image_mime(data: bytes) -> str:
    if len(data) >= 12 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _build_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise RuntimeError("Не задан GEMINI_API_KEY в .env")
    return genai.Client(api_key=GEMINI_API_KEY)


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    if isinstance(exc, APIError) and getattr(exc, "code", None) == 429:
        return True
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _retry_delay_seconds(exc: BaseException, attempt: int) -> float:
    """Сервер иногда отдаёт retryDelay в теле ошибки (например 36s)."""
    text = str(exc)
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+)s", text, re.I)
    if m:
        return min(float(m.group(1)), 120.0)
    m = re.search(r"retry in ([\d.]+)\s*s", text, re.I)
    if m:
        return min(float(m.group(1)), 120.0)
    return min(5.0 * (2**attempt), 60.0)


def format_gemini_user_error(exc: BaseException) -> str:
    """Короткий текст для пользователя; детали — только в логах."""
    if isinstance(exc, APIError) and getattr(exc, "code", None) == 429:
        return (
            "Сервис генерации временно недоступен: лимит запросов Google Gemini "
            "(бесплатный тариф или квота ключа).\n\n"
            "Попробуй через минуту. Если ошибка повторяется — в "
            "<a href=\"https://aistudio.google.com/apikey\">Google AI Studio</a> "
            "проверь квоты и при необходимости включи оплату для API."
        )
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or (
        "quota" in text.lower() and "exceed" in text.lower()
    ):
        return (
            "Сервис генерации временно недоступен: лимит запросов Google Gemini "
            "(бесплатный тариф или квота ключа).\n\n"
            "Попробуй через минуту. Если ошибка повторяется — в "
            "<a href=\"https://aistudio.google.com/apikey\">Google AI Studio</a> "
            "проверь квоты и при необходимости включи оплату для API."
        )
    if "503" in text or "UNAVAILABLE" in text or "500" in text:
        return "Сервис генерации временно перегружен. Попробуй ещё раз через минуту."
    return (
        "Не удалось сгенерировать картинку. Попробуй позже или смени формулировку запроса."
    )


def _extract_image_bytes(response: types.GenerateContentResponse) -> bytes:
    if not response.candidates:
        raise RuntimeError("Пустой ответ модели.")

    for candidate in response.candidates:
        content = candidate.content
        if not content or not content.parts:
            continue
        for part in content.parts:
            try:
                image = part.as_image()
            except Exception:
                image = None
            if image is None:
                continue
            buf = BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()

    raise RuntimeError("Модель не вернула изображение.")


def generate_image_png_sync(prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    client = _build_client()
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
            return _extract_image_bytes(response)
        except Exception as e:
            if attempt < _GEMINI_MAX_ATTEMPTS - 1 and _is_retryable_gemini_error(e):
                delay = _retry_delay_seconds(e, attempt)
                logger.warning(
                    "Gemini generate_content 429/overload, retry %s/%s in %ss",
                    attempt + 1,
                    _GEMINI_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue
            raise


async def generate_image_png(prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    return await asyncio.to_thread(generate_image_png_sync, prompt, model)


def edit_image_png_sync(image_bytes: bytes, prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    client = _build_client()
    mime = _guess_image_mime(image_bytes)
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime),
                    types.Part.from_text(text=prompt),
                ],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
            return _extract_image_bytes(response)
        except Exception as e:
            if attempt < _GEMINI_MAX_ATTEMPTS - 1 and _is_retryable_gemini_error(e):
                delay = _retry_delay_seconds(e, attempt)
                logger.warning(
                    "Gemini edit generate_content 429/overload, retry %s/%s in %ss",
                    attempt + 1,
                    _GEMINI_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue
            raise


async def edit_image_png(image_bytes: bytes, prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    return await asyncio.to_thread(edit_image_png_sync, image_bytes, prompt, model)

