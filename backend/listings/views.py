from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role
from accounts.permissions import HasRole
from dealers.permissions import IsApprovedDealer
from listings import photos, services
from listings.feed import visible_to_dealer
from listings.models import Category, Listing, ListingPhoto
from listings.serializers import (
    CategorySerializer,
    DealerListingSerializer,
    ListingDetailSerializer,
    ListingPhotoSerializer,
    ListingSummarySerializer,
    ListingWriteSerializer,
    PhotoUploadSerializer,
)


class CategoryListView(ListAPIView):
    """Active categories for any logged-in user. Read-only; admins edit them in Django admin."""

    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)


def own_listings(request):
    return Listing.objects.filter(seller=request.user).prefetch_related("items__category", "photos")


def own_listing(request, listing_id):
    # Another user's listing is reported as not found, so its existence isn't revealed.
    return get_object_or_404(own_listings(request), pk=listing_id)


def listing_error(error):
    http_status = (
        status.HTTP_400_BAD_REQUEST
        if error.code in ("needs_items", "needs_photo")
        else status.HTTP_409_CONFLICT
    )
    return Response({"detail": str(error), "code": error.code}, status=http_status)


class ListingListCreateView(APIView):
    """The seller's own listings. Creating one needs the seller role."""

    permission_classes = [HasRole(Role.SELLER)]

    def get(self, request):
        listings = own_listings(request).order_by("-created_at")
        return Response(ListingSummarySerializer(listings, many=True).data)

    def post(self, request):
        serializer = ListingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        listing = services.create_listing(request.user, serializer.validated_data)
        return Response(
            ListingDetailSerializer(own_listing(request, listing.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class ListingDetailView(APIView):
    permission_classes = [HasRole(Role.SELLER)]

    def get(self, request, listing_id):
        return Response(ListingDetailSerializer(own_listing(request, listing_id)).data)

    def patch(self, request, listing_id):
        listing = own_listing(request, listing_id)
        serializer = ListingWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_listing(listing, serializer.validated_data)
        except services.ListingError as error:
            return listing_error(error)
        return Response(ListingDetailSerializer(own_listing(request, listing_id)).data)


class ListingPublishView(APIView):
    permission_classes = [HasRole(Role.SELLER)]

    def post(self, request, listing_id):
        listing = own_listing(request, listing_id)
        try:
            services.publish_listing(listing, request.user)
        except services.ListingError as error:
            return listing_error(error)
        return Response(ListingDetailSerializer(own_listing(request, listing_id)).data)


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


class ListingOffersForSellerView(APIView):
    """The offers on the seller's own listing, for comparing."""

    permission_classes = [HasRole(Role.SELLER)]

    def get(self, request, listing_id):
        from offers.serializers import SellerOfferSerializer
        from offers.services import offers_for_seller

        listing = own_listing(request, listing_id)
        return Response(SellerOfferSerializer(offers_for_seller(listing), many=True).data)


class ListingCancelView(APIView):
    """The seller withdraws a draft or open listing, with a reason."""

    permission_classes = [HasRole(Role.SELLER)]

    def post(self, request, listing_id):
        listing = own_listing(request, listing_id)
        try:
            services.cancel_listing(listing, request.user, request.data.get("reason", ""))
        except services.ListingError as error:
            if error.code == "reason_required":
                return Response(
                    {"detail": str(error), "code": error.code}, status=status.HTTP_400_BAD_REQUEST
                )
            return listing_error(error)
        return Response(ListingDetailSerializer(own_listing(request, listing_id)).data)


class ListingRelistView(APIView):
    """Turn an expired listing into a new draft. Safe to retry."""

    permission_classes = [HasRole(Role.SELLER)]

    def post(self, request, listing_id):
        listing = own_listing(request, listing_id)
        try:
            draft, created = services.relist(listing)
        except services.ListingError as error:
            return listing_error(error)
        return Response(
            ListingDetailSerializer(own_listing(request, draft.pk)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DealerFeedPagination(PageNumberPagination):
    page_size = 20


class DealerListingFeedView(ListAPIView):
    """Open listings in the approved dealer's service area, nearest first."""

    permission_classes = [IsApprovedDealer]
    serializer_class = DealerListingSerializer
    pagination_class = DealerFeedPagination

    def get_queryset(self):
        return visible_to_dealer(self.request.dealer_profile)


class DealerListingDetailView(RetrieveAPIView):
    """One listing from the feed. Anything the feed wouldn't show is not found."""

    permission_classes = [IsApprovedDealer]
    serializer_class = DealerListingSerializer
    lookup_url_kwarg = "listing_id"

    def get_queryset(self):
        return visible_to_dealer(self.request.dealer_profile)
