from datetime import datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from rest_framework.test import APIClient

import scheduler
from accounts.models import User
from audit.models import AuditLog
from listings.models import Listing, ListingStatus
from notifications.models import Notification, NotificationKind
from offers.models import Offer, OfferStatus
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import NoShowStrike, Order, OrderStatus, StrikeStatus
from orders.services import accept_offer
from orders.tests.conftest import IST

pytestmark = pytest.mark.django_db

SHOP = (17.4375, 78.4482)
SLOT_START = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
SLOT_END = SLOT_START + timedelta(hours=2)


def north(metres):
    return SHOP[0] + metres / 110_640, SHOP[1]


@pytest.fixture
def deal(categories, clock):
    """An accepted offer with a second dealer's offer not chosen."""
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer("+919811111111")
    other = make_dealer("+919822222222")
    chosen = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    lost = offer_on(client_for(other), listing, rates(categories, paper=1000)).json()["id"]
    order, _ = accept_offer(seller, Offer.objects.get(pk=chosen))
    owner = APIClient()
    owner.force_login(seller)
    return {
        "order": order,
        "seller": owner,
        "dealer": client_for(dealer),
        "other_dealer": client_for(other),
        "chosen": chosen,
        "lost": lost,
    }


@pytest.fixture
def scheduled(deal):
    """The dealer proposed 10:00–12:00 IST on 12 Nov and the seller confirmed."""
    order = deal["order"]
    deal["dealer"].post(
        f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
    )
    deal["seller"].post(f"/api/orders/{order.pk}/slot/confirm")
    return deal


def cancel(client, order, reason="Can't make it"):
    return client.post(f"/api/orders/{order.pk}/cancel", {"reason": reason}, format="json")


def check_in(client, order, at=SHOP):
    return client.post(
        f"/api/orders/{order.pk}/check-in", {"latitude": at[0], "longitude": at[1]}, format="json"
    )


def run_job():
    out = StringIO()
    call_command("record_no_shows", stdout=out)
    return out.getvalue()


# --- Cancelling an order ---------------------------------------------------


@pytest.mark.parametrize("stage", ["accepted", "scheduled"])
def test_dealer_cancels_and_the_listing_reopens_with_offers_restored(deal, clock, stage):
    order = deal["order"]
    if stage == "scheduled":
        deal["dealer"].post(
            f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
        )
        deal["seller"].post(f"/api/orders/{order.pk}/slot/confirm")
    clock.now = clock.now + timedelta(hours=1)

    response = cancel(deal["dealer"], order, "Truck broke down")

    assert response.status_code == 200
    listing = Listing.objects.get(pk=order.listing_id)
    assert listing.status == ListingStatus.OPEN
    assert listing.published_at == clock.now  # the 7-day expiry restarts
    order.refresh_from_db()
    assert (order.status, order.cancelled_by, order.cancel_reason) == (
        "cancelled",
        "dealer",
        "Truck broke down",
    )
    assert Offer.objects.get(pk=deal["chosen"]).status == OfferStatus.WITHDRAWN
    restored = Offer.objects.get(pk=deal["lost"])
    assert (restored.status, restored.rejection_reason) == (OfferStatus.ACTIVE, "")
    assert NotificationKind.ORDER_CANCELLED in Notification.objects.filter(
        user=order.seller
    ).values_list("kind", flat=True)
    assert AuditLog.objects.filter(
        entity_id=listing.pk, reason="Dealer cancelled: Truck broke down"
    ).exists()


def test_restored_offers_past_their_time_become_expired(deal, clock):
    order = deal["order"]
    clock.now = clock.now + timedelta(hours=25)

    cancel(deal["dealer"], order)

    assert Offer.objects.get(pk=deal["lost"]).status == OfferStatus.EXPIRED


def test_restored_offer_can_be_accepted(deal):
    order = deal["order"]
    cancel(deal["dealer"], order)

    response = deal["seller"].post(f"/api/offers/{deal['lost']}/accept")

    assert response.status_code == 201
    assert Listing.objects.get(pk=order.listing_id).status == ListingStatus.OFFER_ACCEPTED


