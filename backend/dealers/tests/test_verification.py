from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from accounts.models import Role, User
from dealers import services
from dealers.models import DealerProfile, DealerStatus, DocumentType, IdType
from dealers.permissions import IsApprovedDealer

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n%test\n%%EOF\n"
ADMIN_URL = "/admin/dealers/dealerprofile/"
REQUIRED = [DocumentType.TRADE_LICENCE, DocumentType.ID_PROOF, DocumentType.SCALE_CERTIFICATE]


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("+919000000001")


@pytest.fixture
def admin_client(admin_user):
    client = Client()
    client.force_login(admin_user)
    return client


def make_dealer(
    phone="+919876543210", *, status=DealerStatus.PENDING, docs=REQUIRED, expires_in=200
):
    user = User.objects.create_user(phone, roles=[Role.SELLER, Role.DEALER])
    profile = DealerProfile.objects.create(
        user=user,
        status=status,
        business_name="Test Traders",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
        service_radius_km=10,
        id_type=IdType.PAN,
        id_number_last4="234F",
        scale_certificate_expires_on=timezone.localdate() + timedelta(days=expires_in),
    )
    for doc_type in docs:
        services.store_document(profile, doc_type, ContentFile(PDF, name="doc.pdf"))
    return profile


# --- Review rules (the single place dealer status changes) -----------------


@pytest.mark.parametrize(
    ("start", "target", "allowed"),
    [
        (DealerStatus.PENDING, DealerStatus.APPROVED, True),
        (DealerStatus.PENDING, DealerStatus.REJECTED, True),
        (DealerStatus.PENDING, DealerStatus.SUSPENDED, False),
        (DealerStatus.APPROVED, DealerStatus.SUSPENDED, True),
        (DealerStatus.APPROVED, DealerStatus.REJECTED, False),
        (DealerStatus.APPROVED, DealerStatus.PENDING, False),
        (DealerStatus.SUSPENDED, DealerStatus.APPROVED, True),
        (DealerStatus.SUSPENDED, DealerStatus.REJECTED, False),
        (DealerStatus.REJECTED, DealerStatus.APPROVED, False),
        (DealerStatus.REJECTED, DealerStatus.SUSPENDED, False),
    ],
)
def test_review_transitions(admin_user, start, target, allowed):
    profile = make_dealer(status=start)

    if allowed:
        services.review_dealer(profile, target, admin_user, reason="checked")
        profile.refresh_from_db()
        assert profile.status == target
        assert profile.reviewed_by == admin_user
        assert profile.reviewed_at is not None
    else:
        with pytest.raises(services.ReviewError) as error:
            services.review_dealer(profile, target, admin_user, reason="checked")
        assert error.value.code == "not_allowed"
        profile.refresh_from_db()
        assert profile.status == start


@pytest.mark.parametrize("target", [DealerStatus.REJECTED, DealerStatus.SUSPENDED])
def test_reject_and_suspend_need_a_reason(admin_user, target):
    start = DealerStatus.PENDING if target == DealerStatus.REJECTED else DealerStatus.APPROVED
    profile = make_dealer(status=start)

    with pytest.raises(services.ReviewError) as error:
        services.review_dealer(profile, target, admin_user, reason="   ")

    assert error.value.code == "reason_required"


def test_approval_needs_every_required_document(admin_user):
    profile = make_dealer(docs=[DocumentType.TRADE_LICENCE, DocumentType.ID_PROOF])

    with pytest.raises(services.ReviewError) as error:
        services.review_dealer(profile, DealerStatus.APPROVED, admin_user)

    assert error.value.code == "missing_documents"


def test_approval_is_blocked_by_an_expired_scale_certificate(admin_user):
    profile = make_dealer(expires_in=-1)

    with pytest.raises(services.ReviewError) as error:
        services.review_dealer(profile, DealerStatus.APPROVED, admin_user)

    assert error.value.code == "certificate_expired"


def test_reinstating_a_suspended_dealer_rechecks_the_certificate(admin_user):
    profile = make_dealer(status=DealerStatus.SUSPENDED, expires_in=-1)

    with pytest.raises(services.ReviewError):
        services.review_dealer(profile, DealerStatus.APPROVED, admin_user)


