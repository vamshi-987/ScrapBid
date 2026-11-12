"""Runs scheduled management commands every SCHEDULER_INTERVAL_SECONDS.

Started by the `scheduler` service in Docker Compose. A failing job is logged
and does not stop the other jobs or later runs.
"""

import logging
import os
import time

import django

# Management command names, run in order on every pass.
JOBS: list[str] = ["purge_otp_challenges", "expire_listings", "expire_offers", "record_no_shows"]

logger = logging.getLogger("scheduler")


def run_jobs(jobs):
    """Run each job once. Return the names of the jobs that failed."""
    from django.core.management import call_command
    from django.db import close_old_connections

    failed = []
    for job in jobs:
        try:
            call_command(job)
        except Exception:
            logger.exception("Scheduled job failed: %s", job)
            failed.append(job)
        finally:
            close_old_connections()
    return failed


def migrations_pending():
    """True while the database still has unapplied migrations (e.g. on first start)."""
    from django.db import close_old_connections, connection
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connection)
        return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    finally:
        close_old_connections()


def interval_seconds():
    value = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300"))
    if value <= 0:
        raise ValueError("SCHEDULER_INTERVAL_SECONDS must be a positive number of seconds")
    return value


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    interval = interval_seconds()
    logger.info("Scheduler started: %d jobs every %d seconds", len(JOBS), interval)
    while True:
        if migrations_pending():
            # Jobs need the tables; on a fresh setup, wait for `manage.py migrate`.
            logger.info("Scheduler waiting: database migrations not applied yet")
        else:
            failed = run_jobs(JOBS)
            logger.info("Scheduler run finished: %d jobs, %d failed", len(JOBS), len(failed))
        time.sleep(interval)


if __name__ == "__main__":
    main()
