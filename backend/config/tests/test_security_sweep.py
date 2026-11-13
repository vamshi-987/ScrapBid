"""Whole-API checks: every endpoint's access rules, input handling, privacy and retries.

Feature tests cover each rule where it's built; these prove it for every endpoint at once.
A new endpoint fails `test_every_endpoint_is_in_the_table` until it's added here.
"""

import json
import re
from dataclasses import dataclass, field

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from dealers.models import DealerStatus
from listings.models import ListingPhoto
from notifications.models import Notification
from offers.models import Offer
from offers.tests.conftest import categories  # noqa: F401  (shared fixture)
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates
from orders.models import Due, DueKind, NoShowStrike, Order, Party
from orders.tests.conftest import clock  # noqa: F401  (shared fixture)
from orders.tests.test_weighing import pickup  # noqa: F401  (shared fixture)

pytestmark = pytest.mark.django_db

# --- The endpoint table -----------------------------------------------------------
#
# access: "public"  no login needed (on purpose)
#         "user"    any logged-in account
#         "seller"  needs the seller role
#         "dealer"  needs an approved dealer profile
# owner:  who the object in the URL belongs to; outsiders must get 404.


@dataclass
class Endpoint:
    route: str
    methods: tuple
    access: str
    url: object = None  # function(world) -> path; defaults to the route itself
    owner: str = ""  # "seller" | "dealer" | "parties"
    owner_methods: tuple = field(default=())  # methods that check ownership (default: all)

    def path(self, world):
        return self.url(world) if self.url else "/" + self.route


def _listing(w):
    return w["listing"].pk


def _order(w):
    return w["order"].pk


