import json
import math

from django.conf import settings
from django.db.models import Case, Q, When
from django.http import Http404, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.utils.html import format_html
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role
from accounts.permissions import HasRole
from offers.models import Offer
from orders import disputes, endings, handover, payments, scheduling, weighing
from orders.gateway import FakeGateway
from orders.models import Due, DueStatus, NoShowStrike, Order, OrderStatus, Party
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


class OrderContactView(APIView):
    """Contact details for the other side of an open order, and only for its two sides.

    The accepted dealer gets the exact pickup address and the seller's phone; the
    seller gets the dealer's phone. Nothing is returned once the order has closed.
    """

    def get(self, request, order_id):
        order = my_order(request, order_id)
        if order.status != OrderStatus.ACTIVE:
            return Response(
                {
                    "detail": "Contact details are no longer shared for this order.",
                    "code": "contact_closed",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if scheduling.party_of(order, request.user) == Party.DEALER:
            listing = order.listing
            return Response(
                {
                    "name": None,
                    "phone": order.seller.phone,
                    "address_line": listing.address_line,
                    "locality": listing.locality,
                    "pincode": listing.pincode,
                    "latitude": listing.location.y,
                    "longitude": listing.location.x,
                }
            )
        return Response({"name": order.dealer.business_name, "phone": order.dealer.user.phone})


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, allow_blank=True, default="")


class CompleteSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20, allow_blank=True, allow_null=True, default="")
    payment_method = serializers.CharField(
        max_length=20, allow_blank=True, allow_null=True, default=""
    )


class CheckInSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)


def ending_error(error):
    http_status = (
        status.HTTP_400_BAD_REQUEST
        if error.code in ("reason_required", "too_far_away", "outside_slot")
        else status.HTTP_409_CONFLICT
    )
    return Response({"detail": str(error), "code": error.code}, status=http_status)


class CancelOrderView(APIView):
    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            endings.cancel_order(order, request.user, serializer.validated_data["reason"])
        except endings.EndingError as error:
            return ending_error(error)
        return pickup_response(request, order_id)


class CheckInView(APIView):
    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = CheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            endings.check_in(order, request.user, data["latitude"], data["longitude"])
        except endings.EndingError as error:
            return ending_error(error)
        return pickup_response(request, order_id)


class StrikeSerializer(serializers.ModelSerializer):
    can_challenge = serializers.SerializerMethodField()
    penalty = serializers.SerializerMethodField()

    def get_penalty(self, strike):
        due = getattr(strike, "due", None)
        return (
            {"id": due.pk, "amount_paise": due.amount_paise, "status": due.status} if due else None
        )

    class Meta:
        model = NoShowStrike
        fields = [
            "id",
            "order",
            "party",
            "status",
            "penalty",
            "challenge_reason",
            "challenged_at",
            "created_at",
            "can_challenge",
        ]

    def get_can_challenge(self, strike):
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone

        window_end = strike.created_at + timedelta(hours=settings.STRIKE_CHALLENGE_HOURS)
        return strike.challenged_at is None and timezone.now() <= window_end


class MyStrikesView(APIView):
    def get(self, request):
        strikes = NoShowStrike.objects.filter(user=request.user).order_by("-created_at")
        return Response(StrikeSerializer(strikes, many=True).data)


class ChallengeStrikeView(APIView):
    def post(self, request, strike_id):
        strike = get_object_or_404(NoShowStrike, pk=strike_id, user=request.user)
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            endings.challenge_strike(strike, request.user, serializer.validated_data["reason"])
        except endings.EndingError as error:
            return ending_error(error)
        strike.refresh_from_db()
        return Response(StrikeSerializer(strike).data)


