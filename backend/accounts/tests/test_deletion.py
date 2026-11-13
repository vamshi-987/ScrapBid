import threading
from io import StringIO
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient

from accounts import otp
from accounts.models import OtpChallenge, Suspension, User
from audit.models import AuditLog
from dealers.models import DealerDocument, DealerProfile
from listings.models import Listing, ListingPhoto, ListingStatus
from notifications.models import Notification
from offers.models import Offer, OfferStatus
from offers.tests.conftest import categories  # noqa: F401  (shared fixture)
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import Due, DueKind, DueStatus, Order, OrderStatus
from orders.services import accept_offer
from orders.tests.conftest import clock  # noqa: F401  (shared fixture)
from orders.tests.test_endings import deal  # noqa: F401  (shared fixture)
from orders.tests.test_handover import complete, confirm
from orders.tests.test_weighing import lines, pickup, weigh  # noqa: F401  (pickup is a fixture)

pytestmark = pytest.mark.django_db

URL = "/api/auth/delete-account"
SELLER_PHONE = "+919876543210"  # the pickup fixture's seller
ADDRESS = "12-3-45 Secret Street"  # make_listing's address


@pytest.fixture
def completed(pickup):  # noqa: F811
    """The pickup weighed, confirmed and completed: a closed order with an amount."""
    assert weigh(pickup["dealer"], pickup, lines(pickup)).status_code == 200
    assert complete(pickup, confirm(pickup).json()["handover_code"]).status_code == 200
    pickup["order"] = Order.objects.get(pk=pickup["order"].pk)
    return pickup


def logged_in(user):
    client = APIClient()
    client.force_login(user)
    return client


def stored_file(prefix):
    return default_storage.save(f"{prefix}/{uuid4().hex}.jpg", ContentFile(b"data"))


def add_photo(listing):
    return ListingPhoto.objects.create(
        listing=listing,
        file=stored_file("listing-photos"),
        content_type="image/jpeg",
        width=1,
        height=1,
        size_bytes=4,
    )


def add_document(profile, doc_type="trade_licence"):
    return DealerDocument.objects.create(
        dealer=profile,
        doc_type=doc_type,
        file=stored_file("dealer-documents"),
        content_type="image/jpeg",
        size_bytes=4,
    )


def dump_database():
    out = StringIO()
    call_command("dumpdata", stdout=out, exclude=["contenttypes", "auth.permission"])
    return out.getvalue()


def unpaid_fee(user):
    suspension = Suspension.objects.create(user=user, reason="Upheld disputes", fee_paise=50000)
    return Due.objects.create(
        user=user, kind=DueKind.SUSPENSION_FEE, amount_paise=50000, suspension=suspension
    )


# --- Seller --------------------------------------------------------------------


