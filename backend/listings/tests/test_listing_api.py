import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Role, User
from audit.models import AuditLog
from listings.models import Category, Listing, ListingStatus, PricingUnit
from listings.transitions import transition

pytestmark = pytest.mark.django_db

URL = "/api/listings"


@pytest.fixture
def categories():
    return {
        "paper": Category.objects.create(name="Paper"),
        "copper": Category.objects.create(name="Copper"),
        "appliance": Category.objects.create(name="Appliance", pricing_unit=PricingUnit.PER_ITEM),
        "glass": Category.objects.create(name="Glass", is_active=False),
    }


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def client(seller):
    api = APIClient()
    api.force_login(seller)
    return api


def body(categories, **overrides):
    data = {
        "address_line": "12-3-45, Street 6, Ameerpet",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4375,
        "longitude": 78.4482,
        "items": [{"category": categories["paper"].pk, "declared_weight_kg": "20"}],
    }
    data.update(overrides)
    return data


def create(client, categories, **overrides):
    return client.post(URL, body(categories, **overrides), format="json")


def photo():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buffer, format="JPEG")
    return SimpleUploadedFile("p.jpg", buffer.getvalue())


def add_photo(client, listing_id):
    return client.post(f"{URL}/{listing_id}/photos", {"file": photo()})


# --- Creating --------------------------------------------------------------


def test_create_makes_a_draft_with_items(client, categories, seller):
    response = create(
        client,
        categories,
        items=[
            {"category": categories["paper"].pk, "declared_weight_kg": "20.5"},
            {"category": categories["appliance"].pk, "declared_quantity": 2},
        ],
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["is_editable"] is True
    assert [
        (i["category_name"], i["declared_weight_kg"], i["declared_quantity"]) for i in data["items"]
    ] == [
        ("Paper", "20.500", None),
        ("Appliance", None, 2),
    ]
    listing = Listing.objects.get()
    assert listing.seller == seller
    assert listing.items.get(category=categories["paper"]).declared_weight_kg == Decimal("20.500")


def test_bulk_dealer_sized_listings_are_accepted(client, categories):
    response = create(
        client,
        categories,
        items=[{"category": categories["copper"].pk, "declared_weight_kg": "350.5"}],
    )

    assert response.status_code == 201


def test_largest_allowed_weight_is_accepted(client, categories, settings):
    settings.LISTING_MAX_WEIGHT_KG = 10_000
    items = [{"category": categories["paper"].pk, "declared_weight_kg": "10000"}]

    assert create(client, categories, items=items).status_code == 201


@pytest.mark.parametrize(
    "item",
    [
        {"declared_weight_kg": "0"},
        {"declared_weight_kg": "-3"},
        {"declared_weight_kg": "10000.001"},
        {"declared_weight_kg": "1.2345"},
        {},
        {"declared_weight_kg": "5", "declared_quantity": 1},
        {"declared_quantity": 3},
    ],
)
def test_invalid_per_kg_items_are_rejected(client, categories, item):
    items = [{"category": categories["paper"].pk, **item}]

    assert create(client, categories, items=items).status_code == 400
    assert Listing.objects.count() == 0


@pytest.mark.parametrize(
    "item",
    [{"declared_quantity": 0}, {"declared_quantity": 1001}, {"declared_weight_kg": "5"}, {}],
)
def test_invalid_per_item_items_are_rejected(client, categories, item):
    items = [{"category": categories["appliance"].pk, **item}]

    assert create(client, categories, items=items).status_code == 400


@pytest.mark.parametrize(
    "overrides",
    [
        {"items": []},
        {"pincode": "12345"},
        {"latitude": 51.5, "longitude": -0.12},
        {"address_line": ""},
    ],
)
def test_invalid_listings_are_rejected(client, categories, overrides):
    assert create(client, categories, **overrides).status_code == 400


def test_duplicate_or_inactive_or_unknown_categories_are_rejected(client, categories):
    duplicate = [
        {"category": categories["paper"].pk, "declared_weight_kg": "2"},
        {"category": categories["paper"].pk, "declared_weight_kg": "3"},
    ]
    inactive = [{"category": categories["glass"].pk, "declared_weight_kg": "2"}]
    unknown = [{"category": 999999, "declared_weight_kg": "2"}]

    for items in (duplicate, inactive, unknown):
        assert create(client, categories, items=items).status_code == 400
    assert Listing.objects.count() == 0


# --- Who may sell ----------------------------------------------------------


def test_anonymous_users_are_refused(categories):
    anonymous = APIClient()

    assert anonymous.get(URL).status_code == 403
    assert create(anonymous, categories).status_code == 403


def test_dealer_only_accounts_need_the_seller_role(categories):
    dealer = APIClient()
    dealer.force_login(User.objects.create_user("+919123456789", roles=[Role.DEALER]))

    assert create(dealer, categories).status_code == 403


def test_dual_role_dealer_uses_the_same_seller_flow(categories):
    dual = APIClient()
    dual.force_login(User.objects.create_user("+919123456789", roles=[Role.SELLER, Role.DEALER]))

    listing_id = create(dual, categories).json()["id"]
    add_photo(dual, listing_id)

    assert dual.post(f"{URL}/{listing_id}/publish").json()["status"] == "open"


def test_sellers_only_see_their_own_listings(client, categories):
    listing_id = create(client, categories).json()["id"]
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))

    assert other.get(URL).json() == []
    assert other.get(f"{URL}/{listing_id}").status_code == 404
    assert other.patch(f"{URL}/{listing_id}", {"locality": "X"}, format="json").status_code == 404
    assert other.post(f"{URL}/{listing_id}/publish").status_code == 404


