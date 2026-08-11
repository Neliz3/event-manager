from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .test_participation_actions import make_event

User = get_user_model()


class EventListSearchTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            email="organizer@example.com", username="organizer", password="pw"
        )
        self.django_event = make_event(
            self.organizer,
            title="Django Meetup",
            description="Talks about REST APIs and ORMs.",
        )
        self.cooking_event = make_event(
            self.organizer,
            title="Cooking Night",
            description="Learn to make fresh pasta.",
        )

    def test_search_matches_title_case_insensitively(self):
        response = self.client.get(reverse("event-list-create"), {"search": "django"})

        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(str(self.django_event.id), ids)
        self.assertNotIn(str(self.cooking_event.id), ids)

    def test_search_matches_description(self):
        response = self.client.get(reverse("event-list-create"), {"search": "pasta"})

        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(str(self.cooking_event.id), ids)
        self.assertNotIn(str(self.django_event.id), ids)
