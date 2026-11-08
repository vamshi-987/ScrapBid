import pytest
from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from accounts.models import Role, User
from accounts.permissions import HasRole, IsOwner

pytestmark = pytest.mark.django_db


# --- Roles as a set --------------------------------------------------------


def test_new_users_start_as_sellers():
    user = User.objects.create_user("+919876543210")

    assert user.roles == [Role.SELLER]


def test_adding_a_role_twice_keeps_one_copy():
    user = User.objects.create_user("+919876543210")

    user.add_role(Role.DEALER)
    user.add_role(Role.DEALER)

    user.refresh_from_db()
    assert user.roles == [Role.DEALER, Role.SELLER]
    assert user.has_role(Role.DEALER)
    assert user.has_role(Role.SELLER)


def test_duplicate_roles_are_removed_on_save():
    user = User(phone="+919876543210", roles=[Role.SELLER, Role.SELLER, Role.DEALER])
    user.save()

    assert user.roles == [Role.DEALER, Role.SELLER]


def test_unknown_roles_are_rejected():
    user = User.objects.create_user("+919876543210")

    with pytest.raises(ValueError):
        user.add_role("admin")
    user.roles = ["superhero"]
    with pytest.raises(ValueError):
        user.save()


def test_a_phone_number_cannot_have_two_accounts():
    User.objects.create_user("+919876543210", roles=[Role.SELLER])

    with pytest.raises(IntegrityError):
        User.objects.create_user("+919876543210", roles=[Role.DEALER])


# --- /me -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "is_staff", "expected"),
    [
        ([Role.SELLER], False, ["seller"]),
        ([Role.SELLER, Role.DEALER], False, ["dealer", "seller"]),
        ([Role.SELLER], True, ["seller", "admin"]),
    ],
)
def test_me_returns_roles(roles, is_staff, expected):
    user = User.objects.create_user("+919876543210", roles=roles, is_staff=is_staff)
    client = APIClient()
    client.force_login(user)

    assert client.get("/api/auth/me").json()["roles"] == expected


def test_suspended_user_loses_access_immediately():
    user = User.objects.create_user("+919876543210")
    client = APIClient()
    client.force_login(user)
    assert client.get("/api/auth/me").status_code == 200

    User.objects.filter(pk=user.pk).update(is_active=False)

    assert client.get("/api/auth/me").status_code == 403


# --- Permission classes ----------------------------------------------------


def _ok():
    return Response({"ok": True})


class DealerOnlyView(APIView):
    permission_classes = [HasRole(Role.DEALER)]

    def get(self, request):
        return _ok()


class Owned:
    def __init__(self, seller_id):
        self.seller_id = seller_id


class OwnerOnlyView(APIView):
    owner_field = "seller"

    def get(self, request, owner_id):
        self.check_object_permissions(request, Owned(owner_id))
        return _ok()

    def get_permissions(self):
        return [IsOwner()]


def call(view, user=None, **kwargs):
    request = APIRequestFactory().get("/")
    if user is not None:
        force_authenticate(request, user=user)
    return view.as_view()(request, **kwargs)


@pytest.mark.parametrize(
    ("roles", "expected_status"),
    [([Role.SELLER, Role.DEALER], 200), ([Role.DEALER], 200), ([Role.SELLER], 403)],
)
def test_has_role_permission(roles, expected_status):
    user = User.objects.create_user("+919876543210", roles=roles)

    assert call(DealerOnlyView, user).status_code == expected_status


def test_has_role_rejects_anonymous():
    assert call(DealerOnlyView).status_code == 403


def test_is_owner_allows_the_owner_only():
    owner = User.objects.create_user("+919876543210")
    other = User.objects.create_user("+919123456789")

    assert call(OwnerOnlyView, owner, owner_id=owner.id).status_code == 200
    assert call(OwnerOnlyView, other, owner_id=owner.id).status_code == 403
    assert call(OwnerOnlyView, other, owner_id=None).status_code == 403
