"""Rate flags and automatic suspension.

Rate flags: for each category, a dealer's average offered rate over the last
RATE_FLAG_WINDOW_DAYS (withdrawn offers left out) is compared with the average of the
other dealers' averages. More than RATE_FLAG_THRESHOLD_PERCENT above it, with at least
RATE_FLAG_MIN_PEERS other dealers, raises a flag for admins. A dealer and category get at
most one flag per window, so a closed flag isn't raised again on the next run.

Suspension: AUTO_SUSPEND_UPHELD_DISPUTES upheld disputes against a dealer since their last
suspension suspend them from selling and buying. The fee to lift it is SUSPENSION_FEE_PAISE,
doubling from the SUSPENSION_FEE_DOUBLES_FROM-th suspension on.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Q
from django.utils import timezone

from accounts.models import Flag, FlagStatus, Suspension, User
from notifications.models import NotificationKind
from notifications.services import notify
from offers.models import OfferRate, OfferStatus
from orders.models import Order
from orders.payments import add_suspension_fee, cancel_unpaid


def suspension_fee_paise(user):
    """The fee for the user's next suspension: the base, doubling from the configured one."""
    number = Suspension.objects.filter(user=user).count() + 1
    doublings = max(0, number - settings.SUSPENSION_FEE_DOUBLES_FROM + 1)
    return settings.SUSPENSION_FEE_PAISE * 2**doublings


def suspend_if_due(dealer_user):
    """Suspend the dealer if they've reached the upheld-dispute limit since their last
    suspension. Call inside the transaction that records an upheld dispute."""
    User.objects.select_for_update().get(pk=dealer_user.pk)  # one suspension at a time
    if Suspension.objects.filter(user=dealer_user, lifted_at=None).exists():
        return None
    last = Suspension.objects.filter(user=dealer_user).order_by("-created_at").first()
    upheld = Order.objects.filter(dealer__user=dealer_user, dispute_upheld=True)
    if last:
        upheld = upheld.filter(dispute_resolved_at__gt=last.created_at)
    limit = settings.AUTO_SUSPEND_UPHELD_DISPUTES
    if upheld.count() < limit:
        return None
    suspension = Suspension.objects.create(
        user=dealer_user,
        reason=f"{limit} upheld disputes",
        fee_paise=suspension_fee_paise(dealer_user),
    )
    add_suspension_fee(suspension)
    notify(dealer_user, NotificationKind.SUSPENDED)
    return suspension


def lift_suspension(suspension, admin):
    """Lift an active suspension: by an admin (which cancels an unpaid fee), or with
    admin=None once its fee is paid. Returns True if it was lifted now."""
    with transaction.atomic():
        locked = Suspension.objects.select_for_update().get(pk=suspension.pk)
        if locked.lifted_at:
            return False
        locked.lifted_at = timezone.now()
        locked.lifted_by = admin
        locked.save(update_fields=["lifted_at", "lifted_by"])
        cancel_unpaid(getattr(locked, "due", None))
        notify(locked.user, NotificationKind.SUSPENSION_LIFTED)
    return True


def flag_rate_outliers():
    """Raise rate flags for dealers far above their peers. Returns the number raised."""
    now = timezone.now()
    since = now - timedelta(days=settings.RATE_FLAG_WINDOW_DAYS)
    averages = (
        OfferRate.objects.filter(offer__created_at__gte=since)
        .exclude(offer__status=OfferStatus.WITHDRAWN)
        .values("category_id", "offer__dealer__user_id")
        .annotate(average=Avg("rate_paise"))
    )
    by_category = defaultdict(dict)
    for row in averages:
        by_category[row["category_id"]][row["offer__dealer__user_id"]] = Decimal(row["average"])

    recent = set(
        Flag.objects.filter(kind=Flag.RATE_OUTLIER)
        .filter(Q(status=FlagStatus.OPEN) | Q(created_at__gte=since))
        .values_list("user_id", "category_id")
    )
    threshold = 1 + Decimal(settings.RATE_FLAG_THRESHOLD_PERCENT) / 100
    raised = 0
    for category_id, dealers in by_category.items():
        for user_id, average in dealers.items():
            peers = [value for other, value in dealers.items() if other != user_id]
            if len(peers) < settings.RATE_FLAG_MIN_PEERS or (user_id, category_id) in recent:
                continue
            peer_average = sum(peers) / len(peers)
            if average <= peer_average * threshold:
                continue
            try:
                with transaction.atomic():
                    Flag.objects.create(
                        user_id=user_id,
                        kind=Flag.RATE_OUTLIER,
                        category_id=category_id,
                        details={
                            "rate_paise": _paise(average),
                            "peer_average_paise": _paise(peer_average),
                            "peers": len(peers),
                            "above_percent": str(
                                ((average / peer_average - 1) * 100).quantize(
                                    Decimal("0.1"), ROUND_HALF_UP
                                )
                            ),
                        },
                    )
            except IntegrityError:
                continue  # another run raised it first
            raised += 1
    return raised


def _paise(value):
    return int(value.quantize(Decimal("1"), ROUND_HALF_UP))