def test_seller_deletion_anonymises_personal_data_and_keeps_the_records(
    completed,
    categories,  # noqa: F811
    django_capture_on_commit_callbacks,
):
    order = completed["order"]
    seller = order.seller
    open_listing = make_listing(categories, seller=seller)
    draft = make_listing(categories, seller=seller, status=ListingStatus.DRAFT)
    other_dealer = make_dealer("+919822222222")
    offer_id = offer_on(client_for(other_dealer), open_listing, rates(categories)).json()["id"]
    photo = add_photo(open_listing)
    otp.request_otp(SELLER_PHONE, "203.0.113.5")
    other_device = logged_in(seller)
    assert Notification.objects.filter(user=seller).exists()
    audit_before = AuditLog.objects.count()

    with django_capture_on_commit_callbacks(execute=True):
        response = completed["seller"].post(URL)

    assert response.status_code == 204
    user = User.objects.get(pk=seller.pk)
    assert user.phone == f"deleted-{user.pk}"
    assert (user.is_active, user.has_usable_password()) == (False, False)
    assert user.deleted_at is not None
    # Every session stops working: this one and the other device's.
    assert completed["seller"].get("/api/auth/me").status_code == 403
    assert other_device.get("/api/auth/me").status_code == 403
    # Addresses, coordinates and photos are gone from every listing.
    for listing in Listing.objects.filter(seller=user):
        assert (listing.address_line, listing.locality, listing.pincode) == ("", "", "")
        assert (listing.location.x, listing.location.y) == (0, 0)
    assert not ListingPhoto.objects.filter(pk=photo.pk).exists()
    assert not default_storage.exists(photo.file.name)
    assert not Notification.objects.filter(user=user).exists()
    assert not OtpChallenge.objects.exists()
    # Open and draft listings are cancelled with an audit row; their offers close.
    for listing in (open_listing, draft):
        listing.refresh_from_db()
        assert listing.status == ListingStatus.CANCELLED
        log = AuditLog.objects.filter(entity_type="listing", entity_id=listing.pk).last()
        assert (log.to_status, log.actor_id, log.reason) == (
            "cancelled",
            user.pk,
            "Account deleted",
        )
    offer = Offer.objects.get(pk=offer_id)
    assert (offer.status, offer.rejection_reason) == (OfferStatus.REJECTED, "listing_cancelled")
    # The completed order keeps its amounts; GPS points at the pickup are removed.
    saved = Order.objects.get(pk=order.pk)
    assert (saved.status, saved.final_amount_paise) == (OrderStatus.COMPLETED, 328000)
    assert saved.paid_amount_paise == 328000
    assert (saved.weighing_location, saved.dealer_check_in_location) == (None, None)
    assert saved.weighing_distance_m is not None and saved.items.count() == 2
    # Audit rows stay, plus one for the deletion.
    assert AuditLog.objects.count() == audit_before + 3
    assert AuditLog.objects.filter(entity_type="user", entity_id=user.pk, to_status="deleted")
    # Nothing in the database still holds the phone or the address.
    dump = dump_database()
    assert SELLER_PHONE not in dump and ADDRESS not in dump


def test_the_dealer_still_loads_the_completed_order(completed):
    completed["seller"].post(URL)

    response = completed["dealer"].get(f"/api/orders/{completed['order'].pk}")

    assert response.status_code == 200
    body = response.json()
    assert body["final_amount_paise"] == 328000
    assert body["locality"] == ""
    assert SELLER_PHONE not in response.content.decode()


def test_expired_and_completed_listings_keep_their_status(completed, categories):  # noqa: F811
    expired = make_listing(
        categories, seller=completed["order"].seller, status=ListingStatus.EXPIRED
    )

    completed["seller"].post(URL)

    expired.refresh_from_db()
    assert expired.status == ListingStatus.EXPIRED and expired.address_line == ""
    done = Listing.objects.get(pk=completed["order"].listing_id)
    assert done.status == ListingStatus.COMPLETED and done.address_line == ""


# --- Dealer --------------------------------------------------------------------


def test_dealer_deletion_removes_documents_and_withdraws_offers(
    completed,
    categories,  # noqa: F811
    django_capture_on_commit_callbacks,
):
    profile = completed["order"].dealer
    documents = [add_document(profile), add_document(profile, "id_proof")]
    open_listing = make_listing(categories)
    live_id = offer_on(completed["dealer"], open_listing, rates(categories)).json()["id"]

    with django_capture_on_commit_callbacks(execute=True):
        response = completed["dealer"].post(URL)

    assert response.status_code == 204
    profile.refresh_from_db()
    assert profile.business_name == "Deleted dealer"
    assert (profile.locality, profile.pincode, profile.id_type, profile.id_number_last4) == (
        "",
        "",
        "",
        "",
    )
    assert (profile.location.x, profile.location.y) == (0, 0)
    assert not DealerDocument.objects.filter(dealer=profile).exists()
    for document in documents:
        assert not default_storage.exists(document.file.name)
    assert Offer.objects.get(pk=live_id).status == OfferStatus.WITHDRAWN
    # The seller still loads the completed order, without the dealer's details.
    seller_view = completed["seller"].get(f"/api/orders/{completed['order'].pk}")
    assert seller_view.status_code == 200
    assert seller_view.json()["dealer_name"] == "Deleted dealer"
    assert seller_view.json()["final_amount_paise"] == 328000
    assert "+919811111111" not in dump_database()