def test_reopened_listing_is_back_in_the_feed(deal):
    order = deal["order"]
    cancel(deal["dealer"], order)

    ids = [
        item["id"] for item in deal["other_dealer"].get("/api/dealer/listings").json()["results"]
    ]

    assert order.listing_id in ids


@pytest.mark.parametrize("stage", ["accepted", "scheduled"])
def test_seller_cancels_and_the_listing_is_cancelled(deal, stage):
    order = deal["order"]
    if stage == "scheduled":
        deal["dealer"].post(
            f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
        )
        deal["seller"].post(f"/api/orders/{order.pk}/slot/confirm")

    response = cancel(deal["seller"], order, "Sold it elsewhere")

    assert response.status_code == 200
    assert Listing.objects.get(pk=order.listing_id).status == ListingStatus.CANCELLED
    assert Order.objects.get(pk=order.pk).cancelled_by == "seller"
    assert Notification.objects.filter(
        user=order.dealer.user, kind=NotificationKind.ORDER_CANCELLED
    ).exists()


def test_cancelling_needs_a_reason(deal):
    response = cancel(deal["seller"], deal["order"], "   ")

    assert response.status_code == 400
    assert response.json()["code"] == "reason_required"
    assert Order.objects.get(pk=deal["order"].pk).status == OrderStatus.ACTIVE


@pytest.mark.parametrize("listing_status", [ListingStatus.PICKED_UP, ListingStatus.DISPUTED])
def test_no_cancelling_after_pickup(deal, listing_status):
    Listing.objects.filter(pk=deal["order"].listing_id).update(status=listing_status)

    for client in (deal["seller"], deal["dealer"]):
        assert cancel(client, deal["order"]).json()["code"] == "wrong_stage"


def test_a_cancelled_order_cannot_be_cancelled_again(deal):
    cancel(deal["dealer"], deal["order"])

    assert cancel(deal["seller"], deal["order"]).status_code in (404, 409)


def test_outsiders_cannot_cancel(deal):
    assert cancel(deal["other_dealer"], deal["order"]).status_code == 404
    assert cancel(APIClient(), deal["order"]).status_code == 403


# --- Cancelling a listing --------------------------------------------------


@pytest.mark.parametrize("status", [ListingStatus.DRAFT, ListingStatus.OPEN])
def test_seller_cancels_a_draft_or_open_listing(categories, status):
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller, status=status)
    if status == ListingStatus.OPEN:
        offer_on(client_for(make_dealer()), listing, rates(categories))
    owner = APIClient()
    owner.force_login(seller)

    response = owner.post(
        f"/api/listings/{listing.pk}/cancel", {"reason": "Changed my mind"}, format="json"
    )

    assert response.status_code == 200
    assert Listing.objects.get(pk=listing.pk).status == ListingStatus.CANCELLED
    assert not Offer.objects.filter(listing=listing, status=OfferStatus.ACTIVE).exists()
    assert set(
        Offer.objects.filter(listing=listing).values_list("rejection_reason", flat=True)
    ) <= {"listing_cancelled"}


def test_listing_cancel_rules(deal):
    listing_id = deal["order"].listing_id
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))

    assert (
        deal["seller"]
        .post(f"/api/listings/{listing_id}/cancel", {"reason": " "}, format="json")
        .status_code
        == 400
    )
    # An accepted listing is cancelled through its order, not here.
    assert (
        deal["seller"]
        .post(f"/api/listings/{listing_id}/cancel", {"reason": "x"}, format="json")
        .status_code
        == 409
    )
    assert (
        other.post(f"/api/listings/{listing_id}/cancel", {"reason": "x"}, format="json").status_code
        == 404
    )


# --- Dealer check-in -------------------------------------------------------


