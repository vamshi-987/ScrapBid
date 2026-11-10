from django.contrib import admin

from accounts.phone import mask_phone
from listings.models import Category, Listing, ListingItem


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "pricing_unit", "is_restricted", "is_active"]
    list_editable = ["is_active"]
    list_filter = ["pricing_unit", "is_restricted", "is_active"]
    search_fields = ["name"]

    def has_delete_permission(self, request, obj=None):
        # Listings refer to categories, so they are deactivated, never deleted.
        return False


class ListingItemInline(admin.TabularInline):
    model = ListingItem
    fields = ["category", "declared_weight_kg", "declared_quantity"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    """View-only: listing status changes only through the state machine."""

    list_display = ["id", "seller_phone", "status", "locality", "pincode", "published_at"]
    list_filter = ["status"]
    search_fields = ["=id", "pincode"]
    inlines = [ListingItemInline]

    @admin.display(description="Seller")
    def seller_phone(self, listing):
        return mask_phone(listing.seller.phone)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
