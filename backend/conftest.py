"""Test-session setup shared by every app."""

from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connection

# Files written during a test run go under their own prefix in the bucket and
# are deleted when the run ends, so tests never leave files behind in MinIO.
TEST_STORAGE_PREFIX = f"test-runs/{uuid4().hex}"


def pytest_configure(config):
    settings.STORAGES["default"]["OPTIONS"]["location"] = TEST_STORAGE_PREFIX


def pytest_sessionfinish(session, exitstatus):
    from django.core.files.storage import default_storage

    default_storage.bucket.objects.filter(Prefix=f"{TEST_STORAGE_PREFIX}/").delete()


@pytest.fixture
def count_queries():
    """Return a function that runs a callable and counts the SQL queries it executes.

    Hooks query execution directly, so it isn't affected by Django resetting its
    query log at the start of each test-client request.
    """

    def run(fn):
        executed = []

        def record(execute, sql, params, many, context):
            executed.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record):
            fn()
        return len(executed)

    return run
