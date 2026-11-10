import pytest
from django.test import Client

from accounts.models import User
from audit.models import AppendOnlyError, AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def log():
    return AuditLog.objects.create(
        entity_type="listing", entity_id=1, from_status="draft", to_status="open"
    )


def test_audit_rows_cannot_be_changed(log):
    log.reason = "rewritten"

    with pytest.raises(AppendOnlyError):
        log.save()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.filter(pk=log.pk).update(reason="rewritten")

    assert AuditLog.objects.get(pk=log.pk).reason == ""


def test_audit_rows_cannot_be_deleted(log):
    with pytest.raises(AppendOnlyError):
        log.delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().delete()

    assert AuditLog.objects.count() == 1


def test_admin_shows_audit_rows_read_only(log):
    client = Client()
    client.force_login(User.objects.create_superuser("+919000000001"))

    assert client.get("/admin/audit/auditlog/").status_code == 200
    assert client.get(f"/admin/audit/auditlog/{log.pk}/change/").status_code == 200
    assert client.get(f"/admin/audit/auditlog/{log.pk}/delete/").status_code == 403
    assert client.get("/admin/audit/auditlog/add/").status_code == 403

    client.post(f"/admin/audit/auditlog/{log.pk}/change/", {"reason": "edited"})
    assert AuditLog.objects.get(pk=log.pk).reason == ""
