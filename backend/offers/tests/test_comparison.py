from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from dealers.models import DealerProfile, DealerStatus
from offers.models import Offer, OfferStatus
from offers.tests.test_offers import (
    client_for,
    make_dealer,
    make_listing,
    offer_on,
    rates,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def owner(seller):
    client = APIClient()
    client.force_login(seller)
    return client


def offers_url(listing):
    return f"/api/listings/{listing.pk}/offers"


def test_seller_sees_live_offers_highest_first(categories, seller, owner):
    listing = make_listing(categories, seller=seller)
    low = offer_on(client_for(make_dealer("+919811111111")), listing, rates(categories, paper=1000))
    high = offer_on(
        client_for(make_dealer("+919822222222")), listing, rates(categories, paper=2000)
    )

    body = owner.get(offers_url(listing)).json()

    assert [offer["id"] for offer in body] == [high.json()["id"], low.json()["id"]]
    assert body[0]["estimated_total_paise"] == 20 * 2000 + 2 * 150000
    assert body[0]["dealer_name"] == "Dealer" and body[0]["dealer_locality"] == "Ameerpet"
    assert set(body[0]) == {
        "id",
        "status",
        "is_live",
        "expires_at",
        "dealer_name",
        "dealer_locality",
        "rates",
        "estimated_total_paise",
    }


def test_expired_offers_are_shown_last_and_not_selectable(categories, seller, owner):
    listing = make_listing(categories, seller=seller)
    expired = offer_on(
        client_for(make_dealer("+919811111111")), listing, rates(categories, paper=9000)
    )
    live = offer_on(
        client_for(make_dealer("+919822222222")), listing, rates(categories, paper=1000)
    )
    Offer.objects.filter(pk=expired.json()["id"]).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    body = owner.get(offers_url(listing)).json()

    assert [(o["id"], o["is_live"]) for o in body] == [
        (live.json()["id"], True),
        (expired.json()["id"], False),
    ]


def test_withdrawn_offers_are_left_out(categories, seller, owner):
    listing = make_listing(categories, seller=seller)
    dealer = client_for(make_dealer())
    offer_id = offer_on(dealer, listing, rates(categories)).json()["id"]
    dealer.post(f"/api/dealer/offers/{offer_id}/withdraw")

    assert owner.get(offers_url(listing)).json() == []


def test_offers_from_dealers_no_longer_covering_the_address_are_left_out(categories, seller, owner):
    listing = make_listing(categories, seller=seller, at=(17.4375 + 8 / 110.64, 78.4482))  # 8 km
    near = make_dealer("+919811111111", radius=10)
    offer_on(client_for(near), listing, rates(categories))
    DealerProfile.objects.filter(pk=near.pk).update(service_radius_km=5)

    assert owner.get(offers_url(listing)).json() == []


@pytest.mark.parametrize("status", [DealerStatus.SUSPENDED, DealerStatus.REJECTED])
def test_offers_from_dealers_who_lost_approval_are_left_out(categories, seller, owner, status):
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer()
    offer_on(client_for(dealer), listing, rates(categories))
    DealerProfile.objects.filter(pk=dealer.pk).update(status=status)

    assert owner.get(offers_url(listing)).json() == []


def test_offers_from_dealers_with_an_expired_certificate_are_left_out(categories, seller, owner):
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer()
    offer_on(client_for(dealer), listing, rates(categories))
    DealerProfile.objects.filter(pk=dealer.pk).update(
        scale_certificate_expires_on=timezone.localdate() - timedelta(days=1)
    )

    assert owner.get(offers_url(listing)).json() == []


def test_no_dealer_contact_details_before_acceptance(categories, seller, owner):
    listing = make_listing(categories, seller=seller)
    offer_on(client_for(make_dealer("+919811111111")), listing, rates(categories))

    content = owner.get(offers_url(listing)).content.decode()

    assert "9811111111" not in content
    assert "17.4375" not in content and "78.4482" not in content


def test_only_the_owner_can_compare(categories, seller):
    listing = make_listing(categories, seller=seller)
    dealer = make_dealer()
    offer_on(client_for(dealer), listing, rates(categories))
    other_seller = APIClient()
    other_seller.force_login(User.objects.create_user("+919123456789"))

    assert other_seller.get(offers_url(listing)).status_code == 404
    assert client_for(dealer).get(offers_url(listing)).status_code == 403  # dealer-only account
    assert APIClient().get(offers_url(listing)).status_code == 403


def test_dual_role_dealer_cannot_see_offers_on_someone_elses_listing(categories, seller):
    listing = make_listing(categories, seller=seller)
    offer_on(client_for(make_dealer("+919811111111")), listing, rates(categories))
    dual = make_dealer("+919822222222", roles=(Role.SELLER, Role.DEALER))

    assert client_for(dual).get(offers_url(listing)).status_code == 404


def test_query_count_does_not_grow_with_offers(categories, seller, owner, count_queries):
    listing = make_listing(categories, seller=seller)
    offer_on(client_for(make_dealer("+919811111111")), listing, rates(categories))
    one_offer = count_queries(lambda: owner.get(offers_url(listing)))

    for i in range(2, 8):
        offer_on(
            client_for(make_dealer(f"+91981111111{i}")), listing, rates(categories, paper=1000 + i)
        )
    seven_offers = count_queries(lambda: owner.get(offers_url(listing)))

    assert len(owner.get(offers_url(listing)).json()) == 7
    assert seven_offers == one_offer


def test_seeded_seller_compares_three_offers():
    call_command("seed_demo")
    seller = User.objects.get(phone="+919000000002")
    client = APIClient()
    client.force_login(seller)
    listing = seller.listings.get(status="open")

    body = client.get(offers_url(listing)).json()

    totals = [offer["estimated_total_paise"] for offer in body]
    assert len(body) == 3 and totals == sorted(totals, reverse=True)
    assert all(offer["is_live"] and offer["status"] == OfferStatus.ACTIVE for offer in body)
