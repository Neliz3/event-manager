from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.events.models import Event, EventParticipant, expire_reconfirmations

from .test_participation_actions import make_event

User = get_user_model()


class ReconfirmationDeadlineTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)

    def test_mark_reconfirmation_required_sets_24h_deadline(self):
        participant = EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        participant.mark_reconfirmation_required()

        delta = participant.reconfirmation_deadline - timezone.now()
        self.assertAlmostEqual(delta.total_seconds(), 24 * 3600, delta=5)

    def test_accept_from_reconfirmation_required_clears_deadline(self):
        participant = EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline=timezone.now() + timedelta(hours=24),
        )

        participant.accept()

        self.assertIsNone(participant.reconfirmation_deadline)

    def test_cancel_from_reconfirmation_required_clears_deadline(self):
        participant = EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline=timezone.now() + timedelta(hours=24),
        )

        participant.cancel()

        self.assertIsNone(participant.reconfirmation_deadline)


class ExpireReconfirmationsTests(TransactionTestCase):
    """TransactionTestCase: expire_reconfirmations() sends via send_on_commit
    (RQ), which needs a real commit to fire — plain TestCase's rolled-back
    wrapper transaction would swallow it."""

    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer, capacity=1)

    def test_expires_participant_past_deadline(self):
        participant = EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline=timezone.now() - timedelta(minutes=1),
        )

        count = expire_reconfirmations()

        self.assertEqual(count, 1)
        participant.refresh_from_db()
        self.assertEqual(participant.status, EventParticipant.Status.CANCELLED)
        self.assertIsNone(participant.reconfirmation_deadline)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_leaves_participant_before_deadline_untouched(self):
        participant = EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline=timezone.now() + timedelta(hours=1),
        )

        count = expire_reconfirmations()

        self.assertEqual(count, 0)
        participant.refresh_from_db()
        self.assertEqual(participant.status, EventParticipant.Status.RECONFIRMATION_REQUIRED)
        self.assertEqual(len(mail.outbox), 0)

    def test_expiring_releases_capacity_for_new_registration(self):
        EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline=timezone.now() - timedelta(minutes=1),
        )
        expire_reconfirmations()

        other_user = User.objects.create_user(
            email="other@example.com", username="other", password="pw"
        )
        participant = self.event.register(other_user)

        self.assertEqual(participant.status, EventParticipant.Status.CONFIRMED)

    def test_other_statuses_unaffected(self):
        confirmed = EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        count = expire_reconfirmations()

        self.assertEqual(count, 0)
        confirmed.refresh_from_db()
        self.assertEqual(confirmed.status, EventParticipant.Status.CONFIRMED)
