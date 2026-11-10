import io
import time
import urllib.error
import urllib.request

import pytest
from django.contrib.gis.geos import Point
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, PngImagePlugin
from rest_framework.test import APIClient

from accounts.models import User
from config import images
from config.storage import presigned_url
from listings.models import Listing, ListingPhoto, ListingStatus

pytestmark = pytest.mark.django_db


def picture(image_format="JPEG", size=(40, 30), **save_options):
    buffer = io.BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format=image_format, **save_options)
    return buffer.getvalue()


def gps_exif():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x8825] = {2: (17.0, 26.0, 15.0), 4: (78.0, 26.0, 53.0)}  # GPS latitude/longitude
    return exif


@pytest.fixture
def seller():
    return User.objects.create_user("+919876543210")


@pytest.fixture
def client(seller):
    api = APIClient()
    api.force_login(seller)
    return api


@pytest.fixture
def listing(seller):
    return make_listing(seller)


def make_listing(seller, status=ListingStatus.DRAFT):
    return Listing.objects.create(
        seller=seller,
        status=status,
        address_line="12-3-45, Street 6",
        locality="Ameerpet",
        pincode="500016",
        location=Point(78.4482, 17.4375, srid=4326),
    )


def upload(client, listing, content, name="photo.jpg"):
    return client.post(
        f"/api/listings/{listing.pk}/photos", {"file": SimpleUploadedFile(name, content)}
    )


def stored_bytes(photo_id=None):
    photo = ListingPhoto.objects.get(pk=photo_id) if photo_id else ListingPhoto.objects.get()
    return default_storage.open(photo.file.name).read()


# --- Accepted formats and cleaning -----------------------------------------


@pytest.mark.parametrize(
    ("image_format", "name", "content_type"),
    [
        ("JPEG", "a.jpg", "image/jpeg"),
        ("PNG", "a.png", "image/png"),
        ("WEBP", "a.webp", "image/webp"),
    ],
)
def test_allowed_formats_are_stored(client, listing, image_format, name, content_type):
    response = upload(client, listing, picture(image_format), name)

    assert response.status_code == 201
    body = response.json()
    assert body["content_type"] == content_type
    assert (body["width"], body["height"]) == (40, 30)
    assert set(body) == {"id", "url", "content_type", "width", "height", "created_at"}


@pytest.mark.parametrize("image_format", ["JPEG", "WEBP"])
def test_exif_and_gps_are_removed_from_the_stored_file(client, listing, image_format):
    original = picture(image_format, exif=gps_exif())
    assert b"PhoneMaker" in original

    upload(client, listing, original)

    stored = stored_bytes()
    assert b"PhoneMaker" not in stored
    assert b"Exif" not in stored
    assert dict(Image.open(io.BytesIO(stored)).getexif()) == {}


def test_png_text_metadata_is_removed(client, listing):
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Ramesh Kumar, 12-3-45 Ameerpet")
    original = picture("PNG", pnginfo=info)
    assert b"Ramesh" in original

    upload(client, listing, original, "a.png")

    assert b"Ramesh" not in stored_bytes()


def test_exif_rotation_is_applied_before_stripping(client, listing):
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise to display
    response = upload(client, listing, picture("JPEG", size=(40, 30), exif=exif))

    assert (response.json()["width"], response.json()["height"]) == (30, 40)


def test_large_photos_are_resized_keeping_the_shape(client, listing):
    response = upload(client, listing, picture("PNG", size=(4000, 1000)), "wide.png")

    assert (response.json()["width"], response.json()["height"]) == (2048, 512)


# --- Rejected uploads ------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "name"),
    [
        (b"just some text", "photo.jpg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "broken.png"),
        (b"%PDF-1.4\n%%EOF\n", "photo.jpg"),
        (picture("GIF"), "anim.gif"),
        (picture("BMP"), "photo.bmp"),
    ],
)
def test_files_that_are_not_allowed_images_are_rejected(client, listing, content, name):
    response = upload(client, listing, content, name)

    assert response.status_code == 400
    assert ListingPhoto.objects.count() == 0


def test_oversized_photos_are_rejected(client, listing, settings):
    settings.LISTING_PHOTO_MAX_BYTES = 100

    assert upload(client, listing, picture()).status_code == 400


def test_decompression_bombs_are_rejected(client, listing, monkeypatch):
    monkeypatch.setattr(images, "MAX_PIXELS", 100)

    response = upload(client, listing, picture())

    assert response.status_code == 400
    assert response.json()["code"] == "too_many_pixels"


