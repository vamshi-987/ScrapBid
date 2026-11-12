from django.core.management.base import BaseCommand

from orders.endings import record_no_shows


class Command(BaseCommand):
    help = "Record no-shows for confirmed pickups that ended NO_SHOW_GRACE_MINUTES ago."

    def handle(self, *args, **options):
        recorded = record_no_shows()
        self.stdout.write(f"Recorded {recorded} no-show(s).")
