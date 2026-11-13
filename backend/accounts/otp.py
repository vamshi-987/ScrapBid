"""Phone OTP login: issuing codes, rate limits and verification."""

import math
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string, salted_hmac

from accounts.models import OtpChallenge, User
from accounts.otp_providers import get_provider

CODE_LENGTH = 6
# Challenges are kept this long so the daily request limit can be counted, then purged.
RETENTION = timedelta(days=1)


class OtpError(Exception):
    code = "otp_error"


class RateLimited(OtpError):
    code = "rate_limited"

    def __init__(self, wait_seconds):
        super().__init__(wait_seconds)
        self.wait_seconds = wait_seconds


class InvalidCode(OtpError):
    code = "invalid_code"


class TooManyAttempts(OtpError):
    code = "too_many_attempts"


class AccountDisabled(OtpError):
    code = "account_disabled"


def _hash(purpose, value):
    return salted_hmac(f"accounts.otp.{purpose}", value, algorithm="sha256").hexdigest()


def _code_hash(phone, code):
    return _hash("code", f"{phone}:{code}")


def _new_code():
    fixed = settings.DEV_FIXED_OTP
    if fixed and settings.OTP_PROVIDER == "console":
        if not (len(fixed) == CODE_LENGTH and fixed.isdigit()):
            raise ImproperlyConfigured(f"DEV_FIXED_OTP must be {CODE_LENGTH} digits")
        return fixed
    return get_random_string(CODE_LENGTH, allowed_chars="0123456789")


def _seconds_until_allowed(queryset, window, limit, now):
    """Return 0 if another request fits in the window, else seconds until one does."""
    recent = queryset.filter(created_at__gt=now - window)
    if recent.count() < limit:
        return 0
    oldest = recent.order_by("created_at").values_list("created_at", flat=True).first()
    return max(1, math.ceil((oldest + window - now).total_seconds()))


def request_otp(phone, ip):
    """Create a challenge for a normalized phone number and send the code."""
    now = timezone.now()
    phone_hash = _hash("phone", phone)
    ip_hash = _hash("ip", ip or "unknown")
    by_phone = OtpChallenge.objects.filter(phone_hash=phone_hash)
    by_ip = OtpChallenge.objects.filter(ip_hash=ip_hash)
    limits = [
        (by_phone, timedelta(minutes=10), settings.OTP_MAX_REQUESTS_PER_PHONE_10_MIN),
        (by_phone, timedelta(days=1), settings.OTP_MAX_REQUESTS_PER_PHONE_DAY),
        (by_ip, timedelta(hours=1), settings.OTP_MAX_REQUESTS_PER_IP_HOUR),
    ]
    wait = max(_seconds_until_allowed(qs, window, limit, now) for qs, window, limit in limits)
    if wait:
        raise RateLimited(wait)

    code = _new_code()
    OtpChallenge.objects.create(
        phone_hash=phone_hash,
        ip_hash=ip_hash,
        code_hash=_code_hash(phone, code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS),
    )
    get_provider().send(phone, code)


def verify_otp(phone, code):
    """Check the code against the phone's latest challenge and return the user.

    A new request supersedes earlier codes. Wrong attempts are saved even
    though an error is raised, so the attempt limit can't be bypassed.
    """
    now = timezone.now()
    error = None
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(phone_hash=_hash("phone", phone))
            .order_by("-created_at", "-id")
            .first()
        )
        if challenge is None or challenge.consumed_at or challenge.expires_at <= now:
            error = InvalidCode
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            error = TooManyAttempts
        elif not constant_time_compare(challenge.code_hash, _code_hash(phone, code)):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            error = (
                TooManyAttempts if challenge.attempts >= settings.OTP_MAX_ATTEMPTS else InvalidCode
            )
        else:
            challenge.consumed_at = now
            challenge.save(update_fields=["consumed_at"])
            user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
            if user.is_active:
                return user
            error = AccountDisabled
    raise error()


def forget_phone(phone):
    """Delete every challenge for a phone (used when its account is deleted)."""
    OtpChallenge.objects.filter(phone_hash=_hash("phone", phone)).delete()


def purge_old_challenges():
    """Delete challenges older than the retention period. Returns the number deleted."""
    deleted, _ = OtpChallenge.objects.filter(created_at__lt=timezone.now() - RETENTION).delete()
    return deleted
