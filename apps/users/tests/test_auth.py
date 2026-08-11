from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.users.models import RefreshTokenFamily, RefreshTokenRecord

User = get_user_model()


class AuthFlowTests(TestCase):
    def setUp(self):
        cache.clear()  # throttle counters persist across tests otherwise
        self.client = APIClient()
        self.password = "s3cure-pass-word!"
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password=self.password
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=["is_email_verified"])

    def login(self):
        return self.client.post(
            reverse("auth-login"),
            {"email": self.user.email, "password": self.password},
            format="json",
        )

    def test_login_rejects_unverified_user(self):
        self.user.is_email_verified = False
        self.user.save(update_fields=["is_email_verified"])

        response = self.login()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "email_not_verified")
        self.assertNotIn(settings.AUTH_COOKIE_ACCESS_NAME, response.cookies)
        self.assertEqual(RefreshTokenFamily.objects.count(), 0)

    def test_login_issues_family_and_record(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RefreshTokenFamily.objects.count(), 1)
        self.assertEqual(RefreshTokenRecord.objects.count(), 1)
        record = RefreshTokenRecord.objects.get()
        self.assertIsNone(record.used_at)

        access_cookie = response.cookies[settings.AUTH_COOKIE_ACCESS_NAME]
        refresh_cookie = response.cookies[settings.AUTH_COOKIE_REFRESH_NAME]
        self.assertEqual(access_cookie["path"], "/")
        self.assertEqual(refresh_cookie["path"], settings.AUTH_COOKIE_REFRESH_PATH)
        self.assertIn(settings.AUTH_CSRF_COOKIE_NAME, response.cookies)

    def test_refresh_rotates_and_marks_used(self):
        login_response = self.login()
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = login_response.cookies[
            settings.AUTH_COOKIE_REFRESH_NAME
        ].value

        response = self.client.post(reverse("auth-refresh"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(RefreshTokenRecord.objects.count(), 2)
        original_record = RefreshTokenRecord.objects.order_by("issued_at").first()
        self.assertIsNotNone(original_record.used_at)

    def test_refresh_reuse_revokes_family_and_rejects(self):
        login_response = self.login()
        first_refresh_cookie = login_response.cookies[
            settings.AUTH_COOKIE_REFRESH_NAME
        ].value
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = first_refresh_cookie

        # Legitimate rotation.
        first_rotate = self.client.post(reverse("auth-refresh"))
        self.assertEqual(first_rotate.status_code, 200)
        new_refresh_cookie = first_rotate.cookies[
            settings.AUTH_COOKIE_REFRESH_NAME
        ].value

        # Replay the already-used original token: reuse detected.
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = first_refresh_cookie
        replay = self.client.post(reverse("auth-refresh"))
        self.assertEqual(replay.status_code, 401)

        family = RefreshTokenFamily.objects.get()
        self.assertIsNotNone(family.revoked_at)

        # The legitimately-rotated token is now also rejected: whole
        # family was revoked, not just the replayed token.
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = new_refresh_cookie
        second_attempt = self.client.post(reverse("auth-refresh"))
        self.assertEqual(second_attempt.status_code, 401)

    def test_refresh_after_family_revocation_rejects(self):
        login_response = self.login()
        family = RefreshTokenFamily.objects.get()
        family.revoked_at = family.created_at
        family.save(update_fields=["revoked_at"])
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = login_response.cookies[
            settings.AUTH_COOKIE_REFRESH_NAME
        ].value

        response = self.client.post(reverse("auth-refresh"))

        self.assertEqual(response.status_code, 401)

    def test_logout_revokes_family(self):
        login_response = self.login()
        access_cookie = login_response.cookies[settings.AUTH_COOKIE_ACCESS_NAME].value
        refresh_cookie = login_response.cookies[
            settings.AUTH_COOKIE_REFRESH_NAME
        ].value
        csrf_cookie = login_response.cookies[settings.AUTH_CSRF_COOKIE_NAME].value
        self.client.cookies[settings.AUTH_COOKIE_ACCESS_NAME] = access_cookie
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = refresh_cookie
        self.client.cookies[settings.AUTH_CSRF_COOKIE_NAME] = csrf_cookie

        logout_response = self.client.post(
            reverse("auth-logout"), HTTP_X_CSRF_TOKEN=csrf_cookie
        )
        self.assertEqual(logout_response.status_code, 204)

        family = RefreshTokenFamily.objects.get()
        self.assertIsNotNone(family.revoked_at)

        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = refresh_cookie
        refresh_response = self.client.post(reverse("auth-refresh"))
        self.assertEqual(refresh_response.status_code, 401)
