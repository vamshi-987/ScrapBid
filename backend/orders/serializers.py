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
