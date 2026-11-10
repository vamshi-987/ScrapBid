from datetime import timedelta

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from accounts.phone import mask_phone
from dealers.models import DealerProfile, DealerStatus, DocumentType, IdType
from dealers.services import can_authorise_ewaste, review_dealer, store_document
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
    {
        "phone": "+919000000005",
        "roles": [Role.DEALER],
        "is_staff": False,
        "label": "Pending dealer",
    },
    {
        "phone": "+919000000006",
        "roles": [Role.DEALER],
        "is_staff": False,
        "label": "Dealer (e-waste authorised)",
    },
    {"phone": "+919000000007", "roles": [Role.DEALER], "is_staff": False, "label": "Dealer"},
    {
        "phone": "+919000000008",
        "roles": [Role.DEALER],
        "is_staff": False,
        "label": "Dealer out of range (Warangal)",
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

# Dealer profiles. All in Hyderabad except one in Warangal, ~140 km away, out of range.
# Keyed by the owner's phone. Approved ones go through the same review as an admin's.
DEMO_DEALERS = [
    {
        "phone": "+919000000004",
        "business_name": "Ameerpet Dual Role Traders",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4375,
        "longitude": 78.4482,
        "service_radius_km": 8,
        "id_type": IdType.AADHAAR,
        "id_number_last4": "0123",
        "status": DealerStatus.APPROVED,
    },
    {
        "phone": "+919000000005",
        "business_name": "Kukatpally Scrap Collectors",
        "locality": "Kukatpally",
        "pincode": "500072",
        "latitude": 17.4948,
        "longitude": 78.3996,
        "service_radius_km": 10,
        "id_type": IdType.PAN,
        "id_number_last4": "234F",
        "status": DealerStatus.PENDING,
    },
    {
        "phone": "+919000000006",
        "business_name": "Begumpet Metals and E-waste",
        "locality": "Begumpet",
        "pincode": "500016",
        "latitude": 17.4447,
        "longitude": 78.4664,
        "service_radius_km": 10,
        "id_type": IdType.VOTER_ID,
        "id_number_last4": "4567",
        "status": DealerStatus.APPROVED,
        "ewaste_authorised": True,
    },
    {
        "phone": "+919000000007",
        "business_name": "Secunderabad Scrap Mart",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4399,
        "longitude": 78.4983,
        "service_radius_km": 12,
        "id_type": IdType.DRIVING_LICENCE,
        "id_number_last4": "2345",
        "status": DealerStatus.APPROVED,
    },
    {
        "phone": "+919000000008",
        "business_name": "Warangal Recyclers",
        "locality": "Hanamkonda",
        "pincode": "506001",
        "latitude": 17.9689,
        "longitude": 79.5941,
        "service_radius_km": 15,
        "id_type": IdType.PAN,
        "id_number_last4": "678K",
        "status": DealerStatus.APPROVED,
    },
]

# A tiny valid PDF used as every demo verification document.
DEMO_PDF = b"%PDF-1.4\n% ScrapBid demo document\n%%EOF\n"
DEMO_DOCUMENT_TYPES = [
    DocumentType.TRADE_LICENCE,
    DocumentType.ID_PROOF,
    DocumentType.SCALE_CERTIFICATE,
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

            for spec in DEMO_DEALERS:
                self._seed_dealer(spec)
            self.stdout.write(f"Dealer profiles: {len(DEMO_DEALERS)}")

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))

    def _seed_dealer(self, spec):
        """Restore a dealer to its demo state: reset to pending with documents, then review."""
        target = spec["status"]
        ewaste = spec.get("ewaste_authorised", False)
        skip = ("phone", "latitude", "longitude", "status", "ewaste_authorised")
        fields = {k: v for k, v in spec.items() if k not in skip}
        profile, _ = DealerProfile.objects.update_or_create(
            user=User.objects.get(phone=spec["phone"]),
            defaults={
                **fields,
                "location": Point(spec["longitude"], spec["latitude"], srid=4326),
                "scale_certificate_expires_on": timezone.localdate() + timedelta(days=365),
                "status": DealerStatus.PENDING,
                "status_reason": "",
                "reviewed_by": None,
                "reviewed_at": None,
                "ewaste_authorised": False,
            },
        )
        doc_types = [*DEMO_DOCUMENT_TYPES]
        if ewaste:
            doc_types.append(DocumentType.EWASTE_AUTHORISATION)
        uploaded = set(profile.documents.values_list("doc_type", flat=True))
        for doc_type in doc_types:
            if doc_type not in uploaded:
                store_document(profile, doc_type, ContentFile(DEMO_PDF, name="demo.pdf"))
        if target == DealerStatus.APPROVED:
            admin_user = User.objects.get(phone=DEMO_USERS[0]["phone"])
            review_dealer(profile, DealerStatus.APPROVED, admin_user)
        if ewaste and can_authorise_ewaste(profile):
            DealerProfile.objects.filter(pk=profile.pk).update(ewaste_authorised=True)
