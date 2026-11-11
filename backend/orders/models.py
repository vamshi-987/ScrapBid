from django.conf import settings
from django.db import models

from dealers.models import DealerProfile
from listings.models import Category, Listing
from offers.models import Offer


class OrderStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Order(models.Model):
    """Created when a seller accepts an offer. Holds a snapshot of what was agreed:
    the declared amounts and the accepted rates. The listing's status tracks the
    pickup; the order keeps the deal and (later) the pickup details."""

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="orders")
    # One order per accepted offer; also what makes a retried accept return the same order.
    offer = models.OneToOneField(Offer, on_delete=models.PROTECT, related_name="order")
    dealer = models.ForeignKey(DealerProfile, on_delete=models.PROTECT, related_name="orders")
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders_as_seller"
    )
    status = models.CharField(
        max_length=10, choices=OrderStatus.choices, default=OrderStatus.ACTIVE
    )
    estimated_total_paise = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(status="active"),
                name="one_active_order_per_listing",
            ),
        ]

    def __str__(self):
        return f"Order {self.pk} for listing {self.listing_id}"


class OrderItem(models.Model):
    """Snapshot of one category at acceptance: declared amount and agreed rate (paise)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="+")
    declared_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    declared_quantity = models.PositiveIntegerField(null=True, blank=True)
    rate_paise = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "category"], name="one_order_item_per_category"
            ),
        ]

    def __str__(self):
        return f"{self.category} on order {self.order_id}"
