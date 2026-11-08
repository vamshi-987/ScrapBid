from django import forms
from django.contrib import admin, messages

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
    list_display = ["id", "masked_phone", "roles", "is_active", "is_staff", "date_joined"]
    list_filter = ["is_active", "is_staff"]
    # Exact match only, so admins find a user by number without browsing numbers.
    search_fields = ["=phone"]
    readonly_fields = ["date_joined", "last_login"]
    actions = ["suspend", "reactivate"]

    @admin.display(description="Phone")
    def masked_phone(self, user):
        return mask_phone(user.phone)

    @admin.action(description="Suspend selected accounts")
    def suspend(self, request, queryset):
        count = queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, f"Suspended {count} account(s).", messages.SUCCESS)

    @admin.action(description="Reactivate selected accounts")
    def reactivate(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Reactivated {count} account(s).", messages.SUCCESS)