ENDPOINTS = [
    # Public on purpose.
    Endpoint("admin/login/", ("get",), "public"),
    Endpoint("api/health", ("get",), "public"),
    Endpoint("api/auth/csrf", ("get",), "public"),
    Endpoint("api/auth/otp/request", ("post",), "public"),
    Endpoint("api/auth/otp/verify", ("post",), "public"),
    Endpoint("api/payments/callback", ("get",), "public"),
    Endpoint(
        "api/payments/fake/<str:link_id>",
        ("get", "post"),
        "public",
        lambda w: "/api/payments/fake/x",
    ),
    # Account.
    Endpoint("api/auth/logout", ("post",), "user"),
    Endpoint("api/auth/me", ("get",), "user"),
    Endpoint("api/auth/delete-account", ("post",), "user"),
    Endpoint("api/categories", ("get",), "user"),
    Endpoint("api/notifications", ("get",), "user"),
    Endpoint("api/notifications/unread-count", ("get",), "user"),
    Endpoint("api/notifications/read-all", ("post",), "user"),
    Endpoint(
        "api/notifications/<int:notification_id>/read",
        ("post",),
        "user",
        lambda w: f"/api/notifications/{w['notification'].pk}/read",
        owner="seller",
    ),
    # Seller listings.
    Endpoint("api/listings", ("get", "post"), "seller"),
    Endpoint(
        "api/listings/<int:listing_id>",
        ("get", "patch"),
        "seller",
        lambda w: f"/api/listings/{_listing(w)}",
        owner="seller",
    ),
    *[
        Endpoint(
            f"api/listings/<int:listing_id>/{action}",
            (method,),
            "seller",
            lambda w, a=action: f"/api/listings/{_listing(w)}/{a}",
            owner="seller",
        )
        for action, method in [
            ("publish", "post"),
            ("offers", "get"),
            ("cancel", "post"),
            ("relist", "post"),
        ]
    ],
    Endpoint(
        "api/listings/<int:listing_id>/photos",
        ("get", "post"),
        "user",
        lambda w: f"/api/listings/{_listing(w)}/photos",
        owner="seller",
    ),
    Endpoint(
        "api/listings/<int:listing_id>/photos/<int:photo_id>",
        ("delete",),
        "user",
        lambda w: f"/api/listings/{_listing(w)}/photos/{w['photo'].pk}",
        owner="seller",
    ),
    Endpoint(
        "api/offers/<int:offer_id>/accept",
        ("post",),
        "seller",
        lambda w: f"/api/offers/{w['offer'].pk}/accept",
        owner="seller",
    ),
    # Dealer onboarding (own profile only: nothing in the URL to own).
    Endpoint("api/dealer/profile", ("get", "post", "patch"), "user"),
    Endpoint("api/dealer/documents", ("get", "post"), "user"),
    # Buying.
    Endpoint("api/dealer/listings", ("get",), "dealer"),
    Endpoint(
        "api/dealer/listings/<int:listing_id>",
        ("get",),
        "dealer",
        lambda w: f"/api/dealer/listings/{_listing(w)}",
    ),
    Endpoint(
        "api/dealer/listings/<int:listing_id>/offers",
        ("post",),
        "dealer",
        lambda w: f"/api/dealer/listings/{_listing(w)}/offers",
    ),
    Endpoint("api/dealer/offers", ("get",), "dealer"),
    Endpoint(
        "api/dealer/offers/<int:offer_id>",
        ("get", "patch"),
        "dealer",
        lambda w: f"/api/dealer/offers/{w['offer'].pk}",
        owner="dealer",
    ),
    Endpoint(
        "api/dealer/offers/<int:offer_id>/withdraw",
        ("post",),
        "dealer",
        lambda w: f"/api/dealer/offers/{w['offer'].pk}/withdraw",
        owner="dealer",
    ),
    # Orders: only the seller and the accepted dealer.
    Endpoint("api/orders", ("get",), "user"),
    Endpoint(
        "api/orders/<int:order_id>",
        ("get",),
        "user",
        lambda w: f"/api/orders/{_order(w)}",
        owner="parties",
    ),
    *[
        Endpoint(
            f"api/orders/<int:order_id>/{action}",
            (method,),
            "user",
            lambda w, a=action: f"/api/orders/{_order(w)}/{a}",
            owner="parties",
        )
        for action, method in [
            ("contact", "get"),
            ("cancel", "post"),
            ("check-in", "post"),
            ("weigh", "post"),
            ("confirm-weighing", "post"),
            ("complete", "post"),
            ("dispute", "post"),
            ("slot", "post"),
            ("slot/confirm", "post"),
            ("reschedule", "post"),
            ("reschedule/accept", "post"),
            ("reschedule/decline", "post"),
        ]
    ],
    # Penalties and fees.
    Endpoint("api/strikes", ("get",), "user"),
    Endpoint(
        "api/strikes/<int:strike_id>/challenge",
        ("post",),
        "user",
        lambda w: f"/api/strikes/{w['strike'].pk}/challenge",
        owner="seller",
    ),
    Endpoint("api/dues", ("get",), "user"),
    *[
        Endpoint(
            f"api/dues/<int:due_id>/{action}",
            ("post",),
            "user",
            lambda w, a=action: f"/api/dues/{w['due'].pk}/{a}",
            owner="seller",
        )
        for action in ("pay", "check")
    ],
]

BY_ROUTE = {endpoint.route: endpoint for endpoint in ENDPOINTS}
PRIVATE = [e for e in ENDPOINTS if e.access != "public"]
OWNED = [e for e in ENDPOINTS if e.owner]


