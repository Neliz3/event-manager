import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Q
from django.utils import timezone


class EventFormat(models.TextChoices):
    ONLINE = "online", "Online"
    OFFLINE = "offline", "Offline"


class EventAccessType(models.TextChoices):
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"


class Event(models.Model):
    # Module-level so `Meta` (its own nested scope, with no visibility into
    # Event's class body) can reference them for the CheckConstraint below.
    Format = EventFormat
    AccessType = EventAccessType

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    date = models.DateTimeField()

    format = models.CharField(max_length=10, choices=EventFormat.choices)
    location = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Required for offline events, must be null for online events.",
    )

    access_type = models.CharField(max_length=10, choices=EventAccessType.choices)
    capacity = models.PositiveIntegerField()
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organized_events",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(format=EventFormat.OFFLINE, location__isnull=False)
                    | Q(format=EventFormat.ONLINE, location__isnull=True)
                ),
                name="offline_requires_location",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gt=0),
                name="capacity_positive",
            ),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.format == self.Format.OFFLINE and not self.location:
            raise ValidationError(
                "location is required for offline events."
            )
        if self.format == self.Format.ONLINE and self.location:
            raise ValidationError(
                "location must be null for online events."
            )

    @staticmethod
    def _require_postgresql():
        if connection.vendor != "postgresql":
            raise ImproperlyConfigured(
                "select_for_update()-backed capacity checks require PostgreSQL."
            )

    def _effective_participant_count(self):
        """CONFIRMED + RECONFIRMATION_REQUIRED rows reserve capacity (decision #12)."""
        return self.participants.filter(
            status__in=[
                EventParticipant.Status.CONFIRMED,
                EventParticipant.Status.RECONFIRMATION_REQUIRED,
            ]
        ).count()

    def register(self, user):
        """Self-registration. Only valid for PUBLIC events (decision #8/#9)."""
        self._require_postgresql()

        if self.access_type != self.AccessType.PUBLIC:
            raise ValueError("register() is only valid for public events.")
        if self.date < timezone.now():
            raise ValueError("cannot register for an event whose date has passed.")

        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=self.pk)

            participant = (
                EventParticipant.objects.select_for_update()
                .filter(event=event, user=user)
                .first()
            )

            if participant is not None:
                if participant.status == EventParticipant.Status.INVITED:
                    raise EventParticipant.InvitationPending(
                        "user has a pending invite; accept() or reject() it first."
                    )
                if participant.status == EventParticipant.Status.CONFIRMED:
                    raise EventParticipant.AlreadyFinalized(
                        "user is already confirmed for this event."
                    )
                # REJECTED or CANCELLED: allowed to proceed back to CONFIRMED,
                # subject to the capacity check below.

            if event._effective_participant_count() >= event.capacity:
                raise EventParticipant.EventFull("event is at capacity.")

            if participant is not None:
                participant.status = EventParticipant.Status.CONFIRMED
                participant.save(update_fields=["status", "updated_at"])
                return participant

            return EventParticipant.objects.create(
                event=event,
                user=user,
                status=EventParticipant.Status.CONFIRMED,
            )

    def invite(self, user, *, by):
        """Organizer-initiated invite. Available on both public and private events."""
        self._require_postgresql()

        if by != self.organizer:
            raise PermissionError("only the organizer can invite participants.")

        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=self.pk)

            if EventParticipant.objects.select_for_update().filter(
                event=event, user=user
            ).exists():
                raise EventParticipant.AlreadyInvited(
                    "user has already been invited to this event."
                )

            try:
                return EventParticipant.objects.create(
                    event=event,
                    user=user,
                    status=EventParticipant.Status.INVITED,
                )
            except IntegrityError:
                raise EventParticipant.AlreadyInvited(
                    "user has already been invited to this event."
                )


