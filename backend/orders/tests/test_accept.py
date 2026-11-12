import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from audit.models import AuditLog
from dealers.models import DealerProfile, DealerStatus
from listings.models import Listing, ListingStatus
from notifications.models import Notification, NotificationKind
from offers.models import Offer, OfferStatus
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def owner(seller):
    client = APIClient()
    client.force_login(seller)
    return client


def accept(client, offer_id):
    return client.post(f"/api/offers/{offer_id}/accept")


def two_offers(categories, seller):
    """A listing (20 kg paper + 2 appliances) with offers from two dealers."""
    listing = make_listing(categories, seller=seller)
    first = offer_on(
        client_for(make_dealer("+919811111111")), listing, rates(categories, paper=1400)
    )
    second = offer_on(
        client_for(make_dealer("+919822222222")), listing, rates(categories, paper=1500)
    )
    return listing, first.json()["id"], second.json()["id"]


# --- Accepting -------------------------------------------------------------


def test_accepting_creates_the_order_and_rejects_the_others(categories, seller, owner):
    listing, chosen, other = two_offers(categories, seller)

    response = accept(owner, chosen)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active" and body["offer"] == chosen
    # 20 kg × ₹14 + 2 × ₹1,500 = ₹3,280
    assert body["estimated_total_paise"] == 328000
    assert [
        (i["category_name"], i["declared_weight_kg"], i["declared_quantity"], i["rate_paise"])
        for i in body["items"]
    ] == [("Paper", "20.000", None, 1400), ("Appliance", None, 2, 150000)]
    listing.refresh_from_db()
    assert listing.status == ListingStatus.OFFER_ACCEPTED
    assert Offer.objects.get(pk=chosen).status == OfferStatus.ACCEPTED
    rejected = Offer.objects.get(pk=other)
    assert (rejected.status, rejected.rejection_reason) == (OfferStatus.REJECTED, "other_accepted")
    log = AuditLog.objects.get(entity_id=listing.pk)
    assert (log.from_status, log.to_status, log.actor) == ("open", "offer_accepted", seller)


def test_order_snapshot_does_not_change_if_rates_change_later(categories, seller, owner):
    _, chosen, _ = two_offers(categories, seller)
    order_id = accept(owner, chosen).json()["id"]

    Offer.objects.get(pk=chosen).rates.filter(category=categories["paper"]).update(rate_paise=1)

    item = Order.objects.get(pk=order_id).items.get(category=categories["paper"])
    assert item.rate_paise == 1400
    assert item.declared_weight_kg == Decimal("20.000")


def test_accepting_a_different_offer_afterwards_returns_409(categories, seller, owner):
    listing, chosen, other = two_offers(categories, seller)
    accept(owner, chosen)

    response = accept(owner, other)

    assert response.status_code == 409
    assert response.json()["code"] == "not_open"
    assert Order.objects.count() == 1
    assert Offer.objects.get(pk=chosen).status == OfferStatus.ACCEPTED
    assert AuditLog.objects.filter(entity_id=listing.pk).count() == 1


def test_retrying_the_same_accept_returns_the_same_order(categories, seller, owner):
    _, chosen, _ = two_offers(categories, seller)
    first = accept(owner, chosen)

    second = accept(owner, chosen)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert Order.objects.count() == 1
    assert Notification.objects.filter(kind=NotificationKind.OFFER_ACCEPTED).count() == 1


def test_accepted_dealer_is_notified_in_the_same_transaction(categories, seller, owner):
    _, chosen, _ = two_offers(categories, seller)

    order_id = accept(owner, chosen).json()["id"]

    note = Notification.objects.get(kind=NotificationKind.OFFER_ACCEPTED)
    assert note.user.phone == "+919811111111"
    assert note.data == {
        "listing_id": Order.objects.get().listing_id,
        "offer_id": chosen,
        "order_id": order_id,
    }


def test_declared_weight_stays_locked_after_acceptance(categories, seller, owner):
    listing, chosen, _ = two_offers(categories, seller)
    accept(owner, chosen)

    response = owner.patch(
        f"/api/listings/{listing.pk}",
        {"items": [{"category": categories["paper"].pk, "declared_weight_kg": "99"}]},
        format="json",
    )

    assert response.status_code == 409


# --- Offers that can't be accepted -----------------------------------------


