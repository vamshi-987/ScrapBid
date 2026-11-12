from django.conf import settings
from rest_framework import serializers

from config.storage import presigned_url
from dealers.validation import PINCODE, is_in_india
from listings.models import Category, Listing, ListingItem, ListingPhoto, PricingUnit
from listings.services import expires_at, is_editable


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "pricing_unit", "is_restricted"]


class ListingPhotoSerializer(serializers.ModelSerializer):
    # A short-lived presigned link; the storage key itself is never exposed.
    url = serializers.SerializerMethodField()

    class Meta:
        model = ListingPhoto
        fields = ["id", "url", "content_type", "width", "height", "created_at"]

    def get_url(self, photo):
        return presigned_url(photo.file.name)


class PhotoUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


MAX_ITEM_QUANTITY = 1000


class ListingItemInputSerializer(serializers.Serializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.filter(is_active=True))
    declared_weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True
    )
    declared_quantity = serializers.IntegerField(
        min_value=1, max_value=MAX_ITEM_QUANTITY, required=False, allow_null=True
    )

    def validate(self, attrs):
        category = attrs["category"]
        weight = attrs.get("declared_weight_kg")
        quantity = attrs.get("declared_quantity")
        if category.pricing_unit == PricingUnit.PER_KG:
            if weight is None or quantity is not None:
                raise serializers.ValidationError(
                    {"declared_weight_kg": f"Enter the weight in kg for {category.name}."}
                )
            if weight <= 0 or weight > settings.LISTING_MAX_WEIGHT_KG:
                raise serializers.ValidationError(
                    {
                        "declared_weight_kg": (
                            f"Enter a weight above 0 and up to {settings.LISTING_MAX_WEIGHT_KG} kg."
                        )
                    }
                )
            attrs["declared_quantity"] = None
        else:
            if quantity is None or weight is not None:
                raise serializers.ValidationError(
                    {"declared_quantity": f"Enter how many items of {category.name}."}
                )
            attrs["declared_weight_kg"] = None
        return attrs


class ListingWriteSerializer(serializers.Serializer):
    """Input for creating a listing (all fields) and editing one (any fields)."""

    address_line = serializers.CharField(max_length=200)
    locality = serializers.CharField(max_length=80)
    pincode = serializers.CharField(max_length=6)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    items = ListingItemInputSerializer(many=True, allow_empty=False)

    def validate_pincode(self, value):
        if not PINCODE.fullmatch(value):
            raise serializers.ValidationError("Enter a valid 6-digit pincode.")
        return value

    def validate_items(self, items):
        categories = [item["category"].pk for item in items]
        if len(categories) != len(set(categories)):
            raise serializers.ValidationError("Each category can appear only once.")
        return items

    def validate(self, attrs):
        if ("latitude" in attrs) != ("longitude" in attrs):
            raise serializers.ValidationError("Send both latitude and longitude.")
        if "latitude" in attrs and not is_in_india(attrs["latitude"], attrs["longitude"]):
            raise serializers.ValidationError({"latitude": "The location must be in India."})
        return attrs


class ListingItemSerializer(serializers.ModelSerializer):
    category = serializers.IntegerField(source="category_id")
    category_name = serializers.CharField(source="category.name")
    pricing_unit = serializers.CharField(source="category.pricing_unit")
    is_restricted = serializers.BooleanField(source="category.is_restricted")

    class Meta:
        model = ListingItem
        fields = [
            "category",
            "category_name",
            "pricing_unit",
            "is_restricted",
            "declared_weight_kg",
            "declared_quantity",
        ]


class ListingSummarySerializer(serializers.ModelSerializer):
    """For the seller's own list. No exact address in list responses."""

    items = ListingItemSerializer(many=True)
    photo_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    expires_at = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            "id",
            "status",
            "expires_at",
            "locality",
            "pincode",
            "items",
            "photo_count",
            "cover_url",
            "created_at",
            "published_at",
        ]

    def get_expires_at(self, listing):
        moment = expires_at(listing)
        return serializers.DateTimeField().to_representation(moment) if moment else None

    def get_photo_count(self, listing):
        return len(listing.photos.all())

    def get_cover_url(self, listing):
        photos = listing.photos.all()
        return presigned_url(photos[0].file.name) if photos else None


class ListingDetailSerializer(ListingSummarySerializer):
    """For the listing's owner only: includes the exact address and all photos."""

    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    photos = ListingPhotoSerializer(many=True)
    is_editable = serializers.SerializerMethodField()
    order = serializers.SerializerMethodField()

    class Meta(ListingSummarySerializer.Meta):
        fields = [
            *ListingSummarySerializer.Meta.fields,
            "address_line",
            "latitude",
            "longitude",
            "photos",
            "is_editable",
            "relisted_from",
            "order",
        ]

    def get_latitude(self, listing):
        return listing.location.y

    def get_longitude(self, listing):
        return listing.location.x

    def get_is_editable(self, listing):
        return is_editable(listing)

    def get_order(self, listing):
        """The active order's ID once an offer is accepted, for the pickup page."""
        order = listing.orders.filter(status="active").values_list("pk", flat=True).first()
        return order


class DealerListingSerializer(serializers.ModelSerializer):
    """What a dealer sees before an offer is accepted: approximate area only.

    No exact address, coordinates, distance or anything about the seller.
    """

    items = ListingItemSerializer(many=True)
    photos = ListingPhotoSerializer(many=True)
    my_offer = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "locality", "pincode", "items", "photos", "published_at", "my_offer"]

    def get_my_offer(self, listing):
        """The requesting dealer's live offer on this listing, if any (never other dealers').

        Comes prefetched by the feed query (listings.feed.visible_to_dealer)."""
        from offers.serializers import OfferSerializer

        live = [offer for offer in listing.my_offers if offer.is_live]
        return OfferSerializer(live[0]).data if live else None
