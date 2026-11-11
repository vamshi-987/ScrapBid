from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from dealers.models import DealerProfile, DealerStatus, IdType
from listings.models import Category, Listing, ListingItem, ListingPhoto, ListingStatus

pytestmark = pytest.mark.django_db

FEED = "/api/dealer/listings"
# Dealer shop in Ameerpet. One degree of latitude here is about 110.64 km.
SHOP = (17.4375, 78.4482)
KM_PER_DEGREE_LAT = 110.64


def north_of_shop(km):
    return SHOP[0] + km / KM_PER_DEGREE_LAT, SHOP[1]


@pytest.fixture
def categories():
    return {
        "paper": Category.objects.create(name="Paper"),
        "ewaste": Category.objects.create(name="E-waste", is_restricted=True),
    }


def make_dealer(
    phone="+919811111111",
    *,
    status=DealerStatus.APPROVED,
    radius=10,
    ewaste=False,
    expires_in=200,
    roles=(Role.DEALER,),
):
    user = User.objects.create_user(phone, roles=list(roles))
    return DealerProfile.objects.create(
        user=user,
        status=status,
        business_name="Test Dealer",
        locality="Ameerpet",
        pincode="500016",
        location=Point(SHOP[1], SHOP[0], srid=4326),
        service_radius_km=radius,
        id_type=IdType.PAN,
        id_number_last4="234F",
        scale_certificate_expires_on=timezone.localdate() + timedelta(days=expires_in),
        ewaste_authorised=ewaste,
    )


def make_listing(
    categories,
    *,
    at=SHOP,
    status=ListingStatus.OPEN,
    seller=None,
    restricted=False,
    photos=1,
    address="12-3-45 Secret Street",
):
    seller = seller or User.objects.create_user(f"+9198{Listing.objects.count():08d}")
    listing = Listing.objects.create(
        seller=seller,
        status=status,
        address_line=address,
        locality="Ameerpet",
        pincode="500016",
        location=Point(at[1], at[0], srid=4326),
        # Published listings always have a publish time (the state machine sets it).
        published_at=None if status == ListingStatus.DRAFT else timezone.now(),
    )
    ListingItem.objects.create(
        listing=listing, category=categories["paper"], declared_weight_kg=Decimal("20")
    )
    if restricted:
        ListingItem.objects.create(
            listing=listing, category=categories["ewaste"], declared_weight_kg=Decimal("5")
        )
    for i in range(photos):
        ListingPhoto.objects.create(
            listing=listing,
            file=f"listing-photos/test-{listing.pk}-{i}.jpg",
            content_type="image/jpeg",
            width=10,
            height=10,
            size_bytes=10,
        )
    return listing


def client_for(profile_or_user):
    user = getattr(profile_or_user, "user", profile_or_user)
    client = APIClient()
    client.force_login(user)
    return client


def feed_ids(client):
    response = client.get(FEED)
    assert response.status_code == 200
    return [listing["id"] for listing in response.json()["results"]]


# --- Service area ----------------------------------------------------------


def test_listings_inside_the_radius_are_shown_and_outside_are_not(categories):
    dealer = make_dealer(radius=10)
    inside = make_listing(categories, at=north_of_shop(9.5))
    outside = make_listing(categories, at=north_of_shop(10.5))

    ids = feed_ids(client_for(dealer))

    assert inside.pk in ids
    assert outside.pk not in ids


def test_a_bigger_radius_reaches_further(categories):
    listing = make_listing(categories, at=north_of_shop(14))

    assert feed_ids(client_for(make_dealer("+919811111111", radius=10))) == []
    assert feed_ids(client_for(make_dealer("+919822222222", radius=15))) == [listing.pk]


def test_nearest_listings_come_first(categories):
    far = make_listing(categories, at=north_of_shop(8))
    near = make_listing(categories, at=north_of_shop(1))
    middle = make_listing(categories, at=north_of_shop(4))

    assert feed_ids(client_for(make_dealer())) == [near.pk, middle.pk, far.pk]


@pytest.mark.parametrize(
    "status",
    [s for s in ListingStatus.values if s != ListingStatus.OPEN],
)
def test_only_open_listings_are_shown(categories, status):
    make_listing(categories, status=status)

    assert feed_ids(client_for(make_dealer())) == []


def test_dealers_never_see_their_own_listings(categories):
    dealer = make_dealer(roles=(Role.SELLER, Role.DEALER))
    make_listing(categories, seller=dealer.user)
    someone_else = make_listing(categories)

    assert feed_ids(client_for(dealer)) == [someone_else.pk]


# --- Restricted categories -------------------------------------------------


