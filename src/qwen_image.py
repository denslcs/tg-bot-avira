"""Qwen-Image и Qwen-Image-Edit (Alibaba DashScope, multimodal-generation)."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from src.config import (
    DASHSCOPE_API_KEY,
    QWEN_DASHSCOPE_API_V1_BASE,
    QWEN_IMAGE_EDIT_MODEL,
    QWEN_IMAGE_EDIT_SIZE,
    QWEN_IMAGE_MODEL,
    QWEN_IMAGE_SIZE,
)

logger = logging.getLogger(__name__)

_MAX_INPUT_BYTES = 10 * 1024 * 1024


def is_qwen_image_configured() -> bool:
    return bool(DASHSCOPE_API_KEY)


def format_qwen_image_user_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "401" in text or "403" in text:
        return (
            "Ошибка ключа DashScope: проверь DASHSCOPE_API_KEY и регион API "
            "(Singapore vs Beijing)."
        )
    if "429" in text or "throttl" in text or "rate" in text:
        return "Сервис Qwen перегружен или лимит запросов. Попробуй через минуту."
    if "quota" in text or "balance" in text or "insufficient" in text:
        return "Квота или баланс Qwen в Model Studio исчерпаны. Проверь консоль Alibaba Cloud."
    if "10 mb" in text or "file size" in text or "too large" in text:
        return "Фото слишком большое для Qwen (лимит ~10 МБ). Отправь сжатое изображение."
    return "Не удалось обработать картинку через Qwen. Попробуй позже или смени формулировку."


def _multimodal_url() -> str:
    return f"{QWEN_DASHSCOPE_API_V1_BASE}/services/aigc/multimodal-generation/generation"


def _guess_mime(image_bytes: bytes) -> str:
    if len(image_bytes) >= 12 and image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(image_bytes) >= 3 and image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if len(image_bytes) >= 6 and image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _bytes_to_data_url(image_bytes: bytes) -> str:
    mime = _guess_mime(image_bytes)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_first_image_url(data: dict[str, Any]) -> str:
    if data.get("code"):
        raise RuntimeError(data.get("message") or str(data.get("code")))
    out = data.get("output") or {}
    choices = out.get("choices") or []
    if not choices:
        raise RuntimeError("Пустой ответ Qwen (нет choices).")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not content:
        raise RuntimeError("Qwen не вернул content.")
    items = content if isinstance(content, list) else [content]
    for item in items:
        if isinstance(item, dict) and item.get("image"):
            return str(item["image"])
    raise RuntimeError("В ответе Qwen нет URL изображения.")


async def _post_multimodal_and_download(body: dict[str, Any]) -> bytes:
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("Не задан DASHSCOPE_API_KEY")
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    url = _multimodal_url()
    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=body, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 400:
            logger.warning("Qwen multimodal HTTP %s: %s", r.status_code, (r.text or "")[:1200])
            msg = data.get("message") if isinstance(data, dict) else None
            raise RuntimeError(msg or r.text or f"HTTP {r.status_code}")
        if isinstance(data, dict) and data.get("code"):
            raise RuntimeError(data.get("message") or str(data.get("code")))

    img_url = _extract_first_image_url(data)
    async with httpx.AsyncClient(timeout=timeout) as client:
        ir = await client.get(img_url)
        ir.raise_for_status()
        return ir.content


def _gen_parameters() -> dict[str, Any]:
    return {
        "negative_prompt": (
            "Low resolution, low quality, distorted limbs, malformed fingers, "
            "oversaturated colors, blurry text."
        ),
        "prompt_extend": True,
        "watermark": False,
        "size": QWEN_IMAGE_SIZE,
    }


def _edit_parameters(model: str) -> dict[str, Any]:
    """У qwen-image-edit (без plus/max) нет prompt_extend и кастомного size."""
    p: dict[str, Any] = {
        "n": 1,
        "negative_prompt": "low quality, blurry, distorted hands, bad anatomy",
        "watermark": False,
    }
    if model != "qwen-image-edit":
        p["prompt_extend"] = True
    if QWEN_IMAGE_EDIT_SIZE and model != "qwen-image-edit":
        p["size"] = QWEN_IMAGE_EDIT_SIZE
    return p


async def qwen_text_to_image_bytes(prompt: str) -> bytes:
    text = (prompt or "").strip()[:800]
    body: dict[str, Any] = {
        "model": QWEN_IMAGE_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": text}],
                }
            ]
        },
        "parameters": _gen_parameters(),
    }
    return await _post_multimodal_and_download(body)


async def qwen_edit_image_bytes(image_bytes: bytes, prompt: str) -> bytes:
    """Одно фото + инструкция (Qwen-Image-Edit / совместимые модели)."""
    if len(image_bytes) > _MAX_INPUT_BYTES:
        raise RuntimeError("Image exceeds 10 MB limit for Qwen input.")
    text = (prompt or "").strip()[:800]
    model = QWEN_IMAGE_EDIT_MODEL
    body: dict[str, Any] = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": _bytes_to_data_url(image_bytes)},
                        {"text": text},
                    ],
                }
            ]
        },
        "parameters": _edit_parameters(model),
    }
    return await _post_multimodal_and_download(body)
