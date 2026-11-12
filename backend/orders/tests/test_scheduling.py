from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from listings.models import ListingStatus
from notifications.models import Notification, NotificationKind
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import Order, SlotStatus
from orders.scheduling import SlotError, validate_slot
from orders.services import accept_offer

pytestmark = pytest.mark.django_db

IST = ZoneInfo("Asia/Kolkata")
# A fixed "now": Wednesday 11 Nov 2026, 09:00 IST.
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=IST)


def ist(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


# --- Slot rules ------------------------------------------------------------


@pytest.mark.parametrize(
    "start",
    [
        ist(11, 11, 0),  # exactly 2 hours' notice
        ist(11, 12, 30),
        ist(12, 8, 0),  # opening hour
        ist(12, 18, 0),  # ends exactly at 20:00
        ist(18, 8, 30),  # 6 days 23.5 hours ahead
    ],
)
def test_valid_slots(start):
    assert validate_slot(start, now=NOW) == (start, start + timedelta(hours=2))


@pytest.mark.parametrize(
    ("start", "code"),
    [
        (ist(12, 10, 15), "misaligned"),
        (ist(12, 10, 0) + timedelta(seconds=1), "misaligned"),
        (ist(12, 7, 30), "outside_hours"),
        (ist(12, 18, 30), "outside_hours"),  # would end at 20:30
        (ist(12, 22, 0), "outside_hours"),
        (ist(11, 10, 30), "too_soon"),  # 1.5 hours' notice
        (ist(11, 8, 0), "too_soon"),  # within pickup hours, but already passed
        (ist(18, 9, 30), "too_far"),  # 7 days and 30 minutes ahead
    ],
)
def test_invalid_slots(start, code):
    with pytest.raises(SlotError) as error:
        validate_slot(start, now=NOW)
    assert error.value.code == code


def test_slot_hours_come_from_config(settings):
    settings.PICKUP_DAY_START_HOUR = 10
    settings.PICKUP_SLOT_HOURS = 3

    with pytest.raises(SlotError):
        validate_slot(ist(12, 9, 0), now=NOW)
    assert validate_slot(ist(12, 10, 0), now=NOW)[1] == ist(12, 13, 0)


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def deal(categories, clock):
    """An accepted offer: (order, seller client, dealer client)."""
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer()
    offer_id = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    order, _ = accept_offer(seller, listing.offers.get(pk=offer_id))
    owner = APIClient()
    owner.force_login(seller)
    return order, owner, client_for(dealer)


def url(order, action=""):
    return f"/api/orders/{order.pk}{action}"


def propose(client, order, start):
    return client.post(url(order, "/slot"), {"start": start.isoformat()}, format="json")


def reschedule(client, order, start):
    return client.post(url(order, "/reschedule"), {"start": start.isoformat()}, format="json")


def scheduled(deal, start=None):
    """The dealer proposes a time and the seller confirms it."""
    order, seller, dealer = deal
    start = start or ist(12, 10, 0)
    propose(dealer, order, start)
    seller.post(url(order, "/slot/confirm"))
    return order, seller, dealer


def kinds_for(user):
    return list(Notification.objects.filter(user=user).values_list("kind", flat=True))


# --- Propose and confirm ---------------------------------------------------


def test_dealer_proposes_and_seller_is_notified(deal):
    order, _, dealer = deal

    response = propose(dealer, order, ist(12, 10, 0))

    assert response.status_code == 200
    body = response.json()
    assert (body["slot_status"], body["slot_proposed_by"]) == ("proposed", "dealer")
    assert datetime.fromisoformat(body["slot_start"]) == ist(12, 10, 0)
    assert datetime.fromisoformat(body["slot_end"]) == ist(12, 12, 0)
    assert body["listing_status"] == "offer_accepted"
    assert NotificationKind.SLOT_PROPOSED in kinds_for(order.seller)


def test_seller_cannot_propose_first_but_can_counter_propose(deal):
    order, seller, dealer = deal

    assert propose(seller, order, ist(12, 10, 0)).json()["code"] == "wrong_stage"
    propose(dealer, order, ist(12, 10, 0))
    response = propose(seller, order, ist(12, 14, 0))

    assert response.json()["slot_proposed_by"] == "seller"
    assert NotificationKind.SLOT_PROPOSED in kinds_for(order.dealer.user)


def test_invalid_proposals_return_400(deal):
    order, _, dealer = deal

    response = propose(dealer, order, ist(12, 7, 0))

    assert response.status_code == 400
    assert response.json()["code"] == "outside_hours"
    assert Order.objects.get(pk=order.pk).slot_status == SlotStatus.NONE


def test_seller_confirms_and_the_pickup_is_scheduled(deal):
    order, seller, dealer = deal
    propose(dealer, order, ist(12, 10, 0))

    response = seller.post(url(order, "/slot/confirm"))

    assert response.status_code == 200
    assert response.json()["slot_status"] == "confirmed"
    assert response.json()["listing_status"] == "pickup_scheduled"
    log = AuditLog.objects.filter(to_status="pickup_scheduled").get()
    assert log.actor == order.seller
    assert log.details == {
        "slot_start": ist(12, 10, 0).astimezone(UTC).isoformat(),
        "slot_end": ist(12, 12, 0).astimezone(UTC).isoformat(),
    }
    assert NotificationKind.SLOT_CONFIRMED in kinds_for(order.dealer.user)


def test_dealer_confirms_the_sellers_counter_proposal(deal):
    order, seller, dealer = deal
    propose(dealer, order, ist(12, 10, 0))
    propose(seller, order, ist(12, 15, 0))

    response = dealer.post(url(order, "/slot/confirm"))

    assert response.json()["listing_status"] == "pickup_scheduled"
    assert datetime.fromisoformat(response.json()["slot_start"]) == ist(12, 15, 0)


def test_nobody_confirms_their_own_proposal(deal):
    order, seller, dealer = deal
    propose(dealer, order, ist(12, 10, 0))

    assert dealer.post(url(order, "/slot/confirm")).json()["code"] == "own_proposal"
    propose(seller, order, ist(12, 14, 0))
    assert seller.post(url(order, "/slot/confirm")).json()["code"] == "own_proposal"


def test_cannot_confirm_without_a_proposal_or_after_the_slot_started(deal, clock):
    order, seller, dealer = deal
    assert seller.post(url(order, "/slot/confirm")).json()["code"] == "not_proposed"

    propose(dealer, order, ist(11, 12, 0))
    clock.now = ist(11, 12, 0)

    assert seller.post(url(order, "/slot/confirm")).json()["code"] == "slot_passed"
    assert Order.objects.get(pk=order.pk).listing.status == ListingStatus.OFFER_ACCEPTED


def test_proposing_again_after_confirmation_is_refused(deal):
    order, _, dealer = scheduled(deal)

    assert propose(dealer, order, ist(13, 10, 0)).json()["code"] == "wrong_stage"


# --- Rescheduling ----------------------------------------------------------


def test_accepted_reschedule_moves_the_slot_and_is_audited(deal):
    order, seller, dealer = scheduled(deal)

    assert reschedule(dealer, order, ist(13, 16, 0)).status_code == 200
    assert NotificationKind.RESCHEDULE_REQUESTED in kinds_for(order.seller)
    response = seller.post(url(order, "/reschedule/accept"))

    body = response.json()
    assert datetime.fromisoformat(body["slot_start"]) == ist(13, 16, 0)
    assert body["reschedule_requested_by"] == "" and body["reschedule_start"] is None
    assert body["listing_status"] == "pickup_scheduled"
    log = AuditLog.objects.get(reason="Pickup rescheduled")
    utc = UTC
    assert log.details == {
        "old_start": ist(12, 10, 0).astimezone(utc).isoformat(),
        "old_end": ist(12, 12, 0).astimezone(utc).isoformat(),
        "new_start": ist(13, 16, 0).astimezone(utc).isoformat(),
        "new_end": ist(13, 18, 0).astimezone(utc).isoformat(),
        "requested_by": "dealer",
        "accepted_by": "seller",
    }
    assert NotificationKind.RESCHEDULE_ACCEPTED in kinds_for(order.dealer.user)


def test_declined_reschedule_keeps_the_confirmed_slot(deal):
    order, seller, dealer = scheduled(deal)
    reschedule(seller, order, ist(13, 16, 0))

    response = dealer.post(url(order, "/reschedule/decline"))

    assert datetime.fromisoformat(response.json()["slot_start"]) == ist(12, 10, 0)
    assert response.json()["reschedule_requested_by"] == ""
    assert not AuditLog.objects.filter(reason="Pickup rescheduled").exists()
    assert NotificationKind.RESCHEDULE_DECLINED in kinds_for(order.seller)


def test_requests_close_two_hours_before_the_slot(deal, clock):
    order, seller, _ = scheduled(deal, start=ist(12, 14, 0))

    clock.now = ist(12, 12, 0)  # exactly 2 hours before: still allowed
    assert reschedule(seller, order, ist(13, 10, 0)).status_code == 200
    clock.now = ist(12, 12, 0) + timedelta(seconds=1)
    response = reschedule(seller, order, ist(13, 11, 0))

    assert response.status_code == 409
    assert response.json()["code"] == "too_late"


def test_unanswered_request_lapses_when_the_slot_starts(deal, clock):
    order, seller, dealer = scheduled(deal, start=ist(12, 14, 0))
    reschedule(dealer, order, ist(13, 10, 0))
    clock.now = ist(12, 14, 0)

    response = seller.post(url(order, "/reschedule/accept"))

    assert response.json()["code"] == "too_late"
    assert Order.objects.get(pk=order.pk).slot_start == ist(12, 14, 0)


def test_one_pending_request_at_a_time(deal):
    order, seller, dealer = scheduled(deal)
    reschedule(dealer, order, ist(13, 10, 0))

    assert reschedule(seller, order, ist(14, 10, 0)).json()["code"] == "request_pending"
    # The requester may replace their own request.
    response = reschedule(dealer, order, ist(14, 12, 0))
    assert datetime.fromisoformat(response.json()["reschedule_start"]) == ist(14, 12, 0)


def test_answering_rules(deal):
    order, seller, dealer = scheduled(deal)
    assert seller.post(url(order, "/reschedule/accept")).json()["code"] == "no_request"

    reschedule(seller, order, ist(13, 10, 0))

    assert seller.post(url(order, "/reschedule/accept")).json()["code"] == "own_request"
    assert seller.post(url(order, "/reschedule/decline")).json()["code"] == "own_request"


def test_reschedule_needs_a_confirmed_slot(deal):
    order, seller, dealer = deal
    propose(dealer, order, ist(12, 10, 0))

    assert reschedule(seller, order, ist(13, 10, 0)).json()["code"] == "wrong_stage"


def test_new_reschedule_slot_follows_the_slot_rules(deal):
    order, seller, _ = scheduled(deal)

    assert reschedule(seller, order, ist(13, 19, 0)).json()["code"] == "outside_hours"


# --- Who can see and act ---------------------------------------------------


def test_only_the_seller_and_accepted_dealer_can_see_or_act(deal):
    order, _, _ = deal
    outsider = APIClient()
    outsider.force_login(User.objects.create_user("+919123456789"))
    other_dealer = client_for(make_dealer("+919822222222"))

    for client in (outsider, other_dealer):
        assert client.get(url(order)).status_code == 404
        assert propose(client, order, ist(12, 10, 0)).status_code == 404
        assert client.post(url(order, "/slot/confirm")).status_code == 404
        assert client.get("/api/orders").json() == []
    assert APIClient().get(url(order)).status_code == 403


def test_each_side_sees_its_role_and_available_actions(deal):
    order, seller, dealer = deal

    assert (dealer.get(url(order)).json()["my_role"], dealer.get(url(order)).json()["actions"]) == (
        "dealer",
        ["propose", "cancel"],
    )
    assert seller.get(url(order)).json()["actions"] == ["cancel"]
    propose(dealer, order, ist(12, 10, 0))
    assert seller.get(url(order)).json()["actions"] == ["propose", "confirm", "cancel"]
    seller.post(url(order, "/slot/confirm"))
    assert dealer.get(url(order)).json()["actions"] == ["cancel", "request_reschedule"]
    reschedule(dealer, order, ist(13, 10, 0))
    assert seller.get(url(order)).json()["actions"] == [
        "cancel",
        "decline_reschedule",
        "accept_reschedule",
    ]


def test_pickup_has_no_exact_address_or_phone(deal):
    order, seller, dealer = deal

    for client in (seller, dealer):
        content = client.get(url(order)).content.decode()
        assert "Secret Street" not in content and "9876543210" not in content


def test_orders_list_shows_mine_from_either_side(deal):
    order, seller, dealer = deal

    assert [o["id"] for o in seller.get("/api/orders").json()] == [order.pk]
    assert [o["id"] for o in dealer.get("/api/orders").json()] == [order.pk]


def test_listing_detail_links_to_its_order(deal):
    order, seller, _ = deal

    assert seller.get(f"/api/listings/{order.listing_id}").json()["order"] == order.pk


# --- Seed ------------------------------------------------------------------


def test_seed_demo_has_a_scheduled_pickup():
    call_command("seed_demo")
    call_command("seed_demo")

    scheduled_order = Order.objects.get(listing__status=ListingStatus.PICKUP_SCHEDULED)
    assert scheduled_order.slot_status == SlotStatus.CONFIRMED
    assert scheduled_order.slot_start > timezone.now()
    local = scheduled_order.slot_start.astimezone(IST)
    assert (local.hour, local.minute) == (10, 0)
    assert (
        Order.objects.filter(listing__status=ListingStatus.OFFER_ACCEPTED, slot_status="").count()
        == 1
    )
