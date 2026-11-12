"""Pickup slot rules: the dealer proposes a time and the seller confirms it (or
counter-proposes for the dealer to confirm). A confirmed slot moves only when the
other side accepts a reschedule request.

Times are stored in UTC and checked against the pickup hours in IST.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from orders.models import Order, OrderStatus, Party, SlotStatus

IST = ZoneInfo("Asia/Kolkata")


class SlotError(Exception):
    messages = {
        "misaligned": "Pick a start time on the hour or half hour.",
        "outside_hours": "Pickups run between the day's opening and closing hours (IST).",
        "too_soon": "Pick a time further ahead.",
        "too_far": "Pick a time within the booking window.",
        "not_participant": "You aren't part of this pickup.",
        "wrong_stage": "That isn't possible at this stage of the pickup.",
        "own_proposal": "The other side has to confirm your proposed time.",
        "not_proposed": "There's no proposed time to confirm.",
        "slot_passed": "That time has already started.",
        "too_late": "It's too close to the pickup time to change it.",
        "no_request": "There's no reschedule request to answer.",
        "own_request": "The other side has to answer your request.",
        "request_pending": "The other side has asked for a new time; answer that first.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def party_of(order, user):
    if user.pk == order.seller_id:
        return Party.SELLER
    if user.pk == order.dealer.user_id:
        return Party.DEALER
    return None


def _other_user(order, party):
    return order.dealer.user if party == Party.SELLER else order.seller


def validate_slot(start, now=None):
    """Return (start, end) for a slot starting at `start`, or raise SlotError."""
    now = now or timezone.now()
    local = start.astimezone(IST)
    end = start + timedelta(hours=settings.PICKUP_SLOT_HOURS)
    local_end = end.astimezone(IST)
    if local.minute not in (0, 30) or local.second or local.microsecond:
        raise SlotError("misaligned")
    day_end = local.replace(hour=settings.PICKUP_DAY_END_HOUR, minute=0)
    if (
        local.hour < settings.PICKUP_DAY_START_HOUR
        or local_end > day_end
        or local_end.date() != local.date()
    ):
        raise SlotError("outside_hours")
    if start < now + timedelta(hours=settings.PICKUP_MIN_NOTICE_HOURS):
        raise SlotError("too_soon")
    if start > now + timedelta(days=settings.PICKUP_MAX_DAYS_AHEAD):
        raise SlotError("too_far")
    return start, end


def _lock(order):
    listing = Listing.objects.select_for_update().get(pk=order.listing_id)
    locked = (
        Order.objects.select_for_update().select_related("dealer__user", "seller").get(pk=order.pk)
    )
    if locked.status != OrderStatus.ACTIVE:
        raise SlotError("wrong_stage")
    return listing, locked


def _notify(order, party, kind):
    notify(_other_user(order, party), kind, order_id=order.pk, listing_id=order.listing_id)


def propose_slot(order, user, start):
    """Dealer proposes a time; the seller may counter-propose once the dealer has."""
    with transaction.atomic():
        listing, order = _lock(order)
        party = party_of(order, user)
        if party is None:
            raise SlotError("not_participant")
        if (
            listing.status != ListingStatus.OFFER_ACCEPTED
            or order.slot_status == SlotStatus.CONFIRMED
        ):
            raise SlotError("wrong_stage")
        if party == Party.SELLER and order.slot_status != SlotStatus.PROPOSED:
            raise SlotError("wrong_stage")  # the dealer proposes first
        order.slot_start, order.slot_end = validate_slot(start)
        order.slot_status = SlotStatus.PROPOSED
        order.slot_proposed_by = party
        order.save()
        _notify(order, party, NotificationKind.SLOT_PROPOSED)
    return order


def confirm_slot(order, user):
    """The side that didn't propose confirms; the listing becomes PICKUP_SCHEDULED."""
    with transaction.atomic():
        listing, order = _lock(order)
        party = party_of(order, user)
        if party is None:
            raise SlotError("not_participant")
        if order.slot_status != SlotStatus.PROPOSED:
            raise SlotError("not_proposed")
        if order.slot_proposed_by == party:
            raise SlotError("own_proposal")
        if order.slot_start <= timezone.now():
            raise SlotError("slot_passed")
        transition(
            listing,
            ListingStatus.PICKUP_SCHEDULED,
            user,
            details={
                "slot_start": order.slot_start.isoformat(),
                "slot_end": order.slot_end.isoformat(),
            },
        )
        order.slot_status = SlotStatus.CONFIRMED
        order.save(update_fields=["slot_status"])
        _notify(order, party, NotificationKind.SLOT_CONFIRMED)
    return order


