from django.contrib import admin

from offers.models import Offer, OfferRate


class OfferRateInline(admin.TabularInline):
    model = OfferRate
    fields = ["category", "rate_paise"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    """View-only: offers change only through the app's rules."""

    list_display = ["id", "listing", "dealer", "status", "expires_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["=id", "=listing__id"]
    inlines = [OfferRateInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
