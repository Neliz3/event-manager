from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Event, EventParticipant
from .permissions import CanViewParticipants, IsOrganizerOrReadOnly
from .serializers import (
    AcceptActionSerializer,
    CancelActionSerializer,
    EventDetailSerializer,
    EventListSerializer,
    EventParticipantFullSerializer,
    EventWriteSerializer,
    InviteSerializer,
    RegisterActionSerializer,
    RejectActionSerializer,
)


class EventListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/events/

    Filters: organizer_username, date, capacity (ADR 002).
    TODO: private-event visibility rules are not applied to the queryset yet.
    """

    queryset = Event.objects.all().order_by("date")
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return EventWriteSerializer
        return EventListSerializer

    def get_queryset(self):
        qs = super().get_queryset()

        organizer_username = self.request.query_params.get("organizer_username")
        if organizer_username:
            qs = qs.filter(organizer__username=organizer_username)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date__date=date)

        capacity = self.request.query_params.get("capacity")
        if capacity:
            qs = qs.filter(capacity=capacity)

        return qs

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def create(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        event = write_serializer.save()
        read_serializer = EventDetailSerializer(event, context=self.get_serializer_context())
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class EventDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/events/{event_id}/

    TODO: private-event visibility (404 when undiscoverable) is not applied.
    """

    queryset = Event.objects.all()
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOrganizerOrReadOnly]

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return EventWriteSerializer
        return EventDetailSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write_serializer = self.get_serializer(instance, data=request.data, partial=partial)
        write_serializer.is_valid(raise_exception=True)
        event = write_serializer.save()
        read_serializer = EventDetailSerializer(event, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_200_OK)


class _NotImplementedActionView(APIView):
    """Shared shape for participation actions not yet wired to the model
    layer (Event.register/invite, EventParticipant.accept/reject/cancel).

    Input is still validated via the declared serializer so the request
    contract is real; only the state transition itself is deferred.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = None

    def post(self, request, event_id):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(status=status.HTTP_501_NOT_IMPLEMENTED)


class EventRegisterView(_NotImplementedActionView):
    """POST /api/v1/events/{event_id}/register/"""

    serializer_class = RegisterActionSerializer


class EventInviteView(_NotImplementedActionView):
    """POST /api/v1/events/{event_id}/invite/"""

    serializer_class = InviteSerializer


class EventAcceptView(_NotImplementedActionView):
    """POST /api/v1/events/{event_id}/accept/"""

    serializer_class = AcceptActionSerializer


class EventRejectView(_NotImplementedActionView):
    """POST /api/v1/events/{event_id}/reject/"""

    serializer_class = RejectActionSerializer


class EventCancelView(_NotImplementedActionView):
    """POST /api/v1/events/{event_id}/cancel/"""

    serializer_class = CancelActionSerializer


class EventParticipantsListView(generics.ListAPIView):
    """GET /api/v1/events/{event_id}/participants/

    TODO: does not yet apply the organizer/admin-vs-confirmed-member
    visibility split, the `status` filter, or private-event access rules —
    returns 501 pending that business-logic wiring.
    """

    serializer_class = EventParticipantFullSerializer
    permission_classes = [CanViewParticipants]

    def list(self, request, *args, **kwargs):
        return Response(status=status.HTTP_501_NOT_IMPLEMENTED)

    def get_queryset(self):
        return EventParticipant.objects.filter(event_id=self.kwargs["event_id"])
