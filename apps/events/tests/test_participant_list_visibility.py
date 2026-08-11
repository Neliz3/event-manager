from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.events.models import Event, EventParticipant

from .test_participation_actions import make_event

User = get_user_model()


class ParticipantListVisibilityTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.confirmed = User.objects.create_user(
            email="confirmed@example.com", username="confirmed", password="pw"
        )
        self.cancelled = User.objects.create_user(
            email="cancelled@example.com", username="cancelled", password="pw"
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com", username="outsider", password="pw"
        )
        self.event = make_event(self.organizer, access_type=Event.AccessType.PUBLIC, capacity=5)
        EventParticipant.objects.create(
            event=self.event, user=self.confirmed, status=EventParticipant.Status.CONFIRMED
        )
        EventParticipant.objects.create(
            event=self.event, user=self.cancelled, status=EventParticipant.Status.CANCELLED
        )

    def participants(self):
        return self.client.get(reverse("event-participants", args=[self.event.id]))

    def test_organizer_sees_full_data(self):
        self.client.force_authenticate(self.organizer)

        response = self.participants()

        self.assertEqual(response.status_code, 200)
        emails = {row["email"] for row in response.data["results"]}
        self.assertEqual(emails, {self.confirmed.email, self.cancelled.email})

    def test_confirmed_participant_sees_usernames_only_excluding_cancelled(self):
        self.client.force_authenticate(self.confirmed)

        response = self.participants()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [{"username": self.confirmed.username}])

    def test_unrelated_authenticated_user_forbidden(self):
        self.client.force_authenticate(self.outsider)

        response = self.participants()

        self.assertEqual(response.status_code, 403)

    def test_anonymous_forbidden(self):
        response = self.participants()

        self.assertIn(response.status_code, (401, 403))
