"""Сообщения пользователю при недоступности OpenRouter / Polza (ключ, биллинг, 402)."""

from __future__ import annotations

import time
from typing import Literal

from src.config import ADMIN_IDS
from src.openrouter_image import (
    OpenRouterApiError,
    format_openrouter_image_user_error,
    is_openrouter_image_configured,
    openrouter_exc_is_provider_unavailable,
)
from src.image_upstream_errors import text_looks_like_upstream_provider_billing
from src.polza_image import (
    PolzaApiError,
    format_polza_image_user_error,
    is_polza_configured,
    is_polza_image_model,
    polza_exc_is_provider_unavailable,
)

ImageProvider = Literal["openrouter", "polza"]

IMAGE_GEN_UNAVAILABLE_USER_HTML = (
    "<b>Генерация пока не работает</b>\n\n"
    "<blockquote><i>Сервис временно недоступен. Попробуй позже.</i></blockquote>"
)

_IMAGE_GEN_MISSING_ADMIN_HTML = (
    "<b>Генерация картинок выключена.</b>\n\n"
    "<blockquote>Администратору: задай <code>OPENROUTER_API_KEY</code> и при необходимости "
    "<code>OPENROUTER_IMAGE_MODEL</code> в <code>.env</code> (см. .env.example).</blockquote>"
)

_POLZA_MISSING_ADMIN_HTML = (
    "<b>Модель GPT Image (Polza.ai) недоступна.</b>\n\n"
    "<blockquote>Администратору: задай <code>POLZAAI_API_KEY</code> в <code>.env</code> "
    "(см. .env.example).</blockquote>"
)

_PROVIDER_COOLDOWN_SEC = 600.0

_openrouter_unavailable_until: float = 0.0
_polza_unavailable_until: float = 0.0


def image_provider_for_model(model: str) -> ImageProvider:
    return "polza" if is_polza_image_model(model) else "openrouter"


def is_image_model_provider_configured(model: str) -> bool:
    if image_provider_for_model(model) == "polza":
        return is_polza_configured()
    return is_openrouter_image_configured()


def mark_provider_unavailable(provider: ImageProvider) -> None:
    global _openrouter_unavailable_until, _polza_unavailable_until
    until = time.monotonic() + _PROVIDER_COOLDOWN_SEC
    if provider == "polza":
        _polza_unavailable_until = until
    else:
        _openrouter_unavailable_until = until


def is_provider_marked_unavailable(provider: ImageProvider) -> bool:
    now = time.monotonic()
    if provider == "polza":
        return now < _polza_unavailable_until
    return now < _openrouter_unavailable_until


def image_gen_disabled_html(user_id: int, *, provider: ImageProvider | None = None) -> str:
    """Нет ключа в .env: пользователю - «не работает», админу - подсказка по .env."""
    if user_id in ADMIN_IDS:
        if provider == "polza":
            return _POLZA_MISSING_ADMIN_HTML
        return _IMAGE_GEN_MISSING_ADMIN_HTML
    return IMAGE_GEN_UNAVAILABLE_USER_HTML


def provider_blocks_image_use(model: str | None) -> bool:
    """Провайдер не настроен или недавно отдал 402 / ошибку биллинга."""
    if model:
        prov = image_provider_for_model(model)
        if not is_image_model_provider_configured(model):
            return True
        return is_provider_marked_unavailable(prov)
    if not is_openrouter_image_configured():
        return True
    return is_provider_marked_unavailable("openrouter")


def notify_provider_failure_from_exc(exc: BaseException, *, model: str | None = None) -> None:
    prov: ImageProvider | None = None
    if model:
        prov = image_provider_for_model(model)
        if prov == "polza" and polza_exc_is_provider_unavailable(exc):
            mark_provider_unavailable("polza")
        elif prov == "openrouter" and openrouter_exc_is_provider_unavailable(exc):
            mark_provider_unavailable("openrouter")
        return
    if openrouter_exc_is_provider_unavailable(exc):
        mark_provider_unavailable("openrouter")
    if polza_exc_is_provider_unavailable(exc):
        mark_provider_unavailable("polza")


def image_generation_failure_is_service_down(
    exc: BaseException | None,
    *,
    user_id: int,
    model: str | None = None,
) -> bool:
    if provider_blocks_image_use(model):
        return True
    if exc is None:
        return False
    if model:
        prov = image_provider_for_model(model)
        if prov == "polza" and polza_exc_is_provider_unavailable(exc):
            return True
        if prov == "openrouter" and openrouter_exc_is_provider_unavailable(exc):
            return True
        return False
    if openrouter_exc_is_provider_unavailable(exc) or polza_exc_is_provider_unavailable(exc):
        return True
    if user_id in ADMIN_IDS:
        return False
    text = str(exc).lower()
    if isinstance(exc, (OpenRouterApiError, PolzaApiError)) and exc.http_status in (401, 402):
        return True
    if "api_key" in text or "не задан" in text:
        return True
    return False


def format_image_generation_failure_html(
    exc: BaseException | None,
    *,
    user_id: int,
    model: str | None = None,
) -> str:
    if image_generation_failure_is_service_down(exc, user_id=user_id, model=model):
        if exc is not None:
            notify_provider_failure_from_exc(exc, model=model)
        if model and not is_image_model_provider_configured(model):
            return image_gen_disabled_html(user_id, provider=image_provider_for_model(model))
        return IMAGE_GEN_UNAVAILABLE_USER_HTML

    if exc is None:
        return ""

    plain = (
        format_polza_image_user_error(exc)
        if model and is_polza_image_model(model)
        else format_openrouter_image_user_error(exc)
    )
    if user_id not in ADMIN_IDS and text_looks_like_upstream_provider_billing(plain):
        return IMAGE_GEN_UNAVAILABLE_USER_HTML
    if user_id in ADMIN_IDS and (
        "администратору" in plain.lower() or "admin" in plain.lower() or ".env" in plain.lower()
    ):
        return f"<b>Генерация не завершена</b>\n\n<blockquote>{plain}</blockquote>"
    from src.formatting import esc

    return (
        "<b>Генерация не завершена</b>\n\n"
        f"<blockquote><i>{esc(plain)}</i></blockquote>"
    )
