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
    blocked_until = serializers.SerializerMethodField()
    suspension = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone", "roles", "is_staff", "blocked_until", "suspension"]

    def get_blocked_until(self, user):
        """End of a no-show block, if any: blocked sellers can't publish, dealers can't offer."""
        from orders.endings import blocked_until

        moment = blocked_until(user)
        return serializers.DateTimeField().to_representation(moment) if moment else None

    def get_suspension(self, user):
        """An active suspension (selling and buying blocked) and the fee to lift it."""
        from accounts.models import Suspension

        active = Suspension.objects.filter(user=user, lifted_at=None).first()
        if not active:
            return None
        return {
            "since": serializers.DateTimeField().to_representation(active.created_at),
            "fee_paise": active.fee_paise,
        }

    def get_roles(self, user):
        return [*user.roles, "admin"] if user.is_staff else list(user.roles)
