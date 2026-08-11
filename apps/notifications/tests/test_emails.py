import django_rq
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.events.models import Event, EventParticipant
from apps.notifications.emails import send_on_commit, send_registration_confirmed

User = get_user_model()


class SendOnCommitTests(TestCase):
    def setUp(self):
        organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        event = Event.objects.create(
            title="Test Event",
            date=timezone.now() + timezone.timedelta(days=7),
            format=Event.Format.ONLINE,
            location=None,
            access_type=Event.AccessType.PUBLIC,
            capacity=2,
            organizer=organizer,
        )
        self.participant = EventParticipant.objects.create(
            event=event, user=user, status=EventParticipant.Status.CONFIRMED
        )

    def test_email_not_sent_if_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                send_on_commit(send_registration_confirmed, self.participant)
                raise RuntimeError("simulated failure")

        self.assertEqual(len(mail.outbox), 0)

    def test_email_sent_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                send_on_commit(send_registration_confirmed, self.participant)

        self.assertEqual(len(mail.outbox), 1)


class SendOnCommitRQTests(TestCase):
    """§5: sends go through RQ, with a fixed retry policy, not a bare
    function call. RQ_ASYNC=False (test env) runs the job inline against a
    real Redis, so no separate worker process is needed here."""

    def setUp(self):
        organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        event = Event.objects.create(
            title="Test Event",
            date=timezone.now() + timezone.timedelta(days=7),
            format=Event.Format.ONLINE,
            location=None,
            access_type=Event.AccessType.PUBLIC,
            capacity=2,
            organizer=organizer,
        )
        self.participant = EventParticipant.objects.create(
            event=event, user=user, status=EventParticipant.Status.CONFIRMED
        )

    def test_email_delivered_via_rq_queue_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                send_on_commit(send_registration_confirmed, self.participant)

        self.assertEqual(len(mail.outbox), 1)

    def test_enqueued_job_carries_the_retry_policy(self):
        # ASYNC=True here (unlike the module default) so the job stays
        # queued after commit instead of running inline — needed to
        # inspect its retry config before a worker would consume it.
        async_queues = {
            "default": {**settings.RQ_QUEUES["default"], "ASYNC": True}
        }
        with override_settings(RQ_QUEUES=async_queues):
            queue = django_rq.get_queue()
            queue.empty()

            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    send_on_commit(send_registration_confirmed, self.participant)

            jobs = queue.jobs
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job.retries_left, 3)
            self.assertEqual(job.retry_intervals, [10, 60, 300])
