from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import PasswordResetToken
from apps.users.tokens import generate_raw_token, hash_token

User = get_user_model()


class PasswordResetTokenModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="x"
        )

    def test_expires_at_defaults_to_1h_after_creation(self):
        token = PasswordResetToken.objects.create(user=self.user, token_hash="a" * 64)
        delta = token.expires_at - token.created_at
        self.assertAlmostEqual(delta.total_seconds(), 3600, delta=5)


class PasswordResetRequestViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="old-pass-word!"
        )

    def request_reset(self, email=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("auth-password-reset-request"),
                {"email": email or self.user.email},
                format="json",
            )

    def test_creates_token_and_sends_email(self):
        response = self.request_reset()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PasswordResetToken.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)

    def test_unknown_email_does_not_error_or_leak(self):
        response = self.request_reset(email="nobody@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class PasswordResetConfirmViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.old_password = "old-pass-word!"
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password=self.old_password
        )
        self.raw_token = generate_raw_token()
        self.token = PasswordResetToken.objects.create(
            user=self.user, token_hash=hash_token(self.raw_token)
        )

    def confirm(self, raw_token, new_password="new-pass-word!"):
        return self.client.post(
            reverse("auth-password-reset-confirm"),
            {"token": raw_token, "new_password": new_password},
            format="json",
        )

    def test_valid_token_sets_new_password_and_marks_token_used(self):
        response = self.confirm(self.raw_token, "brand-new-pass-word!")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-pass-word!"))
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.used_at)

    def test_unknown_token_rejected(self):
        response = self.confirm("not-a-real-token")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))

    def test_expired_token_rejected(self):
        self.token.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.token.save(update_fields=["expires_at"])

        response = self.confirm(self.raw_token)

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))


class PasswordResetConfirmPageViewTests(TestCase):
    """GET/POST server-rendered form route for the emailed link."""

    def setUp(self):
        self.client = APIClient()
        self.old_password = "old-pass-word!"
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password=self.old_password
        )
        self.raw_token = generate_raw_token()
        PasswordResetToken.objects.create(
            user=self.user, token_hash=hash_token(self.raw_token)
        )

    def test_get_with_valid_token_renders_form(self):
        response = self.client.get(
            "/auth/password-reset/confirm/", {"token": self.raw_token}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")

    def test_get_with_invalid_token_renders_error(self):
        response = self.client.get(
            "/auth/password-reset/confirm/", {"token": "bogus"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<form")

    def test_post_with_matching_passwords_changes_password(self):
        response = self.client.post(
            "/auth/password-reset/confirm/",
            {
                "token": self.raw_token,
                "new_password": "brand-new-pass-word!",
                "confirm_password": "brand-new-pass-word!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-pass-word!"))

    def test_post_with_mismatched_passwords_rerenders_form_with_error(self):
        response = self.client.post(
            "/auth/password-reset/confirm/",
            {
                "token": self.raw_token,
                "new_password": "brand-new-pass-word!",
                "confirm_password": "does-not-match!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_password))
