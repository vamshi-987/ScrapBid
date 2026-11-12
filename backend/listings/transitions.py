"""The listing state machine. This is the only code that may change Listing.status.

    DRAFT → OPEN → OFFER_ACCEPTED → PICKUP_SCHEDULED → PICKED_UP → COMPLETED
    Before PICKED_UP: → CANCELLED. Dealer cancels after acceptance: → OPEN.
    PICKED_UP → DISPUTED → COMPLETED | CANCELLED (admin only)
    OPEN → EXPIRED (system only)
    PICKUP_SCHEDULED → CANCELLED | OPEN may also be made by the system (no-shows)

Callers (feature services) check who the actor is, such as the seller or the
accepted dealer. This module enforces the graph, the admin-only and system-only
moves, required reasons, row locking and the audit log.
"""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from listings.models import Listing, ListingStatus

S = ListingStatus

ALLOWED = {
    S.DRAFT: {S.OPEN, S.CANCELLED},
    S.OPEN: {S.OFFER_ACCEPTED, S.CANCELLED, S.EXPIRED},
    S.OFFER_ACCEPTED: {S.PICKUP_SCHEDULED, S.CANCELLED, S.OPEN},
    S.PICKUP_SCHEDULED: {S.PICKED_UP, S.CANCELLED, S.OPEN},
    S.PICKED_UP: {S.COMPLETED, S.DISPUTED},
    S.DISPUTED: {S.COMPLETED, S.CANCELLED},
    S.COMPLETED: set(),
    S.CANCELLED: set(),
    S.EXPIRED: set(),
}

# Moves only an admin may make.
ADMIN_ONLY = {(S.DISPUTED, S.COMPLETED), (S.DISPUTED, S.CANCELLED)}
# Moves only the system (no user) may make.
SYSTEM_ONLY = {(S.OPEN, S.EXPIRED)}
# Moves the system may also make, besides users: recording a pickup no-show.
SYSTEM_ALSO = {(S.PICKUP_SCHEDULED, S.CANCELLED), (S.PICKUP_SCHEDULED, S.OPEN)}


def needs_reason(from_status, to_status):
    return to_status in (S.CANCELLED, S.DISPUTED) or (
        to_status == S.OPEN and from_status in (S.OFFER_ACCEPTED, S.PICKUP_SCHEDULED)
    )


class TransitionError(Exception):
    messages = {
        "not_allowed": "This status change isn't allowed.",
        "admin_only": "Only an admin can make this change.",
        "system_only": "Only the system can make this change.",
        "reason_required": "A reason is required for this change.",
    }

    def __init__(self, code, from_status=None, to_status=None):
        super().__init__(self.messages[code])
        self.code = code
        self.from_status = from_status
        self.to_status = to_status


def transition(listing, to_status, actor, reason="", details=None):
    """Move a listing to `to_status` and record it. Returns the updated listing.

    `actor` is the user making the change, or None for the system. The row is
    locked and its current status re-read, so a stale object or a concurrent
    change can't slip through. Runs in the caller's transaction if there is one.
    """
    reason = reason.strip()
    with transaction.atomic():
        current = Listing.objects.select_for_update().get(pk=listing.pk)
        from_status = current.status
        move = (from_status, to_status)
        if to_status not in ALLOWED[from_status]:
            raise TransitionError("not_allowed", from_status, to_status)
        if move in SYSTEM_ONLY and actor is not None:
            raise TransitionError("system_only", from_status, to_status)
        if actor is None and move not in SYSTEM_ONLY | SYSTEM_ALSO:
            raise TransitionError("not_allowed", from_status, to_status)
        if move in ADMIN_ONLY and (actor is None or not actor.is_staff):
            raise TransitionError("admin_only", from_status, to_status)
        if needs_reason(from_status, to_status) and not reason:
            raise TransitionError("reason_required", from_status, to_status)

        updates = {"status": to_status, "updated_at": timezone.now()}
        if to_status == S.OPEN:
            # Publishing, or reopening after a dealer cancels: the listing gets a
            # fresh expiry window from now.
            updates["published_at"] = updates["updated_at"]
        Listing.objects.filter(pk=current.pk).update(**updates)
        AuditLog.objects.create(
            entity_type="listing",
            entity_id=current.pk,
            actor=actor,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            details=details or {},
        )

    for field, value in updates.items():
        setattr(listing, field, value)
    return listing
