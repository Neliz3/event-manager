import hashlib
import secrets


def hash_jti(jti: str) -> str:
    """sha256 hex digest of a token's jti — never store the raw token."""
    return hashlib.sha256(jti.encode()).hexdigest()


def generate_raw_token() -> str:
    """A cryptographically random, URL-safe raw token (email verification,
    password reset, ...). Never stored as-is — see hash_token()."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """sha256 hex digest of a raw single-use token — never store the raw
    value, matching the hash-and-lookup pattern used for jti above."""
    return hashlib.sha256(raw.encode()).hexdigest()
