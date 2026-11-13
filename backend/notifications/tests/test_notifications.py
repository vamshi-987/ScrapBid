from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from dealers.models import DealerProfile, DealerStatus, IdType
from listings.models import Category, Listing, ListingItem, ListingStatus
from notifications.models import Notification, NotificationKind
from notifications.services import notify
from offers.services import OfferError, create_offer

pytestmark = pytest.mark.django_db

URL = "/api/notifications"


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def listing(seller):
    listing = Listing.objects.create(
        seller=seller,
        status=ListingStatus.OPEN,
        address_line="12-3-45 Secret Street",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
        published_at=timezone.now(),
    )
    ListingItem.objects.create(
        listing=listing,
        category=Category.objects.create(name="Paper"),
        declared_weight_kg=Decimal("20"),
    )
    return listing


def make_dealer(phone="+919811111111"):
    return DealerProfile.objects.create(
        user=User.objects.create_user(phone, roles=[Role.DEALER]),
        status=DealerStatus.APPROVED,
        business_name="Dealer",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
        service_radius_km=10,
        id_type=IdType.PAN,
        id_number_last4="234F",
        scale_certificate_expires_on=timezone.localdate() + timedelta(days=100),
    )


def offer(dealer, listing, paise=1400):
    category = listing.items.get().category_id
    return create_offer(dealer, listing.pk, {category: paise})


def client_for(user):
    client = APIClient()
    client.force_login(user)
    return client


# --- Created by new offers -------------------------------------------------


def test_a_new_offer_notifies_the_seller_once(seller, listing):
    dealer = make_dealer()

    made = offer(dealer, listing)

    note = Notification.objects.get()
    assert note.user == seller
    assert note.kind == NotificationKind.NEW_OFFER
    assert note.data == {"listing_id": listing.pk, "offer_id": made.pk}
    assert not Notification.objects.filter(user=dealer.user).exists()


def test_a_refused_offer_creates_no_notification(listing):
    dealer = make_dealer()
    offer(dealer, listing)

    with pytest.raises(OfferError):
        offer(dealer, listing)  # second active offer is refused

    assert Notification.objects.count() == 1


def test_editing_or_withdrawing_does_not_notify(listing):
    from offers.services import update_offer, withdraw_offer

    made = offer(make_dealer(), listing)
    update_offer(made, {listing.items.get().category_id: 1500})
    withdraw_offer(made)

    assert Notification.objects.count() == 1


def test_each_dealers_offer_is_its_own_notification(seller, listing):
    for phone in ("+919811111111", "+919822222222"):
        offer(make_dealer(phone), listing)

    assert Notification.objects.filter(user=seller).count() == 2


def test_notifications_carry_ids_only(seller):
    with pytest.raises(ValueError):
        notify(seller, NotificationKind.NEW_OFFER, listing_id=1, phone="+919876543210")


def test_notification_response_has_no_personal_data(seller, listing):
    offer(make_dealer(), listing)

    content = client_for(seller).get(URL).content.decode()

    for secret in ("Secret Street", "9876543210", "9811111111", "Dealer"):
        assert secret not in content


# --- API -------------------------------------------------------------------


def test_list_is_newest_first_and_paginated(seller):
    for i in range(23):
        notify(seller, NotificationKind.NEW_OFFER, listing_id=i, offer_id=i)

    first = client_for(seller).get(URL).json()

    assert first["count"] == 23 and len(first["results"]) == 20 and first["next"]
    assert first["results"][0]["data"]["listing_id"] == 22


def test_unread_count_and_marking_read(seller):
    a = notify(seller, NotificationKind.NEW_OFFER, listing_id=1, offer_id=1)
    notify(seller, NotificationKind.NEW_OFFER, listing_id=2, offer_id=2)
    client = client_for(seller)
    assert client.get(f"{URL}/unread-count").json() == {"unread": 2}

    response = client.post(f"{URL}/{a.pk}/read")

    assert response.status_code == 200 and response.json()["read_at"] is not None
    assert client.get(f"{URL}/unread-count").json() == {"unread": 1}
    first_read_at = Notification.objects.get(pk=a.pk).read_at
    client.post(f"{URL}/{a.pk}/read")  # marking again keeps the first time
    assert Notification.objects.get(pk=a.pk).read_at == first_read_at

    assert client.post(f"{URL}/read-all").status_code == 204
    assert client.get(f"{URL}/unread-count").json() == {"unread": 0}


def test_users_only_see_and_mark_their_own(seller):
    note = notify(seller, NotificationKind.NEW_OFFER, listing_id=1, offer_id=1)
    other = client_for(User.objects.create_user("+919123456789"))

    assert other.get(URL).json()["count"] == 0
    assert other.get(f"{URL}/unread-count").json() == {"unread": 0}
    assert other.post(f"{URL}/{note.pk}/read").status_code == 404
    other.post(f"{URL}/read-all")
    assert Notification.objects.get(pk=note.pk).read_at is None


def test_anonymous_users_are_refused():
    anonymous = APIClient()

    assert anonymous.get(URL).status_code == 403
    assert anonymous.get(f"{URL}/unread-count").status_code == 403
    assert anonymous.post(f"{URL}/read-all").status_code == 403


# --- Seed ------------------------------------------------------------------


def test_seeded_offers_give_sellers_notifications_without_duplicates():
    call_command("seed_demo")
    call_command("seed_demo")

    by_seller = {
        phone: Notification.objects.filter(user__phone=phone).count()
        for phone in ("+919000000002", "+919000000003", "+919000000004")
    }
    # Seller 2's missed pickup adds 2 new offers, the proposed slot and the missed pickup.
    assert by_seller == {"+919000000002": 14, "+919000000003": 10, "+919000000004": 2}
    assert not Notification.objects.filter(read_at__isnull=False).exists()


def test_rolled_back_offer_leaves_no_notification(listing):
    from django.db import transaction

    with pytest.raises(RuntimeError), transaction.atomic():
        offer(make_dealer(), listing)
        raise RuntimeError("a later step failed")

    assert not Notification.objects.exists()