def test_a_deleted_dealers_lost_offer_never_comes_back(completed, categories):  # noqa: F811
    seller = User.objects.create_user("+919833333333")
    listing = make_listing(categories, seller=seller)
    lost_id = offer_on(completed["dealer"], listing, rates(categories)).json()["id"]
    winner = make_dealer("+919844444444")
    won_id = offer_on(client_for(winner), listing, rates(categories, paper=1500)).json()["id"]
    order, _ = accept_offer(seller, Offer.objects.get(pk=won_id))
    assert Offer.objects.get(pk=lost_id).rejection_reason == "other_accepted"

    completed["dealer"].post(URL)
    # The winning dealer cancels: the listing reopens and lost offers are restored.
    cancel = client_for(winner).post(
        f"/api/orders/{order.pk}/cancel", {"reason": "Truck broke down"}, format="json"
    )

    assert cancel.status_code == 200
    assert Listing.objects.get(pk=listing.pk).status == ListingStatus.OPEN
    lost = Offer.objects.get(pk=lost_id)
    assert (lost.status, lost.rejection_reason) == (OfferStatus.REJECTED, "account_deleted")


def test_dual_role_user_has_listings_and_offers_closed(categories):  # noqa: F811
    profile = make_dealer("+919855555555", roles=("seller", "dealer"))
    client = client_for(profile)
    own = make_listing(categories, seller=profile.user)
    other = make_listing(categories)
    offer_id = offer_on(client, other, rates(categories)).json()["id"]

    assert client.post(URL).status_code == 204

    assert Listing.objects.get(pk=own.pk).status == ListingStatus.CANCELLED
    assert Offer.objects.get(pk=offer_id).status == OfferStatus.WITHDRAWN
    assert DealerProfile.objects.get(pk=profile.pk).business_name == "Deleted dealer"


# --- Refused while something is open (Q30, Q40) --------------------------------


def assert_unchanged(client, user, phone):
    user.refresh_from_db()
    assert (user.phone, user.is_active, user.deleted_at) == (phone, True, None)
    assert client.get("/api/auth/me").status_code == 200
    assert not AuditLog.objects.filter(entity_type="user").exists()


@pytest.fixture(params=["accepted", "scheduled", "picked_up", "disputed"])
def open_order(request, categories, clock):  # noqa: F811
    """An open order at each stage before it closes."""
    if request.param == "accepted":
        data = request.getfixturevalue("deal")
        return data
    data = request.getfixturevalue("pickup")
    if request.param in ("picked_up", "disputed"):
        assert weigh(data["dealer"], data, lines(data)).status_code == 200
    if request.param == "disputed":
        disputed = data["seller"].post(
            f"/api/orders/{data['order'].pk}/dispute", {"reason": "Scale was off"}, format="json"
        )
        assert disputed.status_code == 200
    return data


@pytest.mark.parametrize("side", ["seller", "dealer"])
def test_deletion_is_refused_while_an_order_is_open(open_order, side):
    order = Order.objects.get(pk=open_order["order"].pk)
    client = open_order[side]
    user = order.seller if side == "seller" else order.dealer.user
    photo = add_photo(order.listing)
    status_before = Listing.objects.get(pk=order.listing_id).status

    response = client.post(URL)

    assert response.status_code == 409
    assert response.json()["code"] == "active_order"
    assert_unchanged(client, user, user.phone)
    assert Listing.objects.get(pk=order.listing_id).status == status_before
    assert Listing.objects.get(pk=order.listing_id).address_line == ADDRESS
    assert ListingPhoto.objects.filter(pk=photo.pk).exists()
    assert default_storage.exists(photo.file.name)
    assert Order.objects.get(pk=order.pk).status == OrderStatus.ACTIVE


def test_deletion_is_refused_while_a_due_is_unpaid(categories):  # noqa: F811
    user = User.objects.create_user("+919866666666")
    client = logged_in(user)
    listing = make_listing(categories, seller=user)
    due = unpaid_fee(user)

    response = client.post(URL)

    assert response.status_code == 409
    assert response.json()["code"] == "unpaid_dues"
    assert_unchanged(client, user, "+919866666666")
    assert Listing.objects.get(pk=listing.pk).status == ListingStatus.OPEN
    assert Due.objects.get(pk=due.pk).status == DueStatus.UNPAID


