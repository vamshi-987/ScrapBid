import io
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from listings.models import Category, Listing, ListingStatus
from notifications.models import Notification, NotificationKind
from offers.models import Offer
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.endings import record_no_shows
from orders.models import NoShowStrike, Order, OrderStatus
from orders.services import accept_offer
from orders.tests.conftest import IST

pytestmark = pytest.mark.django_db

SHOP = (17.4375, 78.4482)
SLOT_START = datetime(2026, 11, 12, 10, 0, tzinfo=IST)


def north(metres):
    return SHOP[0] + metres / 110_640, SHOP[1]


def scale_photo(exif=None, kind="JPEG"):
    buffer = io.BytesIO()
    options = {"exif": exif} if exif is not None else {}
    Image.new("RGB", (60, 40), "white").save(buffer, format=kind, **options)
    return SimpleUploadedFile("scale.jpg", buffer.getvalue())


@pytest.fixture
def pickup(categories, clock):
    """A confirmed pickup for 20 kg paper (₹14/kg) and 2 appliances (₹1,500 each)."""
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer("+919811111111")
    offer_id = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    order, _ = accept_offer(seller, Offer.objects.get(pk=offer_id))
    dealer_client = client_for(dealer)
    owner = APIClient()
    owner.force_login(seller)
    dealer_client.post(
        f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
    )
    owner.post(f"/api/orders/{order.pk}/slot/confirm")
    clock.now = SLOT_START + timedelta(minutes=20)
    return {"order": order, "dealer": dealer_client, "seller": owner, "categories": categories}


def lines(pickup, paper="20", paper_choice=None, fridge="2", fridge_choice=None, **extra):
    c = pickup["categories"]
    result = [
        {"category": c["paper"].pk, "verified": paper, "choice": paper_choice, **extra},
        {"category": c["fridge"].pk, "verified": fridge, "choice": fridge_choice},
    ]
    return result


def weigh(client, pickup, data_lines, *, at=SHOP, photo="default", **extra):
    payload = {
        "latitude": str(at[0]),
        "longitude": str(at[1]),
        "lines": json.dumps(data_lines),
        **extra,
    }
    if photo == "default":
        payload["photo"] = scale_photo()
    elif photo is not None:
        payload["photo"] = photo
    return client.post(f"/api/orders/{pickup['order'].pk}/weigh", payload)


def listing_status(pickup):
    return Listing.objects.get(pk=pickup["order"].listing_id).status


# --- Every settlement path through the API ---------------------------------


@pytest.mark.parametrize(
    ("paper", "choice", "paper_paise"),
    [
        ("20", None, 28000),  # equal
        ("20.5", None, 28700),  # within 5%
        ("19", None, 26600),  # exactly 5% short: still equal
        ("25", "take_excess", 35000),
        ("25", "decline_excess", 28000),
        ("15", "accept_short", 21000),
    ],
)
def test_pickup_paths(pickup, paper, choice, paper_paise):
    response = weigh(pickup["dealer"], pickup, lines(pickup, paper=paper, paper_choice=choice))

    assert response.status_code == 200
    body = response.json()
    assert body["listing_status"] == "picked_up"
    assert body["final_amount_paise"] == paper_paise + 2 * 150000
    paper_line = next(i for i in body["items"] if i["category_name"] == "Paper")
    assert (
        paper_line["verified_amount"] == Decimal(paper).quantize(Decimal("0.001")).to_eng_string()
    )
    assert paper_line["amount_paise"] == paper_paise
    assert (paper_line["choice"] or None) == choice
    assert Notification.objects.filter(
        user__phone="+919876543210", kind=NotificationKind.WEIGHED
    ).exists()
    log = AuditLog.objects.get(to_status="picked_up")
    assert log.details == {"final_amount_paise": paper_paise + 300000}


