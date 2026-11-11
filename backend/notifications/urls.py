from django.urls import path

from notifications import views

urlpatterns = [
    path("notifications", views.NotificationListView.as_view(), name="notification-list"),
    path(
        "notifications/unread-count",
        views.UnreadCountView.as_view(),
        name="notification-unread-count",
    ),
    path("notifications/read-all", views.MarkAllReadView.as_view(), name="notification-read-all"),
    path(
        "notifications/<int:notification_id>/read",
        views.MarkReadView.as_view(),
        name="notification-read",
    ),
]
