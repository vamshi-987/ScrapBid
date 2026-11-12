from django.core.management.base import BaseCommand

from orders.misuse import flag_rate_outliers


class Command(BaseCommand):
    help = "Flag dealers whose rates are far above their peers' (for admin review)."

    def handle(self, *args, **options):
        self.stdout.write(f"Rate flags raised: {flag_rate_outliers()}")
