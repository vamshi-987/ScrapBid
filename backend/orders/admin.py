from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from accounts.phone import mask_phone
from config.storage import presigned_url
from listings.models import ListingStatus
from orders.disputes import DisputeError, resolve_dispute
from orders.endings import resolve_strike
from orders.models import DisputeOutcome, Due, NoShowStrike, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    fields = [
        "category",
        "declared_weight_kg",
        "declared_quantity",
        "rate_paise",
        "verified_amount",
        "taken_amount",
        "choice",
        "amount_paise",
    ]
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
    list_filter = ["listing__status", "status", "cancelled_by", "no_show_party"]
    exclude = ["handover_code_hash"]
    readonly_fields = ["scale_photo_link", "resolve_link"]
    inlines = [OrderItemInline]

    @admin.display(description="Scale photo")
    def scale_photo_link(self, order):
        if not order.scale_photo:
            return "-"
        return format_html(
            '<a href="{}" target="_blank">Open the scale photo</a>',
            presigned_url(order.scale_photo.name),
        )

    @admin.display(description="Dispute")
    def resolve_link(self, order):
        if order.listing.status != ListingStatus.DISPUTED:
            return "-"
        url = reverse("admin:orders_order_resolve", args=[order.pk])
        return format_html('<a class="button" href="{}">Resolve dispute</a>', url)

    def get_urls(self):
        return [
            path(
                "<int:order_id>/resolve/",
                self.admin_site.admin_view(self.resolve_view),
                name="orders_order_resolve",
            ),
            *super().get_urls(),
        ]

    def resolve_view(self, request, order_id):
        """Resolve a disputed order: complete at the measured or adjusted amounts, or cancel."""
        order = get_object_or_404(
            Order.objects.select_related("listing").prefetch_related("items__category"),
            pk=order_id,
        )
        if not self.has_view_permission(request, order):
            return self.admin_site.login(request)
        items = list(order.items.all())
        form = ResolveDisputeForm(request.POST or None, items=items)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            amounts = {item.category_id: data[f"amount_{item.category_id}"] for item in items}
            try:
                resolve_dispute(
                    order,
                    request.user,
                    outcome=data["outcome"],
                    note=data["note"],
                    upheld=data["upheld"] == "yes",
                    amounts=amounts
                    if data["outcome"] == DisputeOutcome.COMPLETED_ADJUSTED
                    else None,
                )
            except DisputeError as error:
                form.add_error(None, str(error))
            else:
                self.message_user(request, "Dispute resolved.", messages.SUCCESS)
                return redirect("admin:orders_order_change", order.pk)
        context = {
            **self.admin_site.each_context(request),
            "title": f"Resolve dispute: order {order.pk}",
            "order": order,
            "items": items,
            "form": form,
            "disputed": order.listing.status == ListingStatus.DISPUTED,
            "scale_photo_url": presigned_url(order.scale_photo.name) if order.scale_photo else None,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/orders/order/resolve_dispute.html", context)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ResolveDisputeForm(forms.Form):
    outcome = forms.ChoiceField(choices=DisputeOutcome.choices, widget=forms.RadioSelect)
    upheld = forms.ChoiceField(
        label="Was the seller right? (upheld counts against the dealer)",
        choices=[("yes", "Yes, upheld"), ("no", "No, not upheld")],
        widget=forms.RadioSelect,
    )
    note = forms.CharField(max_length=500, widget=forms.Textarea, help_text="Shown to both sides.")

    def __init__(self, *args, items, **kwargs):
        super().__init__(*args, **kwargs)
        for item in items:
            unit = "kg" if item.declared_weight_kg is not None else "items"
            self.fields[f"amount_{item.category_id}"] = forms.DecimalField(
                label=f"{item.category.name}: amount to pay for ({unit})",
                required=False,
                min_value=0,
                decimal_places=3,
                max_digits=10,
                initial=item.taken_amount,
                help_text="Only used when completing at amounts set by the admin.",
            )

    def clean(self):
        data = super().clean()
        if data.get("outcome") == DisputeOutcome.COMPLETED_ADJUSTED:
            for name in self.fields:
                if name.startswith("amount_") and data.get(name) is None:
                    self.add_error(name, "Enter the amount to pay for.")
        return data


@admin.register(Due)
class DueAdmin(admin.ModelAdmin):
    """Penalties and fees owed to the platform, and their payments. View-only: dues change
    only through the payment rules."""

    list_display = ["id", "user_phone", "kind", "amount_paise", "status", "created_at", "paid_at"]
    list_filter = ["status", "kind"]
    fields = [
        "user_phone",
        "kind",
        "amount_paise",
        "status",
        "strike",
        "suspension",
        "link_id",
        "payment_id",
        "created_at",
        "paid_at",
        "cancelled_at",
    ]
    readonly_fields = fields

    @admin.display(description="User")
    def user_phone(self, due):
        return mask_phone(due.user.phone)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NoShowStrike)
class NoShowStrikeAdmin(admin.ModelAdmin):
    """Every missed pickup, with the check-in evidence. Admins uphold or remove strikes;
    removing one cancels its penalty if it's still unpaid (which lifts the block)."""

    list_display = [
        "id",
        "user_phone",
        "party",
        "status",
        "challenged",
        "penalty",
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
        "penalty",
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

    @admin.display(description="Penalty")
    def penalty(self, strike):
        due = getattr(strike, "due", None)
        return f"{due.amount_paise} paise, {due.get_status_display().lower()}" if due else "-"

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
