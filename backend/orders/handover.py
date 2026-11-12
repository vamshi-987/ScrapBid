"""Seller confirmation and handover: the order completes only when the dealer enters
the code shown on the seller's phone.

The seller confirms the weighing and gets a 6-digit code. Only its hash is stored.
Asking again gives a fresh code, which replaces the old one and resets the tries.
The dealer enters the code with the payment method (cash or UPI). The amount paid is
the server's final amount; the app only records the payment, which happens offline.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string, salted_hmac

from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from orders.models import Order, OrderStatus, Party, PaymentMethod
from orders.scheduling import party_of

CODE_LENGTH = 6


class HandoverError(Exception):
    messages = {
        "seller_only": "Only the seller confirms the weighing.",
        "dealer_only": "Only the dealer completes the pickup.",
        "wrong_stage": "That isn't possible at this stage.",
        "not_confirmed": "The seller hasn't confirmed the weighing yet.",
        "invalid_payment_method": "Choose cash or UPI.",
        "invalid_code": "That code isn't right.",
        "code_expired": "The code has expired. Ask the seller for a fresh one.",
        "too_many_attempts": "Too many wrong codes. Ask the seller for a fresh one.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def _code_hash(order_id, code):
    return salted_hmac("orders.handover", f"{order_id}:{code}", algorithm="sha256").hexdigest()


def _lock(order):
    listing = Listing.objects.select_for_update().get(pk=order.listing_id)
    locked = Order.objects.select_for_update().select_related("seller").get(pk=order.pk)
    return listing, locked


def confirm_weighing(order, user):
    """The seller confirms the weighing. Returns (order, code): a fresh code each call.

    The confirmation is recorded once; a repeat only replaces the code.
    """
    if party_of(order, user) != Party.SELLER:
        raise HandoverError("seller_only")
    with transaction.atomic():
        listing, locked = _lock(order)
        if locked.status != OrderStatus.ACTIVE or listing.status != ListingStatus.PICKED_UP:
            raise HandoverError("wrong_stage")
        now = timezone.now()
        if not locked.seller_confirmed_at:
            locked.seller_confirmed_at = now
        code = get_random_string(CODE_LENGTH, allowed_chars="0123456789")
        locked.handover_code_hash = _code_hash(locked.pk, code)
        locked.handover_code_expires_at = now + timedelta(
            minutes=settings.HANDOVER_CODE_EXPIRY_MINUTES
        )
        locked.handover_attempts = 0
        locked.save()
    return locked, code


def complete_order(order, user, *, code, payment_method):
    """The dealer enters the seller's code and the payment method. Returns the order.

    A retry after completion returns the completed order. Wrong tries are saved even
    though an error is raised, so the limit can't be bypassed.
    """
    if party_of(order, user) != Party.DEALER:
        raise HandoverError("dealer_only")
    if payment_method not in PaymentMethod.values:
        raise HandoverError("invalid_payment_method")

    error = None
    with transaction.atomic():
        listing, locked = _lock(order)
        if locked.status == OrderStatus.COMPLETED:
            return locked
        if locked.status != OrderStatus.ACTIVE or listing.status != ListingStatus.PICKED_UP:
            raise HandoverError("wrong_stage")
        if not locked.seller_confirmed_at or not locked.handover_code_hash:
            raise HandoverError("not_confirmed")
        now = timezone.now()
        if locked.handover_code_expires_at <= now:
            error = "code_expired"
        elif locked.handover_attempts >= settings.HANDOVER_CODE_MAX_ATTEMPTS:
            error = "too_many_attempts"
        elif not constant_time_compare(
            locked.handover_code_hash, _code_hash(locked.pk, str(code).strip())
        ):
            locked.handover_attempts += 1
            locked.save(update_fields=["handover_attempts"])
            maxed = locked.handover_attempts >= settings.HANDOVER_CODE_MAX_ATTEMPTS
            error = "too_many_attempts" if maxed else "invalid_code"
        else:
            transition(
                listing,
                ListingStatus.COMPLETED,
                user,
                details={
                    "final_amount_paise": locked.final_amount_paise,
                    "payment_method": payment_method,
                },
            )
            locked.status = OrderStatus.COMPLETED
            locked.completed_at = now
            locked.payment_method = payment_method
            locked.paid_amount_paise = locked.final_amount_paise
            locked.handover_code_hash = ""
            locked.save()
            notify(
                locked.seller,
                NotificationKind.ORDER_COMPLETED,
                order_id=locked.pk,
                listing_id=listing.pk,
            )
    if error:
        raise HandoverError(error)
    return locked
