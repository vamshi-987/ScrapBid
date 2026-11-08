import pytest
from rest_framework.test import APIClient

from config import health

pytestmark = pytest.mark.django_db

URL = "/api/health"


def fail(message="boom"):
    def _raise():
        raise RuntimeError(message)

    return _raise


def test_health_ok_when_database_and_storage_reachable():
    response = APIClient().get(URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "storage": "ok"}}


def test_health_503_when_storage_down(monkeypatch):
    monkeypatch.setattr(health, "check_storage", fail())

    response = APIClient().get(URL)

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"database": "ok", "storage": "error"}}


def test_health_503_when_database_down(monkeypatch):
    monkeypatch.setattr(health, "check_database", fail())

    response = APIClient().get(URL)

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error", "storage": "ok"}


def test_health_does_not_leak_error_details(monkeypatch):
    monkeypatch.setattr(health, "check_storage", fail("secret-bucket-detail"))

    response = APIClient().get(URL)

    assert b"secret-bucket-detail" not in response.content


def test_health_is_read_only():
    response = APIClient().post(URL)

    assert response.status_code == 405
