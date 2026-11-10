from django.conf import settings
from django.contrib.gis.db import models
from django.db.models.functions import Lower


class PricingUnit(models.TextChoices):
    PER_KG = "per_kg", "Per kg"
    PER_ITEM = "per_item", "Per item"


class Category(models.Model):
    """Admin-managed scrap category. Deactivate instead of deleting."""

    name = models.CharField(max_length=60)
    pricing_unit = models.CharField(
        max_length=10, choices=PricingUnit.choices, default=PricingUnit.PER_KG
    )
    # Restricted categories (e.g. e-waste, batteries) accept offers only from authorised dealers.
    is_restricted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="category_name_unique_ci"),
            models.CheckConstraint(
                condition=models.Q(pricing_unit__in=PricingUnit.values),
                name="category_pricing_unit_valid",
            ),
        ]

    def __str__(self):
        return self.name


class ListingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
    PICKUP_SCHEDULED = "pickup_scheduled", "Pickup scheduled"
    PICKED_UP = "picked_up", "Picked up"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    DISPUTED = "disputed", "Disputed"
    EXPIRED = "expired", "Expired"


class Listing(models.Model):
    """Scrap a seller wants to sell.

    `status` changes only through listings.transitions.transition(), never directly.
    """

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="listings"
    )
    status = models.CharField(
        max_length=20, choices=ListingStatus.choices, default=ListingStatus.DRAFT
    )
    # Exact pickup address: personal data, shown only to the seller and the accepted dealer.
    address_line = models.CharField(max_length=200)
    # Approximate area, shown to dealers before an offer is accepted.
    locality = models.CharField(max_length=80)
    pincode = models.CharField(max_length=6)
    location = models.PointField(geography=True, srid=4326)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ListingStatus.values), name="listing_status_valid"
            ),
        ]
        indexes = [models.Index(fields=["status", "published_at"])]

    def __str__(self):
        return f"Listing {self.pk}"


class ListingItem(models.Model):
    """One category on a listing, with the declared weight (per-kg) or quantity (per-item)."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="items")
    # PROTECT: a category in use can't be deleted.
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="+")
    declared_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    declared_quantity = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["listing", "category"], name="one_item_per_category"),
            models.CheckConstraint(
                condition=(
                    models.Q(declared_weight_kg__isnull=False, declared_quantity__isnull=True)
                    | models.Q(declared_weight_kg__isnull=True, declared_quantity__isnull=False)
                ),
                name="item_weight_xor_quantity",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_weight_kg__gt=0)
                | models.Q(declared_weight_kg__isnull=True),
                name="item_weight_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_quantity__gt=0)
                | models.Q(declared_quantity__isnull=True),
                name="item_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.category} on listing {self.listing_id}"


class ListingPhoto(models.Model):
    """A cleaned photo (no EXIF) in private storage, shown only through presigned links."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField(max_length=200)
    content_type = models.CharField(max_length=20)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Photo {self.pk} on listing {self.listing_id}"
