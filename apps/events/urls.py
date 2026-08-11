from django.urls import path

from . import views

urlpatterns = [
    path("", views.EventListCreateView.as_view(), name="event-list-create"),
    path("<uuid:pk>/", views.EventDetailView.as_view(), name="event-detail"),
    path(
        "<uuid:event_id>/register/",
        views.EventRegisterView.as_view(),
        name="event-register",
    ),
    path(
        "<uuid:event_id>/invite/",
        views.EventInviteView.as_view(),
        name="event-invite",
    ),
    path(
        "<uuid:event_id>/accept/",
        views.EventAcceptView.as_view(),
        name="event-accept",
    ),
    path(
        "<uuid:event_id>/reject/",
        views.EventRejectView.as_view(),
        name="event-reject",
    ),
    path(
        "<uuid:event_id>/cancel/",
        views.EventCancelView.as_view(),
        name="event-cancel",
    ),
    path(
        "<uuid:event_id>/participants/",
        views.EventParticipantsListView.as_view(),
        name="event-participants",
    ),
]
