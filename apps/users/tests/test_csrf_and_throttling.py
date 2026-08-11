from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class CSRFPermissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="csrf@example.com", username="csrf", password="s3cure-pass-word!"
        )

    def test_cookie_authenticated_post_without_csrf_header_is_blocked(self):
        access = RefreshToken.for_user(self.user).access_token
        self.client.cookies[settings.AUTH_COOKIE_ACCESS_NAME] = str(access)
        self.client.cookies[settings.AUTH_CSRF_COOKIE_NAME] = "some-csrf-value"

        response = self.client.post(reverse("auth-logout"))

        self.assertEqual(response.status_code, 403)

    def test_header_authenticated_post_is_exempt_from_csrf(self):
        access = RefreshToken.for_user(self.user).access_token

        response = self.client.post(
            reverse("auth-logout"), HTTP_AUTHORIZATION=f"Bearer {access}"
        )

        self.assertEqual(response.status_code, 204)


class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_login_email_throttle_trips_after_configured_rate(self):
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"]
        limit = int(rate.split("/")[0])

        for _ in range(limit):
            response = self.client.post(
                reverse("auth-login"),
                {"email": "victim@example.com", "password": "wrong"},
                format="json",
            )
            self.assertNotEqual(response.status_code, 429)

        response = self.client.post(
            reverse("auth-login"),
            {"email": "victim@example.com", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_register_ip_throttle_trips_after_configured_rate(self):
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["register"]
        limit = int(rate.split("/")[0])

        for i in range(limit):
            response = self.client.post(
                reverse("auth-register"),
                {
                    "email": f"user{i}@example.com",
                    "username": f"user{i}",
                    "password": "s3cure-pass-word!",
                },
                format="json",
            )
            self.assertNotEqual(response.status_code, 429)

        response = self.client.post(
            reverse("auth-register"),
            {
                "email": "onemore@example.com",
                "username": "onemore",
                "password": "s3cure-pass-word!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 429)
