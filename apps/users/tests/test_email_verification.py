from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import EmailVerificationToken
from apps.users.tokens import generate_raw_token, hash_token

User = get_user_model()


class EmailVerificationTokenModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="x"
        )

    def test_expires_at_defaults_to_24h_after_creation(self):
        token = EmailVerificationToken.objects.create(
            user=self.user, token_hash="a" * 64
        )
        delta = token.expires_at - token.created_at
        self.assertAlmostEqual(delta.total_seconds(), 24 * 3600, delta=5)

    def test_used_at_defaults_to_none(self):
        token = EmailVerificationToken.objects.create(
            user=self.user, token_hash="a" * 64
        )
        self.assertIsNone(token.used_at)


class EmailVerificationRequestViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="x"
        )

    def request_verification(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("auth-email-verification-request"),
                {"email": self.user.email},
                format="json",
            )

    def test_creates_token_and_sends_email(self):
        response = self.request_verification()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailVerificationToken.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)

    def test_unknown_email_does_not_error_or_leak(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("auth-email-verification-request"),
                {"email": "nobody@example.com"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class EmailVerificationConfirmViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="x"
        )
        self.raw_token = generate_raw_token()
        self.token = EmailVerificationToken.objects.create(
            user=self.user, token_hash=hash_token(self.raw_token)
        )

    def confirm(self, raw_token):
        return self.client.post(
            reverse("auth-email-verification-confirm"),
            {"token": raw_token},
            format="json",
        )

    def test_valid_token_verifies_user_and_marks_token_used(self):
        response = self.confirm(self.raw_token)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_verified)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.used_at)

    def test_unknown_token_rejected(self):
        response = self.confirm("not-a-real-token")

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_email_verified)

    def test_expired_token_rejected(self):
        self.token.expires_at = timezone.now() - timezone.timedelta(hours=1)
        self.token.save(update_fields=["expires_at"])

        response = self.confirm(self.raw_token)

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_email_verified)

    def test_already_used_token_rejected(self):
        self.confirm(self.raw_token)
        mail.outbox.clear()

        response = self.confirm(self.raw_token)

        self.assertEqual(response.status_code, 400)

    def test_confirming_invalidates_other_outstanding_tokens_for_user(self):
        other_raw = generate_raw_token()
        other_token = EmailVerificationToken.objects.create(
            user=self.user, token_hash=hash_token(other_raw)
        )

        self.confirm(self.raw_token)

        other_token.refresh_from_db()
        self.assertIsNotNone(other_token.used_at)


class EmailVerificationConfirmPageViewTests(TestCase):
    """GET, template-rendering route for the link inside the email."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="a@example.com", username="a", password="x"
        )
        self.raw_token = generate_raw_token()
        EmailVerificationToken.objects.create(
            user=self.user, token_hash=hash_token(self.raw_token)
        )

    def test_valid_token_renders_success_template(self):
        response = self.client.get(
            "/auth/email-verification/confirm/", {"token": self.raw_token}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verified")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_verified)

    def test_invalid_token_renders_error_template(self):
        response = self.client.get(
            "/auth/email-verification/confirm/", {"token": "bogus"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")
