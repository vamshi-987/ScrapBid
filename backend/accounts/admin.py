from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from accounts.models import Flag, FlagStatus, Role, Suspension, User
from accounts.phone import mask_phone


class UserAdminForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(
        choices=Role.choices, widget=forms.CheckboxSelectMultiple, required=False
    )

    class Meta:
        model = User
        fields = ["phone", "roles", "is_active", "is_staff", "is_superuser"]


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserAdminForm
    list_display = [
        "id",
        "masked_phone",
        "roles",
        "is_active",
        "is_staff",
        "cancellations",
        "no_shows",
        "date_joined",
    ]
    list_filter = ["is_active", "is_staff"]
    # Exact match only, so admins find a user by number without browsing numbers.
    search_fields = ["=phone"]
    readonly_fields = ["date_joined", "last_login", "misuse_stats"]
    actions = ["suspend", "reactivate"]

    @admin.display(description="Phone")
    def masked_phone(self, user):
        return mask_phone(user.phone)

    @admin.display(description="Cancellations")
    def cancellations(self, user):
        """Pickups this user cancelled, as seller or as dealer (short-weight denials excluded)."""
        from orders.stats import cancellation_count

        return cancellation_count(user)

    @admin.display(description="Misuse stats")
    def misuse_stats(self, user):
        """Numbers that reveal weight misuse. Variance: (verified − declared) / declared."""
        from orders.stats import user_stats

        if not user.pk:
            return "-"
        s = user_stats(user)

        def percent(value):
            return "no data" if value is None else f"{value:+}%"

        rows = [
            ("As dealer: pickups weighed", s["dealer_weighed"]),
            (
                "As dealer: short-weight denials",
                f"{s['dealer_denials']}"
                + (
                    f" ({s['dealer_denial_rate_percent']}%)"
                    if s["dealer_denial_rate_percent"] is not None
                    else ""
                ),
            ),
            ("As dealer: average weight variance", percent(s["dealer_variance_percent"])),
            (
                "As dealer: disputes",
                f"{s['dealer_disputes']} ({s['dealer_disputes_upheld']} upheld)",
            ),
            ("As seller: average weight variance", percent(s["seller_variance_percent"])),
            ("Cancellations", s["cancellations"]),
            ("No-shows", s["no_shows"]),
        ]
        return format_html(
            "<table>{}</table>",
            format_html_join("", "<tr><th>{}</th><td>{}</td></tr>", rows),
        )

    @admin.display(description="No-shows")
    def no_shows(self, user):
        return user.no_show_strikes.exclude(status="removed").count()

    @admin.action(description="Suspend selected accounts")
    def suspend(self, request, queryset):
        count = queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, f"Suspended {count} account(s).", messages.SUCCESS)

    @admin.action(description="Reactivate selected accounts")
    def reactivate(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Reactivated {count} account(s).", messages.SUCCESS)


class FlagActionForm(ActionForm):
    note = forms.CharField(required=False, max_length=300, label="Note (needed to close)")


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    """Unusual activity for admins to review. Close a flag with a note once reviewed."""

    action_form = FlagActionForm
    actions = ["close"]
    list_display = ["id", "user_phone", "kind", "category", "summary", "status", "created_at"]
    list_filter = ["status", "kind"]
    fields = ["user_phone", "kind", "category", "summary", "status", "note", "closed_at"]
    readonly_fields = fields

    @admin.display(description="User")
    def user_phone(self, flag):
        return mask_phone(flag.user.phone)

    @admin.display(description="Details")
    def summary(self, flag):
        d = flag.details
        if flag.kind == Flag.RATE_OUTLIER and d:
            return (
                f"{d['rate_paise']} paise vs peer average {d['peer_average_paise']} "
                f"({d['above_percent']}% above, {d['peers']} peers)"
            )
        return "-"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Close selected flags (note needed)")
    def close(self, request, queryset):
        note = request.POST.get("note", "").strip()
        if not note:
            self.message_user(request, "Add a note to close flags.", messages.ERROR)
            return
        count = queryset.filter(status=FlagStatus.OPEN).update(
            status=FlagStatus.CLOSED, note=note, closed_at=timezone.now(), closed_by=request.user
        )
        self.message_user(request, f"Closed {count} flag(s).", messages.SUCCESS)


@admin.register(Suspension)
class SuspensionAdmin(admin.ModelAdmin):
    """Suspensions from selling and buying, with the fee to lift them."""

    actions = ["lift"]
    list_display = ["id", "user_phone", "reason", "fee_paise", "created_at", "lifted_at"]
    list_filter = [("lifted_at", admin.EmptyFieldListFilter)]
    fields = ["user_phone", "reason", "fee_paise", "created_at", "lifted_at", "lifted_by"]
    readonly_fields = fields

    @admin.display(description="User")
    def user_phone(self, suspension):
        return mask_phone(suspension.user.phone)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Lift selected suspensions")
    def lift(self, request, queryset):
        from orders.misuse import lift_suspension

        count = sum(lift_suspension(suspension, request.user) for suspension in queryset)
        self.message_user(request, f"Lifted {count} suspension(s).", messages.SUCCESS)
