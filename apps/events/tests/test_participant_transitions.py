from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.events.models import EventParticipant

from .test_participation_actions import make_event

User = get_user_model()


class EventAcceptViewTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer, capacity=1)
        self.client.force_authenticate(self.user)

    def accept(self):
        return self.client.post(reverse("event-accept", args=[self.event.id]))

    def test_accept_invited_becomes_confirmed(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        response = self.accept()

        self.assertEqual(response.status_code, 200)
        participant = EventParticipant.objects.get(event=self.event, user=self.user)
        self.assertEqual(participant.status, EventParticipant.Status.CONFIRMED)

    def test_accept_reconfirmation_required_becomes_confirmed(self):
        EventParticipant.objects.create(
            event=self.event,
            user=self.user,
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
        )

        response = self.accept()

        self.assertEqual(response.status_code, 200)

    def test_accept_is_idempotent_when_already_confirmed(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        response = self.accept()

        self.assertEqual(response.status_code, 200)

    def test_accept_returns_409_when_event_full(self):
        other = User.objects.create_user(
            email="other@example.com", username="other", password="pw"
        )
        EventParticipant.objects.create(
            event=self.event, user=other, status=EventParticipant.Status.CONFIRMED
        )
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        response = self.accept()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "capacity_exceeded")


class EventRejectViewTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)
        self.client.force_authenticate(self.user)

    def reject(self):
        return self.client.post(reverse("event-reject", args=[self.event.id]))

    def test_reject_invited_becomes_rejected(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        response = self.reject()

        self.assertEqual(response.status_code, 200)
        participant = EventParticipant.objects.get(event=self.event, user=self.user)
        self.assertEqual(participant.status, EventParticipant.Status.REJECTED)

    def test_reject_is_idempotent_when_already_rejected(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.REJECTED
        )

        response = self.reject()

        self.assertEqual(response.status_code, 200)


class EventCancelViewTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)
        self.client.force_authenticate(self.user)

    def cancel(self):
        return self.client.post(reverse("event-cancel", args=[self.event.id]))

    def test_cancel_confirmed_becomes_cancelled(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )

        response = self.cancel()

        self.assertEqual(response.status_code, 200)
        participant = EventParticipant.objects.get(event=self.event, user=self.user)
        self.assertEqual(participant.status, EventParticipant.Status.CANCELLED)

    def test_cancel_is_idempotent_when_already_cancelled(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CANCELLED
        )

        response = self.cancel()

        self.assertEqual(response.status_code, 200)

    def test_cancel_invalid_state_returns_400(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.INVITED
        )

        response = self.cancel()

        self.assertEqual(response.status_code, 400)