@pytest.mark.parametrize(
    "make_unavailable",
    [
        lambda o: Offer.objects.filter(pk=o).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        ),
        lambda o: Offer.objects.filter(pk=o).update(status=OfferStatus.WITHDRAWN),
        lambda o: Offer.objects.filter(pk=o).update(status=OfferStatus.EXPIRED),
        lambda o: Offer.objects.filter(pk=o).update(status=OfferStatus.REJECTED),
    ],
)
def test_offers_that_are_not_live_cannot_be_accepted(categories, seller, owner, make_unavailable):
    listing, chosen, _ = two_offers(categories, seller)
    make_unavailable(chosen)

    response = accept(owner, chosen)

    assert response.status_code == 409
    assert response.json()["code"] == "not_live"
    assert Listing.objects.get(pk=listing.pk).status == ListingStatus.OPEN
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"status": DealerStatus.SUSPENDED},
        {"scale_certificate_expires_on": timezone.localdate() - timedelta(days=1)},
        {"service_radius_km": 1, "location": None},  # set below: moved far away
    ],
)
def test_offers_from_dealers_who_are_no_longer_eligible_cannot_be_accepted(
    categories, seller, owner, change
):
    from django.contrib.gis.geos import Point

    listing, chosen, _ = two_offers(categories, seller)
    if change.get("location", "unset") is None:
        change = {"service_radius_km": 1, "location": Point(79.5941, 17.9689, srid=4326)}
    DealerProfile.objects.filter(offers__pk=chosen).update(**change)

    response = accept(owner, chosen)

    assert response.status_code == 409
    assert response.json()["code"] == "dealer_unavailable"
    assert not Order.objects.exists()


@pytest.mark.parametrize("status", [ListingStatus.CANCELLED, ListingStatus.EXPIRED])
def test_closed_listings_cannot_accept(categories, seller, owner, status):
    listing, chosen, _ = two_offers(categories, seller)
    Listing.objects.filter(pk=listing.pk).update(status=status)

    assert accept(owner, chosen).json()["code"] == "not_open"


# --- Who may accept --------------------------------------------------------


def test_only_the_listing_owner_can_accept(categories, seller):
    _, chosen, _ = two_offers(categories, seller)
    other_seller = APIClient()
    other_seller.force_login(User.objects.create_user("+919123456789"))
    dealer = client_for(DealerProfile.objects.get(offers__pk=chosen))
    dual = make_dealer("+919833333333", roles=(Role.SELLER, Role.DEALER))

    assert accept(other_seller, chosen).status_code == 404
    assert accept(client_for(dual), chosen).status_code == 404
    assert accept(dealer, chosen).status_code == 403  # dealer-only account
    assert accept(APIClient(), chosen).status_code == 403
    assert not Order.objects.exists()


def test_unknown_offer_returns_404(owner):
    assert accept(owner, 999999).status_code == 404


# --- Database guarantees ---------------------------------------------------


def test_database_allows_only_one_active_order_per_listing(categories, seller, owner):
    _, chosen, other = two_offers(categories, seller)
    order = Order.objects.get(pk=accept(owner, chosen).json()["id"])

    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.create(
            listing=order.listing,
            offer=Offer.objects.get(pk=other),
            dealer=order.dealer,
            seller=seller,
            estimated_total_paise=1,
        )
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.CANCELLED)
    Order.objects.create(
        listing=order.listing,
        offer=Offer.objects.get(pk=other),
        dealer=order.dealer,
        seller=seller,
        estimated_total_paise=1,
    )


# --- Concurrency -----------------------------------------------------------


def race(seller, offer_ids):
    barrier = threading.Barrier(len(offer_ids))
    codes = []

    def attempt(offer_id):
        client = APIClient()
        client.force_login(seller)
        barrier.wait()
        codes.append(accept(client, offer_id).status_code)
        connection.close()

    threads = [threading.Thread(target=attempt, args=(offer_id,)) for offer_id in offer_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(codes)


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_accepts_of_different_offers_accept_only_one(categories, seller):
    listing, first, second = two_offers(categories, seller)

    assert race(seller, [first, second]) == [201, 409]

    assert Offer.objects.filter(status=OfferStatus.ACCEPTED).count() == 1
    assert Offer.objects.filter(status=OfferStatus.REJECTED).count() == 1
    assert Order.objects.count() == 1
    assert Listing.objects.get(pk=listing.pk).status == ListingStatus.OFFER_ACCEPTED
    assert AuditLog.objects.filter(entity_id=listing.pk).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_accepts_of_the_same_offer_make_one_order(categories, seller):
    _, chosen, _ = two_offers(categories, seller)

    assert race(seller, [chosen, chosen]) == [200, 201]

    assert Order.objects.count() == 1
    assert Notification.objects.filter(kind=NotificationKind.OFFER_ACCEPTED).count() == 1


# --- Seed ------------------------------------------------------------------


def test_seed_demo_has_an_accepted_listing():
    call_command("seed_demo")
    call_command("seed_demo")

    order = Order.objects.get(listing__address_line__startswith="Demo listing 6:")
    assert order.listing.status == ListingStatus.OFFER_ACCEPTED
    assert order.dealer.user.phone == "+919000000007"
    assert order.estimated_total_paise == 30 * 11500
    other = order.listing.offers.exclude(pk=order.offer_id).get()
    assert (other.status, other.rejection_reason) == (OfferStatus.REJECTED, "other_accepted")
