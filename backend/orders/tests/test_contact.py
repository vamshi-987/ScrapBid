import logging

import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from listings.models import Listing, ListingStatus
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import Order, OrderStatus
from orders.services import accept_offer

pytestmark = pytest.mark.django_db

SELLER_PHONE = "+919876543210"
DEALER_PHONE = "+919811111111"
LOSING_DEALER_PHONE = "+919822222222"
ADDRESS = "12-3-45 Secret Street"


@pytest.fixture
def deal(categories):
    """An accepted offer, with a second dealer whose offer was not chosen."""
    seller = User.objects.create_user(SELLER_PHONE)
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer(DEALER_PHONE)
    loser = make_dealer(LOSING_DEALER_PHONE)
    winning = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    offer_on(client_for(loser), listing, rates(categories, paper=1000))
    order, _ = accept_offer(seller, listing.offers.get(pk=winning))
    owner = APIClient()
    owner.force_login(seller)
    return order, owner, client_for(dealer), client_for(loser)


def contact(client, order):
    return client.get(f"/api/orders/{order.pk}/contact")


# --- What each side gets ---------------------------------------------------


def test_accepted_dealer_gets_the_exact_address_and_seller_phone(deal):
    order, _, dealer, _ = deal

    response = contact(dealer, order)

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == SELLER_PHONE
    assert body["address_line"] == ADDRESS
    assert (body["locality"], body["pincode"]) == ("Ameerpet", "500016")
    assert (body["latitude"], body["longitude"]) == (17.4375, 78.4482)


def test_seller_gets_the_dealers_phone(deal):
    order, seller, _, _ = deal

    body = contact(seller, order).json()

    assert body == {"name": "Dealer", "phone": DEALER_PHONE}


@pytest.mark.parametrize(
    "listing_status",
    [
        ListingStatus.OFFER_ACCEPTED,
        ListingStatus.PICKUP_SCHEDULED,
        ListingStatus.PICKED_UP,
        ListingStatus.DISPUTED,
    ],
)
def test_contact_is_shared_while_the_order_is_open(deal, listing_status):
    order, seller, dealer, _ = deal
    Listing.objects.filter(pk=order.listing_id).update(status=listing_status)

    assert contact(seller, order).status_code == 200
    assert contact(dealer, order).status_code == 200


@pytest.mark.parametrize("order_status", [OrderStatus.COMPLETED, OrderStatus.CANCELLED])
def test_contact_stops_when_the_order_closes(deal, order_status):
    order, seller, dealer, _ = deal
    Order.objects.filter(pk=order.pk).update(status=order_status)

    for client in (seller, dealer):
        response = contact(client, order)
        assert response.status_code == 403
        assert response.json()["code"] == "contact_closed"
        assert SELLER_PHONE not in response.content.decode()
        assert ADDRESS not in response.content.decode()


# --- Nobody else -----------------------------------------------------------


def test_nobody_else_can_see_contact_details(deal):
    order, _, _, loser = deal
    other_seller = APIClient()
    other_seller.force_login(User.objects.create_user("+919123456789"))
    other_dual = client_for(make_dealer("+919833333333", roles=(Role.SELLER, Role.DEALER)))
    admin = APIClient()
    admin.force_login(User.objects.create_superuser("+919000000001"))

    for client in (loser, other_seller, other_dual, admin):
        assert contact(client, order).status_code == 404
    assert contact(APIClient(), order).status_code == 403


# --- Never anywhere else ---------------------------------------------------


def test_contact_details_appear_in_no_other_response(deal, caplog):
    caplog.set_level(logging.DEBUG)
    order, seller, dealer, loser = deal
    listing = order.listing
    responses = [
        seller.get("/api/orders"),
        seller.get(f"/api/orders/{order.pk}"),
        seller.get("/api/listings"),
        seller.get(f"/api/listings/{listing.pk}/offers"),
        seller.get("/api/notifications"),
        dealer.get("/api/orders"),
        dealer.get(f"/api/orders/{order.pk}"),
        dealer.get("/api/dealer/offers"),
        dealer.get("/api/dealer/listings"),
        dealer.get("/api/notifications"),
        loser.get("/api/dealer/offers"),
        loser.get("/api/dealer/listings"),
        loser.get(f"/api/dealer/listings/{listing.pk}"),
        loser.get("/api/notifications"),
    ]

    for response in responses:
        content = response.content.decode()
        assert ADDRESS not in content, response.request["PATH_INFO"]
        assert SELLER_PHONE not in content and SELLER_PHONE[3:] not in content
        assert DEALER_PHONE not in content and DEALER_PHONE[3:] not in content
    contact(dealer, order)
    contact(seller, order)
    assert SELLER_PHONE[3:] not in caplog.text and DEALER_PHONE[3:] not in caplog.text
    assert ADDRESS not in caplog.text
