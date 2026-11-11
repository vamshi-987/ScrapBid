from rest_framework import serializers

from offers.models import Offer
from offers.pricing import estimated_total_paise, line_paise


class RateInputSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    rate_paise = serializers.IntegerField(min_value=1)


class OfferInputSerializer(serializers.Serializer):
    """Rates in integer paise. Any total the client sends is ignored."""

    rates = RateInputSerializer(many=True, allow_empty=False)

    def validate_rates(self, rates):
        by_category = {rate["category"]: rate["rate_paise"] for rate in rates}
        if len(by_category) != len(rates):
            raise serializers.ValidationError("Each category can have only one rate.")
        return by_category


class OfferSerializer(serializers.ModelSerializer):
    """An offer with its estimate, computed on the server from the declared amounts."""

    listing_locality = serializers.CharField(source="listing.locality")
    rates = serializers.SerializerMethodField()
    estimated_total_paise = serializers.SerializerMethodField()
    is_live = serializers.BooleanField()

    class Meta:
        model = Offer
        fields = [
            "id",
            "listing",
            "listing_locality",
            "status",
            "is_live",
            "expires_at",
            "created_at",
            "updated_at",
            "rates",
            "estimated_total_paise",
        ]

    def _rates(self, offer):
        return {rate.category_id: rate.rate_paise for rate in offer.rates.all()}

    def get_rates(self, offer):
        rates = self._rates(offer)
        return [
            {
                "category": item.category_id,
                "category_name": item.category.name,
                "pricing_unit": item.category.pricing_unit,
                "rate_paise": rates[item.category_id],
                "estimated_paise": line_paise(item, rates[item.category_id]),
            }
            for item in offer.listing.items.all()
            if item.category_id in rates
        ]

    def get_estimated_total_paise(self, offer):
        rates = self._rates(offer)
        items = [item for item in offer.listing.items.all() if item.category_id in rates]
        return estimated_total_paise(items, rates)