def test_approval_clears_an_earlier_reason(admin_user):
    profile = make_dealer(status=DealerStatus.APPROVED)
    services.review_dealer(profile, DealerStatus.SUSPENDED, admin_user, reason="Complaints")
    services.review_dealer(profile, DealerStatus.APPROVED, admin_user)

    profile.refresh_from_db()
    assert profile.status_reason == ""


# --- The approved-dealer gate ----------------------------------------------


class DealerOnlyView(APIView):
    permission_classes = [IsApprovedDealer]

    def get(self, request):
        return Response({"dealer": request.dealer_profile.pk})


def call_gate(user=None):
    request = APIRequestFactory().get("/")
    if user is not None:
        force_authenticate(request, user=user)
    return DealerOnlyView.as_view()(request)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (DealerStatus.APPROVED, 200),
        (DealerStatus.PENDING, 403),
        (DealerStatus.REJECTED, 403),
        (DealerStatus.SUSPENDED, 403),
    ],
)
def test_only_approved_dealers_pass_the_gate(status, expected):
    profile = make_dealer(status=status, docs=[])

    response = call_gate(profile.user)

    assert response.status_code == expected
    if expected == 200:
        assert response.data == {"dealer": profile.pk}


def test_gate_rejects_users_without_a_dealer_profile_or_role():
    seller = User.objects.create_user("+919123456789")
    profile = make_dealer(status=DealerStatus.APPROVED, docs=[])
    profile.user.roles = [Role.SELLER]
    profile.user.save()

    assert call_gate(seller).status_code == 403
    assert call_gate(profile.user).status_code == 403
    assert call_gate().status_code == 403


def test_suspended_account_loses_dealer_access_through_the_api():
    profile = make_dealer(status=DealerStatus.APPROVED, docs=[])
    client = APIClient()
    client.force_login(profile.user)
    assert client.get("/api/auth/me").status_code == 200

    User.objects.filter(pk=profile.user.pk).update(is_active=False)

    assert client.get("/api/dealer/profile").status_code == 403


# --- Django admin review actions -------------------------------------------


def run_action(client, action, profiles, reason=""):
    return client.post(
        ADMIN_URL,
        {"action": action, "_selected_action": [p.pk for p in profiles], "reason": reason},
        follow=True,
    )


def test_admin_approves_from_the_list(admin_client, admin_user):
    profile = make_dealer()

    run_action(admin_client, "approve", [profile])

    profile.refresh_from_db()
    assert profile.status == DealerStatus.APPROVED
    assert profile.reviewed_by == admin_user


def test_admin_reject_shows_the_reason_to_the_dealer(admin_client):
    profile = make_dealer()

    run_action(admin_client, "reject", [profile], reason="Licence photo is unreadable")

    client = APIClient()
    client.force_login(profile.user)
    body = client.get("/api/dealer/profile").json()
    assert body["status"] == "rejected"
    assert body["status_reason"] == "Licence photo is unreadable"


def test_admin_action_reports_errors_and_changes_nothing(admin_client):
    incomplete = make_dealer(docs=[DocumentType.ID_PROOF])

    response = run_action(admin_client, "approve", [incomplete])

    incomplete.refresh_from_db()
    assert incomplete.status == DealerStatus.PENDING
    assert "Required documents are missing" in response.content.decode()


def test_reject_without_reason_is_refused_in_admin(admin_client):
    profile = make_dealer()

    run_action(admin_client, "reject", [profile])

    profile.refresh_from_db()
    assert profile.status == DealerStatus.PENDING


def test_admin_suspends_and_reinstates(admin_client):
    profile = make_dealer(status=DealerStatus.APPROVED)

    run_action(admin_client, "suspend", [profile], reason="Short-weight complaints")
    profile.refresh_from_db()
    assert profile.status == DealerStatus.SUSPENDED

    run_action(admin_client, "reinstate", [profile])
    profile.refresh_from_db()
    assert profile.status == DealerStatus.APPROVED


def test_admin_cannot_edit_status_directly(admin_client):
    profile = make_dealer()

    admin_client.post(
        f"{ADMIN_URL}{profile.pk}/change/",
        {
            "status": "approved",
            "ewaste_authorised": "",
            "documents-TOTAL_FORMS": "0",
            "documents-INITIAL_FORMS": "0",
        },
    )

    profile.refresh_from_db()
    assert profile.status == DealerStatus.PENDING


