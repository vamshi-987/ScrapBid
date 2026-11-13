import io
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image

from accounts.models import Flag, FlagStatus, Role, Suspension, User
from accounts.phone import mask_phone
from dealers.models import DealerProfile, DealerStatus, DocumentType, IdType
from dealers.services import can_authorise_ewaste, review_dealer, store_document
from listings.models import Category, Listing, ListingStatus, PricingUnit
from listings.photos import add_photo
from listings.services import cancel_listing, create_listing, expiry_cutoff, publish_listing
from listings.transitions import transition
from offers.models import OfferStatus
from offers.services import create_offer
from orders.disputes import open_dispute
from orders.endings import cancel_order, record_no_shows
from orders.models import Due, DueStatus, NoShowStrike, Order, StrikeStatus
from orders.payments import add_suspension_fee
from orders.scheduling import IST, confirm_slot, propose_slot
from orders.services import accept_offer
from orders.weighing import record_weighing

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
    {
        "phone": "+919000000009",
        "roles": [Role.DEALER],
        "is_staff": False,
        "label": "Dealer who missed a pickup",
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
        "phone": "+919000000009",
        "business_name": "Secunderabad Iron Traders",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4410,
        "longitude": 78.5000,
        "service_radius_km": 12,
        "id_type": IdType.PAN,
        "id_number_last4": "567K",
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

# Demo listings, keyed by seller phone + a marker in the address. Items are
# (category name, weight in kg or None, quantity or None).
DEMO_LISTINGS = [
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 1: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Paper", "25", None), ("Cardboard", "12.5", None)],
        "photos": 2,
        "status": ListingStatus.OPEN,
    },
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 2: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Iron", "40", None)],
        "photos": 0,  # add a photo in the app, then publish
        "status": ListingStatus.DRAFT,
    },
    {
        "phone": "+919000000003",
        "address_line": "Demo listing 3: 1-7-80, Secunderabad Market Road",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4405,
        "longitude": 78.4990,
        "items": [("E-waste", "8", None), ("Appliance", None, 2)],
        "photos": 1,
        "status": ListingStatus.OPEN,
    },
    {
        "phone": "+919000000004",
        "address_line": "Demo listing 4: Dual Role Traders yard, Ameerpet",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4380,
        "longitude": 78.4490,
        "items": [("Copper", "350.5", None)],
        "photos": 1,
        "status": ListingStatus.OPEN,
    },
    {
        "phone": "+919000000003",
        "address_line": "Demo listing 5: 1-7-80, Secunderabad Market Road",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4405,
        "longitude": 78.4990,
        "items": [("Plastic", "15", None)],
        "photos": 1,
        "status": ListingStatus.EXPIRED,  # relist it in the app
    },
    {
        "phone": "+919000000003",
        "address_line": "Demo listing 6: 1-7-80, Secunderabad Market Road",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4405,
        "longitude": 78.4990,
        "items": [("Aluminium", "30", None)],
        "photos": 1,
        "status": ListingStatus.OFFER_ACCEPTED,  # offers below; one accepted
    },
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 7: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Cardboard", "40", None)],
        "photos": 1,
        "status": ListingStatus.PICKUP_SCHEDULED,  # accepted, slot confirmed for tomorrow
    },
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 8: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Plastic", "10", None)],
        "photos": 1,
        "status": ListingStatus.CANCELLED,  # dealer cancelled, then the seller withdrew it
    },
    {
        "phone": "+919000000003",
        "address_line": "Demo listing 9: 6-3-45, Punjagutta Road",
        "locality": "Punjagutta",
        "pincode": "500082",
        "latitude": 17.4260,
        "longitude": 78.4510,
        "items": [("Paper", "25", None)],
        "photos": 1,
        "status": ListingStatus.PICKED_UP,  # weighed by the dealer, waiting for the seller
    },
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 10: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Iron", "30", None)],
        "photos": 1,
        "status": ListingStatus.DISPUTED,  # weighed, then disputed by the seller
    },
    {
        "phone": "+919000000002",
        "address_line": "Demo listing 11: 8-2-120, Ameerpet Main Road",
        "locality": "Ameerpet",
        "pincode": "500016",
        "latitude": 17.4370,
        "longitude": 78.4475,
        "items": [("Cardboard", "20", None)],
        "photos": 1,
        "status": ListingStatus.CANCELLED,  # weighed short and denied by the dealer
    },
    {
        "phone": "+919000000003",
        "address_line": "Demo listing 12: 1-7-80, Secunderabad Market Road",
        "locality": "Secunderabad",
        "pincode": "500003",
        "latitude": 17.4405,
        "longitude": 78.4990,
        "items": [("Iron", "30", None)],
        "photos": 1,
        "status": ListingStatus.OPEN,  # reopened after the dealer missed the pickup
    },
]