def _deadline(order):
    return order.slot_start - timedelta(hours=settings.PICKUP_RESCHEDULE_DEADLINE_HOURS)


def request_reschedule(order, user, start):
    """Ask to move a confirmed slot. Closes PICKUP_RESCHEDULE_DEADLINE_HOURS before it."""
    with transaction.atomic():
        listing, order = _lock(order)
        party = party_of(order, user)
        if party is None:
            raise SlotError("not_participant")
        if (
            listing.status != ListingStatus.PICKUP_SCHEDULED
            or order.slot_status != SlotStatus.CONFIRMED
        ):
            raise SlotError("wrong_stage")
        if timezone.now() > _deadline(order):
            raise SlotError("too_late")
        if order.reschedule_requested_by and order.reschedule_requested_by != party:
            raise SlotError("request_pending")
        order.reschedule_start, order.reschedule_end = validate_slot(start)
        order.reschedule_requested_by = party
        order.save()
        _notify(order, party, NotificationKind.RESCHEDULE_REQUESTED)
    return order


def _clear_request(order):
    order.reschedule_start = order.reschedule_end = None
    order.reschedule_requested_by = ""


def answer_reschedule(order, user, accept):
    """The other side accepts (the new slot applies) or declines (the confirmed slot stays).

    Acceptance must happen before the confirmed slot starts; after that the request
    lapses and the confirmed slot stands.
    """
    with transaction.atomic():
        listing, order = _lock(order)
        party = party_of(order, user)
        if party is None:
            raise SlotError("not_participant")
        if not order.reschedule_requested_by:
            raise SlotError("no_request")
        if order.reschedule_requested_by == party:
            raise SlotError("own_request")
        if accept and timezone.now() >= order.slot_start:
            raise SlotError("too_late")
        requested_by = order.reschedule_requested_by
        if accept:
            old = {"old_start": order.slot_start.isoformat(), "old_end": order.slot_end.isoformat()}
            order.slot_start, order.slot_end = order.reschedule_start, order.reschedule_end
            AuditLog.objects.create(
                entity_type="listing",
                entity_id=listing.pk,
                actor=user,
                from_status=listing.status,
                to_status=listing.status,
                reason="Pickup rescheduled",
                details={
                    **old,
                    "new_start": order.slot_start.isoformat(),
                    "new_end": order.slot_end.isoformat(),
                    "requested_by": requested_by,
                    "accepted_by": party,
                },
            )
        _clear_request(order)
        order.save()
        kind = (
            NotificationKind.RESCHEDULE_ACCEPTED if accept else NotificationKind.RESCHEDULE_DECLINED
        )
        _notify(order, party, kind)
    return order


def available_actions(order, user):
    """What this user can do with the order right now, for the app to show."""
    party = party_of(order, user)
    status = order.listing.status
    now = timezone.now()
    actions = []
    if party is None or order.status != OrderStatus.ACTIVE:
        return actions
    if status == ListingStatus.OFFER_ACCEPTED:
        if party == Party.DEALER or order.slot_status == SlotStatus.PROPOSED:
            actions.append("propose")
        if (
            order.slot_status == SlotStatus.PROPOSED
            and order.slot_proposed_by != party
            and order.slot_start > now
        ):
            actions.append("confirm")
    if status in (ListingStatus.OFFER_ACCEPTED, ListingStatus.PICKUP_SCHEDULED):
        actions.append("cancel")
    if status == ListingStatus.PICKUP_SCHEDULED and order.slot_status == SlotStatus.CONFIRMED:
        in_slot = order.slot_start <= now <= order.slot_end
        if party == Party.DEALER and in_slot and not order.dealer_checked_in_at:
            actions.append("check_in")
        if party == Party.DEALER and not order.weighed_at:
            actions.append("weigh")
        if now <= _deadline(order) and order.reschedule_requested_by in ("", party):
            actions.append("request_reschedule")
        if order.reschedule_requested_by and order.reschedule_requested_by != party:
            actions.append("decline_reschedule")
            if now < order.slot_start:
                actions.append("accept_reschedule")
    if status == ListingStatus.PICKED_UP:
        if party == Party.SELLER:
            actions.extend(["confirm_weighing", "dispute"])
        elif order.seller_confirmed_at:
            actions.append("complete")
    return actions
