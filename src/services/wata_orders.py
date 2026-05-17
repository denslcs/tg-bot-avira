"""Заказы Wata: создание ссылки, проверка оплаты, начисление в БД."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.database import (
    create_wata_payment_order,
    get_wata_payment_order,
    list_pending_wata_orders_for_user,
    mark_wata_order_benefits_applied,
    mark_wata_payment_order_declined,
    mark_wata_payment_order_paid,
    release_star_payment_claim,
    star_payment_claim_belongs_to,
    try_claim_star_payment,
    try_lock_wata_order_for_finalize,
    unlock_wata_order_finalize,
)
from src.services.payments_apply import PlanPurchaseApplyResult, apply_plan_purchase_from_stars
from src.services.wata_client import WataApiError, WataClient, wata_configured
from src.subscription_catalog import BONUS_PACKS, PLANS

logger = logging.getLogger(__name__)


class WataFinalizeStatus(str, Enum):
    PAID = "paid"
    ALREADY_PAID = "already_paid"
    PENDING = "pending"
    DECLINED = "declined"
    NOT_FOUND = "not_found"
    WRONG_USER = "wrong_user"
    AMOUNT_MISMATCH = "amount_mismatch"
    APPLY_FAILED = "apply_failed"
    NOT_ALLOWED = "not_allowed"
    ERROR = "error"


@dataclass
class WataFinalizeResult:
    status: WataFinalizeStatus
    order_id: str
    kind: str = ""
    item_id: str = ""
    plan_apply: PlanPurchaseApplyResult | None = None
    pack_credits: int = 0
    transaction_id: str = ""
    error_message: str = ""


def build_wata_order_id(*, user_id: int, kind: str, item_id: str) -> str:
    nonce = secrets.token_hex(4)
    return f"tg{int(user_id)}_{kind}_{item_id}_{nonce}"


def parse_wata_order_id(order_id: str) -> tuple[int, str, str] | None:
    raw = (order_id or "").strip()
    if not raw.startswith("tg") or raw.count("_") < 3:
        return None
    body = raw[2:]
    parts = body.split("_")
    if len(parts) < 4:
        return None
    try:
        user_id = int(parts[0])
    except ValueError:
        return None
    kind = parts[1]
    item_id = parts[2]
    if kind == "plan" and item_id not in PLANS:
        return None
    if kind == "pack" and item_id not in BONUS_PACKS:
        return None
    if kind not in ("plan", "pack"):
        return None
    return user_id, kind, item_id


async def create_wata_checkout(
    *,
    user_id: int,
    kind: str,
    item_id: str,
    amount_rub: int,
    description: str,
    success_redirect_url: str | None = None,
    order_id: str | None = None,
) -> tuple[str, str]:
    """
    Создаёт заказ в БД и одноразовую ссылку Wata.
    Возвращает (order_id, payment_url).
    """
    if not wata_configured():
        raise WataApiError("WATA_ACCESS_TOKEN не настроен")
    order_id = (order_id or "").strip() or build_wata_order_id(
        user_id=user_id, kind=kind, item_id=item_id
    )
    client = WataClient()
    link = await client.create_payment_link(
        amount_rub=float(amount_rub),
        order_id=order_id,
        description=description,
        link_type="OneTime",
        success_redirect_url=success_redirect_url,
    )
    url = str(link.get("url") or "").strip()
    if not url:
        raise WataApiError("Wata не вернула url платёжной ссылки")
    link_id = str(link.get("id") or "").strip() or None
    await create_wata_payment_order(
        order_id=order_id,
        user_id=user_id,
        kind=kind,
        item_id=item_id,
        amount_rub=int(amount_rub),
        wata_link_id=link_id,
    )
    return order_id, url


async def finalize_wata_order(
    order_id: str,
    *,
    expected_user_id: int,
    can_buy_plan,
) -> WataFinalizeResult:
    """Проверяет оплату в Wata и применяет тариф/пакет (идемпотентно)."""
    order = await get_wata_payment_order(order_id)
    if order is None:
        return WataFinalizeResult(status=WataFinalizeStatus.NOT_FOUND, order_id=order_id)
    if int(order["user_id"]) != int(expected_user_id):
        return WataFinalizeResult(
            status=WataFinalizeStatus.WRONG_USER,
            order_id=order_id,
            kind=str(order["kind"]),
            item_id=str(order["item_id"]),
        )
    kind = str(order["kind"])
    item_id = str(order["item_id"])
    if order.get("status") == "paid":
        return WataFinalizeResult(
            status=WataFinalizeStatus.ALREADY_PAID,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
        )

    try:
        client = WataClient()
        paid_txns = await client.find_paid_transactions(order_id)
        declined_txns = await client.find_declined_transactions(order_id)
    except WataApiError as exc:
        return WataFinalizeResult(
            status=WataFinalizeStatus.ERROR,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            error_message=str(exc),
        )

    if not paid_txns and declined_txns:
        await mark_wata_payment_order_declined(order_id)
        return WataFinalizeResult(
            status=WataFinalizeStatus.DECLINED,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
        )

    if not paid_txns:
        return WataFinalizeResult(
            status=WataFinalizeStatus.PENDING,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
        )

    txns = paid_txns

    txn = txns[0]
    txn_id = str(txn.get("id") or "").strip()
    if not txn_id:
        return WataFinalizeResult(
            status=WataFinalizeStatus.ERROR,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            error_message="Нет id транзакции в ответе Wata",
        )

    expected_amount = float(order["amount_rub"])
    paid_amount = float(txn.get("amount") or 0)
    if abs(paid_amount - expected_amount) > 0.02:
        logger.warning(
            "wata amount mismatch order=%s expected=%s got=%s",
            order_id,
            expected_amount,
            paid_amount,
        )
        return WataFinalizeResult(
            status=WataFinalizeStatus.AMOUNT_MISMATCH,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            transaction_id=txn_id,
        )

    if kind == "plan":
        allowed, reason = await can_buy_plan(expected_user_id, item_id)
        if not allowed:
            return WataFinalizeResult(
                status=WataFinalizeStatus.NOT_ALLOWED,
                order_id=order_id,
                kind=kind,
                item_id=item_id,
                error_message=reason or "Покупка недоступна",
                transaction_id=txn_id,
            )

    claim_id = f"wata:{txn_id}"
    claimed = await try_claim_star_payment(claim_id, expected_user_id)
    owns_claim = claimed or await star_payment_claim_belongs_to(claim_id, expected_user_id)
    if not owns_claim:
        return WataFinalizeResult(
            status=WataFinalizeStatus.PENDING,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            transaction_id=txn_id,
        )

    lock_state = await try_lock_wata_order_for_finalize(order_id)
    if lock_state == "paid":
        return WataFinalizeResult(
            status=WataFinalizeStatus.ALREADY_PAID,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            transaction_id=txn_id,
        )
    if lock_state == "processing":
        if not await star_payment_claim_belongs_to(claim_id, expected_user_id):
            return WataFinalizeResult(
                status=WataFinalizeStatus.PENDING,
                order_id=order_id,
                kind=kind,
                item_id=item_id,
                transaction_id=txn_id,
            )
        fresh = await get_wata_payment_order(order_id)
        skip_apply = bool(fresh and fresh.get("benefits_applied"))
    elif lock_state == "locked":
        skip_apply = False
    else:
        return WataFinalizeResult(
            status=WataFinalizeStatus.PENDING,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            transaction_id=txn_id,
        )

    apply_result: PlanPurchaseApplyResult | None = None
    pack_credits = 0

    try:
        if not skip_apply:
            if kind == "plan":
                apply_result = await apply_plan_purchase_from_stars(
                    user_id=expected_user_id,
                    item_id=item_id,
                )
                if apply_result is None:
                    await unlock_wata_order_finalize(order_id)
                    if claimed:
                        await release_star_payment_claim(claim_id)
                    return WataFinalizeResult(
                        status=WataFinalizeStatus.APPLY_FAILED,
                        order_id=order_id,
                        kind=kind,
                        item_id=item_id,
                        transaction_id=txn_id,
                    )
            elif kind == "pack":
                from src.database import add_credits_with_reason

                pack = BONUS_PACKS.get(item_id)
                if not pack:
                    await unlock_wata_order_finalize(order_id)
                    if claimed:
                        await release_star_payment_claim(claim_id)
                    return WataFinalizeResult(
                        status=WataFinalizeStatus.ERROR,
                        order_id=order_id,
                        kind=kind,
                        item_id=item_id,
                        error_message="Пакет не найден",
                    )
                credited = await add_credits_with_reason(
                    expected_user_id,
                    pack.credits,
                    source="bonus_pack",
                    details=f"pack {item_id} wata",
                )
                if not credited:
                    await unlock_wata_order_finalize(order_id)
                    if claimed:
                        await release_star_payment_claim(claim_id)
                    return WataFinalizeResult(
                        status=WataFinalizeStatus.APPLY_FAILED,
                        order_id=order_id,
                        kind=kind,
                        item_id=item_id,
                        transaction_id=txn_id,
                    )
                pack_credits = pack.credits
            await mark_wata_order_benefits_applied(order_id)

        marked = await mark_wata_payment_order_paid(
            order_id,
            wata_transaction_id=txn_id,
            wata_link_id=str(order.get("wata_link_id") or "") or None,
        )
        if not marked:
            await unlock_wata_order_finalize(order_id)
            if claimed:
                await release_star_payment_claim(claim_id)
            return WataFinalizeResult(
                status=WataFinalizeStatus.APPLY_FAILED,
                order_id=order_id,
                kind=kind,
                item_id=item_id,
                transaction_id=txn_id,
            )
    except Exception:
        await unlock_wata_order_finalize(order_id)
        if claimed:
            await release_star_payment_claim(claim_id)
        raise

    if kind == "plan":
        if apply_result is None and skip_apply:
            return WataFinalizeResult(
                status=WataFinalizeStatus.ALREADY_PAID,
                order_id=order_id,
                kind=kind,
                item_id=item_id,
                transaction_id=txn_id,
            )
        return WataFinalizeResult(
            status=WataFinalizeStatus.PAID,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            plan_apply=apply_result,
            transaction_id=txn_id,
        )

    if kind == "pack":
        if skip_apply and pack_credits == 0:
            pack = BONUS_PACKS.get(item_id)
            pack_credits = int(pack.credits) if pack else 0
        return WataFinalizeResult(
            status=WataFinalizeStatus.PAID,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            pack_credits=pack_credits,
            transaction_id=txn_id,
        )

    return WataFinalizeResult(
        status=WataFinalizeStatus.ERROR,
        order_id=order_id,
        error_message="Неизвестный тип заказа",
    )


async def finalize_latest_pending_wata_for_user(
    user_id: int,
    *,
    can_buy_plan,
) -> WataFinalizeResult | None:
    """Незавершённые заказы Wata после редиректа (wata_ok — все pending по очереди)."""
    rows = await list_pending_wata_orders_for_user(user_id)
    if not rows:
        return None
    last: WataFinalizeResult | None = None
    for row in rows:
        result = await finalize_wata_order(
            str(row["order_id"]),
            expected_user_id=user_id,
            can_buy_plan=can_buy_plan,
        )
        if result.status == WataFinalizeStatus.PAID:
            return result
        last = result
    return last


def parse_wata_start_payload(start_payload: str | None) -> str | None:
    """
    Диплинк после оплаты: wata_<order_id> или legacy wata_ok (None = все pending).
    """
    raw = (start_payload or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower == "wata_ok":
        return None
    if lower.startswith("wata_") and len(raw) > 5:
        order_id = raw[5:]
        if order_id.startswith("tg"):
            return order_id
    return None
