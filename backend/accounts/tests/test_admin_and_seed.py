import pytest
from django.core.management import CommandError, call_command
from django.test import Client

from accounts.management.commands.seed_demo import DEMO_USERS
from accounts.models import Role, User

pytestmark = pytest.mark.django_db


# --- Admin access through the OTP session (Q33) ----------------------------


def test_staff_user_logged_in_by_otp_session_opens_admin():
    admin_user = User.objects.create_superuser("+919000000001")
    client = Client()
    client.force_login(admin_user)

    assert client.get("/admin/").status_code == 200


def test_non_staff_user_is_sent_to_login():
    client = Client()
    client.force_login(User.objects.create_user("+919876543210"))

    response = client.get("/admin/")

    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", "/login?next=%2Fadmin%2F"),
        ("?next=/admin/accounts/user/", "/login?next=%2Fadmin%2Faccounts%2Fuser%2F"),
        ("?next=https://evil.example/", "/login?next=%2Fadmin%2F"),
        ("?next=//evil.example/", "/login?next=%2Fadmin%2F"),
        ("?next=javascript:alert(1)", "/login?next=%2Fadmin%2F"),
    ],
)
def test_admin_login_redirects_to_app_login(query, expected):
    response = Client().get(f"/admin/login/{query}")

    assert response.status_code == 302
    assert response["Location"] == expected


def test_admin_login_refuses_logged_in_non_staff_instead_of_looping():
    client = Client()
    client.force_login(User.objects.create_user("+919876543210"))

    assert client.get("/admin/login/?next=/admin/").status_code == 403


def test_admin_login_sends_logged_in_staff_to_next():
    client = Client()
    client.force_login(User.objects.create_superuser("+919000000001"))

    response = client.get("/admin/login/?next=/admin/accounts/user/")

    assert response.status_code == 302
    assert response["Location"] == "/admin/accounts/user/"


def test_admin_can_suspend_and_reactivate_accounts():
    admin_user = User.objects.create_superuser("+919000000001")
    target = User.objects.create_user("+919876543210")
    client = Client()
    client.force_login(admin_user)
    url = "/admin/accounts/user/"

    client.post(url, {"action": "suspend", "_selected_action": [target.pk, admin_user.pk]})
    target.refresh_from_db()
    admin_user.refresh_from_db()
    assert target.is_active is False
    assert admin_user.is_active is True  # admins can't suspend themselves

    client.post(url, {"action": "reactivate", "_selected_action": [target.pk]})
    target.refresh_from_db()
    assert target.is_active is True


def test_admin_user_list_masks_phone_numbers():
    client = Client()
    client.force_login(User.objects.create_superuser("+919000000001"))
    User.objects.create_user("+919876543210")

    content = client.get("/admin/accounts/user/").content.decode()

    assert "+91XXXXXX3210" in content
    assert "9876543210" not in content


# --- seed_demo -------------------------------------------------------------


def test_seed_demo_creates_the_demo_accounts():
    call_command("seed_demo")

    assert User.objects.count() == len(DEMO_USERS)
    admin_user = User.objects.get(phone="+919000000001")
    assert admin_user.is_staff and admin_user.is_superuser
    dual = User.objects.get(phone="+919000000004")
    assert dual.roles == [Role.DEALER, Role.SELLER]


def test_seed_demo_is_idempotent():
    call_command("seed_demo")
    User.objects.filter(phone="+919000000002").update(is_active=False, roles=[Role.DEALER])

    call_command("seed_demo")

    assert User.objects.count() == len(DEMO_USERS)
    restored = User.objects.get(phone="+919000000002")
    assert restored.is_active and restored.roles == [Role.SELLER]


def test_seed_demo_refuses_to_run_in_production(settings):
    settings.DJANGO_ENV = "production"

    with pytest.raises(CommandError):
        call_command("seed_demo")
    assert User.objects.count() == 0
