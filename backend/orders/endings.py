"""How an order ends before pickup: a cancellation by either side, or a no-show
recorded after a confirmed slot. Also the dealer's GPS check-in (the evidence) and
no-show strikes with their temporary block.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from offers.models import Offer, OfferStatus
from orders.models import NoShowStrike, Order, OrderStatus, Party, SlotStatus, StrikeStatus
from orders.payments import add_no_show_penalty, cancel_unpaid
from orders.scheduling import party_of
from orders.services import OTHER_ACCEPTED


class EndingError(Exception):
    messages = {
        "not_participant": "You aren't part of this pickup.",
        "reason_required": "Give a reason for cancelling.",
        "wrong_stage": "That isn't possible at this stage of the pickup.",
        "dealer_only": "Only the dealer checks in.",
        "outside_slot": "You can check in only during the confirmed pickup time.",
        "too_far_away": "You're too far from the pickup address to check in.",
        "not_yours": "This isn't your strike.",
        "challenge_closed": "The time to challenge this strike has passed.",
        "already_challenged": "This strike has already been challenged.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


CANCELLABLE = (ListingStatus.OFFER_ACCEPTED, ListingStatus.PICKUP_SCHEDULED)


def _lock(order):
    listing = Listing.objects.select_for_update().get(pk=order.listing_id)
    locked = (
        Order.objects.select_for_update()
        .select_related("dealer__user", "seller", "offer")
        .get(pk=order.pk)
    )
    return listing, locked


def _other_user(order, party):
    return order.dealer.user if party == Party.SELLER else order.seller


def _reopen_after_dealer(listing, order, actor, reason):
    """The dealer is out: the listing goes back to OPEN (fresh expiry) and the offers
    that lost only because this one was accepted become selectable again, if they're
    still within their time. The dealer's own offer doesn't come back."""
    transition(listing, ListingStatus.OPEN, actor, reason=reason, details={"order_id": order.pk})
    Offer.objects.filter(pk=order.offer_id).update(
        status=OfferStatus.WITHDRAWN, updated_at=timezone.now()
    )
    now = timezone.now()
    lost = Offer.objects.filter(
        listing=listing, status=OfferStatus.REJECTED, rejection_reason=OTHER_ACCEPTED
    )
    lost.filter(expires_at__gt=now).update(
        status=OfferStatus.ACTIVE, rejection_reason="", updated_at=now
    )
    lost.filter(expires_at__lte=now).update(
        status=OfferStatus.EXPIRED, rejection_reason="", updated_at=now
    )


def _close_order(order, party, reason):
    order.status = OrderStatus.CANCELLED
    order.cancelled_by = party
    order.cancel_reason = reason
    order.cancelled_at = timezone.now()


def cancel_order(order, user, reason):
    """Either side cancels before pickup, with a reason.

    Dealer cancels: the listing reopens with the other offers restored.
    Seller cancels: the listing is cancelled.
    """
    reason = (reason or "").strip()
    if not reason:
        raise EndingError("reason_required")
    with transaction.atomic():
        listing, order = _lock(order)
        party = party_of(order, user)
        if party is None:
            raise EndingError("not_participant")
        if order.status != OrderStatus.ACTIVE or listing.status not in CANCELLABLE:
            raise EndingError("wrong_stage")
        if party == Party.DEALER:
            _reopen_after_dealer(listing, order, user, f"Dealer cancelled: {reason}")
        else:
            transition(listing, ListingStatus.CANCELLED, user, reason=f"Seller cancelled: {reason}")
        _close_order(order, party, reason)
        order.save()
        notify(
            _other_user(order, party),
            NotificationKind.ORDER_CANCELLED,
            order_id=order.pk,
            listing_id=listing.pk,
        )
    return order


def check_in(order, user, latitude, longitude):
    """The dealer records arriving: only during the confirmed slot and near the address.

    Checking in again changes nothing. A failed check-in records nothing.
    """
    with transaction.atomic():
        listing, order = _lock(order)
        if party_of(order, user) != Party.DEALER:
            raise EndingError("dealer_only")
        if (
            order.status != OrderStatus.ACTIVE
            or listing.status != ListingStatus.PICKUP_SCHEDULED
            or order.slot_status != SlotStatus.CONFIRMED
        ):
            raise EndingError("wrong_stage")
        if order.dealer_checked_in_at:
            return order
        now = timezone.now()
        if not (order.slot_start <= now <= order.slot_end):
            raise EndingError("outside_slot")
        here = Point(longitude, latitude, srid=4326)
        distance = (
            Listing.objects.filter(pk=listing.pk)
            .annotate(d=Distance("location", here))
            .values_list("d", flat=True)
            .get()
        )
        if distance.m > settings.PICKUP_MAX_DISTANCE_METERS:
            raise EndingError("too_far_away")
        order.dealer_checked_in_at = now
        order.dealer_check_in_location = here
        order.dealer_check_in_distance_m = round(distance.m)
        order.save()
        notify(
            order.seller, NotificationKind.DEALER_ARRIVED, order_id=order.pk, listing_id=listing.pk
        )
    return order


def record_no_shows():
    """For confirmed slots that ended NO_SHOW_GRACE_MINUTES ago with no pickup started:
    dealer checked in → seller missed it; no check-in → dealer missed it. Returns how
    many were recorded. Safe to run repeatedly."""
    cutoff = timezone.now() - timedelta(minutes=settings.NO_SHOW_GRACE_MINUTES)
    due = Order.objects.filter(
        status=OrderStatus.ACTIVE,
        slot_status=SlotStatus.CONFIRMED,
        slot_end__lte=cutoff,
        listing__status=ListingStatus.PICKUP_SCHEDULED,
    ).values_list("pk", flat=True)
    recorded = 0
    for pk in list(due):
        with transaction.atomic():
            listing, order = _lock(Order.objects.get(pk=pk))
            if (
                order.status != OrderStatus.ACTIVE
                or listing.status != ListingStatus.PICKUP_SCHEDULED
            ):
                continue  # something changed since the query (e.g. pickup started)
            missed = Party.SELLER if order.dealer_checked_in_at else Party.DEALER
            reason = f"No-show: {missed}"
            if missed == Party.DEALER:
                _reopen_after_dealer(listing, order, None, reason)
            else:
                transition(listing, ListingStatus.CANCELLED, None, reason=reason)
            _close_order(order, "", reason)
            order.no_show_party = missed
            order.save()
            absent = order.dealer.user if missed == Party.DEALER else order.seller
            strike = NoShowStrike.objects.create(user=absent, order=order, party=missed)
            add_no_show_penalty(strike)
            notify(
                absent, NotificationKind.NO_SHOW_RECORDED, order_id=order.pk, strike_id=strike.pk
            )
            present = order.seller if missed == Party.DEALER else order.dealer.user
            notify(
                present, NotificationKind.PICKUP_MISSED, order_id=order.pk, listing_id=listing.pk
            )
            recorded += 1
    return recorded


def challenge_strike(strike, user, reason):
    """The struck user asks an admin to review, within STRIKE_CHALLENGE_HOURS."""
    reason = (reason or "").strip()
    if not reason:
        raise EndingError("reason_required")
    with transaction.atomic():
        strike = NoShowStrike.objects.select_for_update().get(pk=strike.pk)
        if strike.user_id != user.pk:
            raise EndingError("not_yours")
        if strike.challenged_at:
            raise EndingError("already_challenged")
        if timezone.now() > strike.created_at + timedelta(hours=settings.STRIKE_CHALLENGE_HOURS):
            raise EndingError("challenge_closed")
        strike.challenge_reason = reason
        strike.challenged_at = timezone.now()
        strike.save()
    return strike


def resolve_strike(strike, admin, uphold):
    """An admin upholds a strike, or removes it (which cancels its penalty if unpaid)."""
    with transaction.atomic():
        strike.status = StrikeStatus.UPHELD if uphold else StrikeStatus.REMOVED
        strike.resolved_by = admin
        strike.resolved_at = timezone.now()
        strike.save()
        if not uphold:
            cancel_unpaid(getattr(strike, "due", None))
    return strike
