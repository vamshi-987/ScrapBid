from django.contrib import admin

from listings.models import Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "pricing_unit", "is_restricted", "is_active"]
    list_editable = ["is_active"]
    list_filter = ["pricing_unit", "is_restricted", "is_active"]
    search_fields = ["name"]

    def has_delete_permission(self, request, obj=None):
        # Listings refer to categories, so they are deactivated, never deleted.
        return False
