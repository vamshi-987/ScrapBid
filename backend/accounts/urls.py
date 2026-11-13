from django.urls import path

from accounts import views

urlpatterns = [
    path("csrf", views.CsrfView.as_view(), name="auth-csrf"),
    path("otp/request", views.OtpRequestView.as_view(), name="auth-otp-request"),
    path("otp/verify", views.OtpVerifyView.as_view(), name="auth-otp-verify"),
    path("logout", views.LogoutView.as_view(), name="auth-logout"),
    path("me", views.MeView.as_view(), name="auth-me"),
    path("delete-account", views.DeleteAccountView.as_view(), name="auth-delete-account"),
]
