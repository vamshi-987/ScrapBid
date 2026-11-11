"""Which listings a dealer may see: open, in their service area, not their own,
and restricted categories only for authorised dealers. Filtered in the database."""

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db.models import Exists, OuterRef

from listings.models import Listing, ListingItem, ListingStatus
from listings.services import expiry_cutoff


def visible_to_dealer(profile):
    """Listings the dealer may see, nearest first."""
    listings = (
        Listing.objects.filter(
            status=ListingStatus.OPEN,
            # Hidden as soon as it's overdue, even before the expiry job runs.
            published_at__gt=expiry_cutoff(),
            location__dwithin=(profile.location, D(km=profile.service_radius_km)),
        )
        .exclude(seller_id=profile.user_id)
        .annotate(distance=Distance("location", profile.location))
        .order_by("distance", "id")
    )
    if not profile.ewaste_authorised:
        has_restricted = ListingItem.objects.filter(
            listing=OuterRef("pk"), category__is_restricted=True
        )
        listings = listings.exclude(Exists(has_restricted))
    return listings.prefetch_related("items__category", "photos")
