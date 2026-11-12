"""Offer rules: making, editing and withdrawing offers, and closing them.

Lock order is always listing first, then offer, so acceptance (which locks the
listing) and these changes can't deadlock.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import is_suspended
from listings.feed import visible_to_dealer
from listings.models import Listing, ListingStatus, PricingUnit
from notifications.models import NotificationKind
from notifications.services import notify
from offers.models import Offer, OfferRate, OfferStatus

# Sanity caps that catch typing mistakes (₹10,000 per kg, ₹1,00,000 per item).
MAX_RATE_PAISE = {PricingUnit.PER_KG: 10_000_00, PricingUnit.PER_ITEM: 1_00_000_00}


class OfferError(Exception):
    messages = {
        "not_found": "This listing isn't available to you.",
        "already_offered": "You already have an active offer on this listing.",
        "not_open": "Offers can only change while the listing is open.",
        "not_live": "This offer is no longer active.",
        "wrong_categories": "Give exactly one rate for each category on the listing.",
        "rate_out_of_range": "A rate is too high. Check the amount.",
        "blocked": "You can't make offers while blocked for a missed pickup.",
        "suspended": "You can't make offers while your account is suspended.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def _check_rates(listing, rates):
    """`rates` maps category id -> paise. Must cover exactly the listing's categories."""
    units = {item.category_id: item.category.pricing_unit for item in listing.items.all()}
    if set(rates) != set(units):
        raise OfferError("wrong_categories")
    for category_id, paise in rates.items():
        if paise > MAX_RATE_PAISE[units[category_id]]:
            raise OfferError("rate_out_of_range")


def _set_rates(offer, rates):
    offer.rates.all().delete()
    OfferRate.objects.bulk_create(
        OfferRate(offer=offer, category_id=category_id, rate_paise=paise)
        for category_id, paise in rates.items()
    )


def _new_expiry():
    return timezone.now() + timedelta(hours=settings.OFFER_EXPIRY_HOURS)


def _lock_open_listing(listing_id):
    listing = (
        Listing.objects.select_for_update().prefetch_related("items__category").get(pk=listing_id)
    )
    if listing.status != ListingStatus.OPEN:
        raise OfferError("not_open")
    return listing


def create_offer(dealer, listing_id, rates):
    """Make an ACTIVE offer on a listing the dealer can see in their feed."""
    if not visible_to_dealer(dealer).filter(pk=listing_id).exists():
        raise OfferError("not_found")
    from orders.endings import blocked_until

    if blocked_until(dealer.user):
        raise OfferError("blocked")
    if is_suspended(dealer.user):
        raise OfferError("suspended")
    try:
        with transaction.atomic():
            listing = _lock_open_listing(listing_id)
            _check_rates(listing, rates)
            # An offer past its expiry (job not yet run) no longer counts as active.
            Offer.objects.filter(
                listing=listing,
                dealer=dealer,
                status=OfferStatus.ACTIVE,
                expires_at__lte=timezone.now(),
            ).update(status=OfferStatus.EXPIRED)
            if Offer.objects.filter(
                listing=listing, dealer=dealer, status=OfferStatus.ACTIVE
            ).exists():
                raise OfferError("already_offered")
            offer = Offer.objects.create(listing=listing, dealer=dealer, expires_at=_new_expiry())
            _set_rates(offer, rates)
            notify(
                listing.seller, NotificationKind.NEW_OFFER, listing_id=listing.pk, offer_id=offer.pk
            )
    except IntegrityError:
        # Two requests raced past the check: the database constraint caught it.
        raise OfferError("already_offered") from None
    return offer


def _lock_live_offer(offer):
    listing = _lock_open_listing(offer.listing_id)
    locked = Offer.objects.select_for_update().get(pk=offer.pk)
    if not locked.is_live:
        raise OfferError("not_live")
    return listing, locked


def update_offer(offer, rates):
    """New rates for a live offer on an open listing. Resets the expiry: it's a fresh quote."""
    if is_suspended(offer.dealer.user):
        raise OfferError("suspended")
    with transaction.atomic():
        listing, locked = _lock_live_offer(offer)
        _check_rates(listing, rates)
        _set_rates(locked, rates)
        locked.expires_at = _new_expiry()
        locked.save(update_fields=["expires_at", "updated_at"])
    return locked


def withdraw_offer(offer):
    with transaction.atomic():
        _, locked = _lock_live_offer(offer)
        locked.status = OfferStatus.WITHDRAWN
        locked.save(update_fields=["status", "updated_at"])
    return locked


def close_active_offers(listing):
    """Mark a closed listing's active offers EXPIRED (e.g. when the listing expires)."""
    return Offer.objects.filter(listing=listing, status=OfferStatus.ACTIVE).update(
        status=OfferStatus.EXPIRED, updated_at=timezone.now()
    )


def offers_for_seller(listing):
    """Offers the listing's seller may compare: only from dealers who are still approved,
    hold a valid scale certificate and whose service area covers the pickup address.
    The accepted offer first, then live offers by highest estimate; withdrawn and
    rejected offers left out."""
    from django.contrib.gis.db.models.functions import Distance
    from django.db.models import F

    from accounts.models import Suspension
    from dealers.models import DealerStatus
    from offers.pricing import estimated_total_paise

    offers = (
        Offer.objects.filter(
            listing=listing,
            status__in=(OfferStatus.ACTIVE, OfferStatus.EXPIRED, OfferStatus.ACCEPTED),
            dealer__status=DealerStatus.APPROVED,
            dealer__scale_certificate_expires_on__gte=timezone.localdate(),
        )
        .exclude(dealer__user__in=Suspension.objects.filter(lifted_at=None).values("user"))
        .annotate(reach=Distance("dealer__location", listing.location))
        .filter(reach__lte=F("dealer__service_radius_km") * 1000)
        .select_related("dealer", "listing")
        .prefetch_related("rates", "listing__items__category")
    )

    def sort_key(offer):
        rates = {rate.category_id: rate.rate_paise for rate in offer.rates.all()}
        total = estimated_total_paise(list(offer.listing.items.all()), rates)
        return (offer.status != OfferStatus.ACCEPTED, not offer.is_live, -total, offer.pk)

    return sorted(offers, key=sort_key)


def expire_due_offers():
    """Mark ACTIVE offers past their expiry as EXPIRED. Returns how many changed.

    Acceptance checks the time itself, so this only tidies up statuses; an offer
    past its time is never accepted even if this hasn't run yet.
    """
    now = timezone.now()
    return Offer.objects.filter(status=OfferStatus.ACTIVE, expires_at__lte=now).update(
        status=OfferStatus.EXPIRED, updated_at=now
    )
