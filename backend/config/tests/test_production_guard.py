import os
import subprocess
import sys

import pytest
from django.conf import settings

from config.env_checks import DEFAULT_SECRET_KEY, production_problems

SAFE = {
    "debug": False,
    "secret_key": "a-real-production-secret",
    "otp_provider": "msg91",
    "dev_fixed_otp": "",
}

SAFE_ENV = {
    "DJANGO_ENV": "production",
    "DJANGO_DEBUG": "False",
    "DJANGO_SECRET_KEY": "a-real-production-secret",
    "OTP_PROVIDER": "msg91",
    "DEV_FIXED_OTP": "",
}


def test_safe_production_settings_have_no_problems():
    assert production_problems(**SAFE) == []


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"debug": True}, "DJANGO_DEBUG"),
        ({"secret_key": DEFAULT_SECRET_KEY}, "DJANGO_SECRET_KEY"),
        ({"secret_key": ""}, "DJANGO_SECRET_KEY"),
        ({"otp_provider": "console"}, "OTP_PROVIDER"),
        ({"dev_fixed_otp": "123456"}, "DEV_FIXED_OTP"),
    ],
)
def test_each_unsafe_value_is_reported(override, expected):
    problems = production_problems(**{**SAFE, **override})

    assert len(problems) == 1
    assert expected in problems[0]


def start_django(env_overrides):
    """Load settings in a fresh process, the way the server does at start-up."""
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_production_starts_with_safe_settings():
    result = start_django(SAFE_ENV)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "override",
    [
        {"DJANGO_DEBUG": "True"},
        {"DJANGO_SECRET_KEY": ""},
        {"DJANGO_SECRET_KEY": DEFAULT_SECRET_KEY},
        {"OTP_PROVIDER": "console"},
        {"DEV_FIXED_OTP": "123456"},
    ],
)
def test_production_refuses_to_start_with_unsafe_settings(override):
    result = start_django({**SAFE_ENV, **override})

    assert result.returncode != 0
    assert "Refusing to start in production" in result.stderr
