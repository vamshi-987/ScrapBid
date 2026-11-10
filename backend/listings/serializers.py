from rest_framework import serializers

from config.storage import presigned_url
from listings.models import Category, ListingPhoto


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
