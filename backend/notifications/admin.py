from django.contrib import admin

from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "kind", "read_at", "created_at"]
    list_filter = ["kind"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
