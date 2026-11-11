"""Listing photo rules: who may change photos, limits, cleaning and storage."""

from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from config.images import InvalidImage, clean_image
from listings.models import Listing, ListingPhoto
from listings.services import is_editable

MAX_DIMENSION = 2048


class PhotoError(Exception):
    messages = {
        "not_editable": "Photos can't be changed at this stage.",
        "too_many": "This listing already has the maximum number of photos.",
        "too_large": "The photo is too large.",
        "unsupported_type": "Upload a JPG, PNG or WebP photo.",
        "too_many_pixels": "The photo's resolution is too high.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def add_photo(listing, uploaded_file):
    """Clean and store a photo for a listing the caller has already checked they own."""
    if uploaded_file.size > settings.LISTING_PHOTO_MAX_BYTES:
        raise PhotoError("too_large")
    try:
        image = clean_image(uploaded_file, max_dimension=MAX_DIMENSION)
    except InvalidImage as error:
        raise PhotoError(str(error)) from None

    key = default_storage.save(
        f"listing-photos/{uuid4().hex}{image.extension}", ContentFile(image.data)
    )
    try:
        with transaction.atomic():
            # Lock the listing so concurrent uploads can't exceed the photo limit.
            locked = Listing.objects.select_for_update().get(pk=listing.pk)
            if not is_editable(locked):
                raise PhotoError("not_editable")
            if locked.photos.count() >= settings.LISTING_MAX_PHOTOS:
                raise PhotoError("too_many")
            return ListingPhoto.objects.create(
                listing=locked,
                file=key,
                content_type=image.content_type,
                width=image.width,
                height=image.height,
                size_bytes=len(image.data),
            )
    except PhotoError:
        default_storage.delete(key)
        raise


def remove_photo(photo):
    with transaction.atomic():
        listing = Listing.objects.select_for_update().get(pk=photo.listing_id)
        if not is_editable(listing):
            raise PhotoError("not_editable")
        key = photo.file.name
        photo.delete()
        transaction.on_commit(lambda: default_storage.delete(key))
