from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.emails import (
    send_invitation_accepted,
    send_invitation_received,
    send_invitation_rejected,
    send_on_commit,
    send_participation_cancelled,
    send_reconfirmation_required,
    send_registration_confirmed,
)

from .models import Event, EventParticipant
from .permissions import CanViewParticipants, IsOrganizerOrReadOnly
from .serializers import (
    AcceptActionSerializer,
    CancelActionSerializer,
    EventDetailSerializer,
    EventListSerializer,
    EventParticipantFullSerializer,
    EventParticipantPublicSerializer,
    EventWriteSerializer,
    InviteSerializer,
    RegisterActionSerializer,
    RejectActionSerializer,
)


def visible_events_queryset(user):
    """Private events are only discoverable by their organizer or a
    confirmed participant (§7); everyone else must not find them via
    list/detail (404, not 403).
    """
    visible = Q(access_type=Event.AccessType.PUBLIC)
    if user is not None and user.is_authenticated:
        visible |= Q(organizer=user) | Q(
            participants__user=user,
            participants__status=EventParticipant.Status.CONFIRMED,
        )
    return Event.objects.filter(visible).distinct()


class EventListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/events/

    Filters: organizer_username, date, capacity, search (ADR 002).
    """

    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return EventWriteSerializer
        return EventListSerializer

    def get_queryset(self):
        qs = visible_events_queryset(self.request.user).order_by("date")

        organizer_username = self.request.query_params.get("organizer_username")
        if organizer_username:
            qs = qs.filter(organizer__username=organizer_username)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date__date=date)

        capacity = self.request.query_params.get("capacity")
        if capacity:
            qs = qs.filter(capacity=capacity)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

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
    """GET/PATCH/DELETE /api/v1/events/{event_id}/"""

    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOrganizerOrReadOnly]

    def get_queryset(self):
        return visible_events_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return EventWriteSerializer
        return EventDetailSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    # Fields whose change reopens confirmation for already-CONFIRMED
    # participants (§6/§3 of docs/email-integration-spec.md — matches the
    # reconfirmation email copy verbatim: "changed the {date|format|location}").
    RECONFIRMATION_TRIGGER_FIELDS = ("date", "format", "location")

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        before = {
            field: getattr(instance, field) for field in self.RECONFIRMATION_TRIGGER_FIELDS
        }

        write_serializer = self.get_serializer(instance, data=request.data, partial=partial)
        write_serializer.is_valid(raise_exception=True)
        event = write_serializer.save()

        self._notify_reconfirmation_required(event, before)

        read_serializer = EventDetailSerializer(event, context=self.get_serializer_context())
        return Response(read_serializer.data, status=status.HTTP_200_OK)

    def _notify_reconfirmation_required(self, event, before):
        changed_fields = [
            field
            for field in self.RECONFIRMATION_TRIGGER_FIELDS
            if getattr(event, field) != before[field]
        ]
        if not changed_fields:
            return

        for participant in event.participants.filter(
            status=EventParticipant.Status.CONFIRMED
        ):
            participant.mark_reconfirmation_required()
            send_on_commit(
                send_reconfirmation_required,
                participant,
                changed_fields=" and ".join(changed_fields),
            )


def _domain_error(code, message, http_status):
    return Response(
        {"error": {"code": code, "message": message}}, status=http_status
    )


class EventRegisterView(APIView):
    """POST /api/v1/events/{event_id}/register/"""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RegisterActionSerializer

    def post(self, request, event_id):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        event = generics.get_object_or_404(Event, pk=event_id)

        try:
            participant = event.register(request.user)
        except EventParticipant.InvitationPending:
            return _domain_error(
                "invitation_pending",
                "You have a pending invitation for this event; accept or reject it first.",
                status.HTTP_409_CONFLICT,
            )
        except EventParticipant.AlreadyFinalized:
            return _domain_error(
                "already_finalized",
                "You are already confirmed for this event.",
                status.HTTP_409_CONFLICT,
            )
        except EventParticipant.EventFull:
            return _domain_error(
                "capacity_exceeded",
                "Event capacity has been reached.",
                status.HTTP_409_CONFLICT,
            )
        except ValueError as exc:
            return _domain_error("invalid_request", str(exc), status.HTTP_400_BAD_REQUEST)

        send_on_commit(send_registration_confirmed, participant)

        data = EventParticipantFullSerializer(participant).data
        return Response(data, status=status.HTTP_201_CREATED)


class EventInviteView(APIView):
    """POST /api/v1/events/{event_id}/invite/"""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InviteSerializer

    def post(self, request, event_id):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        event = generics.get_object_or_404(Event, pk=event_id)
        invitee = generics.get_object_or_404(
            get_user_model(), username=serializer.validated_data["username"]
        )

        try:
            participant = event.invite(invitee, by=request.user)
        except PermissionError:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except EventParticipant.AlreadyInvited:
            return _domain_error(
                "already_invited",
                "This user has already been invited to this event.",
                status.HTTP_409_CONFLICT,
            )

        send_on_commit(send_invitation_received, participant)

        data = EventParticipantFullSerializer(participant).data
        return Response(data, status=status.HTTP_201_CREATED)


class _ParticipantTransitionView(APIView):
    """Shared shape for accept/reject/cancel: look up the requesting user's
    own EventParticipant row for the event and drive its state machine.

    accept/reject/cancel are idempotent for the same resulting state (§3):
    a repeat call matching the current state returns 200 with the current
    representation rather than erroring.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = None
    idempotent_status = None
    valid_source_statuses = ()

    def transition(self, participant):
        raise NotImplementedError

    def notify(self, participant):
        raise NotImplementedError

    def post(self, request, event_id):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        participant = generics.get_object_or_404(
            EventParticipant, event_id=event_id, user=request.user
        )

        if participant.status == self.idempotent_status:
            data = EventParticipantFullSerializer(participant).data
            return Response(data, status=status.HTTP_200_OK)

        try:
            self.transition(participant)
        except EventParticipant.EventFull:
            return _domain_error(
                "capacity_exceeded",
                "Event capacity has been reached.",
                status.HTTP_409_CONFLICT,
            )
        except ValueError as exc:
            return _domain_error("invalid_request", str(exc), status.HTTP_400_BAD_REQUEST)

        self.notify(participant)

        data = EventParticipantFullSerializer(participant).data
        return Response(data, status=status.HTTP_200_OK)


