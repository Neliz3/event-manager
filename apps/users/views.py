from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

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
    )


def _clear_auth_cookies(response):
    response.delete_cookie(settings.AUTH_COOKIE_ACCESS_NAME)
    response.delete_cookie(settings.AUTH_COOKIE_REFRESH_NAME)


class RegisterView(generics.CreateAPIView):
    """POST /api/v1/auth/register/"""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class LoginView(APIView):
    """POST /api/v1/auth/login/

    Only-verified-user gating is business logic (no verification field
    exists on User yet) and is left as a TODO.
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer

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

        # TODO: reject login when user.is_email_verified is False, once that
        # field/model exists (ADR 002: "Only verified users may log in.").

        refresh = RefreshToken.for_user(user)
        response = Response(UserSerializer(user).data, status=status.HTTP_200_OK)
        _set_auth_cookies(response, refresh.access_token, refresh)
        return response


class LogoutView(APIView):
    """POST /api/v1/auth/logout/

    Refresh-token-family revocation is business logic left as a TODO.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        refresh_raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH_NAME)
        if refresh_raw:
            try:
                RefreshToken(refresh_raw).blacklist()
            except (TokenError, AttributeError):
                pass

        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_auth_cookies(response)
        return response


class RefreshView(APIView):
    """POST /api/v1/auth/refresh/

    Reuse detection / token-family revocation is business logic left as a
    TODO (needs a supporting model).
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = RefreshSerializer

    def post(self, request):
        refresh_raw = request.COOKIES.get(settings.AUTH_COOKIE_REFRESH_NAME)
        if not refresh_raw:
            raise AuthenticationFailed("No refresh token cookie present.")

        try:
            refresh = RefreshToken(refresh_raw)
        except TokenError as exc:
            raise AuthenticationFailed(str(exc))

        access = refresh.access_token

        response = Response(status=status.HTTP_200_OK)
        _set_auth_cookies(response, access, refresh)
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

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PasswordChangeSerializer


class PasswordResetRequestView(_NotImplementedAuthView):
    """POST /api/v1/auth/password/reset/request/"""

    serializer_class = PasswordResetRequestSerializer


class PasswordResetConfirmView(_NotImplementedAuthView):
    """POST /api/v1/auth/password/reset/confirm/"""

    serializer_class = PasswordResetConfirmSerializer


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/users/me/"""

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
