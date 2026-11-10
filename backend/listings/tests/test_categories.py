import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import Client
from rest_framework.test import APIClient

from accounts.management.commands.seed_demo import DEMO_CATEGORIES
from accounts.models import Role, User
from listings.models import Category, PricingUnit

pytestmark = pytest.mark.django_db

URL = "/api/categories"


@pytest.fixture
def seller_client():
    client = APIClient()
    client.force_login(User.objects.create_user("+919876543210"))
    return client


@pytest.fixture
def admin_client():
    client = Client()
    client.force_login(User.objects.create_superuser("+919000000001"))
    return client


# --- API -------------------------------------------------------------------


def test_lists_only_active_categories_sorted_by_name(seller_client):
    Category.objects.create(name="Iron")
    Category.objects.create(name="Appliance", pricing_unit=PricingUnit.PER_ITEM)
    Category.objects.create(name="E-waste", is_restricted=True)
    Category.objects.create(name="Glass", is_active=False)

    response = seller_client.get(URL)

    assert response.status_code == 200
    assert [c["name"] for c in response.json()] == ["Appliance", "E-waste", "Iron"]
    appliance, ewaste, _ = response.json()
    assert appliance["pricing_unit"] == "per_item"
    assert ewaste["is_restricted"] is True
    assert set(appliance) == {"id", "name", "pricing_unit", "is_restricted"}


def test_any_role_can_read_categories():
    dealer = User.objects.create_user("+919876543210", roles=[Role.DEALER])
    client = APIClient()
    client.force_login(dealer)

    assert client.get(URL).status_code == 200


def test_anonymous_users_cannot_read_categories():
    assert APIClient().get(URL).status_code == 403


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_api_is_read_only_even_for_admins(method):
    client = APIClient()
    client.force_login(User.objects.create_superuser("+919000000001"))

    response = getattr(client, method)(URL, {"name": "Glass"}, format="json")

    assert response.status_code == 405
    assert not Category.objects.filter(name="Glass").exists()


# --- Data rules ------------------------------------------------------------


def test_names_are_unique_ignoring_case():
    Category.objects.create(name="Copper")

    with pytest.raises(IntegrityError):
        Category.objects.create(name="copper")


def test_database_rejects_an_invalid_pricing_unit():
    category = Category.objects.create(name="Iron")

    with pytest.raises(IntegrityError), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE listings_category SET pricing_unit = 'per_tonne' WHERE id = %s", [category.id]
        )


# --- Django admin ----------------------------------------------------------


def test_admin_adds_a_category_that_appears_in_the_api(admin_client, seller_client):
    response = admin_client.post(
        "/admin/listings/category/add/",
        {"name": "Brass", "pricing_unit": "per_kg", "is_active": "on"},
    )

    assert response.status_code == 302
    assert [c["name"] for c in seller_client.get(URL).json()] == ["Brass"]


def test_admin_deactivates_instead_of_deleting(admin_client, seller_client):
    category = Category.objects.create(name="Iron")

    delete_page = admin_client.get(f"/admin/listings/category/{category.id}/delete/")
    admin_client.post(
        "/admin/listings/category/",
        {"action": "delete_selected", "_selected_action": [category.id], "post": "yes"},
    )

    assert delete_page.status_code == 403
    assert Category.objects.filter(id=category.id).exists()

    category.is_active = False
    category.save()
    assert seller_client.get(URL).json() == []


def test_non_staff_cannot_open_category_admin():
    client = Client()
    client.force_login(User.objects.create_user("+919876543210"))

    response = client.get("/admin/listings/category/")

    assert response.status_code == 302


# --- Seed ------------------------------------------------------------------


def test_seed_demo_adds_categories_idempotently():
    call_command("seed_demo")
    Category.objects.filter(name="Copper").update(is_active=False, is_restricted=True)

    call_command("seed_demo")

    assert Category.objects.count() == len(DEMO_CATEGORIES)
    copper = Category.objects.get(name="Copper")
    assert copper.is_active and not copper.is_restricted
    assert set(Category.objects.filter(is_restricted=True).values_list("name", flat=True)) == {
        "E-waste",
        "Batteries",
    }
    assert Category.objects.get(name="Appliance").pricing_unit == PricingUnit.PER_ITEM
