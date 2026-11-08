from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User
from accounts.phone import mask_phone

# Demo accounts for local testing. Listed in README.md; log in with DEV_FIXED_OTP.
DEMO_USERS = [
    {"phone": "+919000000001", "roles": [Role.SELLER], "is_staff": True, "label": "Admin"},
    {"phone": "+919000000002", "roles": [Role.SELLER], "is_staff": False, "label": "Seller 1"},
    {"phone": "+919000000003", "roles": [Role.SELLER], "is_staff": False, "label": "Seller 2"},
    {
        "phone": "+919000000004",
        "roles": [Role.SELLER, Role.DEALER],
        "is_staff": False,
        "label": "Dealer who also sells",
    },
]


class Command(BaseCommand):
    help = "Create or update demo data for local testing. Safe to run repeatedly."

    def handle(self, *args, **options):
        if settings.DJANGO_ENV == "production":
            raise CommandError("seed_demo refuses to run in production.")

        with transaction.atomic():
            for spec in DEMO_USERS:
                user = User.objects.filter(phone=spec["phone"]).first()
                if user is None:
                    user = User.objects.create_user(spec["phone"])
                user.roles = spec["roles"]
                user.is_staff = spec["is_staff"]
                user.is_superuser = spec["is_staff"]
                user.is_active = True
                user.save()
                self.stdout.write(f"{spec['label']}: {mask_phone(spec['phone'])}")

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
