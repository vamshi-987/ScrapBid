from django.db import models
from django.utils import timezone

from dealers.models import DealerProfile
from listings.models import Category, Listing


class OfferStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    WITHDRAWN = "withdrawn", "Withdrawn"
    REJECTED = "rejected", "Rejected"
    ACCEPTED = "accepted", "Accepted"
    EXPIRED = "expired", "Expired"


class Offer(models.Model):
    """A dealer's estimate for a listing: one rate per category. Final amount depends
    on the weight measured at pickup."""

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="offers")
    dealer = models.ForeignKey(DealerProfile, on_delete=models.PROTECT, related_name="offers")
    status = models.CharField(
        max_length=10, choices=OfferStatus.choices, default=OfferStatus.ACTIVE
    )
    # Why it was rejected, e.g. "other_accepted" (restorable if that dealer cancels).
    rejection_reason = models.CharField(max_length=30, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "dealer"],
                condition=models.Q(status="active"),
                name="one_active_offer_per_dealer_per_listing",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=OfferStatus.values), name="offer_status_valid"
            ),
        ]
        indexes = [models.Index(fields=["listing", "status"])]

    def __str__(self):
        return f"Offer {self.pk} on listing {self.listing_id}"

    @property
    def is_live(self):
        """Active and not past its expiry, even if the expiry job hasn't run yet."""
        return self.status == OfferStatus.ACTIVE and self.expires_at > timezone.now()


class OfferRate(models.Model):
    """Rate in integer paise: per kg for per-kg categories, per item for per-item ones."""

    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="rates")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="+")
    rate_paise = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["offer", "category"], name="one_rate_per_category"),
            models.CheckConstraint(condition=models.Q(rate_paise__gt=0), name="rate_positive"),
        ]

    def __str__(self):
        return f"{self.rate_paise} paise for {self.category} on offer {self.offer_id}"
