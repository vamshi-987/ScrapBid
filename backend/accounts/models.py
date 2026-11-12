from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """Marketplace roles. Admin access is Django's is_staff flag, not a role here."""

    SELLER = "seller", "Seller"
    DEALER = "dealer", "Dealer"


def default_roles():
    return [Role.SELLER]


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("A phone number is required")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=16, unique=True)
    # A set of Role values: unique and sorted on save. One account holds every role.
    roles = ArrayField(models.CharField(max_length=16, choices=Role.choices), default=default_roles)
    # False means suspended: the user can't log in and existing sessions stop working.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"

    def __str__(self):
        # Never expose the phone number in admin lists or logs.
        return f"User {self.pk}"

    def save(self, *args, **kwargs):
        unknown = set(self.roles) - set(Role.values)
        if unknown:
            raise ValueError(f"Unknown roles: {sorted(unknown)}")
        self.roles = sorted(set(self.roles))
        super().save(*args, **kwargs)

    def has_role(self, role):
        return role in self.roles

    def add_role(self, role):
        """Add a role and save. Adding a role the user already has changes nothing."""
        if role not in Role.values:
            raise ValueError(f"Unknown role: {role}")
        if role not in self.roles:
            self.roles = [*self.roles, role]
            self.save(update_fields=["roles"])


class OtpChallenge(models.Model):
    """One login code sent to a phone.

    Only keyed hashes are stored: the phone, the requester's IP and the code
    can't be read back from this table.
    """

    phone_hash = models.CharField(max_length=64)
    ip_hash = models.CharField(max_length=64)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone_hash", "created_at"]),
            models.Index(fields=["ip_hash", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"OTP challenge {self.pk}"


class Suspension(models.Model):
    """Blocks selling and buying (publishing, accepting offers, making offers); login and
    open orders carry on. Active until lifted. The fee to lift it is recorded here."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="suspensions"
    )
    reason = models.CharField(max_length=300)
    fee_paise = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(lifted_at=None), name="one_active_suspension"
            )
        ]

    def __str__(self):
        return f"Suspension {self.pk} for user {self.user_id}"


class FlagStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class Flag(models.Model):
    """Something unusual about a user for an admin to review, e.g. a rate far above peers."""

    RATE_OUTLIER = "rate_outlier"
    KINDS = [(RATE_OUTLIER, "Rate far above peers")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="flags"
    )
    kind = models.CharField(max_length=30, choices=KINDS)
    category = models.ForeignKey(
        "listings.Category", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Numbers behind the flag, e.g. {"rate_paise": 1800, "peer_average_paise": 1400, "peers": 3}.
    details = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=FlagStatus.choices, default=FlagStatus.OPEN)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "category"],
                condition=models.Q(status="open"),
                name="one_open_flag",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()} for user {self.user_id}"


def is_suspended(user):
    return Suspension.objects.filter(user=user, lifted_at=None).exists()
