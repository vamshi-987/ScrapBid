from rest_framework.permissions import BasePermission

from accounts.models import Role
from dealers.models import DealerProfile, DealerStatus


class IsApprovedDealer(BasePermission):
    """Only logged-in users with the dealer role and an APPROVED dealer profile.

    Every dealer marketplace endpoint (listing feed, offers, pickups) uses this.
    Sets request.dealer_profile for the view.
    """

    message = "Your dealer account isn't approved."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.has_role(Role.DEALER)):
            return False
        profile = DealerProfile.objects.filter(user=user, status=DealerStatus.APPROVED).first()
        request.dealer_profile = profile
        return profile is not None
