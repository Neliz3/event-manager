from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model: login by email, username is a non-unique display name."""

    username = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True)
    # TODO: default should flip to False once email verification
    # (send/confirm) is actually implemented (currently 501 stubs) — True
    # for now so existing registration/login isn't locked out (ADR 003).
    is_email_verified = models.BooleanField(default=True)

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
