from decimal import Decimal

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from listings.models import Listing, ListingStatus
from notifications.models import Notification, NotificationKind
from offers.tests.test_offers import client_for, make_dealer
from orders.disputes import DisputeError, resolve_dispute
from orders.models import DisputeOutcome, Order, OrderStatus
from orders.tests.test_weighing import lines, pickup, weigh  # noqa: F401  (pickup is a fixture)

pytestmark = pytest.mark.django_db

FINAL = 28000 + 300000  # 20 kg paper at ₹14 + 2 appliances at ₹1,500


@pytest.fixture
def weighed(pickup):  # noqa: F811
    assert weigh(pickup["dealer"], pickup, lines(pickup)).status_code == 200
    return pickup


@pytest.fixture
def disputed(weighed):
    assert dispute(weighed).status_code == 200
    return weighed


@pytest.fixture
def admin_user():
    return User.objects.create_user("+919800000001", is_staff=True, is_superuser=True)


def url(data, path=""):
    return f"/api/orders/{data['order'].pk}{path}"


def dispute(data, reason="My scale showed 24 kg", client=None):
    return (client or data["seller"]).post(url(data, "/dispute"), {"reason": reason}, format="json")


def order(data):
    return Order.objects.get(pk=data["order"].pk)


def listing_status(data):
    return Listing.objects.get(pk=data["order"].listing_id).status


def resolve(data, admin, outcome, note="Checked the photo", upheld=False, amounts=None):
    return resolve_dispute(
        order(data), admin, outcome=outcome, note=note, upheld=upheld, amounts=amounts
    )


# --- Opening a dispute ----------------------------------------------------------


def test_seller_disputes_the_weighing(weighed):
    response = dispute(weighed)

    assert response.status_code == 200
    assert response.json()["listing_status"] == "disputed"
    saved = order(weighed)
    assert (saved.status, saved.dispute_reason) == (OrderStatus.ACTIVE, "My scale showed 24 kg")
    assert saved.disputed_at is not None
    log = AuditLog.objects.get(to_status="disputed")
    assert (log.from_status, log.reason) == ("picked_up", "My scale showed 24 kg")
    assert Notification.objects.filter(
        user=saved.dealer.user, kind=NotificationKind.DISPUTE_OPENED
    ).exists()


def test_a_dispute_stops_completion_even_with_a_code_already_shown(weighed):
    code = weighed["seller"].post(url(weighed, "/confirm-weighing")).json()["handover_code"]

    dispute(weighed)

    done = weighed["dealer"].post(
        url(weighed, "/complete"), {"code": code, "payment_method": "cash"}, format="json"
    )
    assert done.status_code == 409
    assert done.json()["code"] == "wrong_stage"
    assert weighed["seller"].post(url(weighed, "/confirm-weighing")).status_code == 409
    assert listing_status(weighed) == ListingStatus.DISPUTED
    assert order(weighed).handover_code_hash == ""


def test_a_reason_is_required(weighed):
    for reason in ("", "   "):
        response = dispute(weighed, reason)
        assert (response.status_code, response.json()["code"]) == (400, "reason_required")
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_only_the_seller_can_dispute(weighed):
    assert dispute(weighed, client=weighed["dealer"]).json()["code"] == "seller_only"
    assert dispute(weighed, client=client_for(make_dealer("+919822222222"))).status_code == 404
    assert dispute(weighed, client=APIClient()).status_code == 403
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_a_dispute_needs_a_weighing(pickup):  # noqa: F811
    response = dispute(pickup)

    assert (response.status_code, response.json()["code"]) == (409, "wrong_stage")
    assert listing_status(pickup) == ListingStatus.PICKUP_SCHEDULED


def test_a_completed_order_cant_be_disputed(weighed):
    code = weighed["seller"].post(url(weighed, "/confirm-weighing")).json()["handover_code"]
    weighed["dealer"].post(
        url(weighed, "/complete"), {"code": code, "payment_method": "cash"}, format="json"
    )

    assert dispute(weighed).json()["code"] == "wrong_stage"
    assert listing_status(weighed) == ListingStatus.COMPLETED


def test_a_denied_pickup_cant_be_disputed(pickup):  # noqa: F811
    weigh(pickup["dealer"], pickup, lines(pickup, paper="15", paper_choice="deny"))

    assert dispute(pickup).json()["code"] == "wrong_stage"


def test_retrying_a_dispute_records_it_once(disputed):
    again = dispute(disputed, "Another reason")

    assert again.status_code == 200
    assert order(disputed).dispute_reason == "My scale showed 24 kg"
    assert AuditLog.objects.filter(to_status="disputed").count() == 1
    assert Notification.objects.filter(kind=NotificationKind.DISPUTE_OPENED).count() == 1


