from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    """Raised on any attempt to change or remove an audit record."""


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit records can't be changed.")

    def delete(self):
        raise AppendOnlyError("Audit records can't be deleted.")


class AuditLog(models.Model):
    """Append-only record of status changes: who, when, from, to and why."""

    entity_type = models.CharField(max_length=30)
    entity_id = models.PositiveBigIntegerField()
    # Null means the system made the change (for example, a scheduled expiry).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    reason = models.CharField(max_length=300, blank=True)
    # Extra facts about the change, such as old and new pickup slots.
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["entity_type", "entity_id", "created_at"])]

    def __str__(self):
        return f"{self.entity_type} {self.entity_id}: {self.from_status} → {self.to_status}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("Audit records can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit records can't be deleted.")
