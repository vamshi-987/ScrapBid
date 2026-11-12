from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Flag, FlagStatus, Suspension, User
from listings.models import ListingStatus
from listings.services import ListingError, publish_listing
from notifications.models import Notification, NotificationKind
from offers.models import Offer, OfferRate, OfferStatus
from offers.services import OfferError, create_offer, offers_for_seller, update_offer
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.disputes import open_dispute, resolve_dispute
from orders.misuse import flag_rate_outliers, lift_suspension, suspension_fee_paise
from orders.models import DisputeOutcome
from orders.services import AcceptError, accept_offer
from orders.tests.test_stats import deal, weigh

pytestmark = pytest.mark.django_db

PAPER = 1000  # peers' rate, paise per kg


# --- Rate flags ---------------------------------------------------------------------


def priced(
    categories, dealer, rate, *, ago=timedelta(days=1), status=OfferStatus.ACTIVE, name="paper"
):
    """An offer by `dealer` at `rate` for one category, made `ago`."""
    listing = make_listing(categories, items=[(name, "20", None)])
    offer = Offer.objects.create(
        listing=listing, dealer=dealer, status=status, expires_at=timezone.now() + timedelta(days=1)
    )
    OfferRate.objects.create(offer=offer, category=categories[name], rate_paise=rate)
    Offer.objects.filter(pk=offer.pk).update(created_at=timezone.now() - ago)
    return offer


@pytest.fixture
def peers(categories):
    """Three dealers offering ₹10/kg for paper."""
    return [priced(categories, make_dealer(f"+91982000000{i}"), PAPER).dealer for i in range(3)]


def flags():
    return list(Flag.objects.values_list("user__phone", "category__name", "status"))


@pytest.mark.parametrize(("rate", "flagged"), [(1200, False), (1201, True), (2000, True)])
def test_a_rate_more_than_20_percent_above_peers_is_flagged(categories, peers, rate, flagged):
    dealer = make_dealer("+919811111111")
    priced(categories, dealer, rate)

    raised = flag_rate_outliers()

    assert raised == (1 if flagged else 0)
    assert flags() == ([("+919811111111", "Paper", "open")] if flagged else [])


def test_flag_records_the_numbers(categories, peers):
    priced(categories, make_dealer("+919811111111"), 1500)

    flag_rate_outliers()

    assert Flag.objects.get().details == {
        "rate_paise": 1500,
        "peer_average_paise": 1000,
        "peers": 3,
        "above_percent": "50.0",
    }


def test_too_few_peers_means_no_flag(categories):
    for i in range(2):
        priced(categories, make_dealer(f"+91982000000{i}"), PAPER)
    priced(categories, make_dealer("+919811111111"), 5000)

    assert flag_rate_outliers() == 0


def test_each_dealer_counts_once_by_their_average(categories, peers):
    # A fourth peer with many low offers doesn't drag the peer average down by volume.
    busy = make_dealer("+919830000000")
    for _ in range(5):
        priced(categories, busy, 600)
    dealer = make_dealer("+919811111111")
    priced(categories, dealer, 1100)
    priced(categories, dealer, 1300)  # average 1200 vs peers (1000·3 + 600) / 4 = 900

    flag_rate_outliers()

    flag = Flag.objects.get(user=dealer.user)
    assert (flag.details["rate_paise"], flag.details["peer_average_paise"]) == (1200, 900)


def test_old_and_withdrawn_offers_are_left_out(categories, peers):
    dealer = make_dealer("+919811111111")
    priced(categories, dealer, 5000, ago=timedelta(days=31))
    priced(categories, dealer, 5000, status=OfferStatus.WITHDRAWN)

    assert flag_rate_outliers() == 0


def test_categories_are_compared_separately(categories, peers):
    dealer = make_dealer("+919811111111")
    priced(categories, dealer, 1000)
    for peer in peers:
        priced(categories, peer, 150000, name="fridge")
    priced(categories, dealer, 300000, name="fridge")

    flag_rate_outliers()

    assert flags() == [("+919811111111", "Appliance", "open")]


def test_repeat_runs_raise_one_flag_and_a_closed_one_isnt_raised_again(categories, peers):
    priced(categories, make_dealer("+919811111111"), 2000)

    assert flag_rate_outliers() == 1
    assert flag_rate_outliers() == 0
    Flag.objects.update(status=FlagStatus.CLOSED, note="Checked")
    assert flag_rate_outliers() == 0
    assert Flag.objects.count() == 1


def test_a_new_flag_after_the_window(categories, peers, clock):
    priced(categories, make_dealer("+919811111111"), 2000)
    flag_rate_outliers()
    Flag.objects.update(status=FlagStatus.CLOSED)

    clock.now = timezone.now() + timedelta(days=31)
    for peer in peers:
        priced(categories, peer, PAPER)
    priced(categories, Flag.objects.get().user.dealer_profile, 2000)

    assert flag_rate_outliers() == 1


