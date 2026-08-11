from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class CookieJWTAuthentication(JWTAuthentication):
    """JWTAuthentication that reads the access token from an HttpOnly cookie.

    Falls back to the standard Authorization header so the API also works
    for non-browser clients / tooling.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
            from_cookie = False
        else:
            raw_token = request.COOKIES.get(settings.AUTH_COOKIE_ACCESS_NAME)
            from_cookie = True

        if raw_token is None:
            return None

        try:
            validated_token = self.get_validated_token(raw_token)
        except (InvalidToken, TokenError):
            # An expired/invalid *cookie* just means "not logged in" — the
            # cookie isn't proactively cleared by the browser when it
            # expires, so treat it like no token was sent at all rather
            # than failing the request with 401 (which would break
            # AllowAny endpoints for anyone with a stale cookie). A bad
            # Authorization header, by contrast, is an explicit auth
            # attempt and should still fail loudly.
            if from_cookie:
                return None
            raise

        return self.get_user(validated_token), validated_token
