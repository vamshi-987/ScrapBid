from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.models import User
from offers.models import Offer
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on
from orders.disputes import open_dispute, resolve_dispute
from orders.endings import cancel_order
from orders.models import DisputeOutcome, NoShowStrike, Party
from orders.scheduling import confirm_slot, propose_slot
from orders.services import accept_offer
from orders.stats import cancellation_count, user_stats
from orders.tests.conftest import IST
from orders.tests.test_weighing import SHOP, scale_photo
from orders.weighing import record_weighing

pytestmark = pytest.mark.django_db

SLOT_START = datetime(2026, 11, 12, 10, 0, tzinfo=IST)


def deal(categories, seller, dealer, items):
    """An accepted offer with a confirmed slot, for `items` [(name, kg, count)]."""
    listing = make_listing(categories, seller=seller, items=items)
    payload = {
        "rates": [
            {"category": categories[name].pk, "rate_paise": 1400 if kg else 150000}
            for name, kg, _ in items
        ]
    }
    offer_id = offer_on(client_for(dealer), listing, payload).json()["id"]
    order, _ = accept_offer(seller, Offer.objects.get(pk=offer_id))
    propose_slot(order, dealer.user, SLOT_START)
    confirm_slot(order, seller)
    return order


def weigh(order, verified, choice=None):
    """Weigh every line of the order at the given verified amounts {name: value}."""
    record_weighing(
        order,
        order.dealer.user,
        latitude=SHOP[0],
        longitude=SHOP[1],
        photo=scale_photo(),
        lines=[
            {
                "category": item.category_id,
                "verified": verified[item.category.name.lower()],
                "choice": choice.get(item.category.name.lower()) if choice else None,
            }
            for item in order.items.select_related("category")
        ],
    )


@pytest.fixture
def history(categories, clock):
    """A hand-calculated history for one dealer and two sellers (paper is per kg).

    A (seller 1): 20 kg → 19 kg (−5%), plus 2 appliances (counts, left out); disputed,
        not upheld.
    B (seller 1): 20 kg → 15 kg, denied (−25%).
    C (seller 2): 40 kg → 44 kg, excess taken (+10%), then disputed and upheld.
    D (seller 2): scheduled, never weighed; the seller cancels it.
    E (seller 1): the dealer cancels before pickup.
    Dealer: 3 weighed, 1 denial (33.3%), variance (−5 − 25 + 10) / 3 = −6.7%,
    2 disputes (1 upheld). Seller 1 variance −15.0%; seller 2 +10.0%.
    """
    seller1 = User.objects.create_user("+919876500001")
    seller2 = User.objects.create_user("+919876500002")
    dealer = make_dealer("+919811111111")
    admin = User.objects.create_user("+919800000001", is_staff=True)

    a = deal(categories, seller1, dealer, [("paper", "20", None), ("fridge", None, 2)])
    clock.now = SLOT_START + timedelta(minutes=10)
    weigh(a, {"paper": "19", "appliance": "2"})
    open_dispute(a, seller1, "Looks light")
    resolve_dispute(a, admin, outcome=DisputeOutcome.COMPLETED_MEASURED, note="ok", upheld=False)
    clock.now = SLOT_START - timedelta(days=1)
    b = deal(categories, seller1, dealer, [("paper", "20", None)])
    weigh(b, {"paper": "15"}, {"paper": "deny"})
    c = deal(categories, seller2, dealer, [("paper", "40", None)])
    weigh(c, {"paper": "44"}, {"paper": "take_excess"})
    open_dispute(c, seller2, "My scale showed 48 kg")
    resolve_dispute(c, admin, outcome=DisputeOutcome.COMPLETED_MEASURED, note="ok", upheld=True)
    d = deal(categories, seller2, dealer, [("paper", "10", None)])
    cancel_order(d, seller2, "Sold elsewhere")
    e = deal(categories, seller1, dealer, [("paper", "10", None)])
    cancel_order(e, dealer.user, "Truck broke down")
    return {"seller1": seller1, "seller2": seller2, "dealer": dealer.user, "admin": admin}


def test_dealer_stats_match_the_hand_calculation(history):
    stats = user_stats(history["dealer"])

    assert stats["dealer_weighed"] == 3
    assert stats["dealer_denials"] == 1
    assert stats["dealer_denial_rate_percent"] == Decimal("33.3")
    assert stats["dealer_variance_percent"] == Decimal("-6.7")
    assert (stats["dealer_disputes"], stats["dealer_disputes_upheld"]) == (2, 1)
    assert stats["cancellations"] == 1  # E only: the short-weight denial isn't one


