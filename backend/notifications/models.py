from django.conf import settings
from django.db import models


class NotificationKind(models.TextChoices):
    NEW_OFFER = "new_offer", "New offer on your listing"
    OFFER_ACCEPTED = "offer_accepted", "Your offer was accepted"
    SLOT_PROPOSED = "slot_proposed", "A pickup time was proposed"
    SLOT_CONFIRMED = "slot_confirmed", "The pickup time is confirmed"
    RESCHEDULE_REQUESTED = "reschedule_requested", "A new pickup time was requested"
    RESCHEDULE_ACCEPTED = "reschedule_accepted", "The new pickup time was accepted"
    RESCHEDULE_DECLINED = "reschedule_declined", "The new pickup time was declined"
    ORDER_CANCELLED = "order_cancelled", "The pickup was cancelled"
    DEALER_ARRIVED = "dealer_arrived", "The dealer has arrived"
    NO_SHOW_RECORDED = "no_show_recorded", "You missed a pickup"
    PICKUP_MISSED = "pickup_missed", "The other side missed the pickup"
    WEIGHED = "weighed", "Your scrap was weighed: please check and confirm"
    WEIGHT_SHORT = "weight_short", "The dealer declined: the weight was short"
    ORDER_COMPLETED = "order_completed", "The pickup is complete"
    DISPUTE_OPENED = "dispute_opened", "The seller disputed the weighing"
    DISPUTE_RESOLVED = "dispute_resolved", "An admin resolved the dispute"
    SUSPENDED = "suspended", "You're suspended from selling and buying"
    SUSPENSION_LIFTED = "suspension_lifted", "Your suspension was lifted"


class Notification(models.Model):
    """An in-app notification. Holds only the kind and record IDs: the app writes the
    message from its own strings, and no personal data is copied here."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=30, choices=NotificationKind.choices)
    # IDs only, e.g. {"listing_id": 4, "offer_id": 9}.
    data = models.JSONField(default=dict)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return f"{self.kind} for user {self.user_id}"
