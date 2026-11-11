from django.contrib import admin

from orders.models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    fields = ["category", "declared_weight_kg", "declared_quantity", "rate_paise"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """View-only: orders change only through the app's rules."""

    list_display = ["id", "listing", "dealer", "status", "estimated_total_paise", "created_at"]
    list_filter = ["status"]
    inlines = [OrderItemInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