def test_seller_variance_matches_the_hand_calculation(history):
    assert user_stats(history["seller1"])["seller_variance_percent"] == Decimal("-15.0")
    assert user_stats(history["seller2"])["seller_variance_percent"] == Decimal("10.0")
    assert user_stats(history["seller2"])["cancellations"] == 1  # D


def test_a_short_weight_denial_is_not_a_cancellation(history):
    assert cancellation_count(history["dealer"]) == 1


def test_no_shows_leave_out_removed_strikes(history):
    order = history["seller1"].orders_as_seller.filter(cancel_reason="WEIGHT_SHORT").get()
    NoShowStrike.objects.create(
        user=history["seller1"], order=order, party=Party.SELLER, blocked_until=SLOT_START
    )
    other = history["seller1"].orders_as_seller.exclude(pk=order.pk).first()
    NoShowStrike.objects.create(
        user=history["seller1"],
        order=other,
        party=Party.SELLER,
        blocked_until=SLOT_START,
        status="removed",
    )

    assert user_stats(history["seller1"])["no_shows"] == 1


def test_a_user_with_no_orders_shows_zeros():
    stats = user_stats(User.objects.create_user("+919876500009"))

    assert stats == {
        "dealer_weighed": 0,
        "dealer_denials": 0,
        "dealer_denial_rate_percent": None,
        "dealer_variance_percent": None,
        "dealer_disputes": 0,
        "dealer_disputes_upheld": 0,
        "seller_variance_percent": None,
        "cancellations": 0,
        "no_shows": 0,
    }


def test_stats_use_a_fixed_number_of_queries(history, count_queries):
    newcomer = User.objects.create_user("+919876500010")

    busy = count_queries(lambda: user_stats(history["dealer"]))
    idle = count_queries(lambda: user_stats(newcomer))

    assert busy == idle


# --- Admin only -----------------------------------------------------------------


def page_for(user, viewer):
    client = APIClient()
    client.force_login(viewer)
    return client.get(f"/admin/accounts/user/{user.pk}/change/")


def test_admins_see_the_stats_on_the_user_page(history):
    superuser = User.objects.create_user("+919800000002", is_staff=True, is_superuser=True)

    content = page_for(history["dealer"], superuser).content.decode()

    assert "Misuse stats" in content
    assert "33.3%" in content and "-6.7%" in content and "2 (1 upheld)" in content
    seller_page = page_for(history["seller1"], superuser).content.decode()
    assert "-15.0%" in seller_page and "no data" in seller_page


def test_admin_user_list_counts_cancellations_without_denials(history):
    superuser = User.objects.create_user("+919800000002", is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_login(superuser)

    page = client.get("/admin/accounts/user/")

    assert page.status_code == 200
    rows = {row.pk: row for row in page.context["cl"].result_list}
    admin_view = page.context["cl"].model_admin
    assert admin_view.cancellations(rows[history["dealer"].pk]) == 1


def test_non_staff_cant_see_the_stats(history):
    response = page_for(history["dealer"], history["seller1"])

    assert response.status_code == 302 and "/admin/login/" in response["Location"]


def test_seeded_history_gives_the_hand_calculated_stats():
    call_command("seed_demo")
    call_command("seed_demo")

    def stats(phone):
        return user_stats(User.objects.get(phone=phone))

    # Dealer 6: iron 30 → 29 kg (−3.33%, disputed) and cardboard 20 → 15 kg (−25%, denied).
    dealer = stats("+919000000006")
    assert (dealer["dealer_weighed"], dealer["dealer_denials"]) == (2, 1)
    assert dealer["dealer_denial_rate_percent"] == Decimal("50.0")
    assert dealer["dealer_variance_percent"] == Decimal("-14.2")
    assert (dealer["dealer_disputes"], dealer["cancellations"]) == (1, 0)
    # Dealer 7: paper 25 → 26 kg (+4%), and one cancellation before pickup.
    other = stats("+919000000007")
    assert (other["dealer_variance_percent"], other["cancellations"]) == (Decimal("4.0"), 1)
    assert stats("+919000000002")["seller_variance_percent"] == Decimal("-14.2")
    assert stats("+919000000003")["seller_variance_percent"] == Decimal("4.0")
