import logging
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import otp, otp_providers
from accounts.models import OtpChallenge, User
from accounts.views import client_ip

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
RAW_PHONE = "98765 43210"
DIGITS = "9876543210"
IP = "203.0.113.5"


@pytest.fixture(autouse=True)
def outbox(settings, monkeypatch):
    """Capture sent codes instead of logging them, with random codes by default."""
    settings.DEV_FIXED_OTP = ""
    sent = []

    class FakeProvider:
        def send(self, phone, code):
            sent.append((phone, code))

    monkeypatch.setattr(otp, "get_provider", FakeProvider)
    return sent


def request_code(client, phone=RAW_PHONE, ip=IP, **extra):
    return client.post(
        "/api/auth/otp/request", {"phone": phone}, format="json", REMOTE_ADDR=ip, **extra
    )


def verify(client, code, phone=RAW_PHONE, **extra):
    return client.post(
        "/api/auth/otp/verify", {"phone": phone, "code": code}, format="json", **extra
    )


def wrong_code(code):
    return "000000" if code != "000000" else "111111"


def last_code(outbox):
    return outbox[-1][1]


# --- Requesting a code -----------------------------------------------------


def test_request_returns_202_and_never_the_code(outbox):
    response = request_code(APIClient())

    assert response.status_code == 202
    assert response.json() == {"expires_in": 300}
    phone, code = outbox[0]
    assert phone == PHONE
    assert code not in response.content.decode()


def test_invalid_phone_is_rejected_without_echoing_it(outbox):
    response = request_code(APIClient(), phone="5876543210")

    assert response.status_code == 400
    assert "phone" in response.json()
    assert "5876543210" not in response.content.decode()
    assert outbox == []


# --- Verifying a code ------------------------------------------------------


def test_correct_code_logs_in_and_creates_account(outbox):
    client = APIClient()
    request_code(client)

    response = verify(client, last_code(outbox))

    assert response.status_code == 200
    assert response.json()["phone"] == PHONE
    assert client.get("/api/auth/me").status_code == 200
    assert User.objects.count() == 1


def test_returning_phone_reuses_the_same_account(outbox):
    first = APIClient()
    request_code(first)
    first_id = verify(first, last_code(outbox)).json()["id"]

    second = APIClient()
    request_code(second)
    second_id = verify(second, last_code(outbox)).json()["id"]

    assert first_id == second_id
    assert User.objects.count() == 1


def test_wrong_code_is_rejected(outbox):
    client = APIClient()
    request_code(client)

    response = verify(client, wrong_code(last_code(outbox)))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_code"
    assert client.get("/api/auth/me").status_code == 403


def test_badly_formatted_code_is_rejected(outbox):
    client = APIClient()
    request_code(client)

    assert verify(client, "12ab").status_code == 400


def test_expired_code_is_rejected(outbox):
    client = APIClient()
    request_code(client)
    OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = verify(client, last_code(outbox))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_code"


def test_code_cannot_be_reused(outbox):
    request_code(APIClient())
    code = last_code(outbox)
    assert verify(APIClient(), code).status_code == 200

    response = verify(APIClient(), code)

    assert response.status_code == 400


def test_new_request_supersedes_the_old_code(outbox, monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr(otp, "get_random_string", lambda *args, **kwargs: next(codes))
    client = APIClient()
    request_code(client)
    request_code(client)

    assert verify(client, "111111").status_code == 400
    assert verify(client, "222222").status_code == 200


def test_code_without_any_request_is_rejected():
    assert verify(APIClient(), "123456").status_code == 400


def test_five_wrong_attempts_lock_the_code(outbox):
    client = APIClient()
    request_code(client)
    code = last_code(outbox)

    for _ in range(4):
        assert verify(client, wrong_code(code)).json()["code"] == "invalid_code"
    assert verify(client, wrong_code(code)).json()["code"] == "too_many_attempts"

    response = verify(client, code)

    assert response.status_code == 400
    assert response.json()["code"] == "too_many_attempts"


def test_disabled_account_cannot_log_in(outbox):
    User.objects.create_user(PHONE, is_active=False)
    client = APIClient()
    request_code(client)

    response = verify(client, last_code(outbox))

    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"
    assert client.get("/api/auth/me").status_code == 403


# --- Rate limits -----------------------------------------------------------


def test_fourth_request_in_ten_minutes_is_limited():
    client = APIClient()
    for _ in range(3):
        assert request_code(client).status_code == 202

    response = request_code(client)

    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 600


def test_eleventh_request_in_a_day_is_limited():
    client = APIClient()
    for i in range(10):
        assert request_code(client, ip=f"198.51.100.{i}").status_code == 202
        # Move earlier requests out of the 10-minute window but keep them within the day.
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(hours=2))

    response = request_code(client, ip="198.51.100.99")

    assert response.status_code == 429


