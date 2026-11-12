import base64
import hashlib
import hmac
import io
import json
import urllib.error
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from rest_framework.test import APIClient

from accounts.models import Suspension, User
from orders import gateway
from orders.endings import resolve_strike
from orders.gateway import FakeGateway, RazorpayGateway, sign
from orders.misuse import lift_suspension
from orders.models import Due, DueKind, DueStatus, NoShowStrike, Party
from orders.payments import add_no_show_penalty, add_suspension_fee
from orders.tests.test_stats import deal

pytestmark = pytest.mark.django_db


@pytest.fixture
def penalty(categories, clock):
    """An unpaid ₹100 no-show penalty for a dealer, and their logged-in client."""
    from offers.tests.test_offers import make_dealer

    dealer = make_dealer("+919811111111")
    seller = User.objects.create_user("+919876500001")
    order = deal(categories, seller, dealer, [("paper", "20", None)])
    strike = NoShowStrike.objects.create(user=dealer.user, order=order, party=Party.DEALER)
    due = add_no_show_penalty(strike)
    client = APIClient()
    client.force_login(dealer.user)
    return {"due": due, "client": client, "user": dealer.user, "strike": strike}


def pay_url(due):
    return f"/api/dues/{due.pk}/pay"


def press(client, path, result):
    """Submit the fake pay page's form the way a browser does (URL-encoded)."""
    return client.post(
        path, urlencode({"result": result}), content_type="application/x-www-form-urlencoded"
    )


def pay_on_fake_gateway(client, due):
    """Start paying, press Pay on the fake page and follow the callback. Returns the final
    redirect (to the dues page)."""
    url = client.post(pay_url(due)).json()["url"]
    path = urlsplit(url).path
    assert client.get(path).status_code == 200
    to_callback = press(client, path, "pay")
    assert to_callback.status_code == 302
    callback = urlsplit(to_callback["Location"])
    return client.get(callback.path, parse_qs(callback.query))


def callback_params(due):
    """The signed parameters the fake gateway would send back for a paid link."""
    location = FakeGateway().pay(due.link_id)
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def status_of(due):
    return Due.objects.get(pk=due.pk).status


# --- The penalty and the dues list ------------------------------------------------------


def test_a_no_show_penalty_is_100_rupees_and_blocks_until_paid(penalty):
    assert (penalty["due"].kind, penalty["due"].amount_paise) == (DueKind.NO_SHOW_PENALTY, 10000)
    assert penalty["client"].get("/api/auth/me").json()["blocked"] is True


def test_the_penalty_amount_comes_from_config(penalty, categories, settings):
    settings.NO_SHOW_PENALTY_PAISE = 25000
    seller = User.objects.create_user("+919876500003")
    from offers.tests.test_offers import make_dealer

    order = deal(categories, seller, make_dealer("+919811111119"), [("paper", "5", None)])
    strike = NoShowStrike.objects.create(user=seller, order=order, party=Party.SELLER)

    assert add_no_show_penalty(strike).amount_paise == 25000


def test_dues_list_shows_only_mine_unpaid_first(penalty):
    other = APIClient()
    other.force_login(User.objects.create_user("+919876500002"))

    mine = penalty["client"].get("/api/dues").json()

    assert [(d["kind"], d["amount_paise"], d["status"]) for d in mine] == [
        ("no_show_penalty", 10000, "unpaid")
    ]
    assert other.get("/api/dues").json() == []
    assert APIClient().get("/api/dues").status_code == 403


def test_strike_shows_its_penalty(penalty):
    strikes = penalty["client"].get("/api/strikes").json()

    assert strikes[0]["penalty"] == {
        "id": penalty["due"].pk,
        "amount_paise": 10000,
        "status": "unpaid",
    }


# --- Paying through the (fake) gateway ----------------------------------------------------


def test_paying_lifts_the_block(penalty):
    final = pay_on_fake_gateway(penalty["client"], penalty["due"])

    assert (final.status_code, final["Location"]) == (302, "/dues?payment=paid")
    due = Due.objects.get(pk=penalty["due"].pk)
    assert due.status == DueStatus.PAID and due.paid_at and due.payment_id.startswith("pay_fake_")
    assert penalty["client"].get("/api/auth/me").json()["blocked"] is False


def test_the_payment_link_is_made_once_with_the_servers_amount(penalty, monkeypatch):
    calls = []
    real = FakeGateway.create_link

    def spy(self, **kwargs):
        calls.append(kwargs)
        return real(self, **kwargs)

    monkeypatch.setattr(FakeGateway, "create_link", spy)
    client = penalty["client"]

    first = client.post(pay_url(penalty["due"]), {"amount_paise": 1}, format="json").json()
    again = client.post(pay_url(penalty["due"])).json()

    assert first == again
    assert len(calls) == 1 and calls[0]["amount_paise"] == 10000
    assert calls[0]["callback_url"] == "http://localhost/api/payments/callback"