# Offers on the open demo listings: (listing marker, dealer phone, {category: paise}).
# Rates are per kg, or per item for appliances. E-waste only from the authorised dealer.
DEMO_OFFERS = [
    ("Demo listing 1:", "+919000000006", {"Paper": 1400, "Cardboard": 1000}),
    ("Demo listing 1:", "+919000000007", {"Paper": 1500, "Cardboard": 900}),
    ("Demo listing 1:", "+919000000004", {"Paper": 1300, "Cardboard": 1100}),
    ("Demo listing 3:", "+919000000006", {"E-waste": 4000, "Appliance": 150000}),
    ("Demo listing 4:", "+919000000006", {"Copper": 72000}),
    ("Demo listing 4:", "+919000000007", {"Copper": 71000}),
    ("Demo listing 6:", "+919000000006", {"Aluminium": 11000}),
    ("Demo listing 6:", "+919000000007", {"Aluminium": 11500}),
    ("Demo listing 7:", "+919000000006", {"Cardboard": 1000}),
    ("Demo listing 8:", "+919000000007", {"Plastic": 1200}),
    ("Demo listing 8:", "+919000000006", {"Plastic": 1100}),
    ("Demo listing 9:", "+919000000007", {"Paper": 1500}),
    ("Demo listing 10:", "+919000000006", {"Iron": 3200}),
    ("Demo listing 11:", "+919000000006", {"Cardboard": 1000}),
]

# Offers the seller has accepted: (listing marker, dealer phone).
DEMO_ACCEPTED = [
    ("Demo listing 6:", "+919000000007"),
    ("Demo listing 7:", "+919000000006"),
    ("Demo listing 8:", "+919000000007"),
    ("Demo listing 9:", "+919000000007"),
    ("Demo listing 10:", "+919000000006"),
    ("Demo listing 11:", "+919000000006"),
]

# Cancel history: (listing marker, dealer's reason, then the seller's reason).
DEMO_CANCELLATIONS = [("Demo listing 8:", "Truck broke down", "Sold to a neighbour")]

# Accepted listings with a pickup slot the dealer proposed and the seller confirmed:
# (listing marker, days from today, start hour IST).
DEMO_SCHEDULED = [
    ("Demo listing 7:", 1, 10),
    ("Demo listing 9:", 1, 12),
    ("Demo listing 10:", 1, 14),
    ("Demo listing 11:", 1, 16),
]

# Scheduled pickups the dealer has weighed: (listing marker, {category: verified kg}).
# Values are (verified, choice); a short weight beyond the tolerance needs a choice.
DEMO_WEIGHED = [
    ("Demo listing 9:", {"Paper": ("26", None)}),
    ("Demo listing 10:", {"Iron": ("29", None)}),
    ("Demo listing 11:", {"Cardboard": ("15", "deny")}),
]

# A confirmed pickup the dealer missed, recorded by the real no-show job: the dealer gets
# a strike and a penalty, and the listing reopens with the other offer selectable again.
# The missing dealer has no other demo offers: a blocked dealer can't make new ones.
# (listing marker, missing dealer, other dealer, {category: paise}).
DEMO_NO_SHOW = ("Demo listing 12:", "+919000000009", "+919000000006", {"Iron": 3000})

# Weighed pickups the seller disputed: (listing marker, seller's reason).
DEMO_DISPUTES = [("Demo listing 10:", "My own scale showed 34 kg")]