def test_dealer_checks_in_during_the_slot_near_the_address(scheduled, clock):
    order = scheduled["order"]
    clock.now = SLOT_START + timedelta(minutes=10)

    response = check_in(scheduled["dealer"], order, at=north(120))

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.dealer_checked_in_at == clock.now
    assert 110 <= order.dealer_check_in_distance_m <= 130
    assert (
        Notification.objects.filter(user=order.seller, kind=NotificationKind.DEALER_ARRIVED).count()
        == 1
    )

    check_in(scheduled["dealer"], order)  # again: nothing changes
    assert Order.objects.get(pk=order.pk).dealer_checked_in_at == SLOT_START + timedelta(minutes=10)
    assert Notification.objects.filter(kind=NotificationKind.DEALER_ARRIVED).count() == 1


@pytest.mark.parametrize(("metres", "ok"), [(190, True), (210, False), (2000, False)])
def test_check_in_distance_limit(scheduled, clock, metres, ok):
    order = scheduled["order"]
    clock.now = SLOT_START + timedelta(minutes=5)

    response = check_in(scheduled["dealer"], order, at=north(metres))

    assert (response.status_code == 200) is ok
    if not ok:
        assert response.json()["code"] == "too_far_away"
        assert Order.objects.get(pk=order.pk).dealer_checked_in_at is None


@pytest.mark.parametrize(
    ("moment", "ok"),
    [
        (SLOT_START - timedelta(seconds=1), False),
        (SLOT_START, True),
        (SLOT_END, True),
        (SLOT_END + timedelta(seconds=1), False),
    ],
)
def test_check_in_only_during_the_slot(scheduled, clock, moment, ok):
    clock.now = moment

    response = check_in(scheduled["dealer"], scheduled["order"])

    assert (response.status_code == 200) is ok
    if not ok:
        assert response.json()["code"] == "outside_slot"


def test_only_the_dealer_checks_in_and_only_once_confirmed(deal, clock):
    order = deal["order"]
    deal["dealer"].post(
        f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
    )
    clock.now = SLOT_START + timedelta(minutes=1)

    assert check_in(deal["dealer"], order).json()["code"] == "wrong_stage"  # not confirmed
    assert check_in(deal["seller"], order).json()["code"] == "dealer_only"
    assert check_in(deal["other_dealer"], order).status_code == 404


def test_check_in_shows_as_an_action_only_in_the_slot(scheduled, clock):
    order = scheduled["order"]
    url = f"/api/orders/{order.pk}"
    assert "check_in" not in scheduled["dealer"].get(url).json()["actions"]

    clock.now = SLOT_START + timedelta(minutes=1)
    assert "check_in" in scheduled["dealer"].get(url).json()["actions"]
    assert "check_in" not in scheduled["seller"].get(url).json()["actions"]


# --- Recording no-shows ----------------------------------------------------


def test_no_check_in_means_the_dealer_missed_it(scheduled, clock):
    order = scheduled["order"]
    clock.now = SLOT_END + timedelta(minutes=30)

    assert "Recorded 1 no-show(s)." in run_job()

    order.refresh_from_db()
    assert (order.status, order.no_show_party) == (OrderStatus.CANCELLED, "dealer")
    listing = Listing.objects.get(pk=order.listing_id)
    assert listing.status == ListingStatus.OPEN
    # The other offer was made 24 h before 09:00 on 12 Nov, so it's past its time now.
    assert Offer.objects.get(pk=scheduled["lost"]).status == OfferStatus.EXPIRED
    strike = NoShowStrike.objects.get()
    assert (strike.user, strike.party) == (order.dealer.user, "dealer")
    assert (strike.due.amount_paise, strike.due.status) == (10000, "unpaid")  # ₹100 penalty
    log = AuditLog.objects.get(entity_id=listing.pk, reason="No-show: dealer")
    assert log.actor is None
    assert Notification.objects.filter(
        user=order.dealer.user, kind=NotificationKind.NO_SHOW_RECORDED
    ).exists()
    assert Notification.objects.filter(
        user=order.seller, kind=NotificationKind.PICKUP_MISSED
    ).exists()


