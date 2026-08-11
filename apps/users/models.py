from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user model: login by email, username is a non-unique display name."""

    username = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True)
    # Email verification (§2, docs/email-integration-spec.md) is implemented:
    # a fresh user starts unverified and is gated out of login until they
    # confirm via EmailVerificationToken.
    is_email_verified = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email


class RefreshTokenFamily(models.Model):
    """One chain of rotated refresh tokens issued from a single login.

    A family is the unit of revocation: revoking it invalidates every
    token ever issued within it, past or future (ADR 003).
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="refresh_token_families"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class RefreshTokenRecord(models.Model):
    """One issued refresh token, tracked by a hash of its jti.

    The raw token is never stored — only sha256(jti), matching the
    hash-and-lookup pattern used elsewhere for single-use tokens.
    """

    family = models.ForeignKey(
        RefreshTokenFamily, on_delete=models.CASCADE, related_name="records"
    )
    jti_hash = models.CharField(max_length=64, unique=True)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["family", "used_at"])]


class BaseSingleUseToken(models.Model):
    """Shared shape for link-based single-use tokens (email verification,
    password reset): only a hash of the raw token is ever stored, matching
    RefreshTokenRecord's jti_hash pattern above. `expires_at` is set from
    the subclass's TTL at creation time, not passed in by callers.
    """

    TTL = None  # subclasses must set a timedelta

    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self._state.adding and not self.expires_at:
            self.expires_at = timezone.now() + self.TTL
        super().save(*args, **kwargs)

    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class EmailVerificationToken(BaseSingleUseToken):
    """§2: 24h TTL, single-use, delivered/consumed over HTTPS."""

    TTL = timedelta(hours=24)

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_verification_tokens"
    )


class PasswordResetToken(BaseSingleUseToken):
    """§3: 1h TTL — shorter than email verification, higher account-takeover
    stakes if leaked."""

    TTL = timedelta(hours=1)

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_reset_tokens"
    )
