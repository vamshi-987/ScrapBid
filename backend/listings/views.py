from rest_framework.generics import ListAPIView

from listings.models import Category
from listings.serializers import CategorySerializer


class CategoryListView(ListAPIView):
    """Active categories for any logged-in user. Read-only; admins edit them in Django admin."""

    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)
