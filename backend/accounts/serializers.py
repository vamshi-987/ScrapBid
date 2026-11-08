from rest_framework import serializers

from accounts.models import User
from accounts.otp import CODE_LENGTH
from accounts.phone import normalize_phone


class PhoneField(serializers.CharField):
    default_error_messages = {"invalid_phone": "Enter a valid Indian mobile number."}

    def to_internal_value(self, data):
        phone = normalize_phone(super().to_internal_value(data))
        if phone is None:
            self.fail("invalid_phone")
        return phone


class OtpRequestSerializer(serializers.Serializer):
    phone = PhoneField(max_length=20)


class OtpVerifySerializer(serializers.Serializer):
    phone = PhoneField(max_length=20)
    code = serializers.RegexField(
        rf"^\d{{{CODE_LENGTH}}}$",
        error_messages={"invalid": f"Enter the {CODE_LENGTH}-digit code."},
    )


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone", "roles", "is_staff"]

    def get_roles(self, user):
        return [*user.roles, "admin"] if user.is_staff else list(user.roles)
