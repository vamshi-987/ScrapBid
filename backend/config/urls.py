from django.contrib import admin
from django.urls import include, path

from accounts.views import AdminLoginRedirectView
from config.health import HealthView

urlpatterns = [
    # Admins log in with phone OTP in the web app; Django admin then uses that session.
    path("admin/login/", AdminLoginRedirectView.as_view(), name="admin-login"),
    path("admin/", admin.site.urls),
    path("api/health", HealthView.as_view(), name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("listings.urls")),
]
