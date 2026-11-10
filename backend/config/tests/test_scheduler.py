import django.core.management
import pytest

import scheduler


# run_jobs closes stale DB connections after each job, which touches the database.
@pytest.mark.django_db
def test_failing_job_does_not_stop_later_jobs(monkeypatch):
    calls = []

    def fake_call_command(name):
        calls.append(name)
        if name == "broken":
            raise RuntimeError("job crashed")

    monkeypatch.setattr(django.core.management, "call_command", fake_call_command)

    failed = scheduler.run_jobs(["broken", "healthy"])

    assert calls == ["broken", "healthy"]
    assert failed == ["broken"]


@pytest.mark.django_db
def test_runs_a_real_management_command():
    assert scheduler.run_jobs(["check"]) == []


@pytest.mark.django_db
def test_unknown_command_is_reported_as_failed():
    assert scheduler.run_jobs(["no_such_command"]) == ["no_such_command"]


def test_interval_defaults_to_five_minutes(monkeypatch):
    monkeypatch.delenv("SCHEDULER_INTERVAL_SECONDS", raising=False)

    assert scheduler.interval_seconds() == 300


@pytest.mark.parametrize("value", ["0", "-5"])
def test_interval_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("SCHEDULER_INTERVAL_SECONDS", value)

    with pytest.raises(ValueError):
        scheduler.interval_seconds()