def test_thresholds_come_from_config(categories, peers, settings):
    settings.RATE_FLAG_THRESHOLD_PERCENT = 50
    settings.RATE_FLAG_MIN_PEERS = 4
    priced(categories, make_dealer("+919811111111"), 2000)

    assert flag_rate_outliers() == 0
    settings.RATE_FLAG_MIN_PEERS = 3
    assert flag_rate_outliers() == 1


def test_the_job_is_scheduled_and_runs(categories, peers):
    from scheduler import JOBS

    priced(categories, make_dealer("+919811111111"), 2000)

    call_command("flag_rate_outliers")

    assert "flag_rate_outliers" in JOBS
    assert Flag.objects.count() == 1


# --- Suspension fee -------------------------------------------------------------------


def test_fee_is_500_then_doubles_from_the_third(settings):
    user = User.objects.create_user("+919811111111")
    fees = []
    for _ in range(4):
        fees.append(suspension_fee_paise(user))
        Suspension.objects.create(
            user=user, reason="x", fee_paise=fees[-1], lifted_at=timezone.now()
        )

    assert fees == [50000, 50000, 100000, 200000]
    settings.SUSPENSION_FEE_PAISE = 10000
    settings.SUSPENSION_FEE_DOUBLES_FROM = 2
    assert suspension_fee_paise(user) == 10000 * 2**4  # 5th suspension, doubling from the 2nd


# --- Automatic suspension --------------------------------------------------------------


@pytest.fixture
def disputes(categories, clock):
    """Returns a function that makes `n` resolved disputes against one dealer."""
    dealer = make_dealer("+919811111111")
    admin = User.objects.create_user("+919800000001", is_staff=True)

    def make(n, upheld=True):
        for _ in range(n):
            clock.now += timedelta(minutes=1)  # each dispute is resolved at its own time
            seller = User.objects.create_user(f"+9198765{User.objects.count():05d}")
            order = deal(categories, seller, dealer, [("paper", "20", None)])
            weigh(order, {"paper": "20"})
            open_dispute(order, seller, "Scale looked off")
            resolve_dispute(
                order,
                admin,
                outcome=DisputeOutcome.COMPLETED_MEASURED,
                note="Reviewed",
                upheld=upheld,
            )

    make.dealer = dealer
    make.admin = admin
    return make


def test_three_upheld_disputes_suspend_the_dealer(disputes):
    disputes(2)
    assert not Suspension.objects.exists()

    disputes(1)

    suspension = Suspension.objects.get()
    assert (suspension.user, suspension.fee_paise, suspension.lifted_at) == (
        disputes.dealer.user,
        50000,
        None,
    )
    assert suspension.reason == "3 upheld disputes"
    assert Notification.objects.filter(
        user=disputes.dealer.user, kind=NotificationKind.SUSPENDED
    ).exists()


def test_disputes_not_upheld_dont_count(disputes):
    disputes(3, upheld=False)
    disputes(2)

    assert not Suspension.objects.exists()


def test_after_lifting_three_more_upheld_disputes_are_needed(disputes):
    disputes(3)
    lift_suspension(Suspension.objects.get(), disputes.admin)

    disputes(2)
    assert Suspension.objects.filter(lifted_at=None).count() == 0
    disputes(1)

    assert Suspension.objects.filter(lifted_at=None).count() == 1
    assert Suspension.objects.count() == 2


def test_more_upheld_disputes_while_suspended_add_no_second_suspension(categories, clock, disputes):
    # Orders made before the suspension carry on, so more disputes can follow it.
    orders = []
    for _ in range(6):
        seller = User.objects.create_user(f"+9198765{User.objects.count():05d}")
        order = deal(categories, seller, disputes.dealer, [("paper", "20", None)])
        weigh(order, {"paper": "20"})
        orders.append((order, seller))

    for order, seller in orders:
        clock.now += timedelta(minutes=1)
        open_dispute(order, seller, "Scale looked off")
        resolve_dispute(
            order, disputes.admin, outcome=DisputeOutcome.CANCELLED, note="Faulty", upheld=True
        )

    assert Suspension.objects.count() == 1
    assert all(order.listing.orders.get().dispute_outcome == "cancelled" for order, _ in orders)


def test_the_limit_comes_from_config(disputes, settings):
    settings.AUTO_SUSPEND_UPHELD_DISPUTES = 1

    disputes(1)

    assert Suspension.objects.filter(lifted_at=None).count() == 1


# --- What a suspension blocks -------------------------------------------------------------


def suspend(user):
    return Suspension.objects.create(user=user, reason="test", fee_paise=50000)


