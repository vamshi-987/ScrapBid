import itertools
import re
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from accounts.models import User
from audit.models import AuditLog
from listings.models import Category, Listing, ListingItem, ListingStatus, PricingUnit
from listings.transitions import ADMIN_ONLY, ALLOWED, SYSTEM_ONLY, TransitionError, transition

S = ListingStatus


@pytest.fixture
def seller(db):
    return User.objects.create_user("+919876543210")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser("+919000000001")


def make_listing(seller, status=S.DRAFT):
    return Listing.objects.create(
        seller=seller,
        status=status,
        address_line="12-3-45, Street 6",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
    )


def actor_for(move, seller, admin_user):
    if move in SYSTEM_ONLY:
        return None
    if move in ADMIN_ONLY:
        return admin_user
    return seller


ALL_PAIRS = list(itertools.product(S.values, S.values))


# --- Every pair of statuses ------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(("start", "target"), ALL_PAIRS)
def test_every_status_pair(seller, admin_user, start, target):
    listing = make_listing(seller, start)
    move = (start, target)

    if target in ALLOWED[start]:
        transition(listing, target, actor_for(move, seller, admin_user), reason="because")
        listing.refresh_from_db()
        assert listing.status == target
        log = AuditLog.objects.get(entity_type="listing", entity_id=listing.pk)
        assert (log.from_status, log.to_status, log.reason) == (start, target, "because")
    else:
        with pytest.raises(TransitionError) as error:
            transition(listing, target, admin_user, reason="because")
        assert error.value.code == "not_allowed"
        listing.refresh_from_db()
        assert listing.status == start
        assert not AuditLog.objects.exists()


def test_allowed_moves_match_the_documented_state_machine():
    expected = {
        (S.DRAFT, S.OPEN),
        (S.DRAFT, S.CANCELLED),
        (S.OPEN, S.OFFER_ACCEPTED),
        (S.OPEN, S.CANCELLED),
        (S.OPEN, S.EXPIRED),
        (S.OFFER_ACCEPTED, S.PICKUP_SCHEDULED),
        (S.OFFER_ACCEPTED, S.CANCELLED),
        (S.OFFER_ACCEPTED, S.OPEN),
        (S.PICKUP_SCHEDULED, S.PICKED_UP),
        (S.PICKUP_SCHEDULED, S.CANCELLED),
        (S.PICKUP_SCHEDULED, S.OPEN),
        (S.PICKED_UP, S.COMPLETED),
        (S.PICKED_UP, S.DISPUTED),
        (S.DISPUTED, S.COMPLETED),
        (S.DISPUTED, S.CANCELLED),
    }
    actual = {(start, target) for start, targets in ALLOWED.items() for target in targets}
    assert actual == expected


@pytest.mark.django_db
@pytest.mark.parametrize("terminal", [S.COMPLETED, S.CANCELLED, S.EXPIRED])
def test_terminal_statuses_never_change(seller, admin_user, terminal):
    listing = make_listing(seller, terminal)

    for target in S.values:
        with pytest.raises(TransitionError):
            transition(listing, target, admin_user, reason="try")


# --- Who may make a move ---------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("target", [S.COMPLETED, S.CANCELLED])
def test_resolving_a_dispute_is_admin_only(seller, admin_user, target):
    listing = make_listing(seller, S.DISPUTED)

    with pytest.raises(TransitionError) as error:
        transition(listing, target, seller, reason="resolved")
    assert error.value.code == "admin_only"

    transition(listing, target, admin_user, reason="resolved")
    listing.refresh_from_db()
    assert listing.status == target


@pytest.mark.django_db
def test_expiry_is_system_only(seller, admin_user):
    listing = make_listing(seller, S.OPEN)

    for user in (seller, admin_user):
        with pytest.raises(TransitionError) as error:
            transition(listing, S.EXPIRED, user)
        assert error.value.code == "system_only"

    transition(listing, S.EXPIRED, None)
    log = AuditLog.objects.get()
    assert log.actor is None and log.to_status == S.EXPIRED


@pytest.mark.django_db
def test_system_can_only_expire(seller):
    listing = make_listing(seller, S.DRAFT)

    with pytest.raises(TransitionError):
        transition(listing, S.OPEN, None)


# --- Reasons ---------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (S.DRAFT, S.CANCELLED),
        (S.OPEN, S.CANCELLED),
        (S.OFFER_ACCEPTED, S.CANCELLED),
        (S.PICKUP_SCHEDULED, S.CANCELLED),
        (S.PICKED_UP, S.DISPUTED),
        (S.OFFER_ACCEPTED, S.OPEN),
        (S.PICKUP_SCHEDULED, S.OPEN),
    ],
)
def test_reason_is_required(seller, start, target):
    listing = make_listing(seller, start)

    with pytest.raises(TransitionError) as error:
        transition(listing, target, seller, reason="   ")

    assert error.value.code == "reason_required"
    listing.refresh_from_db()
    assert listing.status == start


@pytest.mark.django_db
def test_publishing_sets_published_at_and_no_reason_needed(seller):
    listing = make_listing(seller)

    transition(listing, S.OPEN, seller)

    listing.refresh_from_db()
    assert listing.status == S.OPEN
    assert listing.published_at is not None


# --- Audit log -------------------------------------------------------------


