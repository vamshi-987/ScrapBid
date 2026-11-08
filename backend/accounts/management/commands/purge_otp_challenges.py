from django.core.management.base import BaseCommand

from accounts.otp import purge_old_challenges


class Command(BaseCommand):
    help = "Delete login code records older than one day."

    def handle(self, *args, **options):
        deleted = purge_old_challenges()
        self.stdout.write(f"Deleted {deleted} old login code records.")
