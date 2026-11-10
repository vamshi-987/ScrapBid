from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User
from accounts.phone import mask_phone
from listings.models import Category, PricingUnit

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


# (name, pricing unit, restricted)
DEMO_CATEGORIES = [
    ("Paper", PricingUnit.PER_KG, False),
    ("Cardboard", PricingUnit.PER_KG, False),
    ("Iron", PricingUnit.PER_KG, False),
    ("Copper", PricingUnit.PER_KG, False),
    ("Aluminium", PricingUnit.PER_KG, False),
    ("Plastic", PricingUnit.PER_KG, False),
    ("E-waste", PricingUnit.PER_KG, True),
    ("Batteries", PricingUnit.PER_KG, True),
    ("Appliance", PricingUnit.PER_ITEM, False),
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

            for name, pricing_unit, restricted in DEMO_CATEGORIES:
                Category.objects.update_or_create(
                    name__iexact=name,
                    defaults={
                        "name": name,
                        "pricing_unit": pricing_unit,
                        "is_restricted": restricted,
                        "is_active": True,
                    },
                )
            self.stdout.write(f"Categories: {len(DEMO_CATEGORIES)}")

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
