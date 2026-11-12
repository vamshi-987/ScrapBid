from django.conf import settings
from rest_framework import serializers

from orders.models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name")
    pricing_unit = serializers.CharField(source="category.pricing_unit")

    class Meta:
        model = OrderItem
        fields = [
            "category",
            "category_name",
            "pricing_unit",
            "declared_weight_kg",
            "declared_quantity",
            "rate_paise",
            "verified_amount",
            "taken_amount",
            "comparison",
            "choice",
            "amount_paise",
        ]


class OrderSerializer(serializers.ModelSerializer):
    """The agreed deal. Contact details are revealed separately (and only while open)."""

    dealer_name = serializers.CharField(source="dealer.business_name")
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "listing",
            "offer",
            "status",
            "dealer_name",
            "items",
            "estimated_total_paise",
            "created_at",
        ]


class PickupSerializer(OrderSerializer):
    """An order as its seller or dealer sees it, with the pickup slot and the actions
    this user can take. The exact address and phone come from the contact endpoint."""

    listing_status = serializers.CharField(source="listing.status")
    locality = serializers.CharField(source="listing.locality")
    pincode = serializers.CharField(source="listing.pincode")
    my_role = serializers.SerializerMethodField()
    scale_photo_url = serializers.SerializerMethodField()
    weight_tolerance_percent = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = [
            *OrderSerializer.Meta.fields,
            "listing_status",
            "locality",
            "pincode",
            "my_role",
            "slot_start",
            "slot_end",
            "slot_status",
            "slot_proposed_by",
            "reschedule_start",
            "reschedule_end",
            "reschedule_requested_by",
            "dealer_checked_in_at",
            "dealer_check_in_distance_m",
            "cancelled_by",
            "cancel_reason",
            "no_show_party",
            "weighed_at",
            "weighing_distance_m",
            "final_amount_paise",
            "scale_photo_url",
            "weight_tolerance_percent",
            "actions",
        ]

    def get_weight_tolerance_percent(self, order):
        """The server's tolerance, so the app can show which choices apply while weighing."""
        return settings.WEIGHT_TOLERANCE_PERCENT

    def get_scale_photo_url(self, order):
        """A short-lived link to the scale photo, for the seller and the dealer only."""
        from config.storage import presigned_url

        return presigned_url(order.scale_photo.name) if order.scale_photo else None

    def get_my_role(self, order):
        from orders.scheduling import party_of

        return party_of(order, self.context["request"].user)

    def get_actions(self, order):
        from orders.scheduling import available_actions

        return available_actions(order, self.context["request"].user)


class SlotInputSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
