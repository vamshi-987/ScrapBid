from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from listings.models import Listing, ListingStatus
from offers.models import Offer, OfferStatus
from orders.endings import record_no_shows
from orders.models import Due, DueStatus, NoShowStrike, Order, StrikeStatus
from orders.payments import has_unpaid_penalty

pytestmark = pytest.mark.django_db

MISSED = "Demo listing 12:"
MISSING_DEALER = "+919000000009"


def client(phone):
    api = APIClient()
    api.force_login(User.objects.get(phone=phone))
    return api


def demo_strike():
    return NoShowStrike.objects.get(order__listing__address_line__startswith=MISSED)


def test_seed_has_a_missed_pickup_to_challenge_or_pay(django_capture_on_commit_callbacks):
    call_command("seed_demo")
    strike = demo_strike()
    call_command("seed_demo")

    assert demo_strike().pk == strike.pk  # kept on a second run
    assert (strike.user.phone, strike.party, strike.status) == (MISSING_DEALER, "dealer", "active")
    due = Due.objects.get(strike=strike)
    assert (due.amount_paise, due.status) == (10000, DueStatus.UNPAID)
    # The listing reopened, and the other dealer's offer is selectable again.
    listing = Listing.objects.get(address_line__startswith=MISSED)
    assert listing.status == ListingStatus.OPEN
    restored = Offer.objects.get(listing=listing, dealer__user__phone="+919000000006")
    assert restored.status == OfferStatus.ACTIVE and restored.is_live
    seller_view = client("+919000000003").get(f"/api/listings/{listing.pk}/offers").json()
    assert [offer["id"] for offer in seller_view] == [restored.pk]
    # The dealer who missed it can challenge the strike and can't offer until paid.
    dealer = client(MISSING_DEALER)
    assert dealer.get("/api/strikes").json()[0]["can_challenge"] is True
    assert dealer.get("/api/auth/me").json()["blocked"] is True
    other = Listing.objects.get(address_line__startswith="Demo listing 1:")
    rates = {"rates": [{"category": i.category_id, "rate_paise": 1000} for i in other.items.all()]}
    refused = dealer.post(f"/api/dealer/listings/{other.pk}/offers", rates, format="json")
    assert refused.json()["code"] == "blocked"


@pytest.mark.parametrize("used", ["challenged", "paid", "removed", "too_old"])
def test_reseeding_gives_back_a_fresh_strike_once_it_is_used(
    used, django_capture_on_commit_callbacks
):
    call_command("seed_demo")
    strike = demo_strike()
    if used == "challenged":
        NoShowStrike.objects.filter(pk=strike.pk).update(challenged_at=timezone.now())
    elif used == "paid":
        Due.objects.filter(strike=strike).update(status=DueStatus.PAID)
    elif used == "removed":
        NoShowStrike.objects.filter(pk=strike.pk).update(status=StrikeStatus.REMOVED)
        Due.objects.filter(strike=strike).update(status=DueStatus.CANCELLED)
    else:
        NoShowStrike.objects.filter(pk=strike.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )

    call_command("seed_demo")

    fresh = demo_strike()
    assert fresh.pk != strike.pk
    assert (fresh.status, fresh.challenged_at) == (StrikeStatus.ACTIVE, None)
    assert Due.objects.get(strike=fresh).status == DueStatus.UNPAID
    assert NoShowStrike.objects.count() == 1


def test_reseeding_after_the_scheduler_records_a_no_show_on_a_demo_pickup(
    django_capture_on_commit_callbacks,
):
    """A seeded slot is "tomorrow" at seed time; once it has passed, the no-show job
    records a strike on it. The next seed must still restore the demo pickup."""
    call_command("seed_demo")
    listing = Listing.objects.get(address_line__startswith="Demo listing 7:")
    order = listing.orders.get(status="active")
    now = timezone.now()
    Order.objects.filter(pk=order.pk).update(
        slot_start=now - timedelta(hours=3), slot_end=now - timedelta(hours=1)
    )
    assert record_no_shows() == 1
    dealer = order.dealer.user
    assert has_unpaid_penalty(dealer)

    call_command("seed_demo")

    rebuilt = Listing.objects.get(address_line__startswith="Demo listing 7:")
    assert rebuilt.status == ListingStatus.PICKUP_SCHEDULED
    assert rebuilt.orders.get(status="active").slot_start > timezone.now()
    assert not has_unpaid_penalty(dealer)
    assert not NoShowStrike.objects.filter(user=dealer).exists()
