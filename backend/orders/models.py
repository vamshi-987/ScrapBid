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


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"


class DisputeOutcome(models.TextChoices):
    COMPLETED_MEASURED = "completed_measured", "Completed at the measured amounts"
    COMPLETED_ADJUSTED = "completed_adjusted", "Completed at amounts set by the admin"
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
    # Handover: the seller confirms, then the dealer enters the seller's code (hash only).
    seller_confirmed_at = models.DateTimeField(null=True, blank=True)
    handover_code_hash = models.CharField(max_length=64, blank=True)
    handover_code_expires_at = models.DateTimeField(null=True, blank=True)
    handover_attempts = models.PositiveSmallIntegerField(default=0)
    # Payment happens offline at pickup; the app only records it.
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)
    paid_amount_paise = models.PositiveBigIntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Dispute: the seller disputes the weighing; an admin resolves it.
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.CharField(max_length=300, blank=True)
    dispute_outcome = models.CharField(max_length=20, choices=DisputeOutcome.choices, blank=True)
    dispute_upheld = models.BooleanField(null=True, blank=True)  # True: the seller was right
    dispute_note = models.CharField(max_length=500, blank=True)
    dispute_resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
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
    """A missed confirmed pickup. Comes with a penalty (a Due); the user is blocked until
    it's paid, unless an admin removes the strike. The user may challenge it within
    STRIKE_CHALLENGE_HOURS."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="no_show_strikes"
    )
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="strike")
    party = models.CharField(max_length=10, choices=Party.choices)
    # Set only on strikes from before penalties replaced the fixed block; no longer used.
    blocked_until = models.DateTimeField(null=True, blank=True)
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


class DueKind(models.TextChoices):
    NO_SHOW_PENALTY = "no_show_penalty", "No-show penalty"
    SUSPENSION_FEE = "suspension_fee", "Suspension fee"


class DueStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class Due(models.Model):
    """Money a user owes the platform: a no-show penalty or a suspension fee. Paid online
    through the payment gateway; marked paid only after the gateway confirms it."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dues"
    )
    kind = models.CharField(max_length=20, choices=DueKind.choices)
    amount_paise = models.PositiveBigIntegerField()
    status = models.CharField(max_length=10, choices=DueStatus.choices, default=DueStatus.UNPAID)
    strike = models.OneToOneField(
        NoShowStrike, on_delete=models.PROTECT, null=True, blank=True, related_name="due"
    )
    suspension = models.OneToOneField(
        "accounts.Suspension", on_delete=models.PROTECT, null=True, blank=True, related_name="due"
    )
    # The gateway's payment link for this due, and the payment once confirmed.
    link_id = models.CharField(max_length=64, blank=True, db_index=True)
    link_url = models.URLField(max_length=300, blank=True)
    payment_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="no_show_penalty", strike__isnull=False, suspension__isnull=True)
                    | models.Q(kind="suspension_fee", suspension__isnull=False, strike__isnull=True)
                ),
                name="due_has_its_source",
            ),
            models.CheckConstraint(condition=models.Q(amount_paise__gt=0), name="due_positive"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.pk} for user {self.user_id}"