class WeighView(APIView):
    """The dealer records the weighing: scale photo, GPS and the verified amounts (multipart).

    `lines` is JSON: [{"category": id, "verified": "20.5", "choice": null | "take_excess" | ...}].
    Any total or rate sent is ignored; amounts come from the order snapshot.
    """

    parser_classes = [MultiPartParser]

    def post(self, request, order_id):
        order = my_order(request, order_id)
        try:
            lines = json.loads(request.data.get("lines") or "[]")
            latitude = float(request.data["latitude"])
            longitude = float(request.data["longitude"])
            if not (math.isfinite(latitude) and math.isfinite(longitude)):
                raise ValueError("not a position")
        except (ValueError, KeyError, TypeError):
            return Response(
                {
                    "detail": "Send the GPS position and the verified amounts.",
                    "code": "bad_request",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(lines, list):
            lines = []
        try:
            weighing.record_weighing(
                order,
                request.user,
                latitude=latitude,
                longitude=longitude,
                photo=request.FILES.get("photo"),
                lines=lines,
            )
        except weighing.WeighingError as error:
            http_status = (
                status.HTTP_409_CONFLICT
                if error.code in ("dealer_only", "wrong_stage")
                else status.HTTP_400_BAD_REQUEST
            )
            return Response(
                {"detail": str(error), "code": error.code, "category": error.category},
                status=http_status,
            )
        return pickup_response(request, order_id)


class ConfirmWeighingView(APIView):
    """The seller confirms the weighing and gets the handover code to show the dealer.

    Calling again gives a fresh code (the old one stops working). The code is in this
    response only; the server keeps just its hash.
    """

    def post(self, request, order_id):
        order = my_order(request, order_id)
        try:
            order, code = handover.confirm_weighing(order, request.user)
        except handover.HandoverError as error:
            return Response(
                {"detail": str(error), "code": error.code}, status=status.HTTP_409_CONFLICT
            )
        data = PickupSerializer(my_order(request, order_id), context={"request": request}).data
        return Response({**data, "handover_code": code})


class CompleteOrderView(APIView):
    """The dealer enters the seller's code and the payment method (cash or UPI).

    The amount paid is the server's final amount; any amount sent is ignored.
    Safe to retry: a repeat returns the completed order.
    """

    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            handover.complete_order(order, request.user, **serializer.validated_data)
        except handover.HandoverError as error:
            http_status = (
                status.HTTP_409_CONFLICT
                if error.code in ("dealer_only", "wrong_stage", "not_confirmed")
                else status.HTTP_400_BAD_REQUEST
            )
            return Response({"detail": str(error), "code": error.code}, status=http_status)
        return pickup_response(request, order_id)


class DisputeView(APIView):
    """The seller disputes the weighing (with a reason) instead of confirming it.

    The listing goes to an admin for review; nobody can complete it meanwhile.
    """

    def post(self, request, order_id):
        order = my_order(request, order_id)
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            disputes.open_dispute(order, request.user, serializer.validated_data["reason"])
        except disputes.DisputeError as error:
            http_status = (
                status.HTTP_400_BAD_REQUEST
                if error.code == "reason_required"
                else status.HTTP_409_CONFLICT
            )
            return Response({"detail": str(error), "code": error.code}, status=http_status)
        return pickup_response(request, order_id)


class DueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Due
        fields = ["id", "kind", "amount_paise", "status", "created_at", "paid_at"]


def my_due(request, due_id):
    return get_object_or_404(Due, pk=due_id, user=request.user)


def payment_error(error):
    http_status = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if error.code == "gateway_unavailable"
        else status.HTTP_409_CONFLICT
    )
    return Response({"detail": str(error), "code": error.code}, status=http_status)


class MyDuesView(APIView):
    """Penalties and fees the user owes or has paid, unpaid first."""

    def get(self, request):
        dues = Due.objects.filter(user=request.user).order_by(
            Case(When(status=DueStatus.UNPAID, then=0), default=1), "-created_at"
        )
        return Response(DueSerializer(dues, many=True).data)


class PayDueView(APIView):
    """Start paying a due: returns the gateway's payment page URL. The amount is the server's."""

    def post(self, request, due_id):
        try:
            url = payments.start_payment(my_due(request, due_id))
        except payments.PaymentError as error:
            return payment_error(error)
        return Response({"url": url})


class CheckDueView(APIView):
    """Ask the gateway whether the due has been paid (e.g. if the redirect back was missed)."""

    def post(self, request, due_id):
        try:
            due = payments.confirm_payment(my_due(request, due_id))
        except payments.PaymentError as error:
            return payment_error(error)
        return Response(DueSerializer(due).data)


class PaymentCallbackView(APIView):
    """Where the gateway sends the user back. The signature is checked and the payment is
    confirmed with the gateway server-side; then the user goes to their dues page."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            payments.confirm_from_callback(request.query_params)
        except payments.PaymentError:
            return redirect("/dues?payment=failed")
        return redirect("/dues?payment=paid")


class FakePayView(APIView):
    """The local fake gateway's pay page (PAYMENT_PROVIDER=fake only)."""

    permission_classes = [AllowAny]
    parser_classes = [FormParser]

    def _link(self, link_id):
        if settings.PAYMENT_PROVIDER != "fake":
            raise Http404
        link = FakeGateway().get(link_id)
        if link is None:
            raise Http404
        return link

    def get(self, request, link_id):
        link = self._link(link_id)
        return HttpResponse(
            format_html(
                "<!doctype html><meta name=viewport content='width=device-width'>"
                "<title>Test payment</title><body style='font-family:sans-serif;padding:16px'>"
                "<h1>Test payment (local fake gateway)</h1><p>{}: <strong>₹{}</strong></p>"
                "<form method=post><input type=hidden name=csrfmiddlewaretoken value='{}'>"
                "<button name=result value=pay>Pay</button> "
                "<button name=result value=fail>Fail</button></form>",
                link["description"],
                f"{link['amount'] / 100:,.2f}",
                get_token(request),
            )
        )

    def post(self, request, link_id):
        self._link(link_id)
        if request.data.get("result") != "pay":
            return redirect("/dues?payment=failed")
        return redirect(FakeGateway().pay(link_id))
