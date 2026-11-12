from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role
from accounts.permissions import HasRole
from offers.models import Offer
from orders import scheduling
from orders.models import Order
from orders.serializers import OrderSerializer, PickupSerializer, SlotInputSerializer
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


def my_orders(request):
    """Orders where the user is the seller or the accepted dealer."""
    return (
        Order.objects.filter(Q(seller=request.user) | Q(dealer__user=request.user))
        .select_related("listing", "dealer__user", "seller")
        .prefetch_related("items__category")
    )


def my_order(request, order_id):
    return get_object_or_404(my_orders(request), pk=order_id)


def pickup_response(request, order_id):
    return Response(
        PickupSerializer(my_order(request, order_id), context={"request": request}).data
    )


class OrderListView(APIView):
    def get(self, request):
        orders = my_orders(request).order_by("-created_at")
        return Response(PickupSerializer(orders, many=True, context={"request": request}).data)


class OrderDetailView(APIView):
    def get(self, request, order_id):
        return pickup_response(request, order_id)


def slot_error(error):
    http_status = (
        status.HTTP_400_BAD_REQUEST
        if error.code in ("misaligned", "outside_hours", "too_soon", "too_far")
        else status.HTTP_409_CONFLICT
    )
    return Response({"detail": str(error), "code": error.code}, status=http_status)


class ProposeSlotView(APIView):
    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = SlotInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            scheduling.propose_slot(order, request.user, serializer.validated_data["start"])
        except scheduling.SlotError as error:
            return slot_error(error)
        return pickup_response(request, order_id)


class ConfirmSlotView(APIView):
    def post(self, request, order_id):
        try:
            scheduling.confirm_slot(my_order(request, order_id), request.user)
        except scheduling.SlotError as error:
            return slot_error(error)
        return pickup_response(request, order_id)


class RequestRescheduleView(APIView):
    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = SlotInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            scheduling.request_reschedule(order, request.user, serializer.validated_data["start"])
        except scheduling.SlotError as error:
            return slot_error(error)
        return pickup_response(request, order_id)


class AnswerRescheduleView(APIView):
    accept = True

    def post(self, request, order_id):
        try:
            scheduling.answer_reschedule(my_order(request, order_id), request.user, self.accept)
        except scheduling.SlotError as error:
            return slot_error(error)
        return pickup_response(request, order_id)