# --- Privacy ---------------------------------------------------------------


def test_list_never_includes_the_exact_address(client, categories):
    create(client, categories)

    listed = client.get(URL).json()[0]
    detail = client.get(f"{URL}/{listed['id']}").json()

    assert "address_line" not in listed and "latitude" not in listed
    assert "12-3-45" not in str(listed)
    assert detail["address_line"] == "12-3-45, Street 6, Ameerpet"


# --- Publishing ------------------------------------------------------------


def test_publish_with_two_photos_and_twenty_kg_opens_the_listing(client, categories):
    listing_id = create(client, categories).json()["id"]
    assert add_photo(client, listing_id).status_code == 201
    assert add_photo(client, listing_id).status_code == 201

    response = client.post(f"{URL}/{listing_id}/publish")

    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["photo_count"] == 2
    assert response.json()["published_at"] is not None
    log = AuditLog.objects.get(entity_id=listing_id)
    assert (log.from_status, log.to_status) == ("draft", "open")


def test_publish_without_a_photo_is_refused(client, categories):
    listing_id = create(client, categories).json()["id"]

    response = client.post(f"{URL}/{listing_id}/publish")

    assert response.status_code == 400
    assert response.json()["code"] == "needs_photo"
    assert Listing.objects.get().status == ListingStatus.DRAFT


def test_publishing_twice_returns_409(client, categories):
    listing_id = create(client, categories).json()["id"]
    add_photo(client, listing_id)
    client.post(f"{URL}/{listing_id}/publish")

    response = client.post(f"{URL}/{listing_id}/publish")

    assert response.status_code == 409
    assert response.json()["code"] == "already_published"
    assert AuditLog.objects.filter(entity_id=listing_id).count() == 1


# --- Editing ---------------------------------------------------------------


def test_draft_and_open_listings_can_be_edited(client, categories):
    listing_id = create(client, categories).json()["id"]

    response = client.patch(
        f"{URL}/{listing_id}",
        {"items": [{"category": categories["copper"].pk, "declared_weight_kg": "7.25"}]},
        format="json",
    )
    assert response.status_code == 200
    assert [i["category_name"] for i in response.json()["items"]] == ["Copper"]

    add_photo(client, listing_id)
    client.post(f"{URL}/{listing_id}/publish")
    response = client.patch(f"{URL}/{listing_id}", {"locality": "Begumpet"}, format="json")
    assert response.status_code == 200
    assert response.json()["locality"] == "Begumpet"


@pytest.mark.parametrize("status", [ListingStatus.OFFER_ACCEPTED, ListingStatus.PICKUP_SCHEDULED])
def test_declared_weight_is_locked_after_an_offer_is_accepted(client, categories, seller, status):
    listing_id = create(client, categories).json()["id"]
    add_photo(client, listing_id)
    client.post(f"{URL}/{listing_id}/publish")
    listing = Listing.objects.get(pk=listing_id)
    transition(listing, ListingStatus.OFFER_ACCEPTED, seller)
    if status == ListingStatus.PICKUP_SCHEDULED:
        transition(listing, ListingStatus.PICKUP_SCHEDULED, seller)

    response = client.patch(
        f"{URL}/{listing_id}",
        {"items": [{"category": categories["paper"].pk, "declared_weight_kg": "99"}]},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "not_editable"
    assert Listing.objects.get(pk=listing_id).items.get().declared_weight_kg == Decimal("20")
    assert client.get(f"{URL}/{listing_id}").json()["is_editable"] is False


def test_partial_edit_with_only_latitude_is_rejected(client, categories):
    listing_id = create(client, categories).json()["id"]

    assert client.patch(f"{URL}/{listing_id}", {"latitude": 17.4}, format="json").status_code == 400


# --- Seed ------------------------------------------------------------------


def test_seed_demo_listings(django_capture_on_commit_callbacks):
    call_command("seed_demo")
    first_ids = sorted(Listing.objects.values_list("id", flat=True))
    call_command("seed_demo")

    assert sorted(Listing.objects.values_list("id", flat=True)) == first_ids
    statuses = sorted(Listing.objects.values_list("status", flat=True))
    assert statuses == [
        "cancelled",
        "draft",
        "expired",
        "offer_accepted",
        "open",
        "open",
        "open",
        "pickup_scheduled",
    ]
    open_listings = Listing.objects.filter(status=ListingStatus.OPEN)
    assert all(listing.photos.exists() for listing in open_listings)
    assert AuditLog.objects.filter(entity_type="listing", to_status="open").count() == 8
    assert (
        AuditLog.objects.filter(entity_type="listing", to_status="expired", actor=None).count() == 1
    )
    bulk = Listing.objects.get(seller__phone="+919000000004")
    assert bulk.items.get().declared_weight_kg == Decimal("350.500")
    restricted = Listing.objects.filter(items__category__is_restricted=True)
    assert list(restricted.values_list("seller__phone", flat=True)) == ["+919000000003"]