def test_denying_a_short_weight_cancels_without_penalty(pickup):
    response = weigh(pickup["dealer"], pickup, lines(pickup, paper="15", paper_choice="deny"))

    assert response.status_code == 200
    assert listing_status(pickup) == ListingStatus.CANCELLED
    order = Order.objects.get(pk=pickup["order"].pk)
    assert (order.status, order.cancel_reason, order.final_amount_paise) == (
        OrderStatus.CANCELLED,
        "WEIGHT_SHORT",
        0,
    )
    assert not NoShowStrike.objects.exists()
    assert AuditLog.objects.filter(to_status="cancelled", reason="WEIGHT_SHORT").exists()
    assert Notification.objects.filter(kind=NotificationKind.WEIGHT_SHORT).exists()


def test_counts_follow_the_rules_too(pickup):
    response = weigh(
        pickup["dealer"], pickup, lines(pickup, fridge="3", fridge_choice="decline_excess")
    )

    assert response.json()["final_amount_paise"] == 28000 + 2 * 150000


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"paper": "19.5", "paper_choice": "deny"}, "invalid_choice"),  # within tolerance
        ({"paper": "25"}, "choice_required"),
        ({"paper": "15", "paper_choice": "take_excess"}, "invalid_choice"),
        ({"fridge": "3"}, "choice_required"),  # counts are exact
        ({"paper": "abc"}, "invalid_amount"),
        ({"paper": "-1"}, "invalid_amount"),
        ({"paper": "1.2345"}, "invalid_amount"),
        ({"fridge": "2.5"}, "invalid_amount"),
    ],
)
def test_refused_weighings_record_nothing(pickup, data, code):
    response = weigh(pickup["dealer"], pickup, lines(pickup, **data))

    assert response.status_code == 400
    assert response.json()["code"] == code
    assert listing_status(pickup) == ListingStatus.PICKUP_SCHEDULED
    assert Order.objects.get(pk=pickup["order"].pk).weighed_at is None


def test_lines_must_match_the_order(pickup):
    c = pickup["categories"]
    only_paper = [{"category": c["paper"].pk, "verified": "20"}]
    duplicate = [*lines(pickup), {"category": c["paper"].pk, "verified": "20"}]
    extra = [*lines(pickup), {"category": Category.objects.create(name="Iron").pk, "verified": "1"}]

    for data in (only_paper, duplicate, extra, []):
        assert weigh(pickup["dealer"], pickup, data).json()["code"] == "lines_mismatch"


def test_client_totals_and_rates_are_ignored(pickup):
    response = weigh(
        pickup["dealer"],
        pickup,
        lines(pickup, rate_paise=1, amount_paise=1),
        final_amount_paise="1",
        estimated_total_paise="1",
    )

    assert response.json()["final_amount_paise"] == 28000 + 300000


# --- Location, photo and who ------------------------------------------------


@pytest.mark.parametrize(("metres", "ok"), [(190, True), (210, False), (5000, False)])
def test_weighing_must_be_recorded_near_the_address(pickup, metres, ok):
    _, before = (
        default_storage.listdir("scale-photos")
        if default_storage.exists("scale-photos")
        else ([], [])
    )

    response = weigh(pickup["dealer"], pickup, lines(pickup), at=north(metres))

    assert (response.status_code == 200) is ok
    if not ok:
        assert response.json()["code"] == "too_far_away"
        assert Order.objects.get(pk=pickup["order"].pk).weighed_at is None
        _, after = (
            default_storage.listdir("scale-photos")
            if default_storage.exists("scale-photos")
            else ([], [])
        )
        assert len(after) == len(before)
    else:
        assert 180 <= Order.objects.get(pk=pickup["order"].pk).weighing_distance_m <= 200


def test_a_scale_photo_is_required_and_must_be_an_image(pickup):
    assert (
        weigh(pickup["dealer"], pickup, lines(pickup), photo=None).json()["code"]
        == "photo_required"
    )
    fake = SimpleUploadedFile("scale.jpg", b"not an image")
    assert (
        weigh(pickup["dealer"], pickup, lines(pickup), photo=fake).json()["code"] == "photo_invalid"
    )


