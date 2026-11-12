"""Disputes: the seller disputes the weighing instead of confirming it; an admin resolves.

The admin completes the order at the measured amounts, completes it at amounts they
set (each line is admin amount × agreed rate; the rate never changes), or cancels it.
A decision ends the order without the handover code. The admin also records whether
the dispute was upheld (the seller was right), which counts against the dealer.
The dealer's original reading stays on the order for the stats.
"""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from orders.models import DisputeOutcome, Order, OrderStatus, Party
from orders.scheduling import party_of
from orders.settlement import line_amount


class DisputeError(Exception):
    messages = {
        "seller_only": "Only the seller can dispute the weighing.",
        "admin_only": "Only an admin can resolve a dispute.",
        "wrong_stage": "That isn't possible at this stage.",
        "reason_required": "Say what's wrong with the weighing.",
        "note_required": "Add a note explaining the decision.",
        "upheld_required": "Say whether the dispute is upheld.",
        "invalid_outcome": "Choose how to resolve the dispute.",
        "invalid_amount": "Check the amounts.",
    }

    def __init__(self, code, category=None):
        super().__init__(self.messages[code])
        self.code = code
        self.category = category


def _lock(order):
    listing = Listing.objects.select_for_update().get(pk=order.listing_id)
    locked = (
        Order.objects.select_for_update().select_related("seller", "dealer__user").get(pk=order.pk)
    )
    return listing, locked


def open_dispute(order, user, reason):
    """The seller disputes the weighing. Stops completion. A retry returns the order."""
    if party_of(order, user) != Party.SELLER:
        raise DisputeError("seller_only")
    reason = (reason or "").strip()
    if not reason:
        raise DisputeError("reason_required")
    with transaction.atomic():
        listing, locked = _lock(order)
        if listing.status == ListingStatus.DISPUTED and locked.disputed_at:
            return locked
        if locked.status != OrderStatus.ACTIVE or listing.status != ListingStatus.PICKED_UP:
            raise DisputeError("wrong_stage")
        transition(listing, ListingStatus.DISPUTED, user, reason=reason)
        locked.disputed_at = timezone.now()
        locked.dispute_reason = reason[:300]
        locked.handover_code_hash = ""  # any code shown before can no longer complete it
        locked.save()
        notify(
            locked.dealer.user,
            NotificationKind.DISPUTE_OPENED,
            order_id=locked.pk,
            listing_id=listing.pk,
        )
    return locked


def _adjusted_lines(order, amounts):
    """Parse the admin's amounts {category_id: value} against the order's items."""
    items = list(order.items.all())
    if {item.category_id for item in items} != set(amounts):
        raise DisputeError("invalid_amount")
    results = []
    for item in items:
        try:
            value = Decimal(str(amounts[item.category_id]).strip())
        except InvalidOperation:
            raise DisputeError("invalid_amount", item.category_id) from None
        per_kg = item.declared_weight_kg is not None
        if (
            not value.is_finite()
            or value < 0
            or value.as_tuple().exponent < -3
            or value > settings.LISTING_MAX_WEIGHT_KG * 2
            or (not per_kg and value % 1)
        ):
            raise DisputeError("invalid_amount", item.category_id)
        results.append((item, value, line_amount(value, item.rate_paise)))
    return results


def resolve_dispute(order, admin, *, outcome, note, upheld, amounts=None):
    """An admin resolves a dispute. `amounts` ({category_id: kg or count}) only for
    COMPLETED_ADJUSTED. Returns the order."""
    if admin is None or not admin.is_staff:
        raise DisputeError("admin_only")
    note = (note or "").strip()
    if outcome not in DisputeOutcome.values:
        raise DisputeError("invalid_outcome")
    if not note:
        raise DisputeError("note_required")
    if upheld not in (True, False):
        raise DisputeError("upheld_required")

    with transaction.atomic():
        listing, locked = _lock(order)
        if locked.status != OrderStatus.ACTIVE or listing.status != ListingStatus.DISPUTED:
            raise DisputeError("wrong_stage")
        adjusted = (
            _adjusted_lines(locked, amounts or {})
            if (outcome == DisputeOutcome.COMPLETED_ADJUSTED)
            else None
        )
        now = timezone.now()
        details = {"outcome": outcome, "upheld": upheld}
        if outcome == DisputeOutcome.CANCELLED:
            transition(listing, ListingStatus.CANCELLED, admin, reason=note, details=details)
            locked.status = OrderStatus.CANCELLED
            locked.cancel_reason = note[:300]
            locked.cancelled_at = now
        else:
            if adjusted is not None:
                details["previous_final_amount_paise"] = locked.final_amount_paise
                for item, value, paise in adjusted:
                    item.taken_amount = value
                    item.amount_paise = paise
                    item.save(update_fields=["taken_amount", "amount_paise"])
                locked.final_amount_paise = sum(paise for _, _, paise in adjusted)
            details["final_amount_paise"] = locked.final_amount_paise
            transition(listing, ListingStatus.COMPLETED, admin, reason=note, details=details)
            locked.status = OrderStatus.COMPLETED
            locked.completed_at = now
        locked.dispute_outcome = outcome
        locked.dispute_upheld = upheld
        locked.dispute_note = note[:500]
        locked.dispute_resolved_by = admin
        locked.dispute_resolved_at = now
        locked.handover_code_hash = ""
        locked.save()
        for user in (locked.seller, locked.dealer.user):
            notify(
                user,
                NotificationKind.DISPUTE_RESOLVED,
                order_id=locked.pk,
                listing_id=listing.pk,
            )
    return locked
