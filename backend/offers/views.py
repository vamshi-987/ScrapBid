from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from dealers.permissions import IsApprovedDealer
from offers import services
from offers.models import Offer
from offers.serializers import OfferInputSerializer, OfferSerializer

ERROR_STATUS = {"not_found": status.HTTP_404_NOT_FOUND}
BAD_REQUEST = {"wrong_categories", "rate_out_of_range"}


def offer_error(error):
    if error.code in ERROR_STATUS:
        http_status = ERROR_STATUS[error.code]
    elif error.code in BAD_REQUEST:
        http_status = status.HTTP_400_BAD_REQUEST
    else:
        http_status = status.HTTP_409_CONFLICT
    return Response({"detail": str(error), "code": error.code}, status=http_status)


def dealer_offers(request):
    return (
        Offer.objects.filter(dealer=request.dealer_profile)
        .select_related("listing")
        .prefetch_related("rates", "listing__items__category")
    )


def own_offer(request, offer_id):
    return get_object_or_404(dealer_offers(request), pk=offer_id)


class ListingOffersView(APIView):
    """Make an offer on a listing from the dealer's feed."""

    permission_classes = [IsApprovedDealer]

    def post(self, request, listing_id):
        serializer = OfferInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            offer = services.create_offer(
                request.dealer_profile, listing_id, serializer.validated_data["rates"]
            )
        except services.OfferError as error:
            return offer_error(error)
        return Response(
            OfferSerializer(own_offer(request, offer.pk)).data, status=status.HTTP_201_CREATED
        )


class MyOffersView(APIView):
    permission_classes = [IsApprovedDealer]

    def get(self, request):
        offers = dealer_offers(request).order_by("-created_at")
        return Response(OfferSerializer(offers, many=True).data)


class OfferDetailView(APIView):
    permission_classes = [IsApprovedDealer]

    def get(self, request, offer_id):
        return Response(OfferSerializer(own_offer(request, offer_id)).data)

    def patch(self, request, offer_id):
        offer = own_offer(request, offer_id)
        serializer = OfferInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_offer(offer, serializer.validated_data["rates"])
        except services.OfferError as error:
            return offer_error(error)
        return Response(OfferSerializer(own_offer(request, offer_id)).data)


class OfferWithdrawView(APIView):
    permission_classes = [IsApprovedDealer]

    def post(self, request, offer_id):
        offer = own_offer(request, offer_id)
        try:
            services.withdraw_offer(offer)
        except services.OfferError as error:
            return offer_error(error)
        return Response(OfferSerializer(own_offer(request, offer_id)).data)
