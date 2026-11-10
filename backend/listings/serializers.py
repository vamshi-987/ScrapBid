from rest_framework import serializers

from listings.models import Category


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "pricing_unit", "is_restricted"]
