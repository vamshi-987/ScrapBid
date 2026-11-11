import threading
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from dealers.models import DealerProfile, DealerStatus, IdType
from listings.models import Category, Listing, ListingItem, ListingStatus, PricingUnit
from listings.services import expire_overdue_listings, is_editable
from offers.models import Offer, OfferRate, OfferStatus
from offers.pricing import estimated_total_paise, line_paise

SHOP = (17.4375, 78.4482)


# --- Money: exact estimates, rounded half-up to the paisa (Q34) ------------


@pytest.mark.parametrize(
    ("weight", "rate", "expected"),
    [
        ("20", 1400, 28000),  # 20 kg × ₹14 = ₹280
        ("12.5", 1000, 12500),  # 12.5 kg × ₹10 = ₹125
        ("0.001", 1400, 1),  # 1.4 paise → 1
        ("0.002", 1250, 3),  # 2.5 paise → 3 (half up)
        ("0.001", 1500, 2),  # 1.5 paise → 2 (half up)
        ("0.003", 1000, 3),
        ("350.5", 72000, 25236000),  # bulk copper: ₹2,52,360
        ("10000", 1000000, 10000000000),  # large but exact
        ("1.333", 333, 444),  # 443.889 → 444
    ],
)
def test_per_kg_lines_round_half_up_to_the_paisa(weight, rate, expected):
    item = SimpleNamespace(declared_weight_kg=Decimal(weight), declared_quantity=None)

    result = line_paise(item, rate)

    assert result == expected
    assert isinstance(result, int)


def test_per_item_lines_multiply_the_count():
    item = SimpleNamespace(declared_weight_kg=None, declared_quantity=3)

    assert line_paise(item, 150000) == 450000


def test_total_is_the_sum_of_rounded_lines():
    items = [
        SimpleNamespace(category_id=1, declared_weight_kg=Decimal("0.001"), declared_quantity=None),
        SimpleNamespace(category_id=2, declared_weight_kg=Decimal("0.001"), declared_quantity=None),
    ]

    # Each line is 1.5 → 2, so the total is 4 (not round(3.0) = 3).
    assert estimated_total_paise(items, {1: 1500, 2: 1500}) == 4


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def categories(db):
    return {
        "paper": Category.objects.create(name="Paper"),
        "fridge": Category.objects.create(name="Appliance", pricing_unit=PricingUnit.PER_ITEM),
        "ewaste": Category.objects.create(name="E-waste", is_restricted=True),
    }


def make_dealer(
    phone="+919811111111",
    *,
    ewaste=False,
    status=DealerStatus.APPROVED,
    roles=(Role.DEALER,),
    radius=10,
):
    user = User.objects.create_user(phone, roles=list(roles))
    return DealerProfile.objects.create(
        user=user,
        status=status,
        business_name="Dealer",
        locality="Ameerpet",
        pincode="500016",
        location=Point(SHOP[1], SHOP[0], srid=4326),
        service_radius_km=radius,
        id_type=IdType.PAN,
        id_number_last4="234F",
        scale_certificate_expires_on=timezone.localdate() + timedelta(days=100),
        ewaste_authorised=ewaste,
    )


def make_listing(
    categories,
    *,
    seller=None,
    status=ListingStatus.OPEN,
    items=None,
    at=SHOP,
    published_ago=timedelta(hours=1),
):
    seller = seller or User.objects.create_user(f"+9197{Listing.objects.count():08d}")
    listing = Listing.objects.create(
        seller=seller,
        status=status,
        address_line="12-3-45 Secret Street",
        locality="Ameerpet",
        pincode="500016",
        location=Point(at[1], at[0], srid=4326),
        published_at=timezone.now() - published_ago,
    )
    for name, weight, quantity in items or [("paper", "20", None), ("fridge", None, 2)]:
        ListingItem.objects.create(
            listing=listing,
            category=categories[name],
            declared_weight_kg=Decimal(weight) if weight else None,
            declared_quantity=quantity,
        )
    return listing


def client_for(dealer):
    client = APIClient()
    client.force_login(dealer.user)
    return client