@pytest.mark.parametrize("status", [DueStatus.PAID, DueStatus.CANCELLED])
def test_paid_and_cancelled_dues_do_not_block(status):
    user = User.objects.create_user("+919866666666")
    Due.objects.filter(pk=unpaid_fee(user).pk).update(status=status)

    assert logged_in(user).post(URL).status_code == 204


# --- Same phone again, access ---------------------------------------------------


def test_the_same_phone_registers_again_as_a_new_account(settings, completed):
    settings.DEV_FIXED_OTP = "123456"
    old_id = completed["order"].seller_id
    completed["seller"].post(URL)

    client = APIClient()
    assert (
        client.post("/api/auth/otp/request", {"phone": SELLER_PHONE}, format="json").status_code
        == 202
    )
    response = client.post(
        "/api/auth/otp/verify", {"phone": SELLER_PHONE, "code": "123456"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["id"] != old_id
    assert response.json()["phone"] == SELLER_PHONE
    assert client.get("/api/orders").json() == []
    assert client.get("/api/listings").json() == []


def test_anonymous_users_cannot_delete():
    assert APIClient().post(URL).status_code == 403


def test_deleting_needs_the_csrf_token():
    user = User.objects.create_user("+919877777777")
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)

    assert client.post(URL).status_code == 403
    assert User.objects.get(pk=user.pk).deleted_at is None


def test_only_your_own_account_is_deleted():
    user = User.objects.create_user("+919877777777")
    other = User.objects.create_user("+919888888888")
    client = logged_in(user)

    assert client.post(URL).status_code == 204
    # A retry from the same browser is no longer logged in and changes nothing.
    assert client.post(URL).status_code == 403
    other.refresh_from_db()
    assert (other.phone, other.is_active, other.deleted_at) == ("+919888888888", True, None)


def test_get_is_not_allowed():
    user = User.objects.create_user("+919877777777")

    assert logged_in(user).get(URL).status_code == 405
    assert User.objects.get(pk=user.pk).deleted_at is None


def test_admin_user_page_loads_for_a_deleted_user():
    admin_user = User.objects.create_user("+919899999999", is_staff=True, is_superuser=True)
    user = User.objects.create_user("+919877777777")
    logged_in(user).post(URL)

    admin = logged_in(admin_user)
    page = admin.get(f"/admin/accounts/user/{user.pk}/change/")
    user_list = admin.get("/admin/accounts/user/").content.decode()

    assert page.status_code == 200
    assert "+919877777777" not in page.content.decode()
    # The placeholder is shown as is in the masked phone column, not as "delXXXXXX…".
    assert f"deleted-{user.pk}" in user_list and "delXXXXXX" not in user_list


# --- Concurrency ----------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_deletion_racing_an_accept_never_leaves_an_open_order(categories):  # noqa: F811
    seller = User.objects.create_user("+919876500000")
    listing = make_listing(categories, seller=seller)
    offer_id = offer_on(client_for(make_dealer()), listing, rates(categories)).json()["id"]
    barrier = threading.Barrier(2)
    results = {}

    def run(name, call):
        client = logged_in(seller)
        barrier.wait()
        results[name] = call(client).status_code
        connection.close()

    threads = [
        threading.Thread(target=run, args=("delete", lambda c: c.post(URL))),
        threading.Thread(
            target=run,
            args=("accept", lambda c: c.post(f"/api/offers/{offer_id}/accept")),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    deleted = User.objects.get(pk=seller.pk).deleted_at is not None
    open_orders = Order.objects.filter(listing=listing, status=OrderStatus.ACTIVE).count()
    # Either the deletion won and the accept was refused (409, or 403 once logged out),
    # or the accept won and the deletion was refused. Never both.
    if deleted:
        assert open_orders == 0 and results["delete"] == 204 and results["accept"] in (403, 409)
    else:
        assert open_orders == 1 and results == {"delete": 409, "accept": 201}
