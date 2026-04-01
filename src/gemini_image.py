from __future__ import annotations

import asyncio
from io import BytesIO

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, GEMINI_IMAGE_MODEL


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
    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    return _extract_image_bytes(response)


async def generate_image_png(prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    return await asyncio.to_thread(generate_image_png_sync, prompt, model)


def edit_image_png_sync(image_bytes: bytes, prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    client = _build_client()
    mime = _guess_image_mime(image_bytes)
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime),
            types.Part.from_text(text=prompt),
        ],
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    return _extract_image_bytes(response)


async def edit_image_png(image_bytes: bytes, prompt: str, model: str = GEMINI_IMAGE_MODEL) -> bytes:
    return await asyncio.to_thread(edit_image_png_sync, image_bytes, prompt, model)