def test_only_the_owner_can_pay_or_check(penalty):
    stranger = APIClient()
    stranger.force_login(User.objects.create_user("+919876500002"))

    assert stranger.post(pay_url(penalty["due"])).status_code == 404
    assert stranger.post(f"/api/dues/{penalty['due'].pk}/check").status_code == 404
    assert APIClient().post(pay_url(penalty["due"])).status_code == 403


def test_a_paid_or_cancelled_due_cant_be_paid_again(penalty):
    pay_on_fake_gateway(penalty["client"], penalty["due"])

    response = penalty["client"].post(pay_url(penalty["due"]))

    assert (response.status_code, response.json()["code"]) == (409, "not_unpaid")


def test_a_forged_callback_changes_nothing(penalty):
    client = penalty["client"]
    client.post(pay_url(penalty["due"]))
    due = Due.objects.get(pk=penalty["due"].pk)
    forged = {
        "razorpay_payment_id": "pay_forged",
        "razorpay_payment_link_id": due.link_id,
        "razorpay_payment_link_reference_id": "x",
        "razorpay_payment_link_status": "paid",
        "razorpay_signature": "0" * 64,
    }

    for params in (forged, {**forged, "razorpay_signature": ""}, {}):
        response = client.get("/api/payments/callback", params)
        assert response["Location"] == "/dues?payment=failed"
    assert status_of(due) == DueStatus.UNPAID


def test_a_correctly_signed_callback_still_needs_the_gateway_to_say_paid(penalty):
    client = penalty["client"]
    client.post(pay_url(penalty["due"]))
    due = Due.objects.get(pk=penalty["due"].pk)
    link = FakeGateway().get(due.link_id)
    # Signed with the right secret, but the gateway still has the link as unpaid.
    params = {
        "razorpay_payment_id": "pay_x",
        "razorpay_payment_link_id": due.link_id,
        "razorpay_payment_link_reference_id": link["reference_id"],
        "razorpay_payment_link_status": "paid",
    }
    params["razorpay_signature"] = sign(
        FakeGateway.secret, due.link_id, link["reference_id"], "paid", "pay_x"
    )

    assert client.get("/api/payments/callback", params)["Location"] == "/dues?payment=failed"
    assert status_of(due) == DueStatus.UNPAID


def test_a_partial_payment_doesnt_count(penalty, monkeypatch):
    client = penalty["client"]
    client.post(pay_url(penalty["due"]))
    due = Due.objects.get(pk=penalty["due"].pk)
    params = callback_params(due)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_link",
        lambda self, link_id: gateway.LinkStatus("paid", 10000, 9999, "pay_x"),
    )

    assert client.get("/api/payments/callback", params)["Location"] == "/dues?payment=failed"
    assert status_of(due) == DueStatus.UNPAID


def test_replaying_the_callback_records_the_payment_once(penalty):
    client = penalty["client"]
    client.post(pay_url(penalty["due"]))
    due = Due.objects.get(pk=penalty["due"].pk)
    params = callback_params(due)

    client.get("/api/payments/callback", params)
    paid_at = Due.objects.get(pk=due.pk).paid_at
    again = client.get("/api/payments/callback", params)

    assert again["Location"] == "/dues?payment=paid"
    assert Due.objects.get(pk=due.pk).paid_at == paid_at


def test_check_finds_a_payment_whose_redirect_was_missed(penalty):
    client = penalty["client"]
    check = f"/api/dues/{penalty['due'].pk}/check"
    assert client.post(check).json()["code"] == "not_paid"  # no link yet
    client.post(pay_url(penalty["due"]))
    assert client.post(check).json()["code"] == "not_paid"  # link not paid yet

    FakeGateway().pay(Due.objects.get(pk=penalty["due"].pk).link_id)  # paid, never redirected

    response = client.post(check)
    assert (response.status_code, response.json()["status"]) == (200, "paid")


def test_failing_on_the_fake_page_records_nothing(penalty):
    url = penalty["client"].post(pay_url(penalty["due"])).json()["url"]

    response = press(penalty["client"], urlsplit(url).path, "fail")

    assert response["Location"] == "/dues?payment=failed"
    assert status_of(penalty["due"]) == DueStatus.UNPAID


def test_the_fake_page_exists_only_with_the_fake_provider(penalty, settings):
    url = penalty["client"].post(pay_url(penalty["due"])).json()["url"]
    settings.PAYMENT_PROVIDER = "razorpay"

    assert penalty["client"].get(urlsplit(url).path).status_code == 404
    assert press(penalty["client"], urlsplit(url).path, "pay").status_code == 404


# --- Strikes, suspensions and refunds -------------------------------------------------------


