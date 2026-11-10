from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from dealers import services
from dealers.models import DealerProfile
from dealers.serializers import (
    DealerApplicationSerializer,
    DealerDocumentSerializer,
    DealerProfileSerializer,
    DocumentUploadSerializer,
)

DOCUMENT_ERRORS = {
    "too_large": "The file is too large.",
    "unsupported_type": "Upload a PDF, JPG, PNG or WebP file.",
}


def own_profile(request):
    profile = DealerProfile.objects.filter(user=request.user).prefetch_related("documents").first()
    if profile is None:
        raise NotFound("You haven't applied to be a dealer yet.")
    return profile


class DealerProfileView(APIView):
    """The logged-in user's own dealer application. There is no access to other profiles."""

    def get(self, request):
        return Response(DealerProfileSerializer(own_profile(request)).data)

    def post(self, request):
        serializer = DealerApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            profile = services.apply_as_dealer(request.user, serializer.validated_data)
        except services.AlreadyApplied:
            return Response(
                {"detail": "You have already applied.", "code": "already_applied"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(DealerProfileSerializer(profile).data, status=status.HTTP_201_CREATED)

    def patch(self, request):
        profile = own_profile(request)
        serializer = DealerApplicationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_profile(profile, serializer.validated_data)
        except services.NotEditable:
            return Response(
                {"detail": "This application can no longer be edited.", "code": "not_editable"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(DealerProfileSerializer(own_profile(request)).data)


class DealerDocumentsView(APIView):
    parser_classes = [MultiPartParser]

    def get(self, request):
        profile = own_profile(request)
        return Response(DealerDocumentSerializer(profile.documents.all(), many=True).data)

    def post(self, request):
        profile = own_profile(request)
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            document = services.store_document(profile, data["doc_type"], data["file"])
        except services.InvalidDocument as error:
            raise ValidationError({"file": DOCUMENT_ERRORS[str(error)]}) from None
        return Response(DealerDocumentSerializer(document).data, status=status.HTTP_201_CREATED)
