from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

import scheduler
from accounts.models import User
from listings.services import is_editable
from offers.models import Offer, OfferStatus
from offers.tests.test_offers import client_for, make_dealer, make_listing, offer_on, rates

pytestmark = pytest.mark.django_db


def expire():
    out = StringIO()
    call_command("expire_offers", stdout=out)
    return out.getvalue()


def make_offer(categories, listing, phone, *, expires_in):
    offer_id = offer_on(client_for(make_dealer(phone)), listing, rates(categories)).json()["id"]
    Offer.objects.filter(pk=offer_id).update(expires_at=timezone.now() + expires_in)
    return Offer.objects.get(pk=offer_id)


def test_offers_past_their_time_expire_and_others_stay(categories):
    listing = make_listing(categories)
    due = make_offer(categories, listing, "+919811111111", expires_in=-timedelta(minutes=1))
    fresh = make_offer(categories, listing, "+919822222222", expires_in=timedelta(minutes=1))

    assert "Expired 1 offer(s)." in expire()

    due.refresh_from_db()
    fresh.refresh_from_db()
    assert due.status == OfferStatus.EXPIRED
    assert fresh.status == OfferStatus.ACTIVE


@pytest.mark.parametrize(
    "status", [OfferStatus.ACCEPTED, OfferStatus.WITHDRAWN, OfferStatus.REJECTED]
)
def test_settled_offers_are_never_touched(categories, status):
    listing = make_listing(categories)
    offer = make_offer(categories, listing, "+919811111111", expires_in=-timedelta(days=1))
    Offer.objects.filter(pk=offer.pk).update(status=status)

    assert "Expired 0 offer(s)." in expire()

    offer.refresh_from_db()
    assert offer.status == status


def test_running_twice_changes_nothing_the_second_time(categories):
    listing = make_listing(categories)
    make_offer(categories, listing, "+919811111111", expires_in=-timedelta(minutes=1))

    expire()

    assert "Expired 0 offer(s)." in expire()


def test_the_scheduler_runs_the_job():
    assert "expire_offers" in scheduler.JOBS


def test_offer_past_its_time_cannot_be_accepted_before_or_after_the_job(categories):
    seller = User.objects.create_user("+919876543210")
    listing = make_listing(categories, seller=seller)
    offer = make_offer(categories, listing, "+919811111111", expires_in=-timedelta(seconds=1))
    owner = APIClient()
    owner.force_login(seller)

    before = owner.post(f"/api/offers/{offer.pk}/accept")
    expire()
    after = owner.post(f"/api/offers/{offer.pk}/accept")

    assert before.json()["code"] == "not_live"
    assert after.json()["code"] == "not_live"


def test_an_offer_past_its_time_no_longer_locks_the_listing(categories):
    listing = make_listing(categories)
    live = make_offer(categories, listing, "+919811111111", expires_in=timedelta(minutes=1))
    assert not is_editable(listing)

    Offer.objects.filter(pk=live.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert is_editable(listing)  # straight away, before the job runs
    expire()
    assert is_editable(listing)
