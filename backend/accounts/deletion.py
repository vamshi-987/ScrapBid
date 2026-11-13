"""Account deletion: personal data is anonymised; orders, amounts and audit rows stay."""

from django.contrib.gis.geos import Point
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts import otp
from accounts.models import User
from audit.models import AuditLog
from dealers.models import DealerDocument, DealerProfile
from listings.models import Listing, ListingPhoto, ListingStatus
from listings.transitions import transition
from notifications.models import Notification
from offers.models import Offer, OfferStatus
from orders.models import Due, DueStatus, Order, OrderStatus

# Stands in for removed coordinates (the field is required). Far from any service area.
NO_LOCATION = Point(0, 0, srid=4326)
REASON = "Account deleted"


class DeletionError(Exception):
    messages = {
        "active_order": "Finish or cancel your open pickups before deleting your account.",
        "unpaid_dues": "Pay your outstanding penalties and fees before deleting your account.",
    }

    def __init__(self, code):
        super().__init__(self.messages[code])
        self.code = code


def delete_account(user):
    """Anonymise the user's personal data and deactivate the account.

    Refused while an order is open (Q30) or a due is unpaid (Q40). Draft and open
    listings are cancelled and live offers withdrawn (Q41). The phone is freed, so
    the same number can register again as a new account.
    """
    files = []
    with transaction.atomic():
        profile = DealerProfile.objects.filter(user=user).first()
        # Lock what an accept could race on before checking for open orders. Same order
        # as accepting (listing, then offer, then the user row via the new order's
        # foreign key), so the two can't deadlock.
        open_listings = list(
            Listing.objects.select_for_update()
            .filter(seller=user, status__in=[ListingStatus.DRAFT, ListingStatus.OPEN])
            .order_by("pk")
        )
        offers = Offer.objects.filter(dealer=profile) if profile else Offer.objects.none()
        list(offers.select_for_update().filter(status=OfferStatus.ACTIVE).order_by("pk"))
        user = User.objects.select_for_update().get(pk=user.pk)

        orders = Order.objects.filter(Q(seller=user) | Q(dealer__user=user))
        if orders.filter(status=OrderStatus.ACTIVE).exists():
            raise DeletionError("active_order")
        if Due.objects.filter(user=user, status=DueStatus.UNPAID).exists():
            raise DeletionError("unpaid_dues")

        now = timezone.now()
        for listing in open_listings:
            transition(listing, ListingStatus.CANCELLED, user, reason=REASON)
            listing.offers.filter(status=OfferStatus.ACTIVE).update(
                status=OfferStatus.REJECTED, rejection_reason="listing_cancelled", updated_at=now
            )
        offers.filter(status=OfferStatus.ACTIVE).update(
            status=OfferStatus.WITHDRAWN, updated_at=now
        )
        # Offers that lost to another dealer come back if that dealer cancels: not these.
        offers.filter(status=OfferStatus.REJECTED, rejection_reason="other_accepted").update(
            rejection_reason="account_deleted", updated_at=now
        )

        photos = ListingPhoto.objects.filter(listing__seller=user)
        files += [name for name in photos.values_list("file", flat=True)]
        photos.delete()
        Listing.objects.filter(seller=user).update(
            address_line="", locality="", pincode="", location=NO_LOCATION, updated_at=now
        )
        # GPS points at pickups locate the seller's address and the dealer.
        orders.update(dealer_check_in_location=None, weighing_location=None)

        if profile:
            documents = DealerDocument.objects.filter(dealer=profile)
            files += [name for name in documents.values_list("file", flat=True)]
            documents.delete()
            DealerProfile.objects.filter(pk=profile.pk).update(
                business_name="Deleted dealer",
                locality="",
                pincode="",
                location=NO_LOCATION,
                id_type="",
                id_number_last4="",
                updated_at=now,
            )

        Notification.objects.filter(user=user).delete()
        otp.forget_phone(user.phone)
        user.phone = f"deleted-{user.pk}"
        user.set_unusable_password()
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        user.deleted_at = now
        user.save()
        user.groups.clear()
        user.user_permissions.clear()
        AuditLog.objects.create(
            entity_type="user",
            entity_id=user.pk,
            actor=user,
            from_status="active",
            to_status="deleted",
            reason=REASON,
        )
        transaction.on_commit(lambda: [default_storage.delete(name) for name in files])
    return user
