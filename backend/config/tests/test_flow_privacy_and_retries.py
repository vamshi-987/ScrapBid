"""A whole pickup through the API: no personal data leaks, and retried steps record once."""

import io
import logging
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from audit.models import AuditLog
from notifications.models import Notification
from offers.models import Offer
from offers.tests.conftest import categories  # noqa: F401  (shared fixture)
from offers.tests.test_offers import make_dealer
from orders.models import Order, OrderStatus
from orders.tests.conftest import clock  # noqa: F401  (shared fixture)
from orders.tests.test_weighing import SLOT_START, scale_photo

pytestmark = pytest.mark.django_db

SELLER_PHONE = "+919876543210"
DEALER_PHONE = "+919811111111"
LOSER_PHONE = "+919822222222"
ADDRESS = "12-3-45 Secret Street, Ameerpet"
SHOP = ("17.4375", "78.4482")


def digits(phone):
    return phone[-10:]


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buffer, format="JPEG")
    return SimpleUploadedFile("p.jpg", buffer.getvalue())


def login(phone):
    client = APIClient()
    assert client.post("/api/auth/otp/request", {"phone": phone}, format="json").status_code == 202
    response = client.post(
        "/api/auth/otp/verify", {"phone": phone, "code": "123456"}, format="json"
    )
    assert response.status_code == 200
    return client


class Recorder:
    """Every response each side saw, kept to search for leaks afterwards."""

    def __init__(self):
        self.seen = []

    def __call__(self, who, response, *, contact=False):
        self.seen.append((who, response.content.decode(errors="replace"), contact))
        return response


def run_pickup(categories, clock, record, *, repeat=False):  # noqa: F811
    """List, offer, accept, schedule, weigh, confirm, complete. With repeat, each
    state-changing step is sent twice and the second must change nothing."""
    make_dealer(DEALER_PHONE)
    make_dealer(LOSER_PHONE)
    seller, dealer, loser = login(SELLER_PHONE), login(DEALER_PHONE), login(LOSER_PHONE)
    outsider = login("+919833333333")

    def twice(who, client, method, path, **kwargs):
        """Send once; with repeat, send again and check nothing new was recorded."""
        response = record(who, getattr(client, method)(path, **kwargs))
        if repeat:
            before = (AuditLog.objects.count(), Notification.objects.count())
            again = record(who, getattr(client, method)(path, **kwargs))
            assert again.status_code in (200, 201), (path, again.status_code, again.content)
            assert (AuditLog.objects.count(), Notification.objects.count()) == before, path
        return response

    created = record(
        "seller",
        seller.post(
            "/api/listings",
            {
                "address_line": ADDRESS,
                "locality": "Ameerpet",
                "pincode": "500016",
                "latitude": float(SHOP[0]),
                "longitude": float(SHOP[1]),
                "items": [
                    {"category": categories["paper"].pk, "declared_weight_kg": "20"},
                    {"category": categories["fridge"].pk, "declared_quantity": 2},
                ],
            },
            format="json",
        ),
    )
    listing_id = created.json()["id"]
    record("seller", seller.post(f"/api/listings/{listing_id}/photos", {"file": photo()}))
    record("seller", seller.post(f"/api/listings/{listing_id}/publish"))

    rates = {
        "rates": [
            {"category": categories["paper"].pk, "rate_paise": 1400},
            {"category": categories["fridge"].pk, "rate_paise": 150000},
        ]
    }
    for who, client in (("dealer", dealer), ("loser", loser)):
        record(who, client.get("/api/dealer/listings"))
        record(who, client.get(f"/api/dealer/listings/{listing_id}"))
        record(who, client.post(f"/api/dealer/listings/{listing_id}/offers", rates, format="json"))
    # Errors carry no personal data either.
    record("loser", loser.post(f"/api/dealer/listings/{listing_id}/offers", rates, format="json"))
    record("seller", seller.post("/api/listings", {"items": "x"}, format="json"))

    record("seller", seller.get("/api/listings"))
    record("seller", seller.get(f"/api/listings/{listing_id}"))
    record("seller", seller.get(f"/api/listings/{listing_id}/offers"))
    record("seller", seller.get("/api/notifications"))
    chosen = Offer.objects.get(listing_id=listing_id, dealer__user__phone=DEALER_PHONE)
    order_id = twice("seller", seller, "post", f"/api/offers/{chosen.pk}/accept").json()["id"]
    winner, winner_name, other, other_name = dealer, "dealer", loser, "loser"

    path = f"/api/orders/{order_id}"
    record(
        winner_name, winner.post(f"{path}/slot", {"start": SLOT_START.isoformat()}, format="json")
    )
    twice("seller", seller, "post", f"{path}/slot/confirm")
    for who, client in (("seller", seller), (winner_name, winner)):
        record(who, client.get("/api/orders"))
        record(who, client.get(path))
        record(who, client.get(f"{path}/contact"), contact=True)
        record(who, client.get("/api/notifications"))
    record(other_name, other.get(path))
    record(other_name, other.get("/api/dealer/offers"))
    record("outsider", outsider.get(path))
    record("outsider", outsider.get(f"{path}/contact"))

    clock.now = SLOT_START + timedelta(minutes=20)
    record(
        winner_name,
        winner.post(f"{path}/check-in", {"latitude": SHOP[0], "longitude": SHOP[1]}, format="json"),
    )
    lines = (
        f'[{{"category": {categories["paper"].pk}, "verified": "20"}},'
        f' {{"category": {categories["fridge"].pk}, "verified": "2"}}]'
    )

    def weigh_body():
        return {"latitude": SHOP[0], "longitude": SHOP[1], "lines": lines, "photo": scale_photo()}

    record(winner_name, winner.post(f"{path}/weigh", weigh_body()))
    if repeat:
        before = AuditLog.objects.count(), Notification.objects.count()
        assert record(winner_name, winner.post(f"{path}/weigh", weigh_body())).status_code == 200
        assert (AuditLog.objects.count(), Notification.objects.count()) == before
    code = record("seller", seller.post(f"{path}/confirm-weighing")).json()["handover_code"]
    if repeat:  # A repeat shows a fresh code; the confirmation itself is recorded once.
        confirmed_at = Order.objects.get(pk=order_id).seller_confirmed_at
        code = record("seller", seller.post(f"{path}/confirm-weighing")).json()["handover_code"]
        assert Order.objects.get(pk=order_id).seller_confirmed_at == confirmed_at
    twice(
        winner_name,
        winner,
        "post",
        f"{path}/complete",
        data={"code": code, "payment_method": "upi"},
        format="json",
    )
    for who, client in (("seller", seller), (winner_name, winner)):
        record(who, client.get(path))
        record(who, client.get(f"{path}/contact"))
    return Order.objects.get(pk=order_id)