def test_actions_while_picked_up_and_disputed(weighed):
    assert "dispute" in weighed["seller"].get(url(weighed)).json()["actions"]
    assert "dispute" not in weighed["dealer"].get(url(weighed)).json()["actions"]

    dispute(weighed)

    for client in (weighed["seller"], weighed["dealer"]):
        assert client.get(url(weighed)).json()["actions"] == []


# --- Resolving ------------------------------------------------------------------


def test_admin_completes_at_the_measured_amounts(disputed, admin_user):
    resolve(disputed, admin_user, DisputeOutcome.COMPLETED_MEASURED, upheld=False)

    saved = order(disputed)
    assert listing_status(disputed) == ListingStatus.COMPLETED
    assert (saved.status, saved.final_amount_paise) == (OrderStatus.COMPLETED, FINAL)
    assert (saved.dispute_outcome, saved.dispute_upheld, saved.dispute_resolved_by) == (
        "completed_measured",
        False,
        admin_user,
    )
    assert saved.completed_at and saved.dispute_resolved_at
    assert (saved.payment_method, saved.paid_amount_paise) == ("", None)
    log = AuditLog.objects.get(to_status="completed")
    assert (log.from_status, log.actor, log.reason) == ("disputed", admin_user, "Checked the photo")
    assert log.details == {
        "outcome": "completed_measured",
        "upheld": False,
        "final_amount_paise": FINAL,
    }


def test_admin_completes_at_amounts_they_set(disputed, admin_user):
    c = disputed["categories"]

    resolve(
        disputed,
        admin_user,
        DisputeOutcome.COMPLETED_ADJUSTED,
        upheld=True,
        amounts={c["paper"].pk: Decimal("24"), c["fridge"].pk: Decimal("2")},
    )

    saved = order(disputed)
    paper = saved.items.get(category=c["paper"])
    assert paper.verified_amount == Decimal("20.000")  # the dealer's reading stays
    assert (paper.taken_amount, paper.amount_paise) == (Decimal("24.000"), 33600)
    assert saved.final_amount_paise == 33600 + 300000
    assert saved.dispute_upheld is True
    log = AuditLog.objects.get(to_status="completed")
    assert log.details["previous_final_amount_paise"] == FINAL
    assert log.details["final_amount_paise"] == 333600


def test_adjusted_lines_round_half_up_at_the_agreed_rate(disputed, admin_user):
    c = disputed["categories"]

    resolve(
        disputed,
        admin_user,
        DisputeOutcome.COMPLETED_ADJUSTED,
        amounts={c["paper"].pk: "0.001", c["fridge"].pk: "1"},
    )

    assert order(disputed).items.get(category=c["paper"]).amount_paise == 1  # 1.4 → 1
    assert order(disputed).final_amount_paise == 1 + 150000


@pytest.mark.parametrize(
    "amounts",
    [
        None,
        {},
        {"paper": "-1", "fridge": "2"},
        {"paper": "1.2345", "fridge": "2"},
        {"paper": "abc", "fridge": "2"},
        {"paper": "20", "fridge": "2.5"},
        {"paper": "20"},
    ],
)
def test_adjusted_amounts_must_be_valid(disputed, admin_user, amounts):
    c = disputed["categories"]
    by_id = None if amounts is None else {c[k].pk: v for k, v in amounts.items()}

    with pytest.raises(DisputeError) as error:
        resolve(disputed, admin_user, DisputeOutcome.COMPLETED_ADJUSTED, amounts=by_id)

    assert error.value.code == "invalid_amount"
    assert listing_status(disputed) == ListingStatus.DISPUTED


def test_admin_cancels_without_counting_a_cancellation(disputed, admin_user):
    resolve(disputed, admin_user, DisputeOutcome.CANCELLED, note="Scale was faulty", upheld=True)

    saved = order(disputed)
    assert listing_status(disputed) == ListingStatus.CANCELLED
    assert (saved.status, saved.cancelled_by, saved.cancel_reason) == (
        OrderStatus.CANCELLED,
        "",
        "Scale was faulty",
    )
    assert saved.dispute_upheld is True
    log = AuditLog.objects.get(to_status="cancelled")
    assert (log.actor, log.details) == (admin_user, {"outcome": "cancelled", "upheld": True})


