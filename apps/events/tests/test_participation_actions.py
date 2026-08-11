from datetime import timedelta

from django.contrib.auth import get_user_model
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


class EventRegisterViewTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)
        self.client.force_authenticate(self.user)

    def test_register_success_creates_confirmed_participant(self):
        response = self.client.post(reverse("event-register", args=[self.event.id]))

        self.assertEqual(response.status_code, 201)
        participant = EventParticipant.objects.get(event=self.event, user=self.user)
        self.assertEqual(participant.status, EventParticipant.Status.CONFIRMED)

    def test_register_rejects_private_event(self):
        private_event = make_event(self.organizer, access_type=Event.AccessType.PRIVATE)

        response = self.client.post(reverse("event-register", args=[private_event.id]))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            EventParticipant.objects.filter(event=private_event, user=self.user).exists()
        )

    def test_register_rejects_past_event(self):
        past_event = make_event(self.organizer, date=timezone.now() - timedelta(days=1))

        response = self.client.post(reverse("event-register", args=[past_event.id]))

        self.assertEqual(response.status_code, 400)

    def test_register_returns_409_when_invite_pending(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        response = self.client.post(reverse("event-register", args=[self.event.id]))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "invitation_pending")

    def test_register_returns_409_when_already_confirmed(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        response = self.client.post(reverse("event-register", args=[self.event.id]))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "already_finalized")


class EventInviteViewTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.other = User.objects.create_user(
            email="other@example.com", username="other", password="pw"
        )
        self.invitee = User.objects.create_user(
            email="invitee@example.com", username="invitee", password="pw"
        )
        self.event = make_event(self.organizer, access_type=Event.AccessType.PRIVATE)

    def invite(self, actor, email):
        self.client.force_authenticate(actor)
        return self.client.post(
            reverse("event-invite", args=[self.event.id]), {"email": email}
        )

    def test_organizer_invite_success_creates_invited_participant(self):
        response = self.invite(self.organizer, self.invitee.email)

        self.assertEqual(response.status_code, 201)
        participant = EventParticipant.objects.get(event=self.event, user=self.invitee)
        self.assertEqual(participant.status, EventParticipant.Status.INVITED)

    def test_non_organizer_cannot_invite(self):
        response = self.invite(self.other, self.invitee.email)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            EventParticipant.objects.filter(event=self.event, user=self.invitee).exists()
        )

    def test_duplicate_invite_returns_409(self):
        self.invite(self.organizer, self.invitee.email)

        response = self.invite(self.organizer, self.invitee.email)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "already_invited")
