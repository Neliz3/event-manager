from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission


class CookieCSRFPermission(BasePermission):
    """Double-submit CSRF check for cookie-authenticated, state-changing requests.

    SameSite=Lax (apps/users/authentication.py cookie config) is the primary
    CSRF control, but is not treated as sufficient on its own (ADR 003). A
    second, independent control is required here: state-changing requests
    authenticated via the access_token cookie must also present a
    X-CSRF-Token header matching a separate, JS-readable csrf cookie.

    Requests authenticated via the Authorization header (non-browser
    clients) are not subject to CSRF and are exempt.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        if request.META.get("HTTP_AUTHORIZATION"):
            return True

        # Not authenticated via a header, and not a safe method: if the
        # request is authenticated at all, it came in via the access_token
        # cookie, so the double-submit check applies.
        if not (request.user and request.user.is_authenticated):
            return True

        csrf_cookie = request.COOKIES.get(settings.AUTH_CSRF_COOKIE_NAME)
        csrf_header = request.headers.get("X-CSRF-Token")
        return bool(csrf_cookie) and csrf_cookie == csrf_header