def test_restricted_listings_need_an_authorised_dealer(categories):
    mixed = make_listing(categories, restricted=True)  # paper + e-waste: whole listing restricted
    plain = make_listing(categories, at=north_of_shop(1))

    assert feed_ids(client_for(make_dealer("+919811111111"))) == [plain.pk]
    assert feed_ids(client_for(make_dealer("+919822222222", ewaste=True))) == [mixed.pk, plain.pk]


# --- What a dealer sees ----------------------------------------------------


def test_feed_shows_only_the_approximate_area(categories):
    seller = User.objects.create_user("+919876543210")
    make_listing(categories, seller=seller, address="12-3-45 Secret Street")

    response = client_for(make_dealer()).get(FEED)
    listing = response.json()["results"][0]
    content = response.content.decode()

    assert set(listing) == {
        "id",
        "locality",
        "pincode",
        "items",
        "photos",
        "published_at",
        "my_offer",
    }
    assert listing["my_offer"] is None
    assert listing["locality"] == "Ameerpet" and listing["pincode"] == "500016"
    for secret in ("Secret Street", "9876543210", str(SHOP[0]), "distance", "seller"):
        assert secret not in content
    assert listing["photos"][0]["url"].startswith("http")


def test_detail_follows_the_same_rules(categories):
    dealer = make_dealer(roles=(Role.SELLER, Role.DEALER))
    visible = make_listing(categories, photos=2)
    hidden = [
        make_listing(categories, at=north_of_shop(30)),
        make_listing(categories, status=ListingStatus.DRAFT),
        make_listing(categories, restricted=True),
        make_listing(categories, seller=dealer.user),
    ]
    client = client_for(dealer)

    response = client.get(f"{FEED}/{visible.pk}")
    assert response.status_code == 200
    assert len(response.json()["photos"]) == 2
    assert "address_line" not in response.json()
    for listing in hidden:
        assert client.get(f"{FEED}/{listing.pk}").status_code == 404


# --- Who may see the feed --------------------------------------------------


@pytest.mark.parametrize(
    "status", [DealerStatus.PENDING, DealerStatus.REJECTED, DealerStatus.SUSPENDED]
)
def test_unapproved_dealers_are_refused(categories, status):
    listing = make_listing(categories)
    client = client_for(make_dealer(status=status))

    assert client.get(FEED).status_code == 403
    assert client.get(f"{FEED}/{listing.pk}").status_code == 403


def test_approved_dealer_with_an_expired_certificate_is_refused(categories):
    make_listing(categories)

    assert client_for(make_dealer(expires_in=-1)).get(FEED).status_code == 403
    assert client_for(make_dealer("+919822222222", expires_in=0)).get(FEED).status_code == 200


def test_sellers_and_anonymous_users_are_refused(categories):
    make_listing(categories)

    assert client_for(User.objects.create_user("+919123456789")).get(FEED).status_code == 403
    assert APIClient().get(FEED).status_code == 403


# --- Database work ---------------------------------------------------------


def test_query_count_does_not_grow_with_the_number_of_listings(categories, count_queries):
    client = client_for(make_dealer())
    for i in range(3):
        make_listing(categories, at=north_of_shop(i + 1), restricted=False)
    three = count_queries(lambda: client.get(FEED))

    for i in range(12):
        make_listing(categories, at=north_of_shop(i * 0.5 + 0.2), photos=2)
    fifteen = count_queries(lambda: client.get(FEED))

    assert three <= 12
    assert fifteen == three


def test_feed_is_paginated(categories):
    for i in range(23):
        make_listing(categories, at=north_of_shop(i * 0.3), photos=0)
    client = client_for(make_dealer())

    first = client.get(FEED).json()
    second = client.get(f"{FEED}?page=2").json()

    assert first["count"] == 23
    assert len(first["results"]) == 20 and first["next"]
    assert len(second["results"]) == 3


# --- Seed ------------------------------------------------------------------


def test_seeded_dealers_see_the_expected_listings():
    call_command("seed_demo")
    phones = dict(Listing.objects.values_list("pk", "seller__phone"))

    def visible(phone):
        profile = DealerProfile.objects.get(user__phone=phone)
        ids = feed_ids(client_for(profile))
        return sorted(phones[i][-1] for i in ids)

    # Listings by seller: ...2 = Seller 1 (open; draft hidden), ...3 = Seller 2 (e-waste),
    # ...4 = the dual-role dealer's bulk copper.
    assert visible("+919000000006") == ["2", "3", "4"]  # Begumpet, e-waste authorised
    assert visible("+919000000007") == ["2", "4"]  # Secunderabad: e-waste hidden
    assert visible("+919000000004") == ["2"]  # dual role: own and e-waste hidden
    assert visible("+919000000008") == []  # Warangal: out of range
    pending = DealerProfile.objects.get(user__phone="+919000000005")
    assert client_for(pending).get(FEED).status_code == 403
