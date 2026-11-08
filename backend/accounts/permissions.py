"""Shared DRF permissions for role and ownership checks."""

from rest_framework.permissions import BasePermission


def HasRole(role):
    """Permission class allowing only logged-in users who hold `role`."""

    class _HasRole(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.has_role(role))

    _HasRole.__name__ = f"HasRole_{role}"
    return _HasRole


class IsOwner(BasePermission):
    """Object permission: the object's owner field must be the requesting user.

    Views set `owner_field` to the name of the foreign key to the user.
    """

    def has_object_permission(self, request, view, obj):
        owner_id = getattr(obj, f"{view.owner_field}_id")
        return owner_id is not None and owner_id == request.user.id
