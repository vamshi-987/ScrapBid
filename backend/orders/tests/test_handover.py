from datetime import timedelta

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from listings.models import Listing, ListingStatus
from notifications.models import Notification, NotificationKind
from offers.tests.test_offers import client_for, make_dealer
from orders.models import Order, OrderStatus
from orders.tests.test_weighing import lines, pickup, weigh  # noqa: F401  (pickup is a fixture)

pytestmark = pytest.mark.django_db

FINAL = 28000 + 300000  # 20 kg paper at ₹14 + 2 appliances at ₹1,500


@pytest.fixture
def weighed(pickup):  # noqa: F811
    assert weigh(pickup["dealer"], pickup, lines(pickup)).status_code == 200
    return pickup


def url(data, path=""):
    return f"/api/orders/{data['order'].pk}{path}"


def confirm(data, client=None):
    return (client or data["seller"]).post(url(data, "/confirm-weighing"))


def complete(data, code, method="cash", client=None, **extra):
    body = {"code": code, "payment_method": method, **extra}
    return (client or data["dealer"]).post(url(data, "/complete"), body, format="json")


def order(data):
    return Order.objects.get(pk=data["order"].pk)


def listing_status(data):
    return Listing.objects.get(pk=data["order"].listing_id).status


def wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


# --- The full order of steps ------------------------------------------------


def test_seller_confirms_then_dealer_completes_with_the_code(weighed, clock):
    response = confirm(weighed)
    assert response.status_code == 200
    code = response.json()["handover_code"]
    assert len(code) == 6 and code.isdigit()
    assert "complete" in weighed["dealer"].get(url(weighed)).json()["actions"]

    done = complete(weighed, code, "upi")

    assert done.status_code == 200
    assert done.json()["listing_status"] == "completed"
    saved = order(weighed)
    assert (saved.status, saved.payment_method, saved.paid_amount_paise) == (
        OrderStatus.COMPLETED,
        "upi",
        FINAL,
    )
    assert saved.completed_at == clock.now
    assert saved.handover_code_hash == ""
    log = AuditLog.objects.get(to_status="completed")
    assert (log.from_status, log.details) == (
        "picked_up",
        {"final_amount_paise": FINAL, "payment_method": "upi"},
    )
    assert Notification.objects.filter(
        user=saved.seller, kind=NotificationKind.ORDER_COMPLETED
    ).exists()


def test_completing_closes_contact_details(weighed):
    complete(weighed, confirm(weighed).json()["handover_code"])

    for client in (weighed["seller"], weighed["dealer"]):
        response = client.get(url(weighed, "/contact"))
        assert response.status_code == 403
        assert response.json()["code"] == "contact_closed"


def test_the_amount_paid_is_the_servers_final_amount(weighed):
    code = confirm(weighed).json()["handover_code"]

    complete(weighed, code, paid_amount_paise=1, final_amount_paise=1)

    assert order(weighed).paid_amount_paise == FINAL


# --- No code before confirmation; only hashes stored -------------------------


def test_no_code_exists_until_the_seller_confirms(weighed):
    assert order(weighed).handover_code_hash == ""
    assert "complete" not in weighed["dealer"].get(url(weighed)).json()["actions"]

    response = complete(weighed, "123456")

    assert response.status_code == 409
    assert response.json()["code"] == "not_confirmed"
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_confirming_needs_a_weighing(pickup):  # noqa: F811
    response = confirm(pickup)

    assert response.status_code == 409
    assert response.json()["code"] == "wrong_stage"
    assert order(pickup).seller_confirmed_at is None
    assert order(pickup).handover_code_hash == ""


def test_only_the_hash_is_stored_and_the_code_is_never_shown_again(weighed):
    code = confirm(weighed).json()["handover_code"]

    saved = order(weighed)
    assert saved.handover_code_hash and code not in saved.handover_code_hash
    for client in (weighed["seller"], weighed["dealer"]):
        content = client.get(url(weighed)).content.decode()
        assert code not in content and saved.handover_code_hash not in content


def test_dealer_cannot_complete_without_the_code(weighed):
    confirm(weighed)

    for code in ("", "000000x", None):
        assert complete(weighed, code).status_code == 400
    assert listing_status(weighed) == ListingStatus.PICKED_UP


# --- Wrong, expired and too many codes ---------------------------------------


def test_a_wrong_code_counts_a_try_and_completes_nothing(weighed):
    code = confirm(weighed).json()["handover_code"]

    response = complete(weighed, wrong(code))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_code"
    assert order(weighed).handover_attempts == 1
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_five_wrong_tries_lock_the_code_until_a_fresh_one(weighed):
    code = confirm(weighed).json()["handover_code"]

    codes = [complete(weighed, wrong(code)).json()["code"] for _ in range(5)]

    assert codes == ["invalid_code"] * 4 + ["too_many_attempts"]
    assert complete(weighed, code).json()["code"] == "too_many_attempts"
    fresh = confirm(weighed).json()["handover_code"]
    assert complete(weighed, fresh).status_code == 200


