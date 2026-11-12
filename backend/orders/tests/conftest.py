from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from offers.tests.conftest import categories  # noqa: F401  (shared fixture)

IST = ZoneInfo("Asia/Kolkata")
# A fixed "now" for time-sensitive tests: Wednesday 11 Nov 2026, 09:00 IST.
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=IST)


@pytest.fixture
def clock(monkeypatch):
    """Controls timezone.now() for the test. Starts at NOW; set clock.now to move it."""

    class Clock:
        now = NOW

    monkeypatch.setattr(timezone, "now", lambda: Clock.now)
    return Clock
