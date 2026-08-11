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

    def test_organizer_username_filter_is_case_insensitive(self):
        for value in ("organizer", "ORGANIZER", "Organizer"):
            with self.subTest(organizer_username=value):
                response = self.client.get(
                    reverse("event-list-create"), {"organizer_username": value}
                )
                ids = [item["id"] for item in response.data["results"]]
                self.assertIn(str(self.django_event.id), ids)
                self.assertIn(str(self.cooking_event.id), ids)

    def test_capacity_min_max_filter_events_by_range(self):
        small_event = make_event(self.organizer, title="Tiny Chat", capacity=1)
        big_event = make_event(self.organizer, title="Big Conference", capacity=100)
        # self.django_event and self.cooking_event both have capacity=2.

        cases = [
            ({"capacity_min": 50}, {big_event.id}),
            ({"capacity_max": 10}, {small_event.id, self.django_event.id, self.cooking_event.id}),
            ({"capacity_min": 2, "capacity_max": 2}, {self.django_event.id, self.cooking_event.id}),
        ]
        for params, expected_ids in cases:
            with self.subTest(params=params):
                response = self.client.get(reverse("event-list-create"), params)
                ids = {item["id"] for item in response.data["results"]}
                self.assertEqual(ids, {str(i) for i in expected_ids})