def test_removing_a_strike_cancels_its_unpaid_penalty(penalty):
    resolve_strike(penalty["strike"], User.objects.create_user("+919800000001"), uphold=False)

    assert status_of(penalty["due"]) == DueStatus.CANCELLED
    assert penalty["client"].get("/api/auth/me").json()["blocked"] is False
    assert penalty["client"].post(pay_url(penalty["due"])).json()["code"] == "not_unpaid"


def test_removing_a_strike_never_refunds_a_paid_penalty(penalty):
    pay_on_fake_gateway(penalty["client"], penalty["due"])

    resolve_strike(penalty["strike"], User.objects.create_user("+919800000001"), uphold=False)

    assert status_of(penalty["due"]) == DueStatus.PAID


def test_upholding_a_strike_keeps_the_penalty(penalty):
    resolve_strike(penalty["strike"], User.objects.create_user("+919800000001"), uphold=True)

    assert status_of(penalty["due"]) == DueStatus.UNPAID


@pytest.fixture
def suspended():
    user = User.objects.create_user("+919811111112")
    suspension = Suspension.objects.create(user=user, reason="test", fee_paise=50000)
    due = add_suspension_fee(suspension)
    client = APIClient()
    client.force_login(user)
    return {"user": user, "suspension": suspension, "due": due, "client": client}


def test_paying_the_suspension_fee_lifts_the_suspension(suspended):
    assert suspended["client"].get("/api/auth/me").json()["suspension"]["fee_paise"] == 50000

    pay_on_fake_gateway(suspended["client"], suspended["due"])

    suspension = Suspension.objects.get()
    assert suspension.lifted_at is not None and suspension.lifted_by is None
    assert suspended["client"].get("/api/auth/me").json()["suspension"] is None
    assert status_of(suspended["due"]) == DueStatus.PAID


def test_an_admin_lifting_a_suspension_cancels_the_unpaid_fee(suspended):
    lift_suspension(suspended["suspension"], User.objects.create_user("+919800000001"))

    assert status_of(suspended["due"]) == DueStatus.CANCELLED


# --- The Razorpay adapter (network stubbed) ----------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_razorpay_create_link_sends_basic_auth_and_the_amount(monkeypatch, settings):
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "rzp_test_id", "secret"
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(
            url=request.full_url, method=request.get_method(), headers=dict(request.headers)
        )
        sent["body"] = json.loads(request.data)
        return FakeResponse(json.dumps({"id": "plink_1", "short_url": "https://rzp.io/x"}).encode())

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)

    link = RazorpayGateway().create_link(
        amount_paise=10000,
        reference_id="due-1-ab",
        description="No-show penalty",
        callback_url="cb",
    )

    assert link == ("plink_1", "https://rzp.io/x")
    assert (sent["url"], sent["method"]) == ("https://api.razorpay.com/v1/payment_links", "POST")
    assert (
        sent["headers"]["Authorization"]
        == "Basic " + base64.b64encode(b"rzp_test_id:secret").decode()
    )
    assert sent["body"] == {
        "amount": 10000,
        "currency": "INR",
        "reference_id": "due-1-ab",
        "description": "No-show penalty",
        "callback_url": "cb",
        "callback_method": "get",
    }


def test_razorpay_fetch_link_reads_the_captured_payment(monkeypatch):
    body = {
        "status": "paid",
        "amount": 10000,
        "amount_paid": 10000,
        "payments": [{"payment_id": "pay_9", "status": "captured", "amount": 10000}],
    }
    monkeypatch.setattr(
        gateway.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(json.dumps(body).encode()),
    )

    assert RazorpayGateway().fetch_link("plink_1") == gateway.LinkStatus(
        "paid", 10000, 10000, "pay_9"
    )


def test_gateway_outage_gives_a_clear_error(penalty, monkeypatch, settings):
    settings.PAYMENT_PROVIDER = "razorpay"

    def down(request, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(gateway.urllib.request, "urlopen", down)

    response = penalty["client"].post(pay_url(penalty["due"]))

    assert (response.status_code, response.json()["code"]) == (503, "gateway_unavailable")
    assert status_of(penalty["due"]) == DueStatus.UNPAID


def test_signature_matches_razorpays_scheme(settings):
    settings.PAYMENT_PROVIDER = "razorpay"
    settings.RAZORPAY_KEY_SECRET = "secret"
    expected = hmac.new(b"secret", b"plink_1|due-1|paid|pay_9", hashlib.sha256).hexdigest()
    params = {
        "razorpay_payment_link_id": "plink_1",
        "razorpay_payment_link_reference_id": "due-1",
        "razorpay_payment_link_status": "paid",
        "razorpay_payment_id": "pay_9",
        "razorpay_signature": expected,
    }

    assert gateway.callback_is_valid(params) is True
    assert gateway.callback_is_valid({**params, "razorpay_payment_id": "pay_8"}) is False
    settings.RAZORPAY_KEY_SECRET = "other"
    assert gateway.callback_is_valid(params) is False
