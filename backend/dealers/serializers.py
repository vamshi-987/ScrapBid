from django.utils import timezone
from rest_framework import serializers

from dealers.models import (
    MAX_SERVICE_RADIUS_KM,
    REQUIRED_DOCUMENT_TYPES,
    DealerDocument,
    DealerProfile,
    DocumentType,
    IdType,
)
from dealers.validation import PINCODE, is_in_india, normalize_id_number


class DealerApplicationSerializer(serializers.Serializer):
    """Input for applying (all fields) and for editing a pending application (any fields)."""

    business_name = serializers.CharField(max_length=120)
    locality = serializers.CharField(max_length=80)
    pincode = serializers.CharField(max_length=6)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    service_radius_km = serializers.IntegerField(min_value=1, max_value=MAX_SERVICE_RADIUS_KM)
    id_type = serializers.ChoiceField(choices=IdType.choices)
    id_number = serializers.CharField(max_length=30, write_only=True)
    scale_certificate_expires_on = serializers.DateField()

    def validate_pincode(self, value):
        if not PINCODE.fullmatch(value):
            raise serializers.ValidationError("Enter a valid 6-digit pincode.")
        return value

    def validate_scale_certificate_expires_on(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError("This scale certificate has expired.")
        return value

    def validate(self, attrs):
        if ("latitude" in attrs) != ("longitude" in attrs):
            raise serializers.ValidationError("Send both latitude and longitude.")
        if "latitude" in attrs and not is_in_india(attrs["latitude"], attrs["longitude"]):
            raise serializers.ValidationError({"latitude": "The location must be in India."})
        if ("id_type" in attrs) != ("id_number" in attrs):
            raise serializers.ValidationError("Send both the ID type and the ID number.")
        if "id_number" in attrs:
            normalized = normalize_id_number(attrs["id_type"], attrs["id_number"])
            if normalized is None:
                # Never echo the number back.
                raise serializers.ValidationError({"id_number": "Enter a valid ID number."})
            attrs["id_number"] = normalized
        return attrs


class DealerDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DealerDocument
        # No file URL or storage key: documents are visible to admins only.
        fields = ["doc_type", "content_type", "size_bytes", "uploaded_at"]


class DealerProfileSerializer(serializers.ModelSerializer):
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    id_number = serializers.CharField(source="masked_id_number")
    documents = DealerDocumentSerializer(many=True)
    missing_documents = serializers.SerializerMethodField()

    class Meta:
        model = DealerProfile
        fields = [
            "status",
            "status_reason",
            "business_name",
            "locality",
            "pincode",
            "latitude",
            "longitude",
            "service_radius_km",
            "id_type",
            "id_number",
            "scale_certificate_expires_on",
            "ewaste_authorised",
            "documents",
            "missing_documents",
        ]

    def get_latitude(self, profile):
        return profile.location.y

    def get_longitude(self, profile):
        return profile.location.x

    def get_missing_documents(self, profile):
        uploaded = {document.doc_type for document in profile.documents.all()}
        return [doc_type for doc_type in REQUIRED_DOCUMENT_TYPES if doc_type not in uploaded]


class DocumentUploadSerializer(serializers.Serializer):
    doc_type = serializers.ChoiceField(choices=DocumentType.choices)
    file = serializers.FileField()
