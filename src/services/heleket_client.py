"""HTTP-клиент Heleket (создание счёта и проверка статуса)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

import httpx

from src.config import (
    HELEKET_API_BASE,
    HELEKET_MERCHANT_UUID,
    HELEKET_PAYMENT_API_KEY,
    HELEKET_PAYMENT_CURRENCY,
)

logger = logging.getLogger(__name__)

_HELEKET_PAID = frozenset({"paid", "paid_over"})
_HELEKET_DECLINED = frozenset({"cancel", "fail", "system_fail", "refund_fail", "locked"})


class HeleketApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def heleket_configured() -> bool:
    return bool(HELEKET_MERCHANT_UUID and HELEKET_PAYMENT_API_KEY)


def heleket_payment_status_paid(status: str | None) -> bool:
    return str(status or "").strip().lower() in _HELEKET_PAID


def heleket_payment_status_declined(status: str | None) -> bool:
    return str(status or "").strip().lower() in _HELEKET_DECLINED


def _make_sign(body: bytes, api_key: str) -> str:
    encoded = base64.b64encode(body).decode("ascii")
    return hashlib.md5((encoded + api_key).encode("utf-8")).hexdigest()


def _unwrap_result(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if data.get("state") == 0 and isinstance(data.get("result"), dict):
        return data["result"]
    if str(data.get("url") or "").strip():
        return data
    return data if isinstance(data, dict) else {}


class HeleketClient:
    def __init__(
        self,
        *,
        merchant_uuid: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        currency: str | None = None,
    ) -> None:
        self._merchant = (merchant_uuid or HELEKET_MERCHANT_UUID).strip()
        self._api_key = (api_key or HELEKET_PAYMENT_API_KEY).strip()
        self._base = (base_url or HELEKET_API_BASE).rstrip("/")
        self._currency = (currency or HELEKET_PAYMENT_CURRENCY).strip().upper() or "USD"
        if not self._merchant or not self._api_key:
            raise HeleketApiError("HELEKET_MERCHANT_UUID или HELEKET_PAYMENT_API_KEY не заданы")

    async def create_payment(
        self,
        *,
        amount: str,
        order_id: str,
        url_success: str | None = None,
        url_return: str | None = None,
        lifetime: int = 3600,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "amount": amount,
            "currency": self._currency,
            "order_id": order_id,
            "is_payment_multiple": False,
            "lifetime": max(300, min(43200, int(lifetime))),
        }
        if url_success:
            payload["url_success"] = url_success
        if url_return:
            payload["url_return"] = url_return
        return await self._post("/v1/payment", payload)

    async def get_payment_info(self, *, order_id: str) -> dict[str, Any]:
        return await self._post("/v1/payment/info", {"order_id": order_id})

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sign = _make_sign(body, self._api_key)
        headers = {
            "merchant": self._merchant,
            "sign": sign,
            "Content-Type": "application/json",
        }
        url = f"{self._base}{path}"
        timeout = httpx.Timeout(60.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                logger.exception("heleket http error POST %s", path)
                raise HeleketApiError(f"Сеть Heleket: {exc}") from exc
        if resp.status_code >= 400:
            raise HeleketApiError(
                f"Heleket HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise HeleketApiError("Heleket вернула не-JSON") from exc
        if isinstance(data, dict) and data.get("state") not in (0, None):
            errors = data.get("errors")
            if isinstance(errors, dict):
                msg = json.dumps(errors, ensure_ascii=False)[:400]
            else:
                msg = str(data.get("message") or data)[:400]
            raise HeleketApiError(msg or "Ошибка Heleket")
        result = _unwrap_result(data)
        if not result:
            raise HeleketApiError("Пустой ответ Heleket")
        return result