def test_check_in_without_pickup_means_the_seller_missed_it(scheduled, clock):
    order = scheduled["order"]
    clock.now = SLOT_START + timedelta(minutes=15)
    check_in(scheduled["dealer"], order)
    clock.now = SLOT_END + timedelta(minutes=31)

    run_job()

    order.refresh_from_db()
    assert order.no_show_party == "seller"
    assert Listing.objects.get(pk=order.listing_id).status == ListingStatus.CANCELLED
    strike = NoShowStrike.objects.get()
    assert (strike.user, strike.party) == (order.seller, "seller")
    assert Notification.objects.filter(
        user=order.dealer.user, kind=NotificationKind.PICKUP_MISSED
    ).exists()


def test_nothing_is_recorded_before_the_grace_period_ends(scheduled, clock):
    clock.now = SLOT_END + timedelta(minutes=29)

    assert "Recorded 0 no-show(s)." in run_job()
    assert not NoShowStrike.objects.exists()


def test_running_the_job_twice_records_once(scheduled, clock):
    clock.now = SLOT_END + timedelta(hours=1)

    run_job()
    run_job()

    assert NoShowStrike.objects.count() == 1


@pytest.mark.parametrize("listing_status", [ListingStatus.PICKED_UP, ListingStatus.DISPUTED])
def test_pickups_that_started_are_not_no_shows(scheduled, clock, listing_status):
    Listing.objects.filter(pk=scheduled["order"].listing_id).update(status=listing_status)
    clock.now = SLOT_END + timedelta(hours=1)

    assert "Recorded 0 no-show(s)." in run_job()


def test_unconfirmed_proposals_are_not_no_shows(deal, clock):
    order = deal["order"]
    deal["dealer"].post(
        f"/api/orders/{order.pk}/slot", {"start": SLOT_START.isoformat()}, format="json"
    )
    clock.now = SLOT_END + timedelta(hours=1)

    assert "Recorded 0 no-show(s)." in run_job()


def test_the_scheduler_runs_the_no_show_job():
    assert "record_no_shows" in scheduler.JOBS


# --- The block -------------------------------------------------------------


def missed_pickup(scheduled, clock, who):
    if who == "seller":
        clock.now = SLOT_START + timedelta(minutes=5)
        check_in(scheduled["dealer"], scheduled["order"])
    clock.now = SLOT_END + timedelta(hours=1)
    run_job()
    return NoShowStrike.objects.get()


def test_a_blocked_dealer_cannot_make_offers(scheduled, clock, categories):
    missed_pickup(scheduled, clock, "dealer")
    other_listing = make_listing(categories)

    response = offer_on(scheduled["dealer"], other_listing, rates(categories))

    assert response.status_code == 409
    assert response.json()["code"] == "blocked"
    assert scheduled["dealer"].get("/api/auth/me").json()["blocked"] is True


def test_a_blocked_seller_cannot_publish(scheduled, clock, categories):
    from listings.models import ListingPhoto

    missed_pickup(scheduled, clock, "seller")
    seller = User.objects.get(phone="+919876543210")
    draft = make_listing(categories, seller=seller, status=ListingStatus.DRAFT)
    ListingPhoto.objects.create(
        listing=draft,
        file="listing-photos/x.jpg",
        content_type="image/jpeg",
        width=1,
        height=1,
        size_bytes=1,
    )

    response = scheduled["seller"].post(f"/api/listings/{draft.pk}/publish")

    assert response.status_code == 409
    assert response.json()["code"] == "blocked"


def test_the_block_lasts_until_the_penalty_is_paid(scheduled, clock, categories):
    from orders.gateway import FakeGateway
    from orders.payments import confirm_payment, start_payment

    strike = missed_pickup(scheduled, clock, "dealer")
    clock.now += timedelta(days=8)  # past the old 7-day block: no fixed end any more
    fresh = make_listing(categories)
    assert offer_on(scheduled["dealer"], fresh, rates(categories)).status_code == 409

    start_payment(strike.due)
    strike.due.refresh_from_db()
    FakeGateway().pay(strike.due.link_id)
    confirm_payment(strike.due)

    assert offer_on(scheduled["dealer"], fresh, rates(categories)).status_code == 201
    assert scheduled["dealer"].get("/api/auth/me").json()["blocked"] is False


