from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.events.models import Event, EventParticipant

User = get_user_model()


def make_event(organizer, **kwargs):
    defaults = dict(
        title="Test Event",
        date=timezone.now() + timedelta(days=7),
        format=Event.Format.ONLINE,
        location=None,
        access_type=Event.AccessType.PUBLIC,
        capacity=2,
        organizer=organizer,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class RegistrationNotificationTests(APITestCase):
    def test_register_sends_confirmation_email_to_participant(self):
        organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        event = make_event(organizer)
        self.client.force_authenticate(user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("event-register", args=[event.id]))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn(event.title, mail.outbox[0].subject)


class InvitationNotificationTests(APITestCase):
    def test_invite_sends_invitation_email_to_invitee(self):
        organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        invitee = User.objects.create_user(
            email="invitee@example.com", username="invitee", password="pw"
        )
        event = make_event(organizer)
        self.client.force_authenticate(organizer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("event-invite", args=[event.id]), {"email": invitee.email}
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [invitee.email])
        self.assertIn(event.title, mail.outbox[0].subject)


class ParticipantTransitionNotificationTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)
        self.client.force_authenticate(self.user)

    def test_accept_sends_confirmation_email(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("event-accept", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_reject_sends_declined_email(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("event-reject", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_cancel_sends_cancellation_email(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("event-cancel", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_idempotent_repeat_action_does_not_resend_email(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("event-cancel", args=[self.event.id]))
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("event-cancel", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class ReconfirmationRequiredNotificationTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.participant_user = User.objects.create_user(
            email="participant@example.com", username="participant", password="pw"
        )
        self.event = make_event(self.organizer)
        self.participant = EventParticipant.objects.create(
            event=self.event,
            user=self.participant_user,
            status=EventParticipant.Status.CONFIRMED,
        )
        self.client.force_authenticate(self.organizer)

    def patch_event(self, data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(
                reverse("event-detail", args=[self.event.id]), data, format="json"
            )

    def test_date_change_moves_confirmed_participants_to_reconfirmation_required(self):
        new_date = self.event.date + timedelta(days=1)

        response = self.patch_event({"date": new_date.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.participant.refresh_from_db()
        self.assertEqual(
            self.participant.status, EventParticipant.Status.RECONFIRMATION_REQUIRED
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.participant_user.email])
        self.assertIn(self.event.title, mail.outbox[0].subject)

    def test_location_change_on_offline_event_triggers_reconfirmation(self):
        offline_event = make_event(
            self.organizer, format=Event.Format.OFFLINE, location="Old Hall"
        )
        participant = EventParticipant.objects.create(
            event=offline_event,
            user=self.participant_user,
            status=EventParticipant.Status.CONFIRMED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                reverse("event-detail", args=[offline_event.id]),
                {"location": "New Hall"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        participant.refresh_from_db()
        self.assertEqual(
            participant.status, EventParticipant.Status.RECONFIRMATION_REQUIRED
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_format_change_triggers_reconfirmation(self):
        offline_event = make_event(
            self.organizer, format=Event.Format.OFFLINE, location="Old Hall"
        )
        participant = EventParticipant.objects.create(
            event=offline_event,
            user=self.participant_user,
            status=EventParticipant.Status.CONFIRMED,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(
                reverse("event-detail", args=[offline_event.id]),
                {"format": Event.Format.ONLINE, "location": None},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        participant.refresh_from_db()
        self.assertEqual(
            participant.status, EventParticipant.Status.RECONFIRMATION_REQUIRED
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_title_only_change_does_not_trigger_reconfirmation_or_email(self):
        response = self.patch_event({"title": "New Title"})

        self.assertEqual(response.status_code, 200)
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.status, EventParticipant.Status.CONFIRMED)
        self.assertEqual(len(mail.outbox), 0)

    def test_non_confirmed_participant_unaffected(self):
        cancelled_user = User.objects.create_user(
            email="cancelled@example.com", username="cancelled", password="pw"
        )
        cancelled_participant = EventParticipant.objects.create(
            event=self.event, user=cancelled_user, status=EventParticipant.Status.CANCELLED
        )
        new_date = self.event.date + timedelta(days=1)

        self.patch_event({"date": new_date.isoformat()})

        cancelled_participant.refresh_from_db()
        self.assertEqual(cancelled_participant.status, EventParticipant.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 1)  # only the CONFIRMED participant
