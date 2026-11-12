from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from accounts.models import Role, User
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
