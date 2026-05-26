"""Сообщения при ошибках биллинга Polza / OpenRouter (не кредиты бота)."""

from __future__ import annotations

import src.image_provider_user as ipu
from src.config import POLZA_IMAGE_MODEL_GPT_IMAGE_15
from src.image_upstream_errors import UPSTREAM_BILLING_USER_MESSAGE, text_looks_like_upstream_provider_billing
from src.openrouter_image import OpenRouterApiError, format_openrouter_image_user_error
from src.polza_image import PolzaApiError, format_polza_image_user_error


def test_upstream_billing_text_polza_topup() -> None:
    assert text_looks_like_upstream_provider_billing(
        "Пополните баланс на polza.ai для продолжения генерации"
    )


def test_polza_api_error_not_shown_raw_to_user() -> None:
    exc = PolzaApiError("Please top up your balance at https://polza.ai/billing", http_status=400)
    msg = format_polza_image_user_error(exc)
    assert msg == UPSTREAM_BILLING_USER_MESSAGE
    assert "polza.ai" not in msg.lower()
    assert "top up" not in msg.lower()
    assert "billing" not in msg.lower()


def test_openrouter_insufficient_credits_sanitized() -> None:
    exc = OpenRouterApiError("Insufficient credits: add credits at openrouter.ai", http_status=402)
    msg = format_openrouter_image_user_error(exc)
    assert msg == UPSTREAM_BILLING_USER_MESSAGE
    assert "openrouter" not in msg.lower()


def test_generation_failure_html_hides_upstream_billing_for_user(monkeypatch) -> None:
    monkeypatch.setattr(ipu, "provider_blocks_image_use", lambda _model=None: False)
    exc = PolzaApiError("Пополните счёт на Polza.ai", http_status=402)
    html = ipu.format_image_generation_failure_html(
        exc, user_id=999, model=POLZA_IMAGE_MODEL_GPT_IMAGE_15
    )
    assert "Polza.ai" not in html
    assert "Пополните" not in html
    assert "не работает" in html.lower() or "недоступ" in html.lower()
