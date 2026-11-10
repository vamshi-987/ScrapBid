"""Dealer onboarding and review rules: applying, editing, documents and admin review."""

from uuid import uuid4

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from dealers.models import (
    REQUIRED_DOCUMENT_TYPES,
    DealerDocument,
    DealerProfile,
    DealerStatus,
    DocumentType,
)
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
    """Edit an application while it is pending, or fix and resubmit a rejected one.

    Editing a rejected application sends it back to PENDING for another review.
    """
    if profile.status not in (DealerStatus.PENDING, DealerStatus.REJECTED):
        raise NotEditable()
    _apply_fields(profile, data)
    if profile.status == DealerStatus.REJECTED:
        profile.status = DealerStatus.PENDING
        profile.status_reason = ""
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


# --- Admin review ----------------------------------------------------------------

# Status changes an admin may make. Rejected dealers resubmit (back to PENDING) themselves.
ALLOWED_REVIEWS = {
    DealerStatus.PENDING: {DealerStatus.APPROVED, DealerStatus.REJECTED},
    DealerStatus.APPROVED: {DealerStatus.SUSPENDED},
    DealerStatus.SUSPENDED: {DealerStatus.APPROVED},
    DealerStatus.REJECTED: set(),
}
REASON_REQUIRED = {DealerStatus.REJECTED, DealerStatus.SUSPENDED}


class ReviewError(Exception):
    messages = {
        "not_allowed": "This status change isn't allowed.",
        "reason_required": "Give a reason to reject or suspend a dealer.",
        "missing_documents": "Required documents are missing.",
        "certificate_expired": "The scale certificate has expired.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def missing_documents(profile):
    uploaded = set(profile.documents.values_list("doc_type", flat=True))
    return [doc_type for doc_type in REQUIRED_DOCUMENT_TYPES if doc_type not in uploaded]


def review_dealer(profile, to_status, reviewer, reason=""):
    """The only place an admin changes a dealer's status."""
    reason = reason.strip()
    with transaction.atomic():
        profile = DealerProfile.objects.select_for_update().get(pk=profile.pk)
        if to_status not in ALLOWED_REVIEWS[profile.status]:
            raise ReviewError("not_allowed")
        if to_status in REASON_REQUIRED and not reason:
            raise ReviewError("reason_required")
        if to_status == DealerStatus.APPROVED:
            if missing_documents(profile):
                raise ReviewError("missing_documents")
            if profile.scale_certificate_expires_on < timezone.localdate():
                raise ReviewError("certificate_expired")
        profile.status = to_status
        profile.status_reason = "" if to_status == DealerStatus.APPROVED else reason
        profile.reviewed_by = reviewer
        profile.reviewed_at = timezone.now()
        profile.save()
    return profile


def can_authorise_ewaste(profile):
    """E-waste authorisation needs an uploaded authorisation document."""
    return profile.documents.filter(doc_type=DocumentType.EWASTE_AUTHORISATION).exists()