def test_the_code_expires_after_15_minutes(weighed, clock):
    code = confirm(weighed).json()["handover_code"]
    start = clock.now

    clock.now = start + timedelta(minutes=15)
    assert complete(weighed, code).json()["code"] == "code_expired"
    clock.now = start + timedelta(minutes=14, seconds=59)
    assert complete(weighed, code).status_code == 200


def test_a_fresh_code_replaces_the_old_one(weighed, clock):
    old = confirm(weighed).json()["handover_code"]
    confirmed_at = order(weighed).seller_confirmed_at
    clock.now += timedelta(minutes=5)

    new = confirm(weighed).json()["handover_code"]

    assert order(weighed).seller_confirmed_at == confirmed_at
    if old != new:
        assert complete(weighed, old).json()["code"] == "invalid_code"
    assert complete(weighed, new).status_code == 200


def test_an_unknown_payment_method_is_refused_without_using_a_try(weighed):
    code = confirm(weighed).json()["handover_code"]

    for method in ("", "card", None):
        response = complete(weighed, code, method)
        assert response.json()["code"] == "invalid_payment_method"
    assert order(weighed).handover_attempts == 0


# --- Who can do what ----------------------------------------------------------


def test_only_the_seller_confirms_and_only_the_dealer_completes(weighed):
    assert confirm(weighed, weighed["dealer"]).json()["code"] == "seller_only"
    code = confirm(weighed).json()["handover_code"]
    assert complete(weighed, code, client=weighed["seller"]).json()["code"] == "dealer_only"
    outsider = client_for(make_dealer("+919822222222"))
    assert confirm(weighed, outsider).status_code == 404
    assert complete(weighed, code, client=outsider).status_code == 404
    assert confirm(weighed, APIClient()).status_code == 403
    assert complete(weighed, code, client=APIClient()).status_code == 403
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_each_side_sees_its_next_step(weighed):
    assert "confirm_weighing" in weighed["seller"].get(url(weighed)).json()["actions"]
    assert weighed["dealer"].get(url(weighed)).json()["actions"] == []

    complete(weighed, confirm(weighed).json()["handover_code"])

    for client in (weighed["seller"], weighed["dealer"]):
        assert client.get(url(weighed)).json()["actions"] == []


def test_a_short_weight_denial_has_no_handover(pickup):  # noqa: F811
    weigh(pickup["dealer"], pickup, lines(pickup, paper="15", paper_choice="deny"))

    assert confirm(pickup).json()["code"] == "wrong_stage"


# --- Retries --------------------------------------------------------------------


def test_retrying_completion_records_nothing_twice(weighed):
    code = confirm(weighed).json()["handover_code"]
    first = complete(weighed, code)
    completed_at = order(weighed).completed_at

    again = complete(weighed, wrong(code), "upi")

    assert again.status_code == 200
    assert again.json()["listing_status"] == first.json()["listing_status"] == "completed"
    saved = order(weighed)
    assert (saved.payment_method, saved.completed_at) == ("cash", completed_at)
    assert AuditLog.objects.filter(to_status="completed").count() == 1
    assert Notification.objects.filter(kind=NotificationKind.ORDER_COMPLETED).count() == 1


def test_no_new_code_after_completion(weighed):
    complete(weighed, confirm(weighed).json()["handover_code"])

    response = confirm(weighed)

    assert response.json()["code"] == "wrong_stage"
    assert order(weighed).handover_code_hash == ""


def test_seeded_weighed_pickup_completes_and_reseeding_restores_it():
    call_command("seed_demo")
    seeded = Order.objects.get(listing__address_line__startswith="Demo listing 9:")
    assert seeded.listing.status == ListingStatus.PICKED_UP
    seller, dealer = APIClient(), APIClient()
    seller.force_login(seeded.seller)
    dealer.force_login(seeded.dealer.user)
    data = {"order": seeded, "seller": seller, "dealer": dealer}

    assert complete(data, confirm(data).json()["handover_code"]).status_code == 200
    call_command("seed_demo")

    again = Order.objects.get(listing__address_line__startswith="Demo listing 9:")
    assert again.listing.status == ListingStatus.PICKED_UP
    assert (again.status, again.seller_confirmed_at, again.final_amount_paise) == (
        OrderStatus.ACTIVE,
        None,
        39000,
    )


def test_admin_order_page_hides_the_code_hash(weighed):
    confirm(weighed)
    admin = User.objects.create_user("+919800000001", is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_login(admin)

    page = client.get(f"/admin/orders/order/{weighed['order'].pk}/change/")

    assert page.status_code == 200
    assert order(weighed).handover_code_hash not in page.content.decode()
