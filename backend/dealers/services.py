"""Dealer onboarding rules: applying, editing and storing verification documents."""

from uuid import uuid4

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.storage import default_storage
from django.db import transaction

from accounts.models import Role
from dealers.models import DealerDocument, DealerProfile, DealerStatus
from dealers.validation import detect_document_type


class OnboardingError(Exception):
    code = "onboarding_error"


class AlreadyApplied(OnboardingError):
    code = "already_applied"


class NotEditable(OnboardingError):
    code = "not_editable"


class InvalidDocument(OnboardingError):
    code = "invalid_document"


def _apply_fields(profile, data):
    for field in ("business_name", "locality", "pincode", "service_radius_km", "id_type"):
        if field in data:
            setattr(profile, field, data[field])
    if "scale_certificate_expires_on" in data:
        profile.scale_certificate_expires_on = data["scale_certificate_expires_on"]
    if "id_number" in data:
        profile.id_number_last4 = data["id_number"][-4:]
    if "latitude" in data:
        profile.location = Point(data["longitude"], data["latitude"], srid=4326)


def apply_as_dealer(user, data):
    """Create a PENDING dealer profile and add the dealer role to the same account."""
    with transaction.atomic():
        if DealerProfile.objects.select_for_update().filter(user=user).exists():
            raise AlreadyApplied()
        profile = DealerProfile(user=user)
        _apply_fields(profile, data)
        profile.save()
        user.add_role(Role.DEALER)
    return profile


def update_profile(profile, data):
    """Edit an application. Allowed only while it is waiting for review."""
    if profile.status != DealerStatus.PENDING:
        raise NotEditable()
    _apply_fields(profile, data)
    profile.save()
    return profile


def store_document(profile, doc_type, uploaded_file):
    """Validate and save a document privately, replacing any earlier one of the same type."""
    if uploaded_file.size > settings.DEALER_DOCUMENT_MAX_BYTES:
        raise InvalidDocument("too_large")
    uploaded_file.seek(0)
    detected = detect_document_type(uploaded_file.read(16))
    uploaded_file.seek(0)
    if detected is None:
        raise InvalidDocument("unsupported_type")
    content_type, extension = detected

    # Random key: the original file name may contain personal details.
    key = default_storage.save(f"dealer-documents/{uuid4().hex}{extension}", uploaded_file)
    with transaction.atomic():
        document = (
            DealerDocument.objects.select_for_update()
            .filter(dealer=profile, doc_type=doc_type)
            .first()
        )
        old_key = document.file.name if document else None
        if document is None:
            document = DealerDocument(dealer=profile, doc_type=doc_type)
        document.file.name = key
        document.content_type = content_type
        document.size_bytes = uploaded_file.size
        document.save()
        if old_key:
            transaction.on_commit(lambda: default_storage.delete(old_key))
    return document