def test_photo_limit_per_listing(client, listing, settings):
    settings.LISTING_MAX_PHOTOS = 2
    assert upload(client, listing, picture()).status_code == 201
    assert upload(client, listing, picture()).status_code == 201
    _, files_before = default_storage.listdir("listing-photos")

    response = upload(client, listing, picture())

    assert response.status_code == 400
    assert response.json()["code"] == "too_many"
    assert ListingPhoto.objects.count() == 2
    _, files_after = default_storage.listdir("listing-photos")
    assert len(files_after) == len(files_before)  # the rejected file was not left behind


# --- Who may change photos, and when ---------------------------------------


def test_other_users_get_404_for_someone_elses_listing(listing):
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))

    assert other.get(f"/api/listings/{listing.pk}/photos").status_code == 404
    assert upload(other, listing, picture()).status_code == 404
    assert ListingPhoto.objects.count() == 0


def test_other_users_cannot_delete_photos(client, listing):
    photo_id = upload(client, listing, picture()).json()["id"]
    other = APIClient()
    other.force_login(User.objects.create_user("+919123456789"))

    response = other.delete(f"/api/listings/{listing.pk}/photos/{photo_id}")

    assert response.status_code == 404
    assert ListingPhoto.objects.filter(pk=photo_id).exists()


def test_anonymous_users_are_refused(listing):
    anonymous = APIClient()

    assert anonymous.get(f"/api/listings/{listing.pk}/photos").status_code == 403
    assert upload(anonymous, listing, picture()).status_code == 403


@pytest.mark.parametrize("status", [ListingStatus.DRAFT, ListingStatus.OPEN])
def test_photos_can_change_while_draft_or_open(client, seller, status):
    listing = make_listing(seller, status)

    assert upload(client, listing, picture()).status_code == 201


@pytest.mark.parametrize(
    "status",
    [ListingStatus.OFFER_ACCEPTED, ListingStatus.PICKED_UP, ListingStatus.COMPLETED],
)
def test_photos_are_frozen_after_an_offer_is_accepted(client, seller, status):
    listing = make_listing(seller, status)
    photo = ListingPhoto.objects.create(
        listing=listing,
        file="listing-photos/x.jpg",
        content_type="image/jpeg",
        width=1,
        height=1,
        size_bytes=1,
    )

    assert upload(client, listing, picture()).json()["code"] == "not_editable"
    assert client.delete(f"/api/listings/{listing.pk}/photos/{photo.pk}").status_code == 400
    assert ListingPhoto.objects.filter(pk=photo.pk).exists()


def test_deleting_a_photo_removes_its_file(client, listing, django_capture_on_commit_callbacks):
    photo_id = upload(client, listing, picture()).json()["id"]
    name = ListingPhoto.objects.get(pk=photo_id).file.name

    with django_capture_on_commit_callbacks(execute=True):
        response = client.delete(f"/api/listings/{listing.pk}/photos/{photo_id}")

    assert response.status_code == 204
    assert not ListingPhoto.objects.exists()
    assert not default_storage.exists(name)


def test_owner_lists_photos(client, listing):
    upload(client, listing, picture())
    upload(client, listing, picture("PNG"), "b.png")

    body = client.get(f"/api/listings/{listing.pk}/photos").json()

    assert [p["content_type"] for p in body] == ["image/jpeg", "image/png"]


# --- Private storage and presigned links -----------------------------------


def object_url(name):
    from django.conf import settings

    key = "/".join(part for part in (default_storage.location, name) if part)
    return f"{settings.S3_ENDPOINT_URL}/{default_storage.bucket_name}/{key}"


def test_stored_photo_is_not_publicly_readable(client, listing):
    upload(client, listing, picture())
    name = ListingPhoto.objects.get().file.name
    assert default_storage.exists(name)

    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(object_url(name), timeout=10)

    assert error.value.code == 403


def test_links_are_signed_for_the_public_address(client, listing, settings):
    url = upload(client, listing, picture()).json()["url"]

    assert url.startswith(f"{settings.S3_PUBLIC_ENDPOINT_URL}/")
    assert "X-Amz-Signature=" in url
    assert f"X-Amz-Expires={settings.PRESIGNED_URL_TTL_SECONDS}" in url


def test_presigned_link_opens_the_photo_and_then_expires(client, listing, settings):
    # Sign for the address the test container can reach (the browser uses Caddy).
    settings.S3_PUBLIC_ENDPOINT_URL = settings.S3_ENDPOINT_URL
    upload(client, listing, picture())
    photo = ListingPhoto.objects.get()

    with urllib.request.urlopen(presigned_url(photo.file.name), timeout=10) as response:
        assert response.read() == stored_bytes()

    short_lived = presigned_url(photo.file.name, expires_in=1)
    time.sleep(2)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(short_lived, timeout=10)
    assert error.value.code == 403
