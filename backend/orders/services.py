"""Accepting an offer: atomic, safe to retry, and the only way an order is created.

The listing row is locked first (the same lock order as the offer rules), so two
accepts on one listing run one after the other: the second sees the listing is no
longer OPEN and is refused.
"""

from django.contrib.gis.db.models.functions import Distance
from django.db import transaction
from django.utils import timezone

from dealers.models import DealerStatus
from listings.models import Listing, ListingStatus
from listings.transitions import transition
from notifications.models import NotificationKind
from notifications.services import notify
from offers.models import Offer, OfferStatus
from offers.pricing import estimated_total_paise
from orders.models import Order, OrderItem

OTHER_ACCEPTED = "other_accepted"


class AcceptError(Exception):
    messages = {
        "not_open": "This listing has already accepted an offer or is closed.",
        "not_live": "This offer has expired or been withdrawn.",
        "dealer_unavailable": "This dealer can no longer take this pickup.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def _dealer_still_eligible(dealer, listing):
    """Approved, valid scale certificate, and the service area still covers the address."""
    if dealer.status != DealerStatus.APPROVED:
        return False
    if dealer.scale_certificate_expires_on < timezone.localdate():
        return False
    distance = (
        Listing.objects.filter(pk=listing.pk)
        .annotate(d=Distance("location", dealer.location))
        .values_list("d", flat=True)
        .get()
    )
    return distance.m <= dealer.service_radius_km * 1000


def accept_offer(seller, offer):
    """Accept `offer` for the seller's own listing. Returns (order, created).

    Retrying an accept that already succeeded returns the same order with
    created=False and changes nothing.
    """
    with transaction.atomic():
        listing = Listing.objects.select_for_update().get(pk=offer.listing_id)
        locked = Offer.objects.select_for_update().select_related("dealer").get(pk=offer.pk)

        if locked.status == OfferStatus.ACCEPTED:
            return Order.objects.get(offer=locked), False
        if listing.status != ListingStatus.OPEN:
            raise AcceptError("not_open")
        if not locked.is_live:
            raise AcceptError("not_live")
        if not _dealer_still_eligible(locked.dealer, listing):
            raise AcceptError("dealer_unavailable")

        transition(listing, ListingStatus.OFFER_ACCEPTED, seller)
        locked.status = OfferStatus.ACCEPTED
        locked.save(update_fields=["status", "updated_at"])
        Offer.objects.filter(listing=listing, status=OfferStatus.ACTIVE).exclude(
            pk=locked.pk
        ).update(
            status=OfferStatus.REJECTED, rejection_reason=OTHER_ACCEPTED, updated_at=timezone.now()
        )

        items = list(listing.items.all())
        rates = {rate.category_id: rate.rate_paise for rate in locked.rates.all()}
        order = Order.objects.create(
            listing=listing,
            offer=locked,
            dealer=locked.dealer,
            seller=seller,
            estimated_total_paise=estimated_total_paise(items, rates),
        )
        OrderItem.objects.bulk_create(
            OrderItem(
                order=order,
                category_id=item.category_id,
                declared_weight_kg=item.declared_weight_kg,
                declared_quantity=item.declared_quantity,
                rate_paise=rates[item.category_id],
            )
            for item in items
        )
        notify(
            locked.dealer.user,
            NotificationKind.OFFER_ACCEPTED,
            listing_id=listing.pk,
            offer_id=locked.pk,
            order_id=order.pk,
        )
    return order, True
