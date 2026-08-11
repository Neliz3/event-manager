from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.events.models import Event, EventParticipant

from .test_participation_actions import make_event

User = get_user_model()


class PrivateEventVisibilityTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com", username="outsider", password="pw"
        )
        self.confirmed = User.objects.create_user(
            email="confirmed@example.com", username="confirmed", password="pw"
        )
        self.event = make_event(self.organizer, access_type=Event.AccessType.PRIVATE)
        EventParticipant.objects.create(
            event=self.event, user=self.confirmed, status=EventParticipant.Status.CONFIRMED
        )

    def test_outsider_gets_404_on_detail(self):
        self.client.force_authenticate(self.outsider)

        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertEqual(response.status_code, 404)

    def test_outsider_does_not_see_private_event_in_list(self):
        self.client.force_authenticate(self.outsider)

        response = self.client.get(reverse("event-list-create"))

        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(str(self.event.id), ids)

    def test_organizer_can_view_own_private_event(self):
        self.client.force_authenticate(self.organizer)

        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)

    def test_confirmed_participant_can_view_private_event(self):
        self.client.force_authenticate(self.confirmed)

        response = self.client.get(reverse("event-detail", args=[self.event.id]))

        self.assertEqual(response.status_code, 200)
