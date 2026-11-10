"""Test-session setup shared by every app."""

from uuid import uuid4

from django.conf import settings

# Files written during a test run go under their own prefix in the bucket and
# are deleted when the run ends, so tests never leave files behind in MinIO.
TEST_STORAGE_PREFIX = f"test-runs/{uuid4().hex}"


def pytest_configure(config):
    settings.STORAGES["default"]["OPTIONS"]["location"] = TEST_STORAGE_PREFIX


def pytest_sessionfinish(session, exitstatus):
    from django.core.files.storage import default_storage

    default_storage.bucket.objects.filter(Prefix=f"{TEST_STORAGE_PREFIX}/").delete()
