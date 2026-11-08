from django.conf import settings
from django.contrib.auth import login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import otp
from accounts.serializers import MeSerializer, OtpRequestSerializer, OtpVerifySerializer

ERROR_MESSAGES = {
    otp.InvalidCode: "The code is wrong or has expired.",
    otp.TooManyAttempts: "Too many wrong attempts. Request a new code.",
    otp.AccountDisabled: "This account is disabled.",
}


def client_ip(request):
    # Caddy replaces any client-sent X-Forwarded-For with the real client IP,
    # and the backend is only reachable through Caddy.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def otp_error_response(error):
    http_status = (
        status.HTTP_403_FORBIDDEN
        if isinstance(error, otp.AccountDisabled)
        else status.HTTP_400_BAD_REQUEST
    )
    return Response(
        {"detail": ERROR_MESSAGES[type(error)], "code": error.code},
        status=http_status,
    )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Sets the CSRF cookie so the web app can send state-changing requests."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name="dispatch")
class OtpRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            otp.request_otp(serializer.validated_data["phone"], client_ip(request))
        except otp.RateLimited as error:
            raise Throttled(wait=error.wait_seconds) from None
        # Same response whether or not an account exists for this number.
        return Response(
            {"expires_in": settings.OTP_EXPIRY_SECONDS}, status=status.HTTP_202_ACCEPTED
        )


@method_decorator(csrf_protect, name="dispatch")
class OtpVerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = otp.verify_otp(data["phone"], data["code"])
        except (otp.InvalidCode, otp.TooManyAttempts, otp.AccountDisabled) as error:
            return otp_error_response(error)
        login(request, user)
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)
