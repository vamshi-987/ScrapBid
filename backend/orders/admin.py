from django.contrib import admin, messages

from accounts.phone import mask_phone
from orders.endings import resolve_strike
from orders.models import NoShowStrike, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    fields = ["category", "declared_weight_kg", "declared_quantity", "rate_paise"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """View-only: orders change only through the app's rules."""

    list_display = [
        "id",
        "listing",
        "dealer",
        "status",
        "cancelled_by",
        "no_show_party",
        "estimated_total_paise",
        "created_at",
    ]
    list_filter = ["status", "cancelled_by", "no_show_party"]
    exclude = ["handover_code_hash"]
    inlines = [OrderItemInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NoShowStrike)
class NoShowStrikeAdmin(admin.ModelAdmin):
    """Every missed pickup, with the check-in evidence. Admins uphold or remove strikes;
    removing one lifts its block straight away."""

    list_display = [
        "id",
        "user_phone",
        "party",
        "status",
        "challenged",
        "blocked_until",
        "checked_in",
        "check_in_distance",
        "created_at",
    ]
    list_filter = ["status", "party"]
    readonly_fields = [
        "user_phone",
        "order",
        "party",
        "status",
        "blocked_until",
        "challenge_reason",
        "challenged_at",
        "checked_in",
        "check_in_distance",
        "slot",
        "resolved_by",
        "resolved_at",
        "created_at",
    ]
    fields = readonly_fields
    actions = ["uphold", "remove"]

    @admin.display(description="User")
    def user_phone(self, strike):
        return mask_phone(strike.user.phone)

    @admin.display(boolean=True, description="Challenged")
    def challenged(self, strike):
        return strike.challenged_at is not None

    @admin.display(description="Dealer checked in at")
    def checked_in(self, strike):
        return strike.order.dealer_checked_in_at or "No check-in"

    @admin.display(description="Check-in distance (m)")
    def check_in_distance(self, strike):
        return strike.order.dealer_check_in_distance_m

    @admin.display(description="Confirmed slot")
    def slot(self, strike):
        return f"{strike.order.slot_start} – {strike.order.slot_end}"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Uphold selected strikes")
    def uphold(self, request, queryset):
        for strike in queryset:
            resolve_strike(strike, request.user, uphold=True)
            self.log_change(request, strike, "Upheld")
        self.message_user(request, f"Upheld {queryset.count()} strike(s).", messages.SUCCESS)

    @admin.action(description="Remove selected strikes (lifts the block)")
    def remove(self, request, queryset):
        for strike in queryset:
            resolve_strike(strike, request.user, uphold=False)
            self.log_change(request, strike, "Removed")
        self.message_user(request, f"Removed {queryset.count()} strike(s).", messages.SUCCESS)
