from django.utils import timezone

from notifications.models import Notification

ALLOWED_DATA_KEYS = {"listing_id", "offer_id", "order_id", "strike_id"}


def notify(user, kind, **ids):
    """Create a notification. Only record IDs may be attached, never personal data.

    Call inside the transaction that makes the change, so a rolled-back change
    leaves no notification behind.
    """
    unexpected = set(ids) - ALLOWED_DATA_KEYS
    if unexpected:
        raise ValueError(f"Notifications carry IDs only, not {sorted(unexpected)}")
    return Notification.objects.create(user=user, kind=kind, data=ids)


def mark_read(notifications):
    return notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
