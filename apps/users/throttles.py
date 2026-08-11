from rest_framework.throttling import SimpleRateThrottle


class PerEmailThrottle(SimpleRateThrottle):
    """Throttles by the submitted email, not by user/IP.

    login and password/reset/request are guessable-credential attacks
    against a specific account; a shared-IP/NAT attacker could otherwise
    dodge a per-IP throttle alone by rotating source IPs (ADR 003).
    """

    def get_cache_key(self, request, view):
        email = request.data.get("email")
        if not email:
            # No email to key on — the view's own serializer validation
            # handles a missing/malformed email; don't throttle here.
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(email).strip().lower(),
        }


class LoginIPThrottle(SimpleRateThrottle):
    scope = "login-ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class PasswordResetIPThrottle(LoginIPThrottle):
    scope = "password-reset-ip"


class RefreshIPThrottle(LoginIPThrottle):
    scope = "refresh"


class RegisterIPThrottle(LoginIPThrottle):
    scope = "register"


class LoginEmailThrottle(PerEmailThrottle):
    scope = "login"


class PasswordResetEmailThrottle(PerEmailThrottle):
    scope = "password-reset"
