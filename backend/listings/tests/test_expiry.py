from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

import scheduler
from accounts.models import Role, User
from audit.models import AuditLog
from dealers.models import DealerProfile, DealerStatus, IdType
from listings.models import Category, Listing, ListingItem, ListingPhoto, ListingStatus

pytestmark = pytest.mark.django_db

DAY = timedelta(days=1)


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def paper():
    return Category.objects.create(name="Paper")


def make_listing(seller, paper, *, status=ListingStatus.OPEN, published_ago=None, photo=True):
    listing = Listing.objects.create(
        seller=seller,
        status=status,
        address_line="12-3-45 Secret Street",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
        published_at=(timezone.now() - published_ago) if published_ago is not None else None,
    )
    ListingItem.objects.create(listing=listing, category=paper, declared_weight_kg=Decimal("12.5"))
    if photo:
        ListingPhoto.objects.create(
            listing=listing,
            file="listing-photos/x.jpg",
            content_type="image/jpeg",
            width=1,
            height=1,
            size_bytes=1,
        )
    return listing


def expire():
    out = StringIO()
    call_command("expire_listings", stdout=out)
    return out.getvalue()


# --- The expiry job --------------------------------------------------------


def test_listings_older_than_the_limit_expire(seller, paper, settings):
    settings.LISTING_EXPIRY_DAYS = 7
    overdue = make_listing(seller, paper, published_ago=7 * DAY + timedelta(minutes=1))
    fresh = make_listing(seller, paper, published_ago=7 * DAY - timedelta(minutes=1))

    assert "Expired 1 listing(s)." in expire()

    overdue.refresh_from_db()
    fresh.refresh_from_db()
    assert overdue.status == ListingStatus.EXPIRED
    assert fresh.status == ListingStatus.OPEN
    log = AuditLog.objects.get(entity_id=overdue.pk)
    assert (log.from_status, log.to_status, log.actor) == ("open", "expired", None)


def test_the_limit_comes_from_config(seller, paper, settings):
    settings.LISTING_EXPIRY_DAYS = 2
    listing = make_listing(seller, paper, published_ago=3 * DAY)

    expire()

    listing.refresh_from_db()
    assert listing.status == ListingStatus.EXPIRED


@pytest.mark.parametrize(
    "status",
    [
        ListingStatus.DRAFT,
        ListingStatus.OFFER_ACCEPTED,
        ListingStatus.PICKUP_SCHEDULED,
        ListingStatus.COMPLETED,
        ListingStatus.CANCELLED,
    ],
)
def test_other_statuses_are_never_expired(seller, paper, status):
    listing = make_listing(seller, paper, status=status, published_ago=30 * DAY)

    expire()

    listing.refresh_from_db()
    assert listing.status == status
    assert not AuditLog.objects.exists()


def test_running_twice_changes_nothing_the_second_time(seller, paper):
    make_listing(seller, paper, published_ago=10 * DAY)

    expire()
    second = expire()

    assert "Expired 0 listing(s)." in second
    assert AuditLog.objects.count() == 1


def test_a_listing_that_changes_mid_run_is_skipped(seller, paper, monkeypatch):
    listing = make_listing(seller, paper, published_ago=10 * DAY)
    other = make_listing(seller, paper, published_ago=10 * DAY)

    from listings import services

    real_transition = services.transition

    def accept_first_then_expire(target, to_status, actor, **kwargs):
        if target.pk == listing.pk:
            # An offer is accepted between the job's query and its update.
            Listing.objects.filter(pk=listing.pk).update(status=ListingStatus.OFFER_ACCEPTED)
        return real_transition(target, to_status, actor, **kwargs)

    monkeypatch.setattr(services, "transition", accept_first_then_expire)

    assert "Expired 1 listing(s)." in expire()
    listing.refresh_from_db()
    other.refresh_from_db()
    assert listing.status == ListingStatus.OFFER_ACCEPTED
    assert other.status == ListingStatus.EXPIRED


def test_the_scheduler_runs_the_expiry_job():
    assert "expire_listings" in scheduler.JOBS


