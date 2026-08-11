import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import render
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.notifications.emails import (
    send_email_verification,
    send_on_commit,
    send_password_reset,
)

from .models import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshTokenFamily,
    RefreshTokenRecord,
)
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
    EmailVerificationEmailThrottle,
    EmailVerificationIPThrottle,
    LoginEmailThrottle,
    LoginIPThrottle,
    PasswordResetEmailThrottle,
    PasswordResetIPThrottle,
    RefreshIPThrottle,
    RegisterIPThrottle,
)
from .tokens import generate_raw_token, hash_jti, hash_token

User = get_user_model()


def _confirm_email_verification_token(raw_token):
    """Shared by the JSON confirm endpoint and the emailed GET link.

    Returns the verified User on success, None on invalid/expired/used
    token. Marks the token used and invalidates the user's other
    outstanding tokens.
    """
    token = (
        EmailVerificationToken.objects.select_related("user")
        .filter(token_hash=hash_token(raw_token))
        .first()
    )
    if token is None or not token.is_valid():
        return None

    now = timezone.now()
    token.used_at = now
    token.save(update_fields=["used_at"])

    user = token.user
    user.is_email_verified = True
    user.save(update_fields=["is_email_verified"])

    EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).update(
        used_at=now
    )

    return user


def _lookup_password_reset_token(raw_token):
    """Look up and validate (but don't consume) a password-reset token —
    shared by the GET form render and the POST that actually sets the
    password."""
    token = (
        PasswordResetToken.objects.select_related("user")
        .filter(token_hash=hash_token(raw_token))
        .first()
    )
    if token is None or not token.is_valid():
        return None
    return token


def _consume_password_reset_token(token, new_password):
    user = token.user
    user.set_password(new_password)
    user.save(update_fields=["password"])
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    return user


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


class EmailVerificationRequestView(APIView):
    """POST /api/v1/auth/email-verification/request/"""

    permission_classes = [permissions.AllowAny]
    serializer_class = EmailVerificationRequestSerializer
    throttle_classes = [EmailVerificationEmailThrottle, EmailVerificationIPThrottle]

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"]
        ).first()
        if user is not None:
            raw_token = generate_raw_token()
            EmailVerificationToken.objects.create(
                user=user, token_hash=hash_token(raw_token)
            )
            link = request.build_absolute_uri(
                f"/auth/email-verification/confirm/?token={raw_token}"
            )
            send_on_commit(send_email_verification, user, link)

        # Same 200 whether or not the email is registered — don't leak
        # account existence.
        return Response(status=status.HTTP_200_OK)


class EmailVerificationConfirmView(APIView):
    """POST /api/v1/auth/email-verification/confirm/"""

    permission_classes = [permissions.AllowAny]
    serializer_class = EmailVerificationConfirmSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = _confirm_email_verification_token(serializer.validated_data["token"])
        if user is None:
            return Response(
                {
                    "error": {
                        "code": "invalid_token",
                        "message": "This verification link is invalid, expired, or already used.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_200_OK)


class EmailVerificationConfirmPageView(APIView):
    """GET /auth/email-verification/confirm/?token=...

    Server-rendered template for the link inside the email — not part of
    the versioned JSON API (§2 deviation note).
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        raw_token = request.query_params.get("token", "")
        user = _confirm_email_verification_token(raw_token) if raw_token else None
        template = "users/email_verification_success.html" if user else "users/email_verification_error.html"
        return render(request, template, status=200)


class PasswordChangeView(APIView):
    """POST /api/v1/auth/password/change/"""

    permission_classes = [permissions.IsAuthenticated, CookieCSRFPermission]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["old_password"]):
            return Response(
                {
                    "error": {
                        "code": "invalid_old_password",
                        "message": "Current password is incorrect.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        # Changing the password ends every other session too, same as a
        # reset (ADR 003 revocation posture): revoke all refresh token
        # families so previously issued refresh tokens stop working.
        RefreshTokenFamily.objects.filter(user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )

        response = Response(status=status.HTTP_200_OK)
        _clear_auth_cookies(response)
        return response


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password/reset/request/"""

    permission_classes = [permissions.AllowAny]
    serializer_class = PasswordResetRequestSerializer
    throttle_classes = [PasswordResetEmailThrottle, PasswordResetIPThrottle]

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"]
        ).first()
        if user is not None:
            raw_token = generate_raw_token()
            PasswordResetToken.objects.create(
                user=user, token_hash=hash_token(raw_token)
            )
            link = request.build_absolute_uri(
                f"/auth/password-reset/confirm/?token={raw_token}"
            )
            send_on_commit(send_password_reset, user, link)

        # Same 200 whether or not the email is registered — don't leak
        # account existence.
        return Response(status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password/reset/confirm/"""

    permission_classes = [permissions.AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = _lookup_password_reset_token(serializer.validated_data["token"])
        if token is None:
            return Response(
                {
                    "error": {
                        "code": "invalid_token",
                        "message": "This reset link is invalid, expired, or already used.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        _consume_password_reset_token(token, serializer.validated_data["new_password"])
        return Response(status=status.HTTP_200_OK)


class PasswordResetConfirmPageView(APIView):
    """GET/POST /auth/password-reset/confirm/?token=...

    Server-rendered form for the link inside the email — not part of the
    versioned JSON API (§3 "Reset confirm page" note). Unlike email
    verification this needs a form: the user submits a new password, not
    just proof of token possession.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        raw_token = request.query_params.get("token", "")
        token = _lookup_password_reset_token(raw_token) if raw_token else None
        if token is None:
            return render(request, "users/password_reset_error.html", status=200)
        return render(
            request,
            "users/password_reset_form.html",
            {"token": raw_token},
            status=200,
        )

    def post(self, request):
        raw_token = request.data.get("token", "")
        token = _lookup_password_reset_token(raw_token) if raw_token else None
        if token is None:
            return render(request, "users/password_reset_error.html", status=200)

        new_password = request.data.get("new_password", "")
        confirm_password = request.data.get("confirm_password", "")

        if new_password != confirm_password:
            return render(
                request,
                "users/password_reset_form.html",
                {"token": raw_token, "error": "Passwords do not match."},
                status=200,
            )

        try:
            validate_password(new_password)
        except DjangoValidationError as exc:
            return render(
                request,
                "users/password_reset_form.html",
                {"token": raw_token, "error": " ".join(exc.messages)},
                status=200,
            )

        _consume_password_reset_token(token, new_password)
        return render(request, "users/password_reset_success.html", status=200)


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/users/me/"""

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, CookieCSRFPermission]

    def get_object(self):
        return self.request.user
