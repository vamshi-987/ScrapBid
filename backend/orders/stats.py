"""Per-user numbers that reveal weight misuse, for admins only. Computed in the database.

Variance is (verified − declared) / declared × 100 for each weighed per-kg line,
averaged: negative means less was measured than declared. For a dealer, a strongly
negative average can mean under-weighing; for a seller, it can mean inflated
declarations. Counts of per-item goods are left out. A short-weight denial is not a
cancellation: it carries no penalty.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Q

from orders.models import Order, OrderItem, Party
from orders.settlement import WEIGHT_SHORT

VARIANCE = ExpressionWrapper(
    (F("verified_amount") - F("declared_weight_kg")) * 100 / F("declared_weight_kg"),
    output_field=DecimalField(max_digits=20, decimal_places=6),
)


def _one_decimal(value):
    return None if value is None else Decimal(value).quantize(Decimal("0.1"), ROUND_HALF_UP)


def _variance(orders):
    lines = OrderItem.objects.filter(
        order__in=orders, declared_weight_kg__isnull=False, verified_amount__isnull=False
    )
    return _one_decimal(lines.aggregate(v=Avg(VARIANCE))["v"])


def cancellation_count(user):
    """Pickups this user cancelled, as seller or as dealer (short-weight denials excluded)."""
    return (
        Order.objects.filter(
            Q(seller=user, cancelled_by=Party.SELLER)
            | Q(dealer__user=user, cancelled_by=Party.DEALER)
        )
        .exclude(cancel_reason=WEIGHT_SHORT)
        .count()
    )


def user_stats(user):
    as_dealer = Order.objects.filter(dealer__user=user)
    weighed = as_dealer.filter(weighed_at__isnull=False)
    weighed_count = weighed.count()
    denials = weighed.filter(cancel_reason=WEIGHT_SHORT).count()
    disputed = as_dealer.filter(disputed_at__isnull=False)
    return {
        "dealer_weighed": weighed_count,
        "dealer_denials": denials,
        "dealer_denial_rate_percent": (
            _one_decimal(Decimal(denials) * 100 / weighed_count) if weighed_count else None
        ),
        "dealer_variance_percent": _variance(weighed),
        "dealer_disputes": disputed.count(),
        "dealer_disputes_upheld": disputed.filter(dispute_upheld=True).count(),
        "seller_variance_percent": _variance(
            Order.objects.filter(seller=user, weighed_at__isnull=False)
        ),
        "cancellations": cancellation_count(user),
        "no_shows": user.no_show_strikes.exclude(status="removed").count(),
    }
