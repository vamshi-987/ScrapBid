import pytest
from django.core.management import call_command
from django.db import connection


@pytest.mark.django_db
def test_postgis_extension_is_installed():
    with connection.cursor() as cursor:
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
        row = cursor.fetchone()

    assert row is not None


@pytest.mark.django_db
def test_no_missing_migrations():
    # Exits with an error if a model change has no migration.
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
