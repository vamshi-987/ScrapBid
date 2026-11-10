import io
import logging
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Role, User
from dealers.models import DealerDocument, DealerProfile, DealerStatus

pytestmark = pytest.mark.django_db

PROFILE_URL = "/api/dealer/profile"
DOCS_URL = "/api/dealer/documents"
AADHAAR = "2345 6789 0123"
PDF = b"%PDF-1.4\n%test\n%%EOF\n"


def real_image(image_format):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buffer, format=image_format)
    return buffer.getvalue()


PNG = real_image("PNG")


@pytest.fixture
def user():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_login(user)
    return api


def application(**overrides):
    data = {
        "business_name": "Test Scrap Traders",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4375,
        "longitude": 78.4482,
        "service_radius_km": 10,
        "id_type": "aadhaar",
        "id_number": AADHAAR,
        "scale_certificate_expires_on": str(timezone.localdate() + timedelta(days=200)),
    }
    data.update(overrides)
    return data


def apply(client, **overrides):
    return client.post(PROFILE_URL, application(**overrides), format="json")


def upload(client, doc_type="trade_licence", content=PDF, name="licence.pdf"):
    return client.post(DOCS_URL, {"doc_type": doc_type, "file": SimpleUploadedFile(name, content)})


# --- Applying --------------------------------------------------------------


def test_applying_creates_a_pending_profile_on_the_same_account(client, user):
    response = apply(client)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["service_radius_km"] == 10
    assert (body["latitude"], body["longitude"]) == (17.4375, 78.4482)
    assert body["missing_documents"] == ["trade_licence", "id_proof", "scale_certificate"]
    user.refresh_from_db()
    assert user.roles == [Role.DEALER, Role.SELLER]
    assert User.objects.count() == 1


def test_applying_twice_returns_409_and_keeps_one_profile(client):
    apply(client)

    response = apply(client, business_name="Another Name")

    assert response.status_code == 409
    assert DealerProfile.objects.count() == 1
    assert DealerProfile.objects.get().business_name == "Test Scrap Traders"


@pytest.mark.parametrize(
    "overrides",
    [
        {"pincode": "012345"},
        {"pincode": "50001"},
        {"latitude": 51.5, "longitude": -0.12},  # London
        {"latitude": 78.4482, "longitude": 17.4375},  # swapped
        {"service_radius_km": 0},
        {"service_radius_km": 101},
        {"scale_certificate_expires_on": "2000-01-01"},
        {"id_type": "pan", "id_number": "ABCDE12345"},
        {"id_type": "passport", "id_number": "A1234567"},
    ],
)
def test_invalid_applications_are_rejected(client, overrides):
    assert apply(client, **overrides).status_code == 400
    assert DealerProfile.objects.count() == 0


@pytest.mark.parametrize(
    ("id_type", "number", "last4"),
    [
        ("aadhaar", "2345-6789-0123", "0123"),
        ("pan", "abcde1234f", "234F"),
        ("voter_id", "ABC1234567", "4567"),
        ("driving_licence", "TS09 20110012345", "2345"),
    ],
)
def test_valid_id_types_store_only_the_last_four(client, id_type, number, last4):
    response = apply(client, id_type=id_type, id_number=number)

    assert response.status_code == 201
    assert response.json()["id_number"] == f"XXXX-XXXX-{last4}"
    assert DealerProfile.objects.get().id_number_last4 == last4


def test_full_id_number_is_never_stored_logged_or_echoed(client, caplog):
    caplog.set_level(logging.DEBUG)
    digits = AADHAAR.replace(" ", "")

    bad = apply(client, id_number="1234 5678 9012")  # Aadhaar can't start with 1
    good = apply(client)

    assert "123456789012" not in bad.content.decode()
    assert digits not in good.content.decode()
    row = " ".join(str(v) for v in DealerProfile.objects.values().get().values())
    assert digits not in row
    assert digits not in caplog.text


def test_anonymous_users_cannot_apply_or_read():
    anonymous = APIClient()

    assert anonymous.post(PROFILE_URL, application(), format="json").status_code == 403
    assert anonymous.get(PROFILE_URL).status_code == 403
    assert anonymous.get(DOCS_URL).status_code == 403


def test_user_without_application_gets_404(client):
    assert client.get(PROFILE_URL).status_code == 404
    assert upload(client).status_code == 404


def test_users_only_see_their_own_profile(client):
    apply(client)
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))

    assert other.get(PROFILE_URL).status_code == 404
    assert other.get(DOCS_URL).status_code == 404


# --- Editing ---------------------------------------------------------------


