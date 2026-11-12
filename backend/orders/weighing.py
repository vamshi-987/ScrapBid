"""Recording the weighing at pickup: the dealer's verified amounts, the scale photo and
GPS, then the settlement rules decide the outcome and the final amount.

Declared amounts and rates always come from the order's snapshot; the request only
supplies what was measured and the dealer's choice. Nothing a client sends about
money is used.
"""

from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from config.images import InvalidImage, clean_image
from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from orders.models import Order, OrderStatus, Party
from orders.scheduling import party_of
from orders.settlement import WEIGHT_SHORT, LineInput, SettlementError, settle

MAX_PHOTO_DIMENSION = 2048


class WeighingError(Exception):
    messages = {
        "dealer_only": "Only the dealer records the weighing.",
        "wrong_stage": "The weighing can be recorded only for a scheduled pickup.",
        "too_far_away": "You're too far from the pickup address to record the weighing.",
        "photo_required": "Take a photo of the scale reading.",
        "photo_too_large": "The photo is too large.",
        "photo_invalid": "The scale photo must be a JPG, PNG or WebP image.",
        "lines_mismatch": "Give one verified amount for each item on the order.",
        "invalid_amount": "Check the verified amounts.",
        "choice_required": "Choose what to do about the difference.",
        "invalid_choice": "That choice doesn't fit the measured amount.",
    }

    def __init__(self, code, category=None):
        super().__init__(self.messages[code])
        self.code = code
        self.category = category


def _parse_lines(order, lines):
    """Turn the request's lines into settlement inputs using the order's snapshot."""
    items = {item.category_id: item for item in order.items.select_related("category")}
    try:
        by_category = {int(line["category"]): line for line in lines}
    except (KeyError, TypeError, ValueError):
        raise WeighingError("lines_mismatch") from None
    if set(by_category) != set(items) or len(by_category) != len(lines):
        raise WeighingError("lines_mismatch")
    inputs = []
    for category_id, item in items.items():
        line = by_category[category_id]
        per_kg = item.declared_weight_kg is not None
        try:
            verified = Decimal(str(line["verified"]))
        except (KeyError, InvalidOperation):
            raise WeighingError("invalid_amount", category_id) from None
        if not verified.is_finite() or verified < 0 or verified.as_tuple().exponent < -3:
            raise WeighingError("invalid_amount", category_id)
        if verified > settings.LISTING_MAX_WEIGHT_KG * 2:
            raise WeighingError("invalid_amount", category_id)
        inputs.append(
            LineInput(
                category=category_id,
                per_kg=per_kg,
                declared=item.declared_weight_kg if per_kg else Decimal(item.declared_quantity),
                verified=verified,
                rate_paise=item.rate_paise,
                choice=line.get("choice") or None,
            )
        )
    return items, inputs


def _store_photo(photo):
    if photo is None:
        raise WeighingError("photo_required")
    if photo.size > settings.LISTING_PHOTO_MAX_BYTES:
        raise WeighingError("photo_too_large")
    try:
        image = clean_image(photo, max_dimension=MAX_PHOTO_DIMENSION)
    except InvalidImage:
        raise WeighingError("photo_invalid") from None
    return default_storage.save(
        f"scale-photos/{uuid4().hex}{image.extension}", ContentFile(image.data)
    )


def record_weighing(order, user, *, latitude, longitude, photo, lines):
    """Record the weighing and apply the settlement. Returns the order.

    A retry after a recorded weighing returns the order unchanged. A refused
    weighing records nothing.
    """
    if party_of(order, user) != Party.DEALER:
        raise WeighingError("dealer_only")
    if order.weighed_at:
        return order  # already recorded: retries change nothing

    here = Point(longitude, latitude, srid=4326)
    distance = (
        Listing.objects.filter(pk=order.listing_id)
        .annotate(d=Distance("location", here))
        .values_list("d", flat=True)
        .get()
    )
    if distance.m > settings.PICKUP_MAX_DISTANCE_METERS:
        raise WeighingError("too_far_away")
    items, inputs = _parse_lines(order, lines)
    try:
        result = settle(inputs, settings.WEIGHT_TOLERANCE_PERCENT)
    except SettlementError as error:
        raise WeighingError(error.code, error.category) from None

    key = _store_photo(photo)
    try:
        with transaction.atomic():
            listing = Listing.objects.select_for_update().get(pk=order.listing_id)
            locked = Order.objects.select_for_update().select_related("seller").get(pk=order.pk)
            if locked.weighed_at:
                default_storage.delete(key)
                return locked
            if (
                locked.status != OrderStatus.ACTIVE
                or listing.status != ListingStatus.PICKUP_SCHEDULED
            ):
                raise WeighingError("wrong_stage")

            for line in result.lines:
                item = items[line.category]
                item.verified_amount = line.verified
                item.taken_amount = line.taken
                item.comparison = line.comparison
                item.choice = line.choice or ""
                item.amount_paise = line.amount_paise
                item.save()
            locked.weighed_at = timezone.now()
            locked.weighing_location = here
            locked.weighing_distance_m = round(distance.m)
            locked.scale_photo = key
            if result.denied:
                transition(listing, ListingStatus.CANCELLED, user, reason=WEIGHT_SHORT)
                locked.status = OrderStatus.CANCELLED
                locked.cancelled_by = Party.DEALER
                locked.cancel_reason = WEIGHT_SHORT
                locked.cancelled_at = locked.weighed_at
                locked.final_amount_paise = 0
                kind = NotificationKind.WEIGHT_SHORT
            else:
                transition(
                    listing,
                    ListingStatus.PICKED_UP,
                    user,
                    details={"final_amount_paise": result.total_paise},
                )
                locked.final_amount_paise = result.total_paise
                kind = NotificationKind.WEIGHED
            locked.save()
            notify(locked.seller, kind, order_id=locked.pk, listing_id=listing.pk)
    except Exception:
        default_storage.delete(key)
        raise
    return locked
