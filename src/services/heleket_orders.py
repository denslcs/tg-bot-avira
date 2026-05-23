"""Заказы Heleket: счёт, проверка оплаты, начисление в БД."""

from __future__ import annotations

import logging
import time

from src.database import (
    create_heleket_payment_order,
    get_heleket_payment_order,
    list_pending_heleket_orders_for_user,
    mark_heleket_order_benefits_applied,
    mark_heleket_payment_order_declined,
    mark_heleket_payment_order_paid,
    release_star_payment_claim,
    star_payment_claim_belongs_to,
    try_claim_star_payment,
    try_lock_heleket_order_for_finalize,
    unlock_heleket_order_finalize,
)
from src.config import HELEKET_PAYMENT_CURRENCY
from src.services.heleket_client import (
    HeleketApiError,
    HeleketClient,
    heleket_configured,
    heleket_payment_status_declined,
    heleket_payment_status_paid,
)
from src.services.payments_apply import PlanPurchaseApplyResult, apply_plan_purchase_from_stars
from src.services.wata_orders import (
    WataFinalizeResult,
    WataFinalizeStatus,
    build_wata_order_id,
    parse_wata_order_id,
)
from src.subscription_catalog import BONUS_PACKS

logger = logging.getLogger(__name__)

_HELEKET_CHECK_AT: dict[str, float] = {}
_HELEKET_CHECK_MIN_SEC = 31.0


def _heleket_check_cooldown_sec(order_id: str) -> float | None:
    last = _HELEKET_CHECK_AT.get(order_id)
    if last is None:
        return None
    wait = _HELEKET_CHECK_MIN_SEC - (time.time() - last)
    return wait if wait > 0 else None


def _heleket_check_mark(order_id: str) -> None:
    _HELEKET_CHECK_AT[order_id] = time.time()


def build_heleket_order_id(*, user_id: int, kind: str, item_id: str) -> str:
    return build_wata_order_id(user_id=user_id, kind=kind, item_id=item_id)


def parse_heleket_order_id(order_id: str) -> tuple[int, str, str] | None:
    return parse_wata_order_id(order_id)


async def create_heleket_checkout(
    *,
    user_id: int,
    kind: str,
    item_id: str,
    amount_rub: int,
    invoice_amount: str,
    invoice_currency: str | None = None,
    success_redirect_url: str | None = None,
    order_id: str | None = None,
) -> tuple[str, str]:
    if not heleket_configured():
        raise HeleketApiError("Heleket не настроен")
    currency = (invoice_currency or HELEKET_PAYMENT_CURRENCY).strip().upper() or "USD"
    order_id = (order_id or "").strip() or build_heleket_order_id(
        user_id=user_id, kind=kind, item_id=item_id
    )
    client = HeleketClient(currency=currency)
    try:
        invoice = await client.create_payment(
            amount=invoice_amount,
            order_id=order_id,
            url_success=success_redirect_url,
            url_return=success_redirect_url,
        )
    except HeleketApiError:
        if not success_redirect_url:
            raise
        logger.warning(
            "heleket create with redirect failed, retry without urls order=%s",
            order_id,
        )
        invoice = await client.create_payment(
            amount=invoice_amount,
            order_id=order_id,
        )
    url = str(invoice.get("url") or "").strip()
    if not url:
        raise HeleketApiError("Heleket не вернула url оплаты")
    invoice_uuid = str(invoice.get("uuid") or "").strip() or None
    await create_heleket_payment_order(
        order_id=order_id,
        user_id=user_id,
        kind=kind,
        item_id=item_id,
        amount_rub=int(amount_rub),
        invoice_amount=invoice_amount,
        invoice_currency=currency,
        heleket_invoice_uuid=invoice_uuid,
    )
    return order_id, url


async def finalize_heleket_order(
    order_id: str,
    *,
    expected_user_id: int,
    can_buy_plan,
) -> WataFinalizeResult:
    order = await get_heleket_payment_order(order_id)
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

    cooldown = _heleket_check_cooldown_sec(order_id)
    if cooldown is not None:
        sec = int(cooldown) + 1
        return WataFinalizeResult(
            status=WataFinalizeStatus.ERROR,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            error_message=(
                f"Слишком частые проверки. Подожди ещё {sec} с и нажми «Проверить оплату» снова."
            ),
        )

    try:
        client = HeleketClient()
        info = await client.get_payment_info(order_id=order_id)
        _heleket_check_mark(order_id)
    except HeleketApiError as exc:
        return WataFinalizeResult(
            status=WataFinalizeStatus.ERROR,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
            error_message=str(exc),
        )

    payment_status = str(info.get("payment_status") or info.get("status") or "").strip()
    if heleket_payment_status_declined(payment_status):
        await mark_heleket_payment_order_declined(order_id)
        return WataFinalizeResult(
            status=WataFinalizeStatus.DECLINED,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
        )
    if not heleket_payment_status_paid(payment_status):
        return WataFinalizeResult(
            status=WataFinalizeStatus.PENDING,
            order_id=order_id,
            kind=kind,
            item_id=item_id,
        )

    txn_id = str(info.get("txid") or info.get("uuid") or order_id).strip()
    invoice_uuid = str(info.get("uuid") or order.get("heleket_invoice_uuid") or "").strip()

    try:
        expected_amount = float(str(order.get("invoice_amount") or order["amount_rub"]))
        paid_amount = float(info.get("amount") or info.get("payment_amount") or 0)
        tolerance = max(0.05, expected_amount * 0.05)
        if paid_amount > 0 and abs(paid_amount - expected_amount) > tolerance:
            logger.warning(
                "heleket amount mismatch order=%s expected=%s got=%s",
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
    except (TypeError, ValueError):
        pass

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

    claim_id = f"heleket:{txn_id}"
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

    lock_state = await try_lock_heleket_order_for_finalize(order_id)
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
        fresh = await get_heleket_payment_order(order_id)
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
                    await unlock_heleket_order_finalize(order_id)
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
                    await unlock_heleket_order_finalize(order_id)
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
                    details=f"pack {item_id} heleket",
                )
                if not credited:
                    await unlock_heleket_order_finalize(order_id)
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
            await mark_heleket_order_benefits_applied(order_id)

        marked = await mark_heleket_payment_order_paid(
            order_id,
            heleket_txid=txn_id,
            heleket_invoice_uuid=invoice_uuid or None,
        )
        if not marked:
            await unlock_heleket_order_finalize(order_id)
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
        await unlock_heleket_order_finalize(order_id)
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


async def finalize_latest_pending_heleket_for_user(
    user_id: int,
    *,
    can_buy_plan,
) -> WataFinalizeResult | None:
    rows = await list_pending_heleket_orders_for_user(user_id)
    if not rows:
        return None
    last: WataFinalizeResult | None = None
    for row in rows:
        result = await finalize_heleket_order(
            str(row["order_id"]),
            expected_user_id=user_id,
            can_buy_plan=can_buy_plan,
        )
        if result.status == WataFinalizeStatus.PAID:
            return result
        last = result
    return last


def parse_heleket_start_payload(start_payload: str | None) -> str | None:
    raw = (start_payload or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in ("heleket_ok", "hk_ok"):
        return None
    for prefix in ("heleket_", "hk_"):
        if lower.startswith(prefix) and len(raw) > len(prefix):
            order_id = raw[len(prefix) :]
            if order_id.startswith("tg"):
                return order_id
    return None