def test_admin_list_masks_phone_numbers(admin_client):
    make_dealer()

    content = admin_client.get(ADMIN_URL).content.decode()

    assert "+91XXXXXX3210" in content
    assert "9876543210" not in content


# --- E-waste authorisation -------------------------------------------------


def change_ewaste(client, profile, value):
    return client.post(
        f"{ADMIN_URL}{profile.pk}/change/",
        {
            "ewaste_authorised": "on" if value else "",
            "documents-TOTAL_FORMS": str(profile.documents.count()),
            "documents-INITIAL_FORMS": str(profile.documents.count()),
            **{f"documents-{i}-id": d.pk for i, d in enumerate(profile.documents.all())},
            **{f"documents-{i}-dealer": profile.pk for i in range(profile.documents.count())},
        },
    )


def test_ewaste_authorisation_needs_the_document(admin_client):
    profile = make_dealer(status=DealerStatus.APPROVED)

    response = change_ewaste(admin_client, profile, True)

    profile.refresh_from_db()
    assert profile.ewaste_authorised is False
    assert "e-waste authorisation document is needed" in response.content.decode()


def test_ewaste_authorisation_with_the_document(admin_client):
    profile = make_dealer(
        status=DealerStatus.APPROVED, docs=[*REQUIRED, DocumentType.EWASTE_AUTHORISATION]
    )

    change_ewaste(admin_client, profile, True)

    profile.refresh_from_db()
    assert profile.ewaste_authorised is True


# --- Viewing documents -----------------------------------------------------


def document_url(profile, doc_type=DocumentType.TRADE_LICENCE):
    document = profile.documents.get(doc_type=doc_type)
    return f"{ADMIN_URL}documents/{document.pk}/file/"


def test_admin_streams_a_document_without_any_url(admin_client):
    profile = make_dealer()

    response = admin_client.get(document_url(profile))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF
    assert response["Content-Type"] == "application/pdf"
    cache_control = response["Cache-Control"]
    assert "private" in cache_control and "no-store" in cache_control
    assert response["Content-Disposition"] == 'inline; filename="trade_licence.pdf"'


def test_dealer_and_anonymous_users_cannot_open_documents():
    profile = make_dealer()
    dealer_client = Client()
    dealer_client.force_login(profile.user)

    for client in (dealer_client, Client()):
        response = client.get(document_url(profile))
        assert response.status_code in (302, 403)
        assert PDF not in response.content


def test_unknown_document_returns_404(admin_client):
    assert admin_client.get(f"{ADMIN_URL}documents/999999/file/").status_code == 404


# --- Resubmitting after rejection ------------------------------------------


def test_rejected_dealer_resubmits_and_goes_back_to_pending(admin_user):
    profile = make_dealer()
    services.review_dealer(profile, DealerStatus.REJECTED, admin_user, reason="Blurry licence")
    client = APIClient()
    client.force_login(profile.user)

    response = client.patch("/api/dealer/profile", {"locality": "Begumpet"}, format="json")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["status_reason"] == ""


# --- Seed ------------------------------------------------------------------


def test_seed_demo_creates_reviewable_dealers():
    call_command("seed_demo")
    call_command("seed_demo")

    approved = DealerProfile.objects.filter(status=DealerStatus.APPROVED)
    assert approved.count() == 5
    assert DealerProfile.objects.filter(status=DealerStatus.PENDING).count() == 1
    ewaste = DealerProfile.objects.get(ewaste_authorised=True)
    assert ewaste.user.phone == "+919000000006"
    assert ewaste.documents.filter(doc_type=DocumentType.EWASTE_AUTHORISATION).exists()

    hyderabad = Point(78.4867, 17.3850, srid=4326)
    far_away = DealerProfile.objects.filter(location__distance_gt=(hyderabad, D(km=100)))
    assert list(far_away.values_list("user__phone", flat=True)) == ["+919000000008"]


def test_seed_demo_restores_a_dealer_changed_during_testing(admin_user):
    call_command("seed_demo")
    profile = DealerProfile.objects.get(user__phone="+919000000007")
    services.review_dealer(profile, DealerStatus.SUSPENDED, admin_user, reason="testing")

    call_command("seed_demo")

    profile.refresh_from_db()
    assert profile.status == DealerStatus.APPROVED