# --- Feed and owner view ---------------------------------------------------


def test_overdue_listing_leaves_the_feed_before_the_job_runs(seller, paper):
    dealer_user = User.objects.create_user("+919811111111", roles=[Role.DEALER])
    DealerProfile.objects.create(
        user=dealer_user,
        status=DealerStatus.APPROVED,
        business_name="D",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
        service_radius_km=10,
        id_type=IdType.PAN,
        id_number_last4="234F",
        scale_certificate_expires_on=timezone.localdate() + 100 * DAY,
    )
    fresh = make_listing(seller, paper, published_ago=DAY)
    make_listing(seller, paper, published_ago=8 * DAY)  # still OPEN in the database
    client = APIClient()
    client.force_login(dealer_user)

    ids = [item["id"] for item in client.get("/api/dealer/listings").json()["results"]]

    assert ids == [fresh.pk]


def test_owner_sees_when_an_open_listing_expires(seller, paper, settings):
    settings.LISTING_EXPIRY_DAYS = 7
    listing = make_listing(seller, paper, published_ago=DAY)
    draft = make_listing(seller, paper, status=ListingStatus.DRAFT)
    client = APIClient()
    client.force_login(seller)

    body = client.get(f"/api/listings/{listing.pk}").json()

    expected = listing.published_at + 7 * DAY
    assert timezone.datetime.fromisoformat(body["expires_at"]) == expected
    assert client.get(f"/api/listings/{draft.pk}").json()["expires_at"] is None


# --- Relisting -------------------------------------------------------------


def relist(client, listing):
    return client.post(f"/api/listings/{listing.pk}/relist")


@pytest.fixture
def client(seller):
    api = APIClient()
    api.force_login(seller)
    return api


def test_relisting_makes_a_new_draft_with_the_same_details_but_no_photos(client, seller, paper):
    expired = make_listing(seller, paper, status=ListingStatus.EXPIRED, published_ago=10 * DAY)

    response = relist(client, expired)

    assert response.status_code == 201
    draft = Listing.objects.get(pk=response.json()["id"])
    assert draft.status == ListingStatus.DRAFT
    assert draft.relisted_from == expired
    assert draft.address_line == expired.address_line
    assert draft.location == expired.location
    assert list(draft.items.values_list("category", "declared_weight_kg")) == [
        (paper.pk, Decimal("12.500"))
    ]
    assert draft.photos.count() == 0
    expired.refresh_from_db()
    assert expired.status == ListingStatus.EXPIRED  # the old listing is untouched


def test_relisting_twice_returns_the_same_draft(client, seller, paper):
    expired = make_listing(seller, paper, status=ListingStatus.EXPIRED)

    first = relist(client, expired)
    second = relist(client, expired)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert Listing.objects.filter(relisted_from=expired).count() == 1


def test_relisted_draft_needs_new_photos_to_publish(client, seller, paper):
    expired = make_listing(seller, paper, status=ListingStatus.EXPIRED)
    draft_id = relist(client, expired).json()["id"]

    assert client.post(f"/api/listings/{draft_id}/publish").json()["code"] == "needs_photo"


@pytest.mark.parametrize(
    "status", [ListingStatus.DRAFT, ListingStatus.OPEN, ListingStatus.CANCELLED]
)
def test_only_expired_listings_can_be_relisted(client, seller, paper, status):
    listing = make_listing(seller, paper, status=status)

    response = relist(client, listing)

    assert response.status_code == 409
    assert response.json()["code"] == "not_expired"


def test_others_cannot_relist(seller, paper):
    expired = make_listing(seller, paper, status=ListingStatus.EXPIRED)
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))
    dealer = APIClient()
    dealer.force_login(User.objects.create_user("+919811111111", roles=[Role.DEALER]))

    assert relist(other, expired).status_code == 404
    assert relist(dealer, expired).status_code == 403
    assert relist(APIClient(), expired).status_code == 403
    assert not Listing.objects.filter(relisted_from=expired).exists()