def test_a_suspended_seller_cant_publish_or_accept(categories):
    seller = User.objects.create_user("+919876543210")
    draft = make_listing(categories, seller=seller, status=ListingStatus.DRAFT)
    from django.core.files.base import ContentFile

    from listings.photos import add_photo
    from orders.tests.test_weighing import scale_photo

    add_photo(draft, ContentFile(scale_photo().read(), name="p.jpg"))
    listing = make_listing(categories, seller=seller)
    offer_id = offer_on(client_for(make_dealer()), listing, rates(categories)).json()["id"]
    suspend(seller)

    with pytest.raises(ListingError) as error:
        publish_listing(draft, seller)
    assert error.value.code == "suspended"
    with pytest.raises(AcceptError) as error:
        accept_offer(seller, Offer.objects.get(pk=offer_id))
    assert error.value.code == "suspended"


def test_a_suspended_dealer_cant_offer_and_sellers_dont_see_their_offers(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    offer_id = offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    other = make_listing(categories)
    suspend(dealer.user)

    payload = {categories["paper"].pk: 1400, categories["fridge"].pk: 150000}
    with pytest.raises(OfferError) as error:
        create_offer(dealer, other.pk, payload)
    assert error.value.code == "suspended"
    with pytest.raises(OfferError) as error:
        update_offer(Offer.objects.get(pk=offer_id), payload)
    assert error.value.code == "suspended"
    assert list(offers_for_seller(listing)) == []
    with pytest.raises(AcceptError) as error:
        accept_offer(listing.seller, Offer.objects.get(pk=offer_id))
    assert error.value.code == "dealer_unavailable"


def test_suspended_users_can_still_log_in_and_see_the_fee(categories):
    user = User.objects.create_user("+919876543210")
    suspend(user)
    client = APIClient()
    client.force_login(user)

    me = client.get("/api/auth/me").json()

    assert me["suspension"]["fee_paise"] == 50000
    lift_suspension(Suspension.objects.get(), User.objects.create_user("+919800000001"))
    assert client.get("/api/auth/me").json()["suspension"] is None


def test_lifting_restores_access_and_is_recorded(categories):
    dealer = make_dealer()
    listing = make_listing(categories)
    admin = User.objects.create_user("+919800000001", is_staff=True)
    suspension = suspend(dealer.user)

    assert lift_suspension(suspension, admin) is True
    assert lift_suspension(suspension, admin) is False  # already lifted

    suspension.refresh_from_db()
    assert suspension.lifted_by == admin and suspension.lifted_at
    assert offer_on(client_for(dealer), listing, rates(categories)).status_code == 201
    assert Notification.objects.filter(kind=NotificationKind.SUSPENSION_LIFTED).count() == 1


# --- Admin ---------------------------------------------------------------------------


def admin_client():
    client = APIClient()
    client.force_login(User.objects.create_user("+919800000009", is_staff=True, is_superuser=True))
    return client


def test_admin_closes_flags_only_with_a_note(categories, peers):
    priced(categories, make_dealer("+919811111111"), 2000)
    flag_rate_outliers()
    client = admin_client()
    url = "/admin/accounts/flag/"
    assert "100.0% above, 3 peers" in client.get(url).content.decode()
    action = {"action": "close", "_selected_action": [Flag.objects.get().pk]}

    client.post(url, {**action, "note": ""})
    assert Flag.objects.get().status == FlagStatus.OPEN
    client.post(url, {**action, "note": "Rates are fine: premium paper"})

    flag = Flag.objects.get()
    assert (flag.status, flag.note) == (FlagStatus.CLOSED, "Rates are fine: premium paper")
    assert flag.closed_by and flag.closed_at


def test_admin_lifts_suspensions(categories):
    user = User.objects.create_user("+919876543210")
    suspension = suspend(user)

    admin_client().post(
        "/admin/accounts/suspension/", {"action": "lift", "_selected_action": [suspension.pk]}
    )

    assert Suspension.objects.get().lifted_at is not None


def test_non_staff_cant_open_flags_or_suspensions():
    client = APIClient()
    client.force_login(User.objects.create_user("+919876543210"))

    for url in ("/admin/accounts/flag/", "/admin/accounts/suspension/"):
        response = client.get(url)
        assert response.status_code == 302 and "/admin/login/" in response["Location"]


def test_seed_has_a_flagged_and_a_suspended_dealer():
    call_command("seed_demo")
    call_command("seed_demo")

    flag = Flag.objects.get()
    assert (flag.user.phone, flag.category.name, flag.status) == ("+919000000007", "Paper", "open")
    suspension = Suspension.objects.get()
    assert (suspension.user.phone, suspension.fee_paise, suspension.lifted_at) == (
        "+919000000008",
        50000,
        None,
    )
