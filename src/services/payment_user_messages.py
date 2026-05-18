"""Тексты для пользователя после оплаты (Stars, Wata)."""

from __future__ import annotations

from src.formatting import esc, format_subscription_ends_at, plan_subscription_title_html
from src.services.payments_apply import PlanPurchaseApplyResult
from src.subscription_catalog import BONUS_PACKS, PLANS


def payment_support_line_html(*, support_username: str) -> str:
    if support_username:
        return f"Напиши в @{esc(support_username.lstrip('@'))} или через <code>/support</code>."
    return "Напиши в <code>/support</code>."


def plan_purchase_success_html(
    item_id: str,
    apply_result: PlanPurchaseApplyResult,
    *,
    support_username: str = "",
) -> str:
    _ = support_username
    pid = (item_id or "").strip().lower()
    period_days = apply_result.period_days
    if pid in PLANS and PLANS[pid].period_days:
        period_days = PLANS[pid].period_days
    end_h = format_subscription_ends_at(apply_result.new_end)
    title = plan_subscription_title_html(item_id)

    if apply_result.had_active_renewal and pid != "starter":
        release_h = ""
        if apply_result.renewal_release_at:
            release_h = format_subscription_ends_at(apply_result.renewal_release_at)
        credits_when = (
            f"<b>{esc(release_h)}</b> (UTC)"
            if release_h
            else "окончания текущего оплаченного периода"
        )
        bonus_line = ""
        if apply_result.renewal_extra > 0:
            bonus_line = (
                f"\n<i>В том числе бонус за продление того же тарифа:</i> "
                f"<b>+{esc(apply_result.renewal_extra)}</b> кр."
            )
        return (
            "<b>Спасибо за покупку!</b>\n"
            f"Подписка <b>{title}</b> продлена заранее — к сроку добавлены "
            f"<b>{esc(period_days)}</b> дн.\n\n"
            "<blockquote>"
            f"<i>Действует до:</i> <b>{esc(end_h)}</b>\n"
            f"<i>Кредиты по этому продлению (+{esc(apply_result.total_bonus_credits)} кр.) "
            f"начислятся после</i> {credits_when}."
            f"{bonus_line}"
            "</blockquote>\n\n"
            "<blockquote><i>Отправь</i> <code>/start</code><i>, чтобы обновить срок в меню.</i></blockquote>"
        )

    tail = ""
    if pid == "starter":
        tail = (
            "\n\n<blockquote><i>После окончания пробного срока оформи полный тариф в</i> "
            "<code>/start</code> <i>→</i> <b>Оплатить</b><i>.</i></blockquote>"
        )
    return (
        "<b>Спасибо за покупку!</b>\n"
        f"Вы приобрели подписку <b>{title}</b> на <b>{esc(period_days)}</b> дн.\n\n"
        "<blockquote>"
        f"<i>Действует до:</i> <b>{esc(end_h)}</b>\n"
        f"<i>Начислено на баланс:</i> <b>+{esc(apply_result.total_bonus_credits)}</b> кредитов"
        "</blockquote>\n\n"
        "<blockquote><i>Чтобы обновить меню и лимиты, отправь</i> <code>/start</code><i>.</i></blockquote>"
        f"{tail}"
    )


def pack_purchase_success_html(credits: int) -> str:
    return (
        "<b>Оплата прошла</b>\n"
        f"<blockquote><i>Начислено на баланс:</i> <b>+{esc(credits)}</b> кредитов.</i></blockquote>\n\n"
        "<blockquote><i>Отправь</i> <code>/start</code><i>, чтобы обновить баланс в меню.</i></blockquote>"
    )


def wata_not_paid_yet_html(*, kind: str) -> str:
    if kind == "pack":
        return (
            "<b>Оплата не подтверждена</b>\n"
            "<blockquote><i>Пакет ещё не оплачен — сначала заверши платёж на странице Wata, "
            "затем нажми «Проверить оплату».</i></blockquote>"
        )
    return (
        "<b>Подписка не оплачена</b>\n"
        "<blockquote><i>Оплата в кассе пока не подтверждена — сначала заверши платёж на странице Wata, "
        "затем нажми «Проверить оплату».</i></blockquote>"
    )


def wata_not_paid_yet_alert(*, kind: str) -> str:
    if kind == "pack":
        return "Пакет ещё не оплачен. Сначала нажми «Оплатить» и заверши платёж на странице Wata."
    return "Подписка ещё не оплачена. Сначала нажми «Оплатить» и заверши платёж на странице Wata."


def wata_declined_html() -> str:
    return (
        "<b>Оплата не прошла</b>\n"
        "<blockquote><i>Банк или касса отклонили платёж. "
        "Деньги не должны списаться; если удержание всё же есть — "
        "оно обычно возвращается автоматически в течение нескольких дней.</i></blockquote>"
    )


def wata_paid_but_not_applied_html(
    *,
    order_id: str,
    transaction_id: str,
    support_username: str,
) -> str:
    sup = payment_support_line_html(support_username=support_username)
    txn_line = (
        f"\n<i>Транзакция:</i> <code>{esc(transaction_id)}</code>" if transaction_id else ""
    )
    return (
        "<b>Оплата получена, подписка не активирована</b>\n"
        "<blockquote><i>Деньги списаны, но зачислить автоматически не удалось. "
        f"{sup} Укажи номер заказа — оформим подписку или инициируем возврат через кассу.</i>\n"
        f"<i>Заказ:</i> <code>{esc(order_id)}</code>{txn_line}</blockquote>"
    )


def wata_already_applied_plan_html(item_id: str, *, support_username: str = "") -> str:
    _ = support_username
    return (
        "<blockquote><i>Эта оплата уже была зачислена ранее.</i> "
        f"Подписка <b>{plan_subscription_title_html(item_id)}</b> должна быть активна — "
        "проверь <code>/profile</code> или отправь <code>/start</code>.</i></blockquote>"
    )


def wata_already_applied_pack_html(item_id: str) -> str:
    pack = BONUS_PACKS.get(item_id)
    title = pack.title if pack else item_id
    return (
        "<blockquote><i>Эта оплата уже была зачислена ранее.</i> "
        f"Пакет <b>{esc(title)}</b> — проверь баланс в <code>/profile</code>.</i></blockquote>"
    )
