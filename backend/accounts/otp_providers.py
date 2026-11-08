"""Delivery of login codes. Chosen by the OTP_PROVIDER setting."""

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from accounts.phone import mask_phone

logger = logging.getLogger("accounts.otp")


class ConsoleProvider:
    """Local development only: writes the code to the backend log."""

    name = "console"

    def send(self, phone, code):
        logger.info("Login code for %s: %s", mask_phone(phone), code)


PROVIDERS = {ConsoleProvider.name: ConsoleProvider}


def get_provider():
    try:
        return PROVIDERS[settings.OTP_PROVIDER]()
    except KeyError:
        raise ImproperlyConfigured(f"Unknown OTP_PROVIDER: {settings.OTP_PROVIDER!r}") from None