def _routes(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            if prefix == "" and str(pattern.pattern) == "admin/":
                continue  # Django admin: covered by test_every_admin_page_needs_staff
            yield from _routes(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _view_methods(callback):
    view = getattr(callback, "view_class", None) or getattr(callback, "cls", None)
    return {m for m in ("get", "post", "put", "patch", "delete") if hasattr(view, m)}


# --- The world: one of everything, plus outsiders ---------------------------------


def logged_in(user):
    client = APIClient(raise_request_exception=False)
    client.force_login(user)
    return client


@pytest.fixture
def world(pickup, categories):  # noqa: F811
    """The pickup fixture's confirmed order, plus an open listing with a photo and an
    offer, a notification, a strike with its penalty, and outsiders of each kind."""
    order = pickup["order"]
    seller = order.seller
    dealer = order.dealer
    listing = make_listing(categories, seller=seller)
    photo = ListingPhoto.objects.create(
        listing=listing,
        file=default_storage.save("listing-photos/sweep.jpg", ContentFile(b"x")),
        content_type="image/jpeg",
        width=1,
        height=1,
        size_bytes=1,
    )
    offer = Offer.objects.get(
        pk=offer_on(client_for(dealer), listing, rates(categories)).json()["id"]
    )
    strike = NoShowStrike.objects.create(user=seller, order=order, party=Party.SELLER)
    due = Due.objects.create(
        user=seller, kind=DueKind.NO_SHOW_PENALTY, amount_paise=10000, strike=strike
    )
    outsider_seller = User.objects.create_user("+919833333333")
    outsider_dealer = make_dealer("+919844444444")
    pending_dealer = make_dealer("+919855555555", status=DealerStatus.PENDING)
    return {
        "order": order,
        "categories": categories,
        "listing": listing,
        "photo": photo,
        "offer": offer,
        "notification": Notification.objects.filter(user=seller).first(),
        "strike": strike,
        "due": due,
        "clients": {
            "seller": logged_in(seller),
            "dealer": logged_in(dealer.user),
            "outsider_seller": logged_in(outsider_seller),
            "outsider_dealer": logged_in(outsider_dealer.user),
            "pending_dealer": logged_in(pending_dealer.user),
            "admin": logged_in(User.objects.create_user("+919866666666", is_staff=True)),
        },
    }


class Rollback(Exception):
    pass


def send(client, method, path, *, body=None, content_type="application/json", **extra):
    """Make a request, then undo every database change it made."""
    try:
        with transaction.atomic():
            kwargs = dict(extra)
            if body is not None and method in ("post", "patch"):
                kwargs.update(data=body, content_type=content_type)
            response = getattr(client, method)(path, **kwargs)
            raise Rollback(response)
    except Rollback as rolled_back:
        return rolled_back.args[0]


# --- Inventory --------------------------------------------------------------------


def test_every_endpoint_is_in_the_table():
    """Adding an endpoint fails here until its access rule is written down above."""
    found = {
        route: _view_methods(callback) for route, callback in _routes(get_resolver().url_patterns)
    }

    assert set(found) == set(BY_ROUTE), "endpoints missing from (or stale in) the table"
    for route, methods in found.items():
        assert methods == set(BY_ROUTE[route].methods), route


def test_only_the_listed_endpoints_are_public():
    public = {e.route for e in ENDPOINTS if e.access == "public"}

    assert public == {
        "admin/login/",
        "api/health",
        "api/auth/csrf",
        "api/auth/otp/request",
        "api/auth/otp/verify",
        "api/payments/callback",
        "api/payments/fake/<str:link_id>",
    }


# --- Anonymous, wrong role, wrong owner ------------------------------------------


@pytest.mark.parametrize("endpoint", PRIVATE, ids=lambda e: e.route)
def test_anonymous_requests_are_refused(world, endpoint):
    client = APIClient(raise_request_exception=False)
    for method in endpoint.methods:
        response = send(client, method, endpoint.path(world), body="{}")
        assert response.status_code == 403, (method, response.status_code)


SELLER_ONLY = [e for e in ENDPOINTS if e.access == "seller"]
DEALER_ONLY = [e for e in ENDPOINTS if e.access == "dealer"]


@pytest.mark.parametrize("endpoint", SELLER_ONLY, ids=lambda e: e.route)
def test_seller_endpoints_refuse_accounts_without_the_seller_role(world, endpoint):
    for name in ("dealer", "outsider_dealer", "pending_dealer"):
        for method in endpoint.methods:
            response = send(world["clients"][name], method, endpoint.path(world), body="{}")
            assert response.status_code == 403, (name, method, response.status_code)


@pytest.mark.parametrize("endpoint", DEALER_ONLY, ids=lambda e: e.route)
def test_dealer_endpoints_refuse_sellers_and_unapproved_dealers(world, endpoint):
    for name in ("seller", "outsider_seller", "pending_dealer", "admin"):
        for method in endpoint.methods:
            response = send(world["clients"][name], method, endpoint.path(world), body="{}")
            assert response.status_code == 403, (name, method, response.status_code)


OUTSIDERS = {
    # An outsider with the role the endpoint needs, so only ownership refuses them.
    "seller": ["outsider_seller"],
    "dealer": ["outsider_dealer"],
    "user": ["outsider_seller", "outsider_dealer", "admin"],
}


@pytest.mark.parametrize("endpoint", OWNED, ids=lambda e: e.route)
def test_other_peoples_objects_are_not_found(world, endpoint):
    for name in OUTSIDERS[endpoint.access]:
        for method in endpoint.methods:
            response = send(world["clients"][name], method, endpoint.path(world), body="{}")
            assert response.status_code == 404, (name, method, response.status_code)
            assert world["listing"].address_line not in response.content.decode()


@pytest.mark.parametrize(
    "endpoint", [e for e in OWNED if "get" in e.methods], ids=lambda e: e.route
)
def test_owners_can_read_their_objects(world, endpoint):
    """The 404s above come from ownership, not from a wrong URL."""
    owner = "dealer" if endpoint.owner == "dealer" else "seller"

    assert send(world["clients"][owner], "get", endpoint.path(world)).status_code == 200


def test_every_admin_page_needs_staff(world):
    """Every Django admin URL sends non-staff users to the login page."""
    seller = world["clients"]["seller"]
    anonymous = APIClient(raise_request_exception=False)
    admin_patterns = next(
        p for p in get_resolver().url_patterns if str(getattr(p, "pattern", "")) == "admin/"
    )
    checked = 0
    for route, _ in _routes(admin_patterns.url_patterns, "admin/"):
        path = "/" + re.sub(r"<[^>]+>", "1", route)
        path = path.replace("^(?P<app_label>", "").split("|")[0].replace("$", "")
        if "(?P" in path or path.startswith("/admin/login"):
            continue
        for client in (seller, anonymous):
            response = send(client, "get", path)
            assert response.status_code in (302, 405), (path, response.status_code)
            if response.status_code == 302:
                # Logout sends non-staff to the admin index, which sends them to login.
                target = "/admin/" if path == "/admin/logout/" else "/admin/login/"
                assert response["Location"].startswith(target), (path, response["Location"])
        checked += 1
    assert checked > 50


# --- Bad input never breaks the server --------------------------------------------

FIELDS = [
    "reason", "note", "code", "payment_method", "start", "latitude", "longitude", "lines",
    "rates", "items", "address_line", "locality", "pincode", "phone", "business_name",
    "service_radius_km", "id_type", "id_number", "scale_certificate_expires_on", "doc_type",
    "result", "category", "rate_paise", "declared_weight_kg", "declared_quantity",
]  # fmt: skip
BAD_VALUES = [
    "x" * 100_000,
    10**40,
    -(10**40),
    1e308,
    "NaN",
    "Infinity",
    "-Infinity",
    None,
    True,
    [],
    [1, "a", None],
    {"a": {"b": ["c"]}},
    "",
    " ",
    "\x00",
    "<script>",
    "2026-02-30T25:61:00",
    "' OR 1=1 --",
]
BAD_BODIES = ["[]", "[1,2]", '"text"', "123", "null", "{", "\xff\xfe", "{" * 5000 + "}" * 5000]
WRITES = [
    (e, m)
    for e in ENDPOINTS
    for m in e.methods
    if m in ("post", "patch") and e.route != "api/auth/delete-account"
]


def owner_client(world, endpoint):
    if endpoint.access == "dealer" or endpoint.owner == "dealer":
        return world["clients"]["dealer"]
    return world["clients"]["seller"]


@pytest.mark.parametrize(("endpoint", "method"), WRITES, ids=lambda x: getattr(x, "route", x))
def test_bad_input_never_causes_a_server_error(world, endpoint, method):
    """Sent by a user allowed to call the endpoint, so the input is actually parsed."""
    clients = [owner_client(world, endpoint)]
    if endpoint.owner == "parties":
        clients.append(world["clients"]["dealer"])
    path = endpoint.path(world)
    for client in clients:
        for value in BAD_VALUES:
            for body in ({name: value for name in FIELDS},):
                response = send(client, method, path, body=json.dumps(body))
                assert response.status_code < 500, (body, response.status_code)
                form = {k: v if isinstance(v, str) else json.dumps(v) for k, v in body.items()}
                response = send(client, method, path, body=form, content_type=None)
                assert response.status_code < 500, ("multipart", body, response.status_code)
        for raw in BAD_BODIES:
            response = send(client, method, path, body=raw)
            assert response.status_code < 500, (raw, response.status_code)
        response = send(client, method, path, body="a=1", content_type="text/plain")
        assert response.status_code < 500


# --- Limits at the edges ----------------------------------------------------------


def test_reasons_up_to_300_characters_are_kept_and_longer_ones_refused(world):
    """Each reason fits its column even with the audit log's prefix ("Seller cancelled: ")."""
    seller = world["clients"]["seller"]
    order = world["order"]
    paths = [
        f"/api/listings/{world['listing'].pk}/cancel",
        f"/api/orders/{order.pk}/cancel",
        f"/api/strikes/{world['strike'].pk}/challenge",
    ]
    for path in paths:
        too_long = send(seller, "post", path, body=json.dumps({"reason": "x" * 301}))
        assert too_long.status_code == 400, (path, too_long.status_code)
        longest = send(seller, "post", path, body=json.dumps({"reason": "x" * 300}))
        assert longest.status_code == 200, (path, longest.status_code)


def test_a_300_character_dispute_and_a_500_character_admin_note_fit(world, clock):  # noqa: F811
    from orders.disputes import resolve_dispute
    from orders.tests.test_weighing import lines, weigh

    data = {"order": world["order"], "categories": world["categories"]}
    seller = world["clients"]["seller"]
    assert weigh(world["clients"]["dealer"], data, lines(data)).status_code == 200
    path = f"/api/orders/{world['order'].pk}/dispute"

    assert send(seller, "post", path, body=json.dumps({"reason": "x" * 301})).status_code == 400
    assert seller.post(path, {"reason": "x" * 300}, format="json").status_code == 200
    admin = User.objects.get(phone="+919866666666")
    resolve_dispute(
        Order.objects.get(pk=world["order"].pk),
        admin,
        outcome="cancelled",
        note="n" * 500,
        upheld=False,
    )
    assert AuditLog.objects.filter(to_status="cancelled", reason="n" * 500).exists()


@pytest.mark.parametrize("value", ["NaN", "nan", "Infinity", "-inf", "1e400"])
def test_positions_must_be_real_numbers(world, value):
    """NaN would make the distance unknown; it must be refused, never compared."""
    from orders.tests.test_weighing import lines, scale_photo

    data = {"order": world["order"], "categories": world["categories"]}
    dealer = world["clients"]["dealer"]
    order_id = world["order"].pk
    for latitude, longitude in ((value, "78.4482"), ("17.4375", value)):
        check_in = dealer.post(
            f"/api/orders/{order_id}/check-in",
            {"latitude": latitude, "longitude": longitude},
            format="json",
        )
        assert check_in.status_code == 400, check_in.status_code
        weighed = dealer.post(
            f"/api/orders/{order_id}/weigh",
            {
                "latitude": latitude,
                "longitude": longitude,
                "lines": json.dumps(lines(data)),
                "photo": scale_photo(),
            },
        )
        assert weighed.status_code == 400, weighed.status_code
    saved = Order.objects.get(pk=order_id)
    assert (saved.dealer_checked_in_at, saved.weighed_at) == (None, None)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1e400", "1" * 30, "20.0001", 20])
def test_weighed_amounts_must_be_sane(world, value):
    from orders.tests.test_weighing import lines, weigh

    data = {"order": world["order"], "categories": world["categories"]}

    response = weigh(world["clients"]["dealer"], data, lines(data, paper=value))

    assert response.status_code in (200, 400), response.status_code
    saved = Order.objects.get(pk=world["order"].pk)
    if response.status_code == 400:
        assert saved.weighed_at is None
    else:  # 20 kg given as a number is the only valid value here
        assert value == 20
        assert saved.items.get(category__name="Paper").verified_amount == 20
