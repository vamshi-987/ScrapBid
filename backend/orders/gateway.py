"""Payment gateway adapters, chosen by PAYMENT_PROVIDER.

`razorpay`: Razorpay Payment Links over HTTPS with the standard library (no SDK).
`fake` (local only; production refuses it): keeps links in the cache and serves a local
pay page that sends the same signed callback, so the app runs the same code path.

The callback signature is HMAC-SHA256, keyed with the key secret, over
"link_id|reference_id|status|payment_id", as Razorpay's own SDK computes it.
"""

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache

RAZORPAY_API = "https://api.razorpay.com/v1"
FAKE_SECRET = "local-fake-gateway-secret"


class GatewayError(Exception):
    """The gateway couldn't be reached or refused the request."""


@dataclass(frozen=True)
class LinkStatus:
    status: str  # created | partially_paid | expired | cancelled | paid
    amount_paise: int
    amount_paid_paise: int
    payment_id: str


def sign(secret, link_id, reference_id, status, payment_id):
    message = f"{link_id}|{reference_id}|{status}|{payment_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class RazorpayGateway:
    def __init__(self):
        self.secret = settings.RAZORPAY_KEY_SECRET

    def _request(self, method, path, body=None):
        credentials = f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()
        request = urllib.request.Request(
            RAZORPAY_API + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Basic " + base64.b64encode(credentials).decode(),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise GatewayError(str(error)) from None

    def create_link(self, *, amount_paise, reference_id, description, callback_url):
        """Returns (link_id, url)."""
        data = self._request(
            "POST",
            "/payment_links",
            {
                "amount": amount_paise,
                "currency": "INR",
                "reference_id": reference_id,
                "description": description,
                "callback_url": callback_url,
                "callback_method": "get",
            },
        )
        return data["id"], data["short_url"]

    def fetch_link(self, link_id):
        data = self._request("GET", f"/payment_links/{link_id}")
        payments = data.get("payments") or []
        captured = [p for p in payments if p.get("status") == "captured"]
        return LinkStatus(
            status=data["status"],
            amount_paise=int(data["amount"]),
            amount_paid_paise=int(data.get("amount_paid") or 0),
            payment_id=captured[0]["payment_id"] if captured else "",
        )


class FakeGateway:
    """Local stand-in for Razorpay. Its pay page is served by `orders.views.FakePayView`."""

    secret = FAKE_SECRET

    @staticmethod
    def _key(link_id):
        return f"fake-payment-link:{link_id}"

    def create_link(self, *, amount_paise, reference_id, description, callback_url):
        link_id = f"plink_fake_{uuid4().hex[:14]}"
        cache.set(
            self._key(link_id),
            {
                "amount": amount_paise,
                "reference_id": reference_id,
                "description": description,
                "callback_url": callback_url,
                "status": "created",
                "payment_id": "",
            },
            timeout=None,
        )
        return link_id, f"{settings.APP_BASE_URL}/api/payments/fake/{link_id}"

    def get(self, link_id):
        return cache.get(self._key(link_id))

    def pay(self, link_id):
        """Simulate a successful payment. Returns the signed callback URL."""
        link = self.get(link_id)
        if link["status"] != "paid":
            link.update(status="paid", payment_id=f"pay_fake_{uuid4().hex[:14]}")
            cache.set(self._key(link_id), link, timeout=None)
        params = {
            "razorpay_payment_id": link["payment_id"],
            "razorpay_payment_link_id": link_id,
            "razorpay_payment_link_reference_id": link["reference_id"],
            "razorpay_payment_link_status": "paid",
        }
        params["razorpay_signature"] = sign(
            self.secret, link_id, link["reference_id"], "paid", link["payment_id"]
        )
        return f"{link['callback_url']}?{urllib.parse.urlencode(params)}"

    def fetch_link(self, link_id):
        link = self.get(link_id)
        if link is None:
            raise GatewayError("unknown link")
        paid = link["status"] == "paid"
        return LinkStatus(
            status=link["status"],
            amount_paise=link["amount"],
            amount_paid_paise=link["amount"] if paid else 0,
            payment_id=link["payment_id"],
        )


def get_gateway():
    if settings.PAYMENT_PROVIDER == "razorpay":
        return RazorpayGateway()
    if settings.PAYMENT_PROVIDER == "fake":
        return FakeGateway()
    raise GatewayError(f"Unknown PAYMENT_PROVIDER {settings.PAYMENT_PROVIDER!r}")


def callback_is_valid(params):
    """Check the gateway's callback signature (constant time)."""
    try:
        expected = sign(
            get_gateway().secret,
            params["razorpay_payment_link_id"],
            params["razorpay_payment_link_reference_id"],
            params["razorpay_payment_link_status"],
            params["razorpay_payment_id"],
        )
    except KeyError:
        return False
    return hmac.compare_digest(expected, params.get("razorpay_signature", ""))