def rates(categories, paper=1400, fridge=150000, **extra):
    data = [
        {"category": categories["paper"].pk, "rate_paise": paper},
        {"category": categories["fridge"].pk, "rate_paise": fridge},
    ]
    for name, value in extra.items():
        data.append({"category": categories[name].pk, "rate_paise": value})
    return {"rates": data}


def offer_on(client, listing, payload):
    return client.post(f"/api/dealer/listings/{listing.pk}/offers", payload, format="json")


pytestmark = pytest.mark.django_db


# --- Making an offer -------------------------------------------------------


def test_dealer_makes_an_offer_with_a_server_side_estimate(categories, settings):
    settings.OFFER_EXPIRY_HOURS = 24
    dealer = make_dealer()
    listing = make_listing(categories)
    before = timezone.now()

    response = offer_on(
        client_for(dealer), listing, {**rates(categories), "estimated_total_paise": 1}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active" and body["is_live"] is True
    # 20 kg × ₹14 + 2 × ₹1,500 = ₹280 + ₹3,000 = ₹3,280; the client's total is ignored.
    assert body["estimated_total_paise"] == 328000
    assert [line["estimated_paise"] for line in body["rates"]] == [28000, 300000]
    expires = Offer.objects.get().expires_at
    assert before + timedelta(hours=24) <= expires <= timezone.now() + timedelta(hours=24)


@pytest.mark.parametrize(
    "payload_fn",
    [
        lambda c: {"rates": [{"category": c["paper"].pk, "rate_paise": 1400}]},  # missing one
        lambda c: rates(c, ewaste=500),  # extra category
        lambda c: rates(c, paper=0),
        lambda c: rates(c, paper=-5),
        lambda c: {"rates": []},
        lambda c: {"rates": [{"category": c["paper"].pk, "rate_paise": 1}] * 2},
        lambda c: rates(c, paper=10_000_01),  # over ₹10,000 per kg
        lambda c: rates(c, fridge=1_00_000_01),  # over ₹1,00,000 per item
    ],
)
def test_invalid_rates_are_rejected(categories, payload_fn):
    listing = make_listing(categories)

    response = offer_on(client_for(make_dealer()), listing, payload_fn(categories))

    assert response.status_code == 400
    assert not Offer.objects.exists()


def test_highest_allowed_rates_are_accepted(categories):
    listing = make_listing(categories)

    payload = rates(categories, paper=10_000_00, fridge=1_00_000_00)

    assert offer_on(client_for(make_dealer()), listing, payload).status_code == 201


@pytest.mark.parametrize(
    "setup",
    [
        lambda c, d: make_listing(c, status=ListingStatus.DRAFT),
        lambda c, d: make_listing(c, status=ListingStatus.OFFER_ACCEPTED),
        lambda c, d: make_listing(c, at=(SHOP[0] + 0.5, SHOP[1])),  # ~55 km away
        lambda c, d: make_listing(c, seller=d.user),  # own listing
        lambda c, d: make_listing(c, items=[("ewaste", "5", None)]),  # restricted
        lambda c, d: make_listing(c, published_ago=timedelta(days=8)),  # overdue
    ],
)
def test_offers_only_on_listings_the_dealer_can_see(categories, setup):
    dealer = make_dealer(roles=(Role.SELLER, Role.DEALER))
    listing = setup(categories, dealer)
    payload = {
        "rates": [{"category": i.category_id, "rate_paise": 100} for i in listing.items.all()]
    }

    response = offer_on(client_for(dealer), listing, payload)

    assert response.status_code == 404
    assert not Offer.objects.exists()


def test_authorised_dealer_can_offer_on_restricted_listings(categories):
    listing = make_listing(categories, items=[("ewaste", "5", None)])
    payload = {"rates": [{"category": categories["ewaste"].pk, "rate_paise": 4000}]}

    assert offer_on(client_for(make_dealer(ewaste=True)), listing, payload).status_code == 201


@pytest.mark.parametrize(
    "status", [DealerStatus.PENDING, DealerStatus.REJECTED, DealerStatus.SUSPENDED]
)
def test_unapproved_dealers_cannot_offer(categories, status):
    listing = make_listing(categories)

    response = offer_on(client_for(make_dealer(status=status)), listing, rates(categories))

    assert response.status_code == 403


def test_sellers_and_anonymous_users_cannot_offer(categories):
    listing = make_listing(categories)
    seller = APIClient()
    seller.force_login(User.objects.create_user("+919123456789"))

    assert offer_on(seller, listing, rates(categories)).status_code == 403
    assert offer_on(APIClient(), listing, rates(categories)).status_code == 403


# --- One active offer per dealer per listing -------------------------------


def test_a_second_active_offer_is_refused(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_on(client, listing, rates(categories))

    response = offer_on(client, listing, rates(categories, paper=1500))

    assert response.status_code == 409
    assert response.json()["code"] == "already_offered"
    assert Offer.objects.count() == 1


def test_database_allows_only_one_active_offer(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    expires = timezone.now() + timedelta(hours=1)
    Offer.objects.create(listing=listing, dealer=dealer, expires_at=expires)

    with pytest.raises(IntegrityError), transaction.atomic():
        Offer.objects.create(listing=listing, dealer=dealer, expires_at=expires)
    # Withdrawn or expired offers don't count.
    Offer.objects.create(
        listing=listing, dealer=dealer, expires_at=expires, status=OfferStatus.WITHDRAWN
    )


def test_after_withdrawing_the_dealer_can_offer_again(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    first = offer_on(client, listing, rates(categories)).json()["id"]
    client.post(f"/api/dealer/offers/{first}/withdraw")

    assert offer_on(client, listing, rates(categories)).status_code == 201


def test_an_offer_past_its_expiry_no_longer_blocks_a_new_one(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_on(client, listing, rates(categories))
    Offer.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    assert offer_on(client, listing, rates(categories)).status_code == 201
    assert list(Offer.objects.order_by("id").values_list("status", flat=True)) == [
        "expired",
        "active",
    ]


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_offers_from_one_dealer_make_only_one(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    barrier = threading.Barrier(2)
    codes = []

    def attempt():
        client = client_for(dealer)
        barrier.wait()
        codes.append(offer_on(client, listing, rates(categories)).status_code)
        connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(codes) == [201, 409]
    assert Offer.objects.filter(status=OfferStatus.ACTIVE).count() == 1


def test_different_dealers_can_each_offer(categories):
    listing = make_listing(categories)

    for phone in ("+919811111111", "+919822222222", "+919833333333"):
        assert (
            offer_on(client_for(make_dealer(phone)), listing, rates(categories)).status_code == 201
        )


# --- Editing and withdrawing -----------------------------------------------


def test_editing_changes_rates_and_restarts_the_expiry(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]
    Offer.objects.update(expires_at=timezone.now() + timedelta(hours=1))

    response = client.patch(
        f"/api/dealer/offers/{offer_id}", rates(categories, paper=1500), format="json"
    )

    assert response.status_code == 200
    assert response.json()["estimated_total_paise"] == 20 * 1500 + 2 * 150000
    assert Offer.objects.get().expires_at > timezone.now() + timedelta(hours=23)
    assert OfferRate.objects.filter(offer_id=offer_id).count() == 2


def test_withdrawing_an_offer(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]

    response = client.post(f"/api/dealer/offers/{offer_id}/withdraw")

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"


@pytest.mark.parametrize(
    "listing_status",
    [ListingStatus.OFFER_ACCEPTED, ListingStatus.CANCELLED, ListingStatus.EXPIRED],
)
def test_offers_freeze_once_the_listing_is_not_open(categories, listing_status):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]
    Listing.objects.filter(pk=listing.pk).update(status=listing_status)

    edit = client.patch(f"/api/dealer/offers/{offer_id}", rates(categories, paper=1), format="json")
    withdraw = client.post(f"/api/dealer/offers/{offer_id}/withdraw")

    assert (edit.status_code, withdraw.status_code) == (409, 409)
    assert edit.json()["code"] == "not_open"
    assert Offer.objects.get().status == OfferStatus.ACTIVE
    assert OfferRate.objects.get(offer_id=offer_id, category=categories["paper"]).rate_paise == 1400


@pytest.mark.parametrize(
    "offer_status", [OfferStatus.WITHDRAWN, OfferStatus.EXPIRED, OfferStatus.REJECTED]
)
def test_inactive_offers_cannot_be_edited_or_withdrawn(categories, offer_status):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]
    Offer.objects.update(status=offer_status)

    edit = client.patch(f"/api/dealer/offers/{offer_id}", rates(categories), format="json")
    withdraw = client.post(f"/api/dealer/offers/{offer_id}/withdraw")

    assert edit.json()["code"] == "not_live" and withdraw.status_code == 409


def test_an_offer_past_its_expiry_cannot_be_edited(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    client = client_for(dealer)
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]
    Offer.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = client.patch(f"/api/dealer/offers/{offer_id}", rates(categories), format="json")

    assert response.json()["code"] == "not_live"


# --- Ownership -------------------------------------------------------------


def test_dealers_only_see_and_change_their_own_offers(categories):
    listing = make_listing(categories)
    mine = client_for(make_dealer("+919811111111"))
    theirs = client_for(make_dealer("+919822222222"))
    offer_id = offer_on(theirs, listing, rates(categories)).json()["id"]

    assert mine.get("/api/dealer/offers").json() == []
    assert mine.get(f"/api/dealer/offers/{offer_id}").status_code == 404
    assert (
        mine.patch(f"/api/dealer/offers/{offer_id}", rates(categories), format="json").status_code
        == 404
    )
    assert mine.post(f"/api/dealer/offers/{offer_id}/withdraw").status_code == 404
    assert Offer.objects.get().status == OfferStatus.ACTIVE


def test_listing_page_shows_only_my_own_offer(categories):
    listing = make_listing(categories)
    mine = client_for(make_dealer("+919811111111"))
    theirs = client_for(make_dealer("+919822222222"))
    offer_on(theirs, listing, rates(categories, paper=9999))

    assert mine.get(f"/api/dealer/listings/{listing.pk}").json()["my_offer"] is None
    my_id = offer_on(mine, listing, rates(categories)).json()["id"]
    my_offer = mine.get(f"/api/dealer/listings/{listing.pk}").json()["my_offer"]
    assert my_offer["id"] == my_id and my_offer["rates"][0]["rate_paise"] == 1400


# --- Effects on the listing ------------------------------------------------


def test_listing_is_locked_for_the_seller_once_it_has_an_offer(categories):
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    assert is_editable(listing)
    offer_on(client_for(make_dealer()), listing, rates(categories))

    owner = APIClient()
    owner.force_login(seller)
    response = owner.patch(f"/api/listings/{listing.pk}", {"locality": "X"}, format="json")

    assert response.status_code == 409
    assert owner.get(f"/api/listings/{listing.pk}").json()["is_editable"] is False


def test_withdrawn_offers_dont_lock_the_listing(categories):
    listing = make_listing(categories)
    client = client_for(make_dealer())
    offer_id = offer_on(client, listing, rates(categories)).json()["id"]
    client.post(f"/api/dealer/offers/{offer_id}/withdraw")

    assert is_editable(Listing.objects.get(pk=listing.pk))


def test_expiring_a_listing_closes_its_active_offers(categories):
    listing = make_listing(categories, published_ago=timedelta(hours=1))
    offer_on(client_for(make_dealer()), listing, rates(categories))
    Listing.objects.filter(pk=listing.pk).update(published_at=timezone.now() - timedelta(days=8))

    assert expire_overdue_listings() == 1

    assert Offer.objects.get().status == OfferStatus.EXPIRED


# --- Seed ------------------------------------------------------------------


def test_seed_demo_offers():
    from django.core.management import call_command

    call_command("seed_demo")
    first = sorted(Offer.objects.values_list("id", flat=True))
    call_command("seed_demo")

    assert sorted(Offer.objects.values_list("id", flat=True)) == first
    assert Offer.objects.filter(status=OfferStatus.ACTIVE).count() == 6
    ewaste_offers = Offer.objects.filter(listing__items__category__is_restricted=True)
    assert set(ewaste_offers.values_list("dealer__user__phone", flat=True)) == {"+919000000006"}
    assert all(offer.is_live for offer in Offer.objects.all())
