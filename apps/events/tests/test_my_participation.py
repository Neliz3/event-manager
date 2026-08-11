from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.events.models import Event, EventParticipant

from .test_participation_actions import make_event

User = get_user_model()


class MyParticipationFieldTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.user = User.objects.create_user(
            email="user@example.com", username="user", password="pw"
        )
        self.event = make_event(self.organizer)

    def test_confirmed_user_sees_own_status(self):
        EventParticipant.objects.create(
            event=self.event, user=self.user, status=EventParticipant.Status.CONFIRMED
        )
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertEqual(
            response.data["my_participation"], {"status": "confirmed"}
        )

    def test_authenticated_user_with_no_record_sees_null(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertIsNone(response.data["my_participation"])

    def test_anonymous_user_field_omitted(self):
        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertNotIn("my_participation", response.data)
