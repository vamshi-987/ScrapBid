"""Seller listing rules: creating, editing and publishing."""

from django.contrib.gis.geos import Point
from django.db import transaction

from listings.models import Listing, ListingItem, ListingStatus
from listings.transitions import transition


class ListingError(Exception):
    messages = {
        "not_editable": "This listing can't be changed any more.",
        "already_published": "This listing is already published.",
        "needs_items": "Add at least one category.",
        "needs_photo": "Add at least one photo before publishing.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def is_editable(listing):
    """Sellers may change a listing (details, items, photos) only before any offer."""
    return listing.status in (ListingStatus.DRAFT, ListingStatus.OPEN)


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
        transition(locked, ListingStatus.OPEN, actor)
    return locked