class EventParticipant(models.Model):
    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        RECONFIRMATION_REQUIRED = (
            "reconfirmation_required",
            "Reconfirmation required",
        )

    class AlreadyInvited(Exception):
        pass

    class AlreadyFinalized(Exception):
        pass

    class InvitationPending(Exception):
        pass

    class EventFull(Exception):
        pass

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_participations",
    )

    status = models.CharField(max_length=24, choices=Status.choices)

    # Set when entering RECONFIRMATION_REQUIRED (§6); cleared on leaving it
    # (accept()/cancel()). expire_reconfirmations() releases any row whose
    # deadline has passed.
    reconfirmation_deadline = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"], name="unique_event_participant"
            ),
        ]
        indexes = [
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return f"{self.user} @ {self.event} ({self.status})"

    def accept(self):
        """Valid from INVITED or RECONFIRMATION_REQUIRED -> CONFIRMED.

        Enforces the capacity cap (decision #12) and the date window
        (decision #13).
        """
        if self.status not in (
            self.Status.INVITED,
            self.Status.RECONFIRMATION_REQUIRED,
        ):
            raise ValueError(
                f"cannot accept() from status {self.status!r}."
            )

        Event._require_postgresql()

        if self.event.date < timezone.now():
            raise ValueError("cannot accept once the event date has passed.")

        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=self.event_id)
            participant = EventParticipant.objects.select_for_update().get(
                pk=self.pk
            )

            # Reconfirm the still-current status under lock.
            if participant.status not in (
                self.Status.INVITED,
                self.Status.RECONFIRMATION_REQUIRED,
            ):
                raise ValueError(
                    f"cannot accept() from status {participant.status!r}."
                )

            # A participant already CONFIRMED/RECONFIRMATION_REQUIRED already
            # holds a capacity slot; only a fresh INVITED->CONFIRMED transition
            # needs a fresh capacity check.
            if participant.status == self.Status.INVITED:
                if event._effective_participant_count() >= event.capacity:
                    raise self.EventFull("event is at capacity.")

            participant.status = self.Status.CONFIRMED
            participant.reconfirmation_deadline = None
            participant.save(
                update_fields=["status", "reconfirmation_deadline", "updated_at"]
            )
            self.status = participant.status
            self.reconfirmation_deadline = participant.reconfirmation_deadline
            return participant

    def reject(self):
        """Valid only from INVITED -> REJECTED."""
        if self.status != self.Status.INVITED:
            raise ValueError(f"cannot reject() from status {self.status!r}.")

        self.status = self.Status.REJECTED
        self.save(update_fields=["status", "updated_at"])
        return self

    def cancel(self):
        """Valid from CONFIRMED or RECONFIRMATION_REQUIRED -> CANCELLED."""
        if self.status not in (
            self.Status.CONFIRMED,
            self.Status.RECONFIRMATION_REQUIRED,
        ):
            raise ValueError(f"cannot cancel() from status {self.status!r}.")

        self.status = self.Status.CANCELLED
        self.reconfirmation_deadline = None
        self.save(update_fields=["status", "reconfirmation_deadline", "updated_at"])
        return self

    def mark_reconfirmation_required(self):
        """CONFIRMED -> RECONFIRMATION_REQUIRED.

        Triggered when the organizer changes date/format/location on the
        event. CANCELLED stays terminal and is not affected. The slot stays
        reserved (decision #12) until expire_reconfirmations() releases it
        after the 24h deadline set here (§6).
        """
        if self.status != self.Status.CONFIRMED:
            return self

        self.reconfirmation_deadline = timezone.now() + timedelta(hours=24)
        self.status = self.Status.RECONFIRMATION_REQUIRED
        self.save(update_fields=["status", "reconfirmation_deadline", "updated_at"])
        return self


def expire_reconfirmations():
    """§6: release RECONFIRMATION_REQUIRED holds whose 24h deadline has
    passed — CANCELLED (matching the manual cancel() path, decision #12's
    capacity hold is freed the same way), plus a "reservation expired"
    email per released participant. Scheduled via CRONJOBS (§5's cron
    mechanism, no separate scheduler) — see the expire_reconfirmations
    management command.

    Returns the number of participants released.
    """
    from apps.notifications.emails import send_on_commit, send_reservation_expired

    expired_ids = list(
        EventParticipant.objects.filter(
            status=EventParticipant.Status.RECONFIRMATION_REQUIRED,
            reconfirmation_deadline__lte=timezone.now(),
        ).values_list("id", flat=True)
    )

    count = 0
    for participant_id in expired_ids:
        with transaction.atomic():
            participant = EventParticipant.objects.select_for_update().get(
                pk=participant_id
            )
            if (
                participant.status != EventParticipant.Status.RECONFIRMATION_REQUIRED
                or participant.reconfirmation_deadline is None
                or participant.reconfirmation_deadline > timezone.now()
            ):
                continue

            participant.status = EventParticipant.Status.CANCELLED
            participant.reconfirmation_deadline = None
            participant.save(
                update_fields=["status", "reconfirmation_deadline", "updated_at"]
            )
            send_on_commit(send_reservation_expired, participant)
            count += 1

    return count
