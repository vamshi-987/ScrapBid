from django.core.management.base import BaseCommand

from listings.services import expire_overdue_listings


class Command(BaseCommand):
    help = "Expire open listings that have had no accepted offer for LISTING_EXPIRY_DAYS."

    def handle(self, *args, **options):
        expired = expire_overdue_listings()
        self.stdout.write(f"Expired {expired} listing(s).")
