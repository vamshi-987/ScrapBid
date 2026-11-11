from django.conf import settings
from django.db import models


class NotificationKind(models.TextChoices):
    NEW_OFFER = "new_offer", "New offer on your listing"


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
