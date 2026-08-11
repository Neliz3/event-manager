from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.users.models import RefreshTokenFamily

User = get_user_model()


class PasswordChangeViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.old_password = "old-pass-word!"
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password=self.old_password
        )
        self.user.is_email_verified = True
        self.user.save(update_fields=["is_email_verified"])

    def login(self):
        login_response = self.client.post(
            reverse("auth-login"),
            {"email": self.user.email, "password": self.old_password},
            format="json",
        )
        self.access_cookie = login_response.cookies[settings.AUTH_COOKIE_ACCESS_NAME].value
        self.refresh_cookie = login_response.cookies[settings.AUTH_COOKIE_REFRESH_NAME].value
        self.csrf_cookie = login_response.cookies[settings.AUTH_CSRF_COOKIE_NAME].value
        self.client.cookies[settings.AUTH_COOKIE_ACCESS_NAME] = self.access_cookie
        self.client.cookies[settings.AUTH_COOKIE_REFRESH_NAME] = self.refresh_cookie
        self.client.cookies[settings.AUTH_CSRF_COOKIE_NAME] = self.csrf_cookie
        return login_response

    def change_password(self, old_password, new_password):
        return self.client.post(
            reverse("auth-password-change"),
            {"old_password": old_password, "new_password": new_password},
            format="json",
            HTTP_X_CSRF_TOKEN=self.csrf_cookie,
        )

    def test_unauthenticated_request_rejected(self):
        response = self.client.post(
            reverse("auth-password-change"),
            {"old_password": self.old_password, "new_password": "brand-new-pass-word!"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_correct_old_password_changes_password(self):
        self.login()

        response = self.change_password(self.old_password, "brand-new-pass-word!")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-pass-word!"))

    def test_wrong_old_password_rejected(self):
        self.login()

        response = self.change_password("not-the-old-password", "brand-new-pass-word!")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_old_password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))

    def test_invalid_new_password_rejected_by_validators(self):
        self.login()

        response = self.change_password(self.old_password, "short")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))

    def test_success_revokes_all_refresh_token_families(self):
        self.login()
        family = RefreshTokenFamily.objects.get()
        self.assertIsNone(family.revoked_at)

        response = self.change_password(self.old_password, "brand-new-pass-word!")

        self.assertEqual(response.status_code, 200)
        family.refresh_from_db()
        self.assertIsNotNone(family.revoked_at)

        refresh_response = self.client.post(reverse("auth-refresh"))
        self.assertEqual(refresh_response.status_code, 401)

    def test_success_clears_caller_auth_cookies(self):
        self.login()

        response = self.change_password(self.old_password, "brand-new-pass-word!")

        self.assertEqual(response.data, None)
        self.assertEqual(
            response.cookies[settings.AUTH_COOKIE_ACCESS_NAME].value, ""
        )
        self.assertEqual(
            response.cookies[settings.AUTH_COOKIE_REFRESH_NAME].value, ""
        )

    def test_missing_csrf_header_blocked(self):
        self.login()

        response = self.client.post(
            reverse("auth-password-change"),
            {"old_password": self.old_password, "new_password": "brand-new-pass-word!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))
