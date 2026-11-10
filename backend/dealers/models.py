from django.conf import settings
from django.contrib.gis.db import models
from django.core.validators import MaxValueValidator, MinValueValidator

MAX_SERVICE_RADIUS_KM = 100


class DealerStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SUSPENDED = "suspended", "Suspended"


class IdType(models.TextChoices):
    AADHAAR = "aadhaar", "Aadhaar"
    PAN = "pan", "PAN"
    VOTER_ID = "voter_id", "Voter ID"
    DRIVING_LICENCE = "driving_licence", "Driving licence"


class DealerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dealer_profile"
    )
    status = models.CharField(
        max_length=10, choices=DealerStatus.choices, default=DealerStatus.PENDING
    )
    business_name = models.CharField(max_length=120)
    # Shown to sellers and other dealers instead of an exact address.
    locality = models.CharField(max_length=80)
    pincode = models.CharField(max_length=6)
    # Centre of the service area (WGS84). Listings within the radius are in range.
    location = models.PointField(geography=True, srid=4326)
    service_radius_km = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_SERVICE_RADIUS_KM)]
    )
    id_type = models.CharField(max_length=20, choices=IdType.choices)
    # Only the last 4 characters of the ID number are kept; the full number is never stored.
    id_number_last4 = models.CharField(max_length=4)
    scale_certificate_expires_on = models.DateField()
    # Set by an admin after checking an uploaded authorisation document.
    ewaste_authorised = models.BooleanField(default=False)
    # Latest review. The reason is shown to the dealer when rejected or suspended.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    status_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(service_radius_km__gte=1)
                & models.Q(service_radius_km__lte=MAX_SERVICE_RADIUS_KM),
                name="dealer_service_radius_range",
            ),
        ]

    def __str__(self):
        return self.business_name

    @property
    def masked_id_number(self):
        return f"XXXX-XXXX-{self.id_number_last4}"


class DocumentType(models.TextChoices):
    TRADE_LICENCE = "trade_licence", "Shop or trade licence"
    ID_PROOF = "id_proof", "ID proof"
    SCALE_CERTIFICATE = "scale_certificate", "Legal Metrology scale certificate"
    GST = "gst", "GST certificate"
    EWASTE_AUTHORISATION = "ewaste_authorisation", "E-waste authorisation"


# Needed before an admin can approve a dealer (GST and e-waste are optional).
REQUIRED_DOCUMENT_TYPES = (
    DocumentType.TRADE_LICENCE,
    DocumentType.ID_PROOF,
    DocumentType.SCALE_CERTIFICATE,
)


class DealerDocument(models.Model):
    """A verification document in private storage. Only admins may open it."""

    dealer = models.ForeignKey(DealerProfile, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocumentType.choices)
    # Random object key in the private bucket; never exposed through the API.
    file = models.FileField(max_length=200)
    content_type = models.CharField(max_length=50)
    size_bytes = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dealer", "doc_type"], name="one_document_per_type"),
        ]

    def __str__(self):
        return f"{self.get_doc_type_display()} for dealer {self.dealer_id}"