def test_scale_photo_is_stored_without_exif_and_with_server_time(pickup, clock):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[0x8825] = {2: (17.0, 26.0, 15.0)}

    weigh(pickup["dealer"], pickup, lines(pickup), photo=scale_photo(exif=exif))

    order = Order.objects.get(pk=pickup["order"].pk)
    stored = default_storage.open(order.scale_photo.name).read()
    assert b"PhoneMaker" not in stored and b"Exif" not in stored
    assert order.weighed_at == clock.now


def test_both_sides_see_the_scale_photo_link(pickup):
    weigh(pickup["dealer"], pickup, lines(pickup))
    url = f"/api/orders/{pickup['order'].pk}"

    for client in (pickup["seller"], pickup["dealer"]):
        link = client.get(url).json()["scale_photo_url"]
        assert link and "X-Amz-Signature=" in link
    outsider = client_for(make_dealer("+919822222222"))
    assert outsider.get(url).status_code == 404


def test_only_the_accepted_dealer_records_the_weighing(pickup):
    assert weigh(pickup["seller"], pickup, lines(pickup)).json()["code"] == "dealer_only"
    assert weigh(client_for(make_dealer("+919822222222")), pickup, lines(pickup)).status_code == 404
    assert weigh(APIClient(), pickup, lines(pickup)).status_code == 403


def test_weighing_needs_a_confirmed_pickup(categories, clock):
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer("+919811111111")
    offer_id = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    order, _ = accept_offer(seller, Offer.objects.get(pk=offer_id))
    data = {"order": order, "categories": categories}

    response = weigh(client_for(dealer), data, lines(data))

    assert response.status_code == 409
    assert response.json()["code"] == "wrong_stage"


# --- Retries and what follows ----------------------------------------------


def test_retrying_returns_the_recorded_result(pickup):
    first = weigh(pickup["dealer"], pickup, lines(pickup, paper="20.5"))

    again = weigh(pickup["dealer"], pickup, lines(pickup, paper="25", paper_choice="take_excess"))

    assert again.status_code == 200
    assert again.json()["final_amount_paise"] == first.json()["final_amount_paise"]
    assert Notification.objects.filter(kind=NotificationKind.WEIGHED).count() == 1
    # A retry sent after walking away still gets the recorded result.
    away = weigh(pickup["dealer"], pickup, lines(pickup), at=north(5000), photo=None)
    assert away.status_code == 200
    assert away.json()["final_amount_paise"] == first.json()["final_amount_paise"]


def test_a_weighed_pickup_is_not_a_no_show(pickup, clock):
    weigh(pickup["dealer"], pickup, lines(pickup))
    clock.now = SLOT_START + timedelta(hours=5)

    assert record_no_shows() == 0
    assert listing_status(pickup) == ListingStatus.PICKED_UP


def test_weigh_is_offered_only_until_recorded(pickup, settings):
    settings.WEIGHT_TOLERANCE_PERCENT = 3
    url = f"/api/orders/{pickup['order'].pk}"
    assert pickup["dealer"].get(url).json()["weight_tolerance_percent"] == 3
    assert "weigh" in pickup["dealer"].get(url).json()["actions"]
    assert "weigh" not in pickup["seller"].get(url).json()["actions"]

    weigh(pickup["dealer"], pickup, lines(pickup))

    assert "weigh" not in pickup["dealer"].get(url).json()["actions"]


def test_seeded_pickup_can_be_weighed_and_reseeding_restores_it():
    call_command("seed_demo")
    order = Order.objects.get(listing__address_line__startswith="Demo listing 7:", status="active")
    dealer = APIClient()
    dealer.force_login(order.dealer.user)
    data = {"order": order}
    item = order.items.get()
    at = (order.listing.location.y, order.listing.location.x)

    response = weigh(
        dealer, data, [{"category": item.category_id, "verified": "40", "choice": None}], at=at
    )

    assert response.status_code == 200
    photo = Order.objects.get(pk=order.pk).scale_photo.name
    call_command("seed_demo")
    listing = Listing.objects.get(address_line__startswith="Demo listing 7:")
    assert listing.status == ListingStatus.PICKUP_SCHEDULED
    assert not default_storage.exists(photo)