def test_pending_application_can_be_edited(client):
    apply(client)

    response = client.patch(
        PROFILE_URL, {"service_radius_km": 15, "locality": "Begumpet"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["service_radius_km"] == 15
    assert response.json()["locality"] == "Begumpet"


def test_editing_id_type_requires_the_number_too(client):
    apply(client)

    assert client.patch(PROFILE_URL, {"id_type": "pan"}, format="json").status_code == 400


@pytest.mark.parametrize("status", [DealerStatus.APPROVED, DealerStatus.SUSPENDED])
def test_reviewed_application_cannot_be_edited(client, status):
    apply(client)
    DealerProfile.objects.update(status=status)

    response = client.patch(PROFILE_URL, {"service_radius_km": 50}, format="json")

    assert response.status_code == 409
    assert DealerProfile.objects.get().service_radius_km == 10


# --- Documents -------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "name", "content_type"),
    [
        (PDF, "licence.pdf", "application/pdf"),
        (PNG, "id.png", "image/png"),
        (real_image("JPEG"), "cert.jpg", "image/jpeg"),
        (real_image("WEBP"), "cert.webp", "image/webp"),
    ],
)
def test_allowed_document_types_are_stored(client, content, name, content_type):
    apply(client)

    response = upload(client, content=content, name=name)

    assert response.status_code == 201
    assert response.json()["content_type"] == content_type
    document = DealerDocument.objects.get()
    assert default_storage.exists(document.file.name)


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b"MZ\x90\x00 fake executable", "licence.pdf"),
        (b"<html>not a document</html>", "id.png"),
        (b"", "empty.pdf"),
    ],
)
def test_files_that_are_not_real_documents_are_rejected(client, content, name):
    apply(client)

    assert upload(client, content=content, name=name).status_code == 400
    assert DealerDocument.objects.count() == 0


def test_oversized_documents_are_rejected(client, settings):
    settings.DEALER_DOCUMENT_MAX_BYTES = 100
    apply(client)

    response = upload(client, content=PDF + b"\x00" * 200)

    assert response.status_code == 400
    assert DealerDocument.objects.count() == 0


def test_unknown_document_type_is_rejected(client):
    apply(client)

    assert upload(client, doc_type="selfie").status_code == 400


def test_documents_use_random_private_keys_and_no_urls(client):
    apply(client)
    upload(client, name="Ramesh Kumar aadhaar.pdf")

    listing = client.get(DOCS_URL).json()
    key = DealerDocument.objects.get().file.name

    assert set(listing[0]) == {"doc_type", "content_type", "size_bytes", "uploaded_at"}
    assert key.startswith("dealer-documents/") and "Ramesh" not in key
    assert "missing_documents" in client.get(PROFILE_URL).json()


def test_stored_documents_are_not_publicly_readable(client):
    apply(client)
    upload(client)
    name = DealerDocument.objects.get().file.name
    object_key = "/".join(part for part in (default_storage.location, name) if part)
    # The object really is at this key (authenticated read works)...
    default_storage.bucket.Object(object_key).load()
    url = f"{settings.S3_ENDPOINT_URL}/{default_storage.bucket_name}/{object_key}"

    # ...but an anonymous request for it is refused.
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, timeout=10)

    assert error.value.code == 403


def test_reupload_replaces_the_previous_document_and_file(
    client, django_capture_on_commit_callbacks
):
    apply(client)
    upload(client)
    old_key = DealerDocument.objects.get().file.name

    # The old file is deleted only after the database change commits.
    with django_capture_on_commit_callbacks(execute=True):
        response = upload(client, content=PNG, name="new.png")

    assert response.status_code == 201
    document = DealerDocument.objects.get()
    assert document.content_type == "image/png"
    assert document.file.name != old_key
    assert not default_storage.exists(old_key)


def test_uploading_required_documents_clears_missing_list(client):
    apply(client)
    for doc_type in ["trade_licence", "id_proof", "scale_certificate"]:
        upload(client, doc_type=doc_type)

    assert client.get(PROFILE_URL).json()["missing_documents"] == []


# --- Seed ------------------------------------------------------------------


def test_seed_demo_adds_a_pending_dealer_with_documents_idempotently():
    call_command("seed_demo")
    call_command("seed_demo")

    pending = DealerProfile.objects.get(user__phone="+919000000005")
    assert pending.status == DealerStatus.PENDING
    assert pending.documents.count() == 3
    # 4 approved (incl. dual-role) + 1 pending; the e-waste dealer has one extra document.
    assert DealerProfile.objects.count() == 5
    assert DealerDocument.objects.count() == 5 * 3 + 1
    dual = User.objects.get(phone="+919000000004")
    assert dual.dealer_profile.locality == "Ameerpet"
    assert dual.dealer_profile.status == DealerStatus.APPROVED


def test_document_photos_are_stored_without_exif_or_gps(client):
    apply(client)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x8825] = {2: (17.0, 26.0, 15.0)}  # GPS latitude
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buffer, format="JPEG", exif=exif)
    assert b"PhoneMaker" in buffer.getvalue()

    response = upload(client, doc_type="id_proof", content=buffer.getvalue(), name="id.jpg")

    assert response.status_code == 201
    stored = default_storage.open(DealerDocument.objects.get().file.name).read()
    assert b"PhoneMaker" not in stored and b"Exif" not in stored
    assert dict(Image.open(io.BytesIO(stored)).getexif()) == {}


def test_image_with_a_real_header_but_broken_body_is_rejected(client):
    apply(client)

    response = upload(client, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, name="id.png")

    assert response.status_code == 400
