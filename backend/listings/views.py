from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from listings import photos
from listings.models import Category, Listing, ListingPhoto
from listings.serializers import CategorySerializer, ListingPhotoSerializer, PhotoUploadSerializer


class CategoryListView(ListAPIView):
    """Active categories for any logged-in user. Read-only; admins edit them in Django admin."""

    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)


def own_listing(request, listing_id):
    # Another user's listing is reported as not found, so its existence isn't revealed.
    return get_object_or_404(Listing, pk=listing_id, seller=request.user)


def photo_error(error):
    raise ValidationError({"file": error.messages[error.code], "code": error.code})


class ListingPhotosView(APIView):
    parser_classes = [MultiPartParser]

    def get(self, request, listing_id):
        listing = own_listing(request, listing_id)
        return Response(ListingPhotoSerializer(listing.photos.all(), many=True).data)

    def post(self, request, listing_id):
        listing = own_listing(request, listing_id)
        serializer = PhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            photo = photos.add_photo(listing, serializer.validated_data["file"])
        except photos.PhotoError as error:
            photo_error(error)
        return Response(ListingPhotoSerializer(photo).data, status=status.HTTP_201_CREATED)


class ListingPhotoDetailView(APIView):
    def delete(self, request, listing_id, photo_id):
        listing = own_listing(request, listing_id)
        photo = get_object_or_404(ListingPhoto, pk=photo_id, listing=listing)
        try:
            photos.remove_photo(photo)
        except photos.PhotoError as error:
            photo_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)
