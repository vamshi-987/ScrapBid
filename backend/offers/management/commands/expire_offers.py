from django.core.management.base import BaseCommand

from offers.services import expire_due_offers


class Command(BaseCommand):
    help = "Mark active offers past their OFFER_EXPIRY_HOURS validity as expired."

    def handle(self, *args, **options):
        expired = expire_due_offers()
        self.stdout.write(f"Expired {expired} offer(s).")