def test_a_whole_pickup_leaks_no_personal_data(categories, clock, settings, caplog, capsys):  # noqa: F811
    settings.DEV_FIXED_OTP = "123456"
    caplog.set_level(logging.DEBUG)
    record = Recorder()

    order = run_pickup(categories, clock, record)

    assert order.status == OrderStatus.COMPLETED
    seller = [digits(SELLER_PHONE), ADDRESS]
    dealer, loser = digits(DEALER_PHONE), digits(LOSER_PHONE)
    # Logs and console output: no phone number or address at all.
    captured = capsys.readouterr()
    output = "\n".join(r.getMessage() for r in caplog.records) + captured.out + captured.err
    for secret in [*seller, dealer, loser]:
        assert secret not in output, secret
    # Responses: the two parties see each other's details only from the contact
    # endpoint while the order is open; everyone else never sees them.
    hidden = {
        ("seller", False): [dealer, loser],
        ("seller", True): [loser],
        ("dealer", False): [*seller, loser],
        ("dealer", True): [loser],
        ("loser", False): [*seller, dealer],
        ("outsider", False): [*seller, dealer, loser],
    }
    for who, body, contact in record.seen:
        for secret in hidden[(who, contact)]:
            assert secret not in body, (who, secret, body[:200])
    assert len(record.seen) > 30
    # The contact endpoint did share them with the two parties while the order was open.
    assert any(contact and digits(SELLER_PHONE) in body for who, body, contact in record.seen)


def test_retried_steps_record_nothing_twice(categories, clock, settings):  # noqa: F811
    """Accept, slot confirm, weigh, confirm-weighing and complete, each sent twice."""
    settings.DEV_FIXED_OTP = "123456"

    order = run_pickup(categories, clock, Recorder(), repeat=True)

    assert order.status == OrderStatus.COMPLETED
    assert Order.objects.count() == 1
    assert order.paid_amount_paise == 328000
    assert AuditLog.objects.filter(entity_type="listing", to_status="completed").count() == 1
    assert AuditLog.objects.filter(entity_type="listing", to_status="picked_up").count() == 1