# --- Challenging a strike --------------------------------------------------


def challenge(client, strike, reason="I was there; GPS failed"):
    return client.post(f"/api/strikes/{strike.pk}/challenge", {"reason": reason}, format="json")


def test_challenge_within_48_hours(scheduled, clock):
    strike = missed_pickup(scheduled, clock, "dealer")
    clock.now = strike.created_at + timedelta(hours=48)

    response = challenge(scheduled["dealer"], strike)

    assert response.status_code == 200
    assert response.json()["challenged_at"] is not None
    assert challenge(scheduled["dealer"], strike).json()["code"] == "already_challenged"


def test_challenge_after_48_hours_is_refused(scheduled, clock):
    strike = missed_pickup(scheduled, clock, "dealer")
    clock.now = strike.created_at + timedelta(hours=48, seconds=1)

    assert challenge(scheduled["dealer"], strike).json()["code"] == "challenge_closed"


def test_only_the_struck_user_can_challenge(scheduled, clock):
    strike = missed_pickup(scheduled, clock, "dealer")

    assert challenge(scheduled["seller"], strike).status_code == 404
    assert challenge(scheduled["dealer"], strike, " ").json()["code"] == "reason_required"
    assert scheduled["seller"].get("/api/strikes").json() == []
    assert [s["id"] for s in scheduled["dealer"].get("/api/strikes").json()] == [strike.pk]


def test_admin_removes_a_strike_and_lifts_the_block(scheduled, clock, categories):
    strike = missed_pickup(scheduled, clock, "dealer")
    admin = Client()
    admin.force_login(User.objects.create_superuser("+919000000001"))

    page = admin.get("/admin/orders/noshowstrike/").content.decode()
    admin.post("/admin/orders/noshowstrike/", {"action": "remove", "_selected_action": [strike.pk]})

    assert "No check-in" in page
    strike.refresh_from_db()
    assert strike.status == StrikeStatus.REMOVED
    assert (
        offer_on(scheduled["dealer"], make_listing(categories), rates(categories)).status_code
        == 201
    )


def test_admin_upholds_a_strike_and_the_block_stays(scheduled, clock):
    strike = missed_pickup(scheduled, clock, "dealer")
    admin = Client()
    admin.force_login(User.objects.create_superuser("+919000000001"))

    admin.post("/admin/orders/noshowstrike/", {"action": "uphold", "_selected_action": [strike.pk]})

    strike.refresh_from_db()
    assert strike.status == StrikeStatus.UPHELD
    assert scheduled["dealer"].get("/api/auth/me").json()["blocked"] is True


def test_admin_sees_cancellation_and_no_show_counts(deal, clock):
    cancel(deal["dealer"], deal["order"])
    admin = Client()
    admin.force_login(User.objects.create_superuser("+919000000001"))

    page = admin.get("/admin/accounts/user/").content.decode()

    assert "Cancellations" in page and "No-shows" in page


# --- Seed ------------------------------------------------------------------


def test_seed_demo_has_a_cancel_history():
    call_command("seed_demo")
    call_command("seed_demo")

    listing = Listing.objects.get(address_line__startswith="Demo listing 8:")
    assert listing.status == ListingStatus.CANCELLED
    order = listing.orders.get()
    assert (order.cancelled_by, order.cancel_reason) == ("dealer", "Truck broke down")
    reasons = list(AuditLog.objects.filter(entity_id=listing.pk).values_list("reason", flat=True))
    assert "Dealer cancelled: Truck broke down" in reasons
    assert "Seller cancelled: Sold to a neighbour" in reasons


def test_dealer_no_show_restores_offers_still_within_their_time(scheduled, clock):
    Offer.objects.filter(pk=scheduled["lost"]).update(expires_at=SLOT_END + timedelta(days=1))
    clock.now = SLOT_END + timedelta(minutes=30)

    run_job()

    assert Offer.objects.get(pk=scheduled["lost"]).status == OfferStatus.ACTIVE
