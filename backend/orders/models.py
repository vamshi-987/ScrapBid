from django.conf import settings
from django.contrib.gis.db import models

from dealers.models import DealerProfile
from listings.models import Category, Listing
from offers.models import Offer


class OrderStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class SlotStatus(models.TextChoices):
    NONE = "", "Not proposed"
    PROPOSED = "proposed", "Proposed"
    CONFIRMED = "confirmed", "Confirmed"


class Party(models.TextChoices):
    SELLER = "seller", "Seller"
    DEALER = "dealer", "Dealer"


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
    # Pickup slot (UTC). Proposed by one side, confirmed by the other.
    slot_start = models.DateTimeField(null=True, blank=True)
    slot_end = models.DateTimeField(null=True, blank=True)
    slot_status = models.CharField(
        max_length=10, choices=SlotStatus.choices, default=SlotStatus.NONE, blank=True
    )
    slot_proposed_by = models.CharField(max_length=10, choices=Party.choices, blank=True)
    # A pending request to move a confirmed slot; applies only if the other side accepts.
    reschedule_start = models.DateTimeField(null=True, blank=True)
    reschedule_end = models.DateTimeField(null=True, blank=True)
    reschedule_requested_by = models.CharField(max_length=10, choices=Party.choices, blank=True)
    # Dealer's GPS check-in during the slot: the evidence for who missed a pickup.
    dealer_checked_in_at = models.DateTimeField(null=True, blank=True)
    dealer_check_in_location = models.PointField(geography=True, srid=4326, null=True, blank=True)
    dealer_check_in_distance_m = models.PositiveIntegerField(null=True, blank=True)
    # How the order ended, if it was cancelled.
    cancelled_by = models.CharField(max_length=10, choices=Party.choices, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Set by the no-show job: which side missed the confirmed slot.
    no_show_party = models.CharField(max_length=10, choices=Party.choices, blank=True)
    # Weighing at pickup (server time and position) with the scale photo, and the result.
    weighed_at = models.DateTimeField(null=True, blank=True)
    weighing_location = models.PointField(geography=True, srid=4326, null=True, blank=True)
    weighing_distance_m = models.PositiveIntegerField(null=True, blank=True)
    scale_photo = models.FileField(max_length=200, blank=True)
    final_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
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
    # Recorded at pickup: what the scale (or count) showed, what the dealer took, how.
    verified_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    taken_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    comparison = models.CharField(max_length=10, blank=True)  # equal | over | short
    choice = models.CharField(max_length=20, blank=True)
    amount_paise = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "category"], name="one_order_item_per_category"
            ),
        ]

    def __str__(self):
        return f"{self.category} on order {self.order_id}"


class StrikeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    UPHELD = "upheld", "Upheld by an admin"
    REMOVED = "removed", "Removed by an admin"


class NoShowStrike(models.Model):
    """A missed confirmed pickup. Blocks the user until `blocked_until` unless an admin
    removes it. The user may challenge it within STRIKE_CHALLENGE_HOURS."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="no_show_strikes"
    )
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="strike")
    party = models.CharField(max_length=10, choices=Party.choices)
    blocked_until = models.DateTimeField()
    status = models.CharField(
        max_length=10, choices=StrikeStatus.choices, default=StrikeStatus.ACTIVE
    )
    challenge_reason = models.CharField(max_length=500, blank=True)
    challenged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"No-show strike {self.pk} ({self.party}) for order {self.order_id}"