def test_twenty_first_request_from_one_ip_in_an_hour_is_limited():
    client = APIClient()
    for i in range(20):
        assert request_code(client, phone=f"98765{i:05d}").status_code == 202

    response = request_code(client, phone="9123456789")

    assert response.status_code == 429


def test_limits_are_not_shared_between_phones():
    client = APIClient()
    for _ in range(3):
        request_code(client)

    assert request_code(client, phone="9123456789").status_code == 202


# --- CSRF and sessions -----------------------------------------------------


def test_otp_endpoints_require_csrf_token(outbox):
    client = APIClient(enforce_csrf_checks=True)

    assert request_code(client).status_code == 403
    assert verify(client, "123456").status_code == 403

    assert client.get("/api/auth/csrf").status_code == 204
    token = client.cookies["csrftoken"].value
    assert request_code(client, HTTP_X_CSRFTOKEN=token).status_code == 202
    response = verify(client, last_code(outbox), HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200


def test_logout_requires_csrf_and_ends_the_session(outbox):
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/auth/csrf")
    token = client.cookies["csrftoken"].value
    request_code(client, HTTP_X_CSRFTOKEN=token)
    verify(client, last_code(outbox), HTTP_X_CSRFTOKEN=token)

    assert client.post("/api/auth/logout").status_code == 403

    # Django rotates the CSRF token at login.
    token = client.cookies["csrftoken"].value
    assert client.post("/api/auth/logout", HTTP_X_CSRFTOKEN=token).status_code == 204
    assert client.get("/api/auth/me").status_code == 403


def test_me_requires_login():
    assert APIClient().get("/api/auth/me").status_code == 403


# --- Fixed dev code and providers ------------------------------------------


def test_fixed_code_is_used_with_console_provider(settings, outbox):
    settings.OTP_PROVIDER = "console"
    settings.DEV_FIXED_OTP = "123456"

    request_code(APIClient())

    assert last_code(outbox) == "123456"


def test_fixed_code_is_ignored_with_other_providers(settings, outbox, monkeypatch):
    settings.OTP_PROVIDER = "msg91"
    settings.DEV_FIXED_OTP = "123456"
    monkeypatch.setattr(otp, "get_random_string", lambda *args, **kwargs: "654321")

    request_code(APIClient())

    assert last_code(outbox) == "654321"


def test_badly_formatted_fixed_code_is_a_configuration_error(settings):
    settings.OTP_PROVIDER = "console"
    settings.DEV_FIXED_OTP = "12"

    with pytest.raises(ImproperlyConfigured):
        otp.request_otp(PHONE, IP)


def test_unknown_provider_is_a_configuration_error(settings):
    settings.OTP_PROVIDER = "nope"

    with pytest.raises(ImproperlyConfigured):
        otp_providers.get_provider()


def test_console_provider_logs_masked_phone_and_code(monkeypatch, settings, caplog):
    settings.OTP_PROVIDER = "console"
    settings.DEV_FIXED_OTP = "123456"
    monkeypatch.setattr(otp, "get_provider", otp_providers.get_provider)
    caplog.set_level(logging.INFO, logger="accounts.otp")

    request_code(APIClient())

    assert "+91XXXXXX3210" in caplog.text
    assert "123456" in caplog.text
    assert DIGITS not in caplog.text


# --- Privacy ---------------------------------------------------------------


def test_full_phone_never_appears_in_logs(outbox, caplog):
    caplog.set_level(logging.DEBUG)
    client = APIClient()
    request_code(client)
    verify(client, wrong_code(last_code(outbox)))
    for _ in range(3):
        request_code(client)

    assert DIGITS not in caplog.text


def test_challenge_stores_only_hashes(outbox):
    request_code(APIClient())
    code = last_code(outbox)

    row = OtpChallenge.objects.values().get()
    stored = " ".join(str(value) for value in row.values())

    assert DIGITS not in stored
    assert code not in stored
    assert IP not in stored


def test_old_challenges_are_purged():
    request_code(APIClient())
    OtpChallenge.objects.update(created_at=timezone.now() - timedelta(days=2))
    request_code(APIClient(), phone="9123456789")

    call_command("purge_otp_challenges")

    assert OtpChallenge.objects.count() == 1


# --- Client IP -------------------------------------------------------------


def test_client_ip_uses_the_address_added_by_the_proxy():
    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.9", REMOTE_ADDR="172.18.0.5"
    )

    assert client_ip(request) == "203.0.113.9"


def test_client_ip_falls_back_to_remote_addr():
    request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.5")

    assert client_ip(request) == "172.18.0.5"