class EventAcceptView(_ParticipantTransitionView):
    """POST /api/v1/events/{event_id}/accept/"""

    serializer_class = AcceptActionSerializer
    idempotent_status = EventParticipant.Status.CONFIRMED

    def transition(self, participant):
        participant.accept()

    def notify(self, participant):
        send_on_commit(send_invitation_accepted, participant)


class EventRejectView(_ParticipantTransitionView):
    """POST /api/v1/events/{event_id}/reject/"""

    serializer_class = RejectActionSerializer
    idempotent_status = EventParticipant.Status.REJECTED

    def transition(self, participant):
        participant.reject()

    def notify(self, participant):
        send_on_commit(send_invitation_rejected, participant)


class EventCancelView(_ParticipantTransitionView):
    """POST /api/v1/events/{event_id}/cancel/"""

    serializer_class = CancelActionSerializer
    idempotent_status = EventParticipant.Status.CANCELLED

    def transition(self, participant):
        participant.cancel()

    def notify(self, participant):
        send_on_commit(send_participation_cancelled, participant)


class EventParticipantsListView(generics.ListAPIView):
    """GET /api/v1/events/{event_id}/participants/

    Organizer/admin get full data (all statuses); a confirmed participant
    gets usernames only, of confirmed/non-cancelled participants; everyone
    else is forbidden (§7). Private-event discoverability (404) is enforced
    by `get_object_or_404` against the visible-events queryset.
    """

    permission_classes = [CanViewParticipants]

    def get_event(self):
        event = generics.get_object_or_404(
            visible_events_queryset(self.request.user), pk=self.kwargs["event_id"]
        )
        self.check_object_permissions(self.request, event)
        return event

    def get_serializer_class(self):
        if self.is_full_access:
            return EventParticipantFullSerializer
        return EventParticipantPublicSerializer

    def get_queryset(self):
        event = self.get_event()
        user = self.request.user
        self.is_full_access = user.is_staff or event.organizer_id == user.id

        qs = EventParticipant.objects.filter(event=event).order_by("updated_at")
        if self.is_full_access:
            status_filter = self.request.query_params.get("status")
            if status_filter:
                qs = qs.filter(status=status_filter)
        else:
            qs = qs.exclude(status=EventParticipant.Status.CANCELLED)

        return qs
