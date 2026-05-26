"""Определение ошибок биллинга на стороне Polza / OpenRouter (не кредиты бота)."""

from __future__ import annotations

# Сообщение пользователю вместо «пополните Polza.ai» и т.п.
UPSTREAM_BILLING_USER_MESSAGE = "Генерация пока не работает. Попробуй позже."

_UPSTREAM_BILLING_HINTS = (
    "402",
    "payment required",
    "payment",
    "billing",
    "insufficient credits",
    "insufficient balance",
    "insufficient funds",
    "add credits",
    "out of credits",
    "quota exceeded",
    "requires more credits",
    "credit limit",
    "not enough",
    "funds",
    "wallet",
    "deposit",
    "top up",
    "top-up",
    "topup",
    "recharge",
    "polza.ai",
    "polza",
    "openrouter.ai",
    "openrouter",
    "пополн",
    "пополни",
    "баланс",
    "квот",
    "оплат",
    "недостаточно средств",
    "недостаточно баланса",
)


def text_looks_like_upstream_provider_billing(text: str) -> bool:
    """Текст от API провайдера про нехватку средств у нас на аккаунте, не у пользователя бота."""
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(h in t for h in _UPSTREAM_BILLING_HINTS)
