import uuid
from enum import StrEnum

from django.db import models
from django.db.models.functions import Lower

from apps.identity.state import AccountState, derive_state


class AccountType(StrEnum):
    INDIVIDUAL = "individual"
    AGENCY = "agency"
    ADMIN = "admin"


class Account(models.Model):
    """An account. Written only by `identity.services`; the state is derived, never stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.CharField(max_length=254)
    password = models.CharField(max_length=256)  # a Django password hash, never plain text
    account_type = models.CharField(
        max_length=16, choices=[(t.value, t.value) for t in AccountType]
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.CharField(max_length=300, blank=True, default="")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="identity_account_email_ci_uniq"),
            models.CheckConstraint(
                condition=(
                    models.Q(suspended_at__isnull=True, suspension_reason="")
                    | (models.Q(suspended_at__isnull=False) & ~models.Q(suspension_reason=""))
                ),
                name="identity_account_suspension_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(account_type__in=[t.value for t in AccountType]),
                name="identity_account_type_valid",
            ),
        ]

    @property
    def state(self) -> AccountState:
        return derive_state(
            email_verified=self.email_verified_at is not None,
            suspended=self.suspended_at is not None,
            deactivated=self.deactivated_at is not None,
        )


class IndividualProfile(models.Model):
    """Profile of an Individual account. Written only by `identity.services`.

    `phone` is private-class data, stored as E.164 and never returned by any public response.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(
        Account, on_delete=models.CASCADE, related_name="individual_profile"
    )
    full_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EmailVerificationToken(models.Model):
    """A single-use verification link. Only the SHA-256 hash of the token is ever stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="verification_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)  # set on use or when superseded

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="identity_evt_expires_idx"),
            models.Index(fields=["used_at"], name="identity_evt_used_idx"),
        ]


# The one definition of why a session ends; events and services derive their sets from it.
SESSION_END_REASONS = ["sign_out", "password_changed", "deactivated", "replaced"]


class Session(models.Model):
    """A sign-in session. Only the SHA-256 hash of the cookie token is ever stored.

    Written only by `identity.services`. `ended_at` and `ended_reason` are set together.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    expires_at = models.DateTimeField()  # the absolute limit; the idle limit follows last_seen_at
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(ended_at__isnull=True, ended_reason="")
                    | (
                        models.Q(ended_at__isnull=False)
                        & models.Q(ended_reason__in=SESSION_END_REASONS)
                    )
                ),
                name="identity_session_end_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["ended_at"], name="identity_session_ended_idx"),
            models.Index(fields=["expires_at"], name="identity_session_expires_idx"),
            models.Index(fields=["last_seen_at"], name="identity_session_seen_idx"),
        ]
