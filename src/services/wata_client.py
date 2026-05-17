"""HTTP-клиент Wata H2H (платёжные ссылки и поиск транзакций)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import WATA_ACCESS_TOKEN, WATA_API_BASE

logger = logging.getLogger(__name__)


class WataApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def wata_configured() -> bool:
    return bool(WATA_ACCESS_TOKEN)


def _unwrap_link_response(data: dict[str, Any]) -> dict[str, Any]:
    """Нормализует ответ POST /links (плоский JSON или обёртка data)."""
    if str(data.get("url") or "").strip():
        return data
    inner = data.get("data")
    if isinstance(inner, dict):
        return inner
    return data


class WataClient:
    def __init__(self, *, token: str | None = None, base_url: str | None = None) -> None:
        self._token = (token or WATA_ACCESS_TOKEN).strip()
        self._base = (base_url or WATA_API_BASE).rstrip("/")
        if not self._token:
            raise WataApiError("WATA_ACCESS_TOKEN не задан")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_payment_link(
        self,
        *,
        amount_rub: float,
        order_id: str,
        description: str,
        link_type: str = "OneTime",
        success_redirect_url: str | None = None,
        fail_redirect_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": link_type,
            "amount": round(float(amount_rub), 2),
            "currency": "RUB",
            "description": description[:500] if description else "",
            "orderId": order_id,
        }
        if success_redirect_url:
            payload["successRedirectUrl"] = success_redirect_url
        if fail_redirect_url:
            payload["failRedirectUrl"] = fail_redirect_url
        return await self._request("POST", "/links", json_body=payload)

    async def find_transactions_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """
        Все транзакции по orderId одним запросом (Wata: не чаще 1 GET / 30 с на orderId).
        """
        try:
            data = await self._request(
                "GET",
                "/v2/transactions/",
                params={
                    "orderId": order_id,
                    "maxResultCount": 20,
                },
            )
        except WataApiError as exc:
            if exc.status_code == 404:
                return []
            raise
        return _extract_transaction_items(data)

    async def find_paid_transactions(self, order_id: str) -> list[dict[str, Any]]:
        txns = await self.find_transactions_for_order(order_id)
        return [t for t in txns if str(t.get("status") or "").strip() == "Paid"]

    async def find_declined_transactions(self, order_id: str) -> list[dict[str, Any]]:
        txns = await self.find_transactions_for_order(order_id)
        return [t for t in txns if str(t.get("status") or "").strip() == "Declined"]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        timeout = httpx.Timeout(60.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    params=params,
                )
            except httpx.HTTPError as exc:
                logger.exception("wata http error %s %s", method, path)
                raise WataApiError(f"Сеть Wata: {exc}") from exc
        if resp.status_code == 401:
            raise WataApiError("Токен Wata недействителен (401)", status_code=401)
        if resp.status_code == 429:
            retry_raw = (resp.headers.get("Retry-After") or "").strip()
            try:
                retry_sec = max(1, int(float(retry_raw))) if retry_raw else 30
            except ValueError:
                retry_sec = 30
            raise WataApiError(
                f"Слишком частые проверки. Подожди {retry_sec} с и нажми «Проверить оплату» снова.",
                status_code=429,
            )
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise WataApiError(
                f"Wata HTTP {resp.status_code}: {body}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise WataApiError("Wata вернула не-JSON") from exc
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise WataApiError(msg or "Ошибка Wata")
        if isinstance(data, dict):
            return _unwrap_link_response(data)
        return {}


def _extract_transaction_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("items")
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    if data.get("id") and data.get("status"):
        return [data]
    return []
