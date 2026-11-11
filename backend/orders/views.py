from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role
from accounts.permissions import HasRole
from offers.models import Offer
from orders.models import Order
from orders.serializers import OrderSerializer
from orders.services import AcceptError, accept_offer


class AcceptOfferView(APIView):
    """The listing's seller accepts an offer. Safe to retry: a repeat returns the same order."""

    permission_classes = [HasRole(Role.SELLER)]

    def post(self, request, offer_id):
        # Only offers on the seller's own listings; anything else is not found.
        offer = get_object_or_404(Offer, pk=offer_id, listing__seller=request.user)
        try:
            order, created = accept_offer(request.user, offer)
        except AcceptError as error:
            return Response(
                {"detail": str(error), "code": error.code}, status=status.HTTP_409_CONFLICT
            )
        order = (
            Order.objects.select_related("dealer")
            .prefetch_related("items__category")
            .get(pk=order.pk)
        )
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