def test_both_sides_are_notified_and_contact_closes(disputed, admin_user):
    resolve(disputed, admin_user, DisputeOutcome.COMPLETED_MEASURED)

    saved = order(disputed)
    notified = set(
        Notification.objects.filter(kind=NotificationKind.DISPUTE_RESOLVED).values_list(
            "user", flat=True
        )
    )
    assert notified == {saved.seller_id, saved.dealer.user_id}
    for client in (disputed["seller"], disputed["dealer"]):
        assert client.get(url(disputed, "/contact")).status_code == 403
        view = client.get(url(disputed)).json()
        assert (view["dispute_outcome"], view["dispute_note"]) == (
            "completed_measured",
            "Checked the photo",
        )
        assert "dispute_upheld" not in view


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"note": ""}, "note_required"),
        ({"note": "  "}, "note_required"),
        ({"upheld": None}, "upheld_required"),
        ({"outcome": "refund"}, "invalid_outcome"),
    ],
)
def test_a_decision_needs_a_note_upheld_and_an_outcome(disputed, admin_user, kwargs, code):
    args = {"outcome": DisputeOutcome.COMPLETED_MEASURED, "note": "ok", "upheld": False, **kwargs}

    with pytest.raises(DisputeError) as error:
        resolve_dispute(order(disputed), admin_user, **args)

    assert error.value.code == code
    assert listing_status(disputed) == ListingStatus.DISPUTED


def test_only_an_admin_can_resolve(disputed):
    seller = order(disputed).seller
    for user in (seller, order(disputed).dealer.user, None):
        with pytest.raises(DisputeError) as error:
            resolve(disputed, user, DisputeOutcome.COMPLETED_MEASURED)
        assert error.value.code == "admin_only"
    assert listing_status(disputed) == ListingStatus.DISPUTED


def test_only_a_disputed_order_can_be_resolved(weighed, admin_user):
    with pytest.raises(DisputeError) as error:
        resolve(weighed, admin_user, DisputeOutcome.COMPLETED_MEASURED)

    assert error.value.code == "wrong_stage"
    assert listing_status(weighed) == ListingStatus.PICKED_UP


def test_a_resolved_dispute_cant_be_resolved_again(disputed, admin_user):
    resolve(disputed, admin_user, DisputeOutcome.COMPLETED_MEASURED)

    with pytest.raises(DisputeError) as error:
        resolve(disputed, admin_user, DisputeOutcome.CANCELLED)

    assert error.value.code == "wrong_stage"
    assert AuditLog.objects.filter(from_status="disputed").count() == 1


# --- The admin page -------------------------------------------------------------


def admin_client(user):
    client = APIClient()
    client.force_login(user)
    return client


def resolve_url(data):
    return f"/admin/orders/order/{data['order'].pk}/resolve/"


def test_admin_page_shows_the_evidence_and_resolves(disputed, admin_user):
    client = admin_client(admin_user)
    c = disputed["categories"]

    page = client.get(resolve_url(disputed))
    assert page.status_code == 200
    content = page.content.decode()
    assert "My scale showed 24 kg" in content and "X-Amz-Signature=" in content
    assert (
        "Resolve dispute"
        in client.get(f"/admin/orders/order/{disputed['order'].pk}/change/").content.decode()
    )

    response = client.post(
        resolve_url(disputed),
        {
            "outcome": "completed_adjusted",
            "upheld": "yes",
            "note": "Seller's scale was certified",
            f"amount_{c['paper'].pk}": "22",
            f"amount_{c['fridge'].pk}": "2",
        },
    )

    assert response.status_code == 302
    saved = order(disputed)
    assert (saved.dispute_outcome, saved.dispute_upheld) == ("completed_adjusted", True)
    assert saved.final_amount_paise == 30800 + 300000


def test_admin_page_needs_every_adjusted_amount_and_a_note(disputed, admin_user):
    client = admin_client(admin_user)

    response = client.post(
        resolve_url(disputed), {"outcome": "completed_adjusted", "upheld": "no", "note": ""}
    )

    assert response.status_code == 200
    assert listing_status(disputed) == ListingStatus.DISPUTED
    assert response.context["form"].errors.keys() >= {
        "note",
        f"amount_{disputed['categories']['paper'].pk}",
    }


def test_non_staff_cant_open_the_resolve_page(disputed):
    client = admin_client(order(disputed).seller)

    response = client.post(
        resolve_url(disputed), {"outcome": "cancelled", "upheld": "yes", "note": "x"}
    )

    assert response.status_code == 302 and "/admin/login/" in response["Location"]
    assert listing_status(disputed) == ListingStatus.DISPUTED


def test_seeded_dispute_can_be_resolved_and_reseeding_restores_it(admin_user):
    call_command("seed_demo")
    seeded = Order.objects.get(listing__address_line__startswith="Demo listing 10:")
    assert seeded.listing.status == ListingStatus.DISPUTED
    admin = User.objects.get(phone="+919000000001")

    resolve_dispute(seeded, admin, outcome="completed_measured", note="ok", upheld=False)
    call_command("seed_demo")

    again = Order.objects.get(listing__address_line__startswith="Demo listing 10:")
    assert again.listing.status == ListingStatus.DISPUTED
    assert again.dispute_outcome == ""
