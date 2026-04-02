"""Wan 2.7 Image / Editing через DashScope multimodal-generation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
_MAX_CACHE_ITEMS = 128
_text_cache: dict[str, tuple[str, bytes]] = {}
_edit_cache: dict[str, tuple[str, bytes]] = {}
_TEXT2IMG_URL = f"{QWEN_DASHSCOPE_API_V1_BASE}/services/aigc/text2image/image-synthesis"
_IMG2IMG_URL = f"{QWEN_DASHSCOPE_API_V1_BASE}/services/aigc/image2image/image-synthesis"
_TASK_URL = f"{QWEN_DASHSCOPE_API_V1_BASE}/tasks/{{task_id}}"


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
        return "Сервис Wan перегружен или лимит запросов. Попробуй через минуту."
    if "quota" in text or "balance" in text or "insufficient" in text:
        return "Квота или баланс Wan в Model Studio исчерпаны. Проверь консоль Alibaba Cloud."
    if "10 mb" in text or "file size" in text or "too large" in text:
        return "Фото слишком большое для Wan (лимит ~10 МБ). Отправь сжатое изображение."
    return "Не удалось обработать картинку через Wan. Попробуй позже или смени формулировку."


def _multimodal_url() -> str:
    return f"{QWEN_DASHSCOPE_API_V1_BASE}/services/aigc/multimodal-generation/generation"


def _is_wan_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("wan2.7")


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


async def _post_multimodal_and_download(body: dict[str, Any]) -> tuple[str, bytes]:
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
        return img_url, ir.content


async def _upload_to_telegraph(image_bytes: bytes) -> str:
    timeout = httpx.Timeout(60.0, connect=20.0)
    files = {"file": ("image.jpg", image_bytes, _guess_mime(image_bytes))}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post("https://telegra.ph/upload", files=files)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list) or not data or "src" not in data[0]:
        raise RuntimeError("Не удалось загрузить изображение для WAN-edit.")
    return f"https://telegra.ph{data[0]['src']}"


async def _wan_submit_task(endpoint: str, payload: dict[str, Any]) -> str:
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("Не задан DASHSCOPE_API_KEY")
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    timeout = httpx.Timeout(120.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(endpoint, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    task_id = ((data.get("output") or {}).get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("WAN не вернул task_id.")
    return task_id


async def _wan_poll_task_result_url(task_id: str, attempts: int = 45, delay_s: float = 2.0) -> str:
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    timeout = httpx.Timeout(120.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(attempts):
            await asyncio.sleep(delay_s)
            r = await client.get(_TASK_URL.format(task_id=task_id), headers=headers)
            r.raise_for_status()
            data = r.json()
            out = data.get("output") or {}
            status = str(out.get("task_status") or "").upper()
            if status == "SUCCEEDED":
                results = out.get("results") or []
                if results and isinstance(results[0], dict) and results[0].get("url"):
                    return str(results[0]["url"])
                raise RuntimeError("WAN task SUCCEEDED, но URL результата пуст.")
            if status in ("FAILED", "CANCELED"):
                raise RuntimeError(out.get("message") or f"WAN task {status}")
    raise TimeoutError("Превышено время ожидания WAN генерации.")


async def _download_image_bytes(image_url: str) -> bytes:
    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        ir = await client.get(image_url)
        ir.raise_for_status()
        return ir.content


def _gen_parameters() -> dict[str, Any]:
    return {
        "n": 1,
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


def _normalize_prompt(prompt: str) -> str:
    return " ".join((prompt or "").strip().lower().split())


def _cache_put(cache: dict[str, tuple[str, bytes]], key: str, value: tuple[str, bytes]) -> None:
    cache[key] = value
    if len(cache) > _MAX_CACHE_ITEMS:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


def _is_retryable_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "failed",
        "canceled",
        "timeout",
        "timed out",
        "tempor",
        "429",
        "502",
        "503",
        "504",
        "rate limit",
        "connection reset",
    )
    return any(m in text for m in markers)


async def _post_multimodal_and_download_with_retry(
    body: dict[str, Any],
    *,
    max_attempts: int = 4,
) -> tuple[str, bytes]:
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await _post_multimodal_and_download(body)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts - 1 or not _is_retryable_error(exc):
                raise
            delay = 1.0 * (2**attempt)
            logger.warning(
                "Wan task retry %s/%s in %.1fs: %s",
                attempt + 1,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("Wan task failed without exception")


async def _wan_generate_with_retry(endpoint: str, payload: dict[str, Any], max_attempts: int = 4) -> tuple[str, bytes]:
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            task_id = await _wan_submit_task(endpoint, payload)
            img_url = await _wan_poll_task_result_url(task_id)
            data = await _download_image_bytes(img_url)
            return img_url, data
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts - 1 or not _is_retryable_error(exc):
                raise
            delay = 1.0 * (2**attempt)
            logger.warning(
                "WAN async retry %s/%s in %.1fs: %s",
                attempt + 1,
                max_attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("WAN async task failed without exception")


async def qwen_text_to_image_bytes(prompt: str) -> bytes:
    text = (prompt or "").strip()[:800]
    cache_key = f"{QWEN_IMAGE_MODEL}|{QWEN_IMAGE_SIZE}|{_normalize_prompt(text)}"
    cached = _text_cache.get(cache_key)
    if cached:
        return cached[1]
    if _is_wan_model(QWEN_IMAGE_MODEL):
        payload = {
            "model": QWEN_IMAGE_MODEL,
            "input": {"prompt": text},
            "parameters": {"size": QWEN_IMAGE_SIZE, "n": 1},
        }
        img_url, data = await _wan_generate_with_retry(_TEXT2IMG_URL, payload)
    else:
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
        img_url, data = await _post_multimodal_and_download_with_retry(body)
    _cache_put(_text_cache, cache_key, (img_url, data))
    return data


async def qwen_edit_image_bytes(image_bytes: bytes, prompt: str) -> bytes:
    """Одно фото + инструкция (Qwen-Image-Edit / совместимые модели)."""
    if len(image_bytes) > _MAX_INPUT_BYTES:
        raise RuntimeError("Image exceeds 10 MB limit for Qwen input.")
    text = (prompt or "").strip()[:800]
    model = QWEN_IMAGE_EDIT_MODEL
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    cache_key = f"{model}|{QWEN_IMAGE_EDIT_SIZE}|{img_hash}|{_normalize_prompt(text)}"
    cached = _edit_cache.get(cache_key)
    if cached:
        return cached[1]
    if _is_wan_model(model):
        public_url = await _upload_to_telegraph(image_bytes)
        params: dict[str, Any] = {"n": 1}
        if QWEN_IMAGE_EDIT_SIZE:
            params["size"] = QWEN_IMAGE_EDIT_SIZE
        payload = {
            "model": model,
            "input": {"prompt": text, "image_url": public_url},
            "parameters": params,
        }
        img_url, data = await _wan_generate_with_retry(_IMG2IMG_URL, payload)
    else:
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
        img_url, data = await _post_multimodal_and_download_with_retry(body)
    _cache_put(_edit_cache, cache_key, (img_url, data))
    return data
