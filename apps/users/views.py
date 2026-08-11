import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import RefreshTokenFamily, RefreshTokenRecord
from .permissions import CookieCSRFPermission
from .serializers import (
    EmailVerificationConfirmSerializer,
    EmailVerificationRequestSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshSerializer,
    RegisterSerializer,
    UserSerializer,
)
from .throttles import (
    LoginEmailThrottle,
    LoginIPThrottle,
    PasswordResetEmailThrottle,
    PasswordResetIPThrottle,
    RefreshIPThrottle,
    RegisterIPThrottle,
)
from .tokens import hash_jti

User = get_user_model()


def _set_auth_cookies(response, access, refresh):
    response.set_cookie(
        settings.AUTH_COOKIE_ACCESS_NAME,
        str(access),
        httponly=settings.AUTH_COOKIE_HTTPONLY,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    response.set_cookie(
        settings.AUTH_COOKIE_REFRESH_NAME,
        str(refresh),
        httponly=settings.AUTH_COOKIE_HTTPONLY,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path=settings.AUTH_COOKIE_REFRESH_PATH,
    )
    _set_csrf_cookie(response)


def _set_csrf_cookie(response):
    # Deliberately NOT HttpOnly: JS must be able to read it and echo it
    # back as the X-CSRF-Token header (double-submit pattern, ADR 003).
    response.set_cookie(
        settings.AUTH_CSRF_COOKIE_NAME,
        secrets.token_urlsafe(32),
        httponly=False,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )


def _clear_auth_cookies(response):
    response.delete_cookie(settings.AUTH_COOKIE_ACCESS_NAME)
    response.delete_cookie(
        settings.AUTH_COOKIE_REFRESH_NAME, path=settings.AUTH_COOKIE_REFRESH_PATH
    )
    response.delete_cookie(settings.AUTH_CSRF_COOKIE_NAME)


def _issue_family_and_record(user):
    """Create a fresh refresh-token family + its first record for user."""
    refresh = RefreshToken.for_user(user)
    family = RefreshTokenFamily.objects.create(user=user)
    RefreshTokenRecord.objects.create(
        family=family,
        jti_hash=hash_jti(refresh["jti"]),
        issued_at=timezone.now(),
        expires_at=timezone.now() + refresh.lifetime,
    )
    return refresh


class RegisterView(generics.CreateAPIView):
    """POST /api/v1/auth/register/"""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterIPThrottle]


class LoginView(APIView):
    """POST /api/v1/auth/login/"""

    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer
    throttle_classes = [LoginEmailThrottle, LoginIPThrottle]

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        from django.contrib.auth import authenticate

        user = authenticate(
            request,
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            raise AuthenticationFailed("Invalid credentials.")

        if not user.is_email_verified:
            # Business error, not a validation error: credentials were
            # correct, the account just isn't allowed to authenticate yet
            # (ADR 002 error-format contract: {"error": {"code", "message"}}).
            return Response(
                {
                    "error": {
                        "code": "email_not_verified",
                        "message": (
                            "This account's email is not verified. Request a "
                            "new verification email at "
                            "/api/v1/auth/email-verification/request/."
                        ),
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = _issue_family_and_record(user)
        response = Response(UserSerializer(user).data, status=status.HTTP_200_OK)
        _set_auth_cookies(response, refresh.access_token, refresh)
        return response


class LogoutView(APIView):
    """POST /api/v1/auth/logout/"""

    permission_classes = [permissions.IsAuthenticated, CookieCSRFPermission]
    serializer_class = LogoutSerializer

    def post(self, request):
        refresh_raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH_NAME)
        if refresh_raw:
            try:
                refresh = RefreshToken(refresh_raw)
                record = RefreshTokenRecord.objects.select_related("family").filter(
                    jti_hash=hash_jti(refresh["jti"])
                ).first()
                if record:
                    record.family.revoked_at = timezone.now()
                    record.family.save(update_fields=["revoked_at"])
                refresh.blacklist()
            except (TokenError, AttributeError):
                pass

        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_auth_cookies(response)
        return response


class RefreshView(APIView):
    """POST /api/v1/auth/refresh/

    Reuse detection: a rotated-out refresh token being replayed revokes
    its entire family (ADR 003).
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = RefreshSerializer
    throttle_classes = [RefreshIPThrottle]

    def post(self, request):
        refresh_raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH_NAME)
        if not refresh_raw:
            raise AuthenticationFailed("No refresh token cookie present.")

        try:
            refresh = RefreshToken(refresh_raw)
        except TokenError as exc:
            raise AuthenticationFailed(str(exc))

        record = (
            RefreshTokenRecord.objects.select_related("family")
            .filter(jti_hash=hash_jti(refresh["jti"]))
            .first()
        )
        if record is None:
            raise AuthenticationFailed("Unknown refresh token.")
        if record.family.revoked_at is not None:
            raise AuthenticationFailed("Session has been revoked.")
        if record.used_at is not None:
            # Reuse detected: a stolen token replayed after the legitimate
            # client already rotated past it. Revoke the whole family.
            record.family.revoked_at = timezone.now()
            record.family.save(update_fields=["revoked_at"])
            raise AuthenticationFailed("Refresh token reuse detected.")

        record.used_at = timezone.now()
        record.save(update_fields=["used_at"])
        # Deliberately not calling refresh.blacklist() here: our own
        # used_at/family check above is what makes a rotated-out token
        # rejected on replay (reuse detection), and it must run before
        # simplejwt's blacklist would ever get consulted — blacklisting
        # eagerly here would make RefreshToken(raw) fail verification on
        # the next presentation, short-circuiting that check entirely.

        # Rotate: issue a brand-new refresh token into the same family.
        new_refresh = RefreshToken.for_user(record.family.user)
        RefreshTokenRecord.objects.create(
            family=record.family,
            jti_hash=hash_jti(new_refresh["jti"]),
            issued_at=timezone.now(),
            expires_at=timezone.now() + new_refresh.lifetime,
        )

        response = Response(status=status.HTTP_200_OK)
        _set_auth_cookies(response, new_refresh.access_token, new_refresh)
        return response


class _NotImplementedAuthView(APIView):
    """Shared shape for auth endpoints whose backing models don't exist yet.

    Input is still validated via the declared serializer so the request
    contract is real; only the actual side effect is deferred.
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = None

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(status=status.HTTP_501_NOT_IMPLEMENTED)


class EmailVerificationRequestView(_NotImplementedAuthView):
    """POST /api/v1/auth/email-verification/request/"""

    serializer_class = EmailVerificationRequestSerializer


class EmailVerificationConfirmView(_NotImplementedAuthView):
    """POST /api/v1/auth/email-verification/confirm/"""

    serializer_class = EmailVerificationConfirmSerializer


class PasswordChangeView(_NotImplementedAuthView):
    """POST /api/v1/auth/password/change/"""

    permission_classes = [permissions.IsAuthenticated, CookieCSRFPermission]
    serializer_class = PasswordChangeSerializer


class PasswordResetRequestView(_NotImplementedAuthView):
    """POST /api/v1/auth/password/reset/request/"""

    serializer_class = PasswordResetRequestSerializer
    throttle_classes = [PasswordResetEmailThrottle, PasswordResetIPThrottle]


class PasswordResetConfirmView(_NotImplementedAuthView):
    """POST /api/v1/auth/password/reset/confirm/"""

    serializer_class = PasswordResetConfirmSerializer


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/users/me/"""

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, CookieCSRFPermission]

    def get_object(self):
        return self.request.user
