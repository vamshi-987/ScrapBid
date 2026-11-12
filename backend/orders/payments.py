"""Dues (no-show penalties and suspension fees) and paying them through the gateway.

A no-show creates a NO_SHOW_PENALTY_PAISE due; the user is blocked (sellers can't publish,
dealers can't offer) while it's unpaid. A suspension creates a due for its fee; paying it
lifts the suspension. A due is marked paid only after the gateway confirms the full amount
server-side (a signed callback alone isn't enough). Removing a strike, or an admin lifting
a suspension, cancels an unpaid due; a paid one is never refunded.
"""

from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from orders.gateway import GatewayError, callback_is_valid, get_gateway
from orders.models import Due, DueKind, DueStatus


class PaymentError(Exception):
    messages = {
        "not_unpaid": "This is already paid or cancelled.",
        "gateway_unavailable": "Payments are unavailable right now. Try again shortly.",
        "invalid_callback": "The payment couldn't be verified.",
        "not_paid": "The payment hasn't gone through yet.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def add_no_show_penalty(strike):
    return Due.objects.create(
        user=strike.user,
        kind=DueKind.NO_SHOW_PENALTY,
        amount_paise=settings.NO_SHOW_PENALTY_PAISE,
        strike=strike,
    )


def add_suspension_fee(suspension):
    return Due.objects.create(
        user=suspension.user,
        kind=DueKind.SUSPENSION_FEE,
        amount_paise=suspension.fee_paise,
        suspension=suspension,
    )


def has_unpaid_penalty(user):
    """True while the user has an unpaid no-show penalty (they're blocked until it's paid)."""
    return Due.objects.filter(
        user=user, kind=DueKind.NO_SHOW_PENALTY, status=DueStatus.UNPAID
    ).exists()


def cancel_unpaid(due):
    """Cancel a due if it's still unpaid (a paid one stays paid: no refunds)."""
    if due is None:
        return
    Due.objects.filter(pk=due.pk, status=DueStatus.UNPAID).update(
        status=DueStatus.CANCELLED, cancelled_at=timezone.now()
    )


def start_payment(due):
    """Return the gateway URL to pay this due, creating the payment link once."""
    if due.status != DueStatus.UNPAID:
        raise PaymentError("not_unpaid")
    if due.link_url:
        return due.link_url
    try:
        link_id, url = get_gateway().create_link(
            amount_paise=due.amount_paise,
            reference_id=f"due-{due.pk}-{uuid4().hex[:8]}",
            description=due.get_kind_display(),
            callback_url=f"{settings.APP_BASE_URL}/api/payments/callback",
        )
    except GatewayError:
        raise PaymentError("gateway_unavailable") from None
    Due.objects.filter(pk=due.pk, link_id="").update(link_id=link_id, link_url=url)
    due.refresh_from_db()
    return due.link_url


def confirm_payment(due):
    """Ask the gateway whether the due's link is fully paid; record it if so. Idempotent."""
    if due.status == DueStatus.PAID:
        return due
    if not due.link_id:
        raise PaymentError("not_paid")
    try:
        link = get_gateway().fetch_link(due.link_id)
    except GatewayError:
        raise PaymentError("gateway_unavailable") from None
    if link.status != "paid" or link.amount_paid_paise < due.amount_paise:
        raise PaymentError("not_paid")
    with transaction.atomic():
        locked = (
            Due.objects.select_for_update(of=("self",)).select_related("suspension").get(pk=due.pk)
        )
        if locked.status == DueStatus.PAID:
            return locked
        locked.status = DueStatus.PAID  # also when it was cancelled meanwhile: money came in
        locked.paid_at = timezone.now()
        locked.payment_id = link.payment_id
        locked.save(update_fields=["status", "paid_at", "payment_id"])
        if locked.kind == DueKind.SUSPENSION_FEE and not locked.suspension.lifted_at:
            from orders.misuse import lift_suspension

            lift_suspension(locked.suspension, None)
    return locked


def confirm_from_callback(params):
    """The gateway sent the user back. Check the signature, then confirm with the gateway."""
    if not callback_is_valid(params):
        raise PaymentError("invalid_callback")
    due = Due.objects.filter(link_id=params["razorpay_payment_link_id"]).first()
    if due is None:
        raise PaymentError("invalid_callback")
    return confirm_payment(due)
