import hashlib


def hash_jti(jti: str) -> str:
    """sha256 hex digest of a token's jti — never store the raw token."""
    return hashlib.sha256(jti.encode()).hexdigest()
