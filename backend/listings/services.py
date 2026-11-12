"""Seller listing rules: creating, editing and publishing."""

from datetime import timedelta

from django.conf import settings
from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from listings.models import Listing, ListingItem, ListingStatus
from listings.transitions import TransitionError, transition


class ListingError(Exception):
    messages = {
        "not_editable": "This listing can't be changed any more.",
        "already_published": "This listing is already published.",
        "needs_items": "Add at least one category.",
        "needs_photo": "Add at least one photo before publishing.",
        "not_expired": "Only expired listings can be relisted.",
        "blocked": "You can't publish listings while blocked for a missed pickup.",
        "suspended": "You can't publish listings while your account is suspended.",
        "reason_required": "Give a reason for cancelling.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def is_editable(listing):
    """Sellers may change a listing (details, items, photos) only while it has no
    live offer: dealers priced what they saw. An offer past its time doesn't count."""
    if listing.status == ListingStatus.DRAFT:
        return True
    live_offers = listing.offers.filter(status="active", expires_at__gt=timezone.now())
    return listing.status == ListingStatus.OPEN and not live_offers.exists()


def _set_fields(listing, data):
    for field in ("address_line", "locality", "pincode"):
        if field in data:
            setattr(listing, field, data[field])
    if "latitude" in data:
        listing.location = Point(data["longitude"], data["latitude"], srid=4326)


def _replace_items(listing, items):
    listing.items.all().delete()
    ListingItem.objects.bulk_create(
        ListingItem(
            listing=listing,
            category=item["category"],
            declared_weight_kg=item.get("declared_weight_kg"),
            declared_quantity=item.get("declared_quantity"),
        )
        for item in items
    )


def create_listing(seller, data):
    with transaction.atomic():
        listing = Listing(seller=seller)
        _set_fields(listing, data)
        listing.save()
        _replace_items(listing, data["items"])
    return listing


def update_listing(listing, data):
    with transaction.atomic():
        locked = Listing.objects.select_for_update().get(pk=listing.pk)
        if not is_editable(locked):
            raise ListingError("not_editable")
        _set_fields(locked, data)
        locked.save(update_fields=["address_line", "locality", "pincode", "location", "updated_at"])
        if "items" in data:
            _replace_items(locked, data["items"])
    return locked


def publish_listing(listing, actor):
    """DRAFT → OPEN once the listing has at least one item and one photo."""
    with transaction.atomic():
        locked = Listing.objects.select_for_update().get(pk=listing.pk)
        if locked.status == ListingStatus.OPEN:
            raise ListingError("already_published")
        if locked.status != ListingStatus.DRAFT:
            raise ListingError("not_editable")
        if not locked.items.exists():
            raise ListingError("needs_items")
        if not locked.photos.exists():
            raise ListingError("needs_photo")
        from accounts.models import is_suspended
        from orders.endings import blocked_until

        if blocked_until(actor):
            raise ListingError("blocked")
        if is_suspended(actor):
            raise ListingError("suspended")
        transition(locked, ListingStatus.OPEN, actor)
    return locked


# --- Expiry and relisting ------------------------------------------------------


def expiry_cutoff(now=None):
    """Open listings published at or before this moment have expired."""
    return (now or timezone.now()) - timedelta(days=settings.LISTING_EXPIRY_DAYS)


def expires_at(listing):
    if listing.status != ListingStatus.OPEN or listing.published_at is None:
        return None
    return listing.published_at + timedelta(days=settings.LISTING_EXPIRY_DAYS)


def expire_overdue_listings():
    """Move overdue OPEN listings to EXPIRED as the system. Returns how many expired.

    A listing whose status changes while this runs (for example an offer is
    accepted) is skipped: the state machine re-checks each one under a lock.
    """
    overdue = Listing.objects.filter(
        status=ListingStatus.OPEN, published_at__lte=expiry_cutoff()
    ).values_list("pk", flat=True)
    expired = 0
    from offers.services import close_active_offers

    for pk in list(overdue):
        try:
            with transaction.atomic():
                listing = transition(Listing(pk=pk), ListingStatus.EXPIRED, None)
                close_active_offers(listing)
        except TransitionError:
            continue
        expired += 1
    return expired


def relist(listing):
    """Make a new DRAFT from an EXPIRED listing, with the same address and items.

    Photos aren't copied: the seller adds fresh ones before publishing. Calling
    this again returns the draft already made, so retries don't create duplicates.
    """
    with transaction.atomic():
        source = Listing.objects.select_for_update().get(pk=listing.pk)
        if source.status != ListingStatus.EXPIRED:
            raise ListingError("not_expired")
        existing = source.relistings.filter(status=ListingStatus.DRAFT).first()
        if existing:
            return existing, False
        draft = Listing.objects.create(
            seller=source.seller,
            address_line=source.address_line,
            locality=source.locality,
            pincode=source.pincode,
            location=source.location,
            relisted_from=source,
        )
        ListingItem.objects.bulk_create(
            ListingItem(
                listing=draft,
                category=item.category,
                declared_weight_kg=item.declared_weight_kg,
                declared_quantity=item.declared_quantity,
            )
            for item in source.items.all()
        )
    return draft, True


def cancel_listing(listing, actor, reason):
    """The seller withdraws a draft or open listing, with a reason. Its active offers close."""
    reason = (reason or "").strip()
    if not reason:
        raise ListingError("reason_required")
    with transaction.atomic():
        locked = Listing.objects.select_for_update().get(pk=listing.pk)
        if locked.status not in (ListingStatus.DRAFT, ListingStatus.OPEN):
            raise ListingError("not_editable")
        transition(locked, ListingStatus.CANCELLED, actor, reason=f"Seller cancelled: {reason}")
        locked.offers.filter(status="active").update(
            status="rejected", rejection_reason="listing_cancelled", updated_at=timezone.now()
        )
    return locked