PHOTO_COLOURS = ["#8d6e63", "#78909c", "#a1887f"]

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

            for spec in DEMO_LISTINGS:
                self._seed_listing(spec)
            self.stdout.write(f"Listings: {len(DEMO_LISTINGS)}")

            for marker, phone, rates in DEMO_OFFERS:
                self._seed_offer(marker, phone, rates)
            self.stdout.write(f"Offers: {len(DEMO_OFFERS)}")

            for marker, phone in DEMO_ACCEPTED:
                self._seed_acceptance(marker, phone)
            self.stdout.write(f"Accepted offers: {len(DEMO_ACCEPTED)}")

            for marker, days, hour in DEMO_SCHEDULED:
                self._seed_schedule(marker, days, hour)
            self.stdout.write(f"Scheduled pickups: {len(DEMO_SCHEDULED)}")

            for marker, verified in DEMO_WEIGHED:
                self._seed_weighing(marker, verified)
            self.stdout.write(f"Weighed pickups: {len(DEMO_WEIGHED)}")

            for marker, reason in DEMO_DISPUTES:
                listing = Listing.objects.get(address_line__startswith=marker)
                if listing.status == ListingStatus.PICKED_UP:
                    open_dispute(listing.orders.get(status="active"), listing.seller, reason)
            self.stdout.write(f"Disputes: {len(DEMO_DISPUTES)}")

            self._seed_flag_and_suspension()
            self._seed_no_show(*DEMO_NO_SHOW)
            self.stdout.write("Missed pickups: 1")

            for marker, dealer_reason, seller_reason in DEMO_CANCELLATIONS:
                self._seed_cancellation(marker, dealer_reason, seller_reason)
            self.stdout.write(f"Cancellation histories: {len(DEMO_CANCELLATIONS)}")

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

    def _seed_listing(self, spec):
        """Keep a demo listing that is already in its demo state; otherwise rebuild it."""
        seller = User.objects.get(phone=spec["phone"])
        existing = Listing.objects.filter(seller=seller, address_line=spec["address_line"]).first()
        if existing:
            overdue = (
                existing.status == ListingStatus.OPEN and existing.published_at <= expiry_cutoff()
            )
            slot_passed = existing.orders.filter(
                status="active", slot_end__lte=timezone.now()
            ).exists()
            if (
                existing.status == spec["status"]
                and existing.photos.count() == spec["photos"]
                and not overdue
                and not slot_passed
            ):
                return
            self._delete_listing(existing)

        categories = {c.name: c for c in Category.objects.all()}
        listing = create_listing(
            seller,
            {
                **{
                    k: spec[k]
                    for k in ("address_line", "locality", "pincode", "latitude", "longitude")
                },
                "items": [
                    {
                        "category": categories[name],
                        "declared_weight_kg": Decimal(weight) if weight else None,
                        "declared_quantity": quantity,
                    }
                    for name, weight, quantity in spec["items"]
                ],
            },
        )
        for index in range(spec["photos"]):
            buffer = io.BytesIO()
            colour = PHOTO_COLOURS[index % len(PHOTO_COLOURS)]
            Image.new("RGB", (800, 600), colour).save(buffer, format="JPEG")
            add_photo(listing, ContentFile(buffer.getvalue(), name="demo.jpg"))
        if spec["status"] in (
            ListingStatus.OPEN,
            ListingStatus.EXPIRED,
            ListingStatus.OFFER_ACCEPTED,
            ListingStatus.PICKUP_SCHEDULED,
            ListingStatus.PICKED_UP,
            ListingStatus.DISPUTED,
            ListingStatus.CANCELLED,
        ):
            publish_listing(listing, seller)
        if spec["status"] == ListingStatus.EXPIRED:
            # Published long enough ago to have expired, then expired by the system.
            published = timezone.now() - timedelta(days=settings.LISTING_EXPIRY_DAYS + 1)
            Listing.objects.filter(pk=listing.pk).update(published_at=published)
            transition(listing, ListingStatus.EXPIRED, None)

    def _delete_listing(self, listing):
        """Remove a demo listing and everything hanging off it, files included."""
        for photo in listing.photos.all():
            default_storage.delete(photo.file.name)
        for order in listing.orders.exclude(scale_photo=""):
            default_storage.delete(order.scale_photo.name)
        # A missed pickup (e.g. the scheduler ran after a seeded slot passed) leaves a
        # strike and its penalty on the order; they go with it.
        Due.objects.filter(strike__order__listing=listing).delete()
        NoShowStrike.objects.filter(order__listing=listing).delete()
        listing.orders.all().delete()
        listing.offers.all().delete()
        listing.delete()

    def _seed_no_show(self, marker, missing_phone, other_phone, rates):
        """Offers from two dealers; the first is accepted and confirmed for a slot that has
        already passed, and the no-show job records it. Rebuilt once the strike has been
        challenged, paid or resolved, can no longer be challenged, or is someone else's."""
        listing = Listing.objects.get(address_line__startswith=marker)
        strike = NoShowStrike.objects.filter(order__listing=listing).first()
        if strike:
            challenge_open = strike.created_at > timezone.now() - timedelta(
                hours=settings.STRIKE_CHALLENGE_HOURS
            )
            unpaid = Due.objects.filter(strike=strike, status=DueStatus.UNPAID).exists()
            unused = strike.status == StrikeStatus.ACTIVE and not strike.challenged_at and unpaid
            if unused and challenge_open and strike.user.phone == missing_phone:
                return
            spec = next(s for s in DEMO_LISTINGS if s["address_line"].startswith(marker))
            self._delete_listing(listing)
            self._seed_listing(spec)
            listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.OPEN:
            return
        categories = {c.name: c for c in Category.objects.all()}
        offers = {}
        for phone in (missing_phone, other_phone):
            dealer = DealerProfile.objects.get(user__phone=phone)
            offers[phone] = create_offer(
                dealer, listing.pk, {categories[name].pk: paise for name, paise in rates.items()}
            )
        order, _ = accept_offer(listing.seller, offers[missing_phone])
        start = datetime.combine(timezone.localdate() + timedelta(days=1), time(10), tzinfo=IST)
        propose_slot(order, order.dealer.user, start)
        confirm_slot(order, listing.seller)
        # The confirmed slot ended an hour ago (past the grace period), with no check-in.
        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(
            slot_start=now - timedelta(hours=3), slot_end=now - timedelta(hours=1)
        )
        record_no_shows()

    def _seed_offer(self, marker, phone, rates):
        """Keep a live demo offer; otherwise make a fresh one through the offer rules."""
        listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.OPEN:
            return  # already accepted (or closed): its offers are settled
        dealer = DealerProfile.objects.get(user__phone=phone)
        current = listing.offers.filter(dealer=dealer, status=OfferStatus.ACTIVE).first()
        if current and current.is_live:
            return
        categories = {item.category.name: item.category_id for item in listing.items.all()}
        create_offer(dealer, listing.pk, {categories[name]: paise for name, paise in rates.items()})

    def _seed_acceptance(self, marker, phone):
        """Accept the dealer's offer through the real accept rules (skipped if done)."""
        listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.OPEN:
            return
        offer = listing.offers.get(dealer__user__phone=phone, status=OfferStatus.ACTIVE)
        accept_offer(listing.seller, offer)

    def _seed_schedule(self, marker, days, hour):
        """Dealer proposes a slot and the seller confirms it, through the real rules."""
        listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.OFFER_ACCEPTED:
            return
        order = listing.orders.get(status="active")
        day = timezone.localdate() + timedelta(days=days)
        start = datetime.combine(day, time(hour), tzinfo=IST)
        propose_slot(order, order.dealer.user, start)
        confirm_slot(order, listing.seller)

    def _seed_flag_and_suspension(self):
        """A demo rate flag and a demo suspension, created directly: the seed has too few
        dealers in one city to raise a flag from offers, and suspending an active demo
        dealer would block the other demo flows."""
        dealer = User.objects.get(phone="+919000000007")
        paper = Category.objects.get(name="Paper")
        Flag.objects.get_or_create(
            user=dealer,
            kind=Flag.RATE_OUTLIER,
            category=paper,
            status=FlagStatus.OPEN,
            defaults={
                "details": {
                    "rate_paise": 1800,
                    "peer_average_paise": 1400,
                    "peers": 3,
                    "above_percent": "28.6",
                    "demo": True,
                }
            },
        )
        far_dealer = User.objects.get(phone="+919000000008")
        suspension = Suspension.objects.filter(user=far_dealer, lifted_at=None).first()
        if suspension is None:
            suspension = Suspension.objects.create(
                user=far_dealer, reason="Demo: 3 upheld disputes", fee_paise=50000
            )
        if not Due.objects.filter(suspension=suspension).exists():
            add_suspension_fee(suspension)

    def _seed_weighing(self, marker, verified):
        """The dealer weighs at the pickup point, through the real weighing rules."""
        listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.PICKUP_SCHEDULED:
            return
        order = listing.orders.get(status="active")
        buffer = io.BytesIO()
        Image.new("RGB", (800, 600), "#eceff1").save(buffer, format="JPEG")
        record_weighing(
            order,
            order.dealer.user,
            latitude=listing.location.y,
            longitude=listing.location.x,
            photo=ContentFile(buffer.getvalue(), name="scale.jpg"),
            lines=[
                {
                    "category": item.category_id,
                    "verified": verified[item.category.name][0],
                    "choice": verified[item.category.name][1],
                }
                for item in order.items.select_related("category")
            ],
        )

    def _seed_cancellation(self, marker, dealer_reason, seller_reason):
        """The accepted dealer cancels (the listing reopens), then the seller withdraws it."""
        listing = Listing.objects.get(address_line__startswith=marker)
        if listing.status != ListingStatus.OFFER_ACCEPTED:
            return
        order = listing.orders.get(status="active")
        cancel_order(order, order.dealer.user, dealer_reason)
        cancel_listing(listing, listing.seller, seller_reason)