@pytest.mark.django_db
def test_each_change_writes_exactly_one_audit_row(seller):
    listing = make_listing(seller)

    transition(listing, S.OPEN, seller)
    transition(listing, S.OFFER_ACCEPTED, seller)
    transition(listing, S.OPEN, seller, reason="Dealer cancelled", details={"by": "dealer"})

    logs = list(
        AuditLog.objects.filter(entity_id=listing.pk).values_list("from_status", "to_status")
    )
    assert logs == [
        (S.DRAFT, S.OPEN),
        (S.OPEN, S.OFFER_ACCEPTED),
        (S.OFFER_ACCEPTED, S.OPEN),
    ]
    last = AuditLog.objects.last()
    assert last.actor == seller
    assert last.details == {"by": "dealer"}
    assert last.created_at is not None


@pytest.mark.django_db
def test_rollback_leaves_no_change_and_no_audit_row(seller):
    listing = make_listing(seller)

    with pytest.raises(RuntimeError), transaction.atomic():
        transition(listing, S.OPEN, seller)
        raise RuntimeError("later step failed")

    listing.refresh_from_db()
    assert listing.status == S.DRAFT
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_stale_object_cannot_skip_the_check(seller):
    listing = make_listing(seller)
    stale = Listing.objects.get(pk=listing.pk)
    transition(listing, S.OPEN, seller)
    transition(listing, S.OFFER_ACCEPTED, seller)

    # `stale` still says DRAFT in memory; DRAFT → OPEN must be judged on the real status.
    with pytest.raises(TransitionError):
        transition(stale, S.OPEN, seller)


# --- Concurrency -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_changes_cannot_both_succeed(seller):
    listing = make_listing(seller, S.OPEN)
    barrier = threading.Barrier(2)
    results = []

    def attempt(target, reason):
        try:
            barrier.wait()
            transition(Listing.objects.get(pk=listing.pk), target, seller, reason=reason)
            results.append(("ok", target))
        except TransitionError as error:
            results.append(("refused", error.code))
        finally:
            connection.close()

    # The same move twice at once (the double-accept race). Whichever runs second
    # must see the first one's result and be refused. Two *different* moves aren't
    # a fair check: OPEN → OFFER_ACCEPTED then OFFER_ACCEPTED → CANCELLED is valid.
    threads = [
        threading.Thread(target=attempt, args=(S.OFFER_ACCEPTED, "")),
        threading.Thread(target=attempt, args=(S.OFFER_ACCEPTED, "")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcome for outcome, _ in results) == ["ok", "refused"]
    assert ("refused", "not_allowed") in results
    listing.refresh_from_db()
    assert listing.status == S.OFFER_ACCEPTED
    assert AuditLog.objects.filter(entity_id=listing.pk).count() == 1


# --- Listing items ---------------------------------------------------------


@pytest.fixture
def iron(db):
    return Category.objects.create(name="Iron")


@pytest.mark.django_db
def test_item_needs_weight_or_quantity_but_not_both(seller, iron):
    listing = make_listing(seller)

    with pytest.raises(IntegrityError), transaction.atomic():
        ListingItem.objects.create(listing=listing, category=iron)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingItem.objects.create(
            listing=listing, category=iron, declared_weight_kg=Decimal("5"), declared_quantity=2
        )


@pytest.mark.django_db
@pytest.mark.parametrize("weight", [Decimal("0"), Decimal("-1.5")])
def test_item_weight_must_be_positive(seller, iron, weight):
    listing = make_listing(seller)

    with pytest.raises(IntegrityError):
        ListingItem.objects.create(listing=listing, category=iron, declared_weight_kg=weight)


@pytest.mark.django_db
def test_weights_are_exact_decimals(seller, iron):
    listing = make_listing(seller)
    ListingItem.objects.create(
        listing=listing, category=iron, declared_weight_kg=Decimal("1234.567")
    )

    item = ListingItem.objects.get()

    assert item.declared_weight_kg == Decimal("1234.567")
    assert isinstance(item.declared_weight_kg, Decimal)


@pytest.mark.django_db
def test_one_item_per_category(seller, iron):
    listing = make_listing(seller)
    ListingItem.objects.create(listing=listing, category=iron, declared_weight_kg=Decimal("5"))

    with pytest.raises(IntegrityError):
        ListingItem.objects.create(listing=listing, category=iron, declared_weight_kg=Decimal("3"))


@pytest.mark.django_db
def test_category_in_use_cannot_be_deleted(seller):
    appliance = Category.objects.create(name="Appliance", pricing_unit=PricingUnit.PER_ITEM)
    ListingItem.objects.create(
        listing=make_listing(seller), category=appliance, declared_quantity=2
    )

    with pytest.raises(ProtectedError):
        appliance.delete()


# --- Status is never set outside the state machine -------------------------

DIRECT_STATUS_WRITE = re.compile(
    r"\.status\s*=\s*(ListingStatus|S)\.|update\([^)]*\bstatus\s*=\s*(ListingStatus|S)\."
)


def test_no_code_sets_listing_status_outside_the_state_machine():
    root = Path(settings.BASE_DIR)
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if (
            "tests" in relative.parts
            or "migrations" in relative.parts
            or relative == Path("listings/transitions.py")
        ):
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if DIRECT_STATUS_WRITE.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert offenders == []
