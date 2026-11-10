from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.core.files.storage import default_storage
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from accounts.phone import mask_phone
from dealers import services
from dealers.models import DealerDocument, DealerProfile, DealerStatus

EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class DealerActionForm(ActionForm):
    reason = forms.CharField(
        required=False,
        max_length=300,
        label="Reason (needed to reject or suspend; shown to the dealer)",
    )


class DealerProfileAdminForm(forms.ModelForm):
    class Meta:
        model = DealerProfile
        fields = ["ewaste_authorised"]

    def clean_ewaste_authorised(self):
        value = self.cleaned_data["ewaste_authorised"]
        if value and not services.can_authorise_ewaste(self.instance):
            raise forms.ValidationError(
                "Upload of an e-waste authorisation document is needed first."
            )
        return value


class DealerDocumentInline(admin.TabularInline):
    model = DealerDocument
    fields = ["doc_type", "content_type", "size_bytes", "uploaded_at", "open_link"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def open_link(self, document):
        url = reverse("admin:dealers_document_file", args=[document.pk])
        return format_html('<a href="{}" target="_blank" rel="noopener">Open</a>', url)


@admin.register(DealerProfile)
class DealerProfileAdmin(admin.ModelAdmin):
    form = DealerProfileAdminForm
    action_form = DealerActionForm
    actions = ["approve", "reject", "suspend", "reinstate"]
    inlines = [DealerDocumentInline]
    list_display = [
        "business_name",
        "masked_phone",
        "status",
        "locality",
        "pincode",
        "missing",
        "scale_certificate_expires_on",
        "ewaste_authorised",
        "reviewed_at",
    ]
    list_filter = ["status", "ewaste_authorised"]
    search_fields = ["business_name", "=user__phone"]
    # Status changes only through the review actions; dealer details only through the app.
    readonly_fields = [
        "masked_phone",
        "status",
        "status_reason",
        "business_name",
        "locality",
        "pincode",
        "coordinates",
        "service_radius_km",
        "id_type",
        "masked_id",
        "scale_certificate_expires_on",
        "missing",
        "reviewed_by",
        "reviewed_at",
    ]
    fieldsets = [
        ("Review", {"fields": ["status", "status_reason", "reviewed_by", "reviewed_at"]}),
        (
            "Dealer",
            {
                "fields": [
                    "masked_phone",
                    "business_name",
                    "locality",
                    "pincode",
                    "coordinates",
                    "service_radius_km",
                    "id_type",
                    "masked_id",
                    "scale_certificate_expires_on",
                    "missing",
                ]
            },
        ),
        ("E-waste", {"fields": ["ewaste_authorised"]}),
    ]

    def has_add_permission(self, request):
        return False  # dealers apply from the app

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Phone")
    def masked_phone(self, profile):
        return mask_phone(profile.user.phone)

    @admin.display(description="ID number")
    def masked_id(self, profile):
        return profile.masked_id_number

    @admin.display(description="Location (lat, lng)")
    def coordinates(self, profile):
        return f"{profile.location.y:.5f}, {profile.location.x:.5f}"

    @admin.display(description="Missing documents")
    def missing(self, profile):
        return ", ".join(services.missing_documents(profile)) or "None"

    # --- Review actions ---

    def _review(self, request, queryset, to_status, verb):
        reason = request.POST.get("reason", "")
        done = 0
        for profile in queryset:
            try:
                services.review_dealer(profile, to_status, request.user, reason)
            except services.ReviewError as error:
                self.message_user(request, f"{profile}: {error}", messages.ERROR)
            else:
                done += 1
                self.log_change(request, profile, f"{verb}. {reason}".strip())
        if done:
            self.message_user(request, f"{verb}: {done} dealer(s).", messages.SUCCESS)

    @admin.action(description="Approve selected dealers")
    def approve(self, request, queryset):
        self._review(
            request, queryset.filter(status=DealerStatus.PENDING), DealerStatus.APPROVED, "Approved"
        )
        self._report_skipped(request, queryset.exclude(status=DealerStatus.PENDING), "approve")

    @admin.action(description="Reject selected dealers (reason needed)")
    def reject(self, request, queryset):
        self._review(request, queryset, DealerStatus.REJECTED, "Rejected")

    @admin.action(description="Suspend selected dealers (reason needed)")
    def suspend(self, request, queryset):
        self._review(request, queryset, DealerStatus.SUSPENDED, "Suspended")

    @admin.action(description="Reinstate selected suspended dealers")
    def reinstate(self, request, queryset):
        self._review(
            request,
            queryset.filter(status=DealerStatus.SUSPENDED),
            DealerStatus.APPROVED,
            "Reinstated",
        )
        self._report_skipped(request, queryset.exclude(status=DealerStatus.SUSPENDED), "reinstate")

    def _report_skipped(self, request, skipped, verb):
        for profile in skipped:
            self.message_user(
                request,
                f"{profile}: can't {verb} a dealer who is {profile.status}.",
                messages.ERROR,
            )

    # --- Private document viewing ---

    def get_urls(self):
        return [
            path(
                "documents/<int:document_id>/file/",
                self.admin_site.admin_view(self.document_file_view),
                name="dealers_document_file",
            ),
            *super().get_urls(),
        ]

    def document_file_view(self, request, document_id):
        """Streams a document from private storage. No public or presigned URL is ever made."""
        document = get_object_or_404(DealerDocument, pk=document_id)
        if not self.has_view_permission(request, document.dealer):
            return self.admin_site.login(request)
        response = FileResponse(
            default_storage.open(document.file.name, "rb"), content_type=document.content_type
        )
        filename = f"{document.doc_type}{EXTENSIONS.get(document.content_type, '')}"
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["Cache-Control"] = "private, no-store"
        return response
