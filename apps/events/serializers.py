from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Event, EventFormat, EventParticipant

User = get_user_model()


class MyParticipationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventParticipant
        fields = ["status"]


class _MyParticipationMixin(serializers.Serializer):
    """Adds `my_participation` per ADR 002: null when authenticated with no
    record, omitted entirely for anonymous users.

    TODO: this currently always returns None — wiring it to the requesting
    user's actual EventParticipant record is left for the business-logic pass.
    """

    my_participation = serializers.SerializerMethodField()

    def get_my_participation(self, obj):
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request is None or not request.user or not request.user.is_authenticated:
            data.pop("my_participation", None)
        return data


class EventListSerializer(_MyParticipationMixin, serializers.ModelSerializer):
    """Compact representation used by the event list endpoint."""

    organizer = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "date",
            "format",
            "access_type",
            "capacity",
            "organizer",
            "created_at",
            "my_participation",
        ]


class EventDetailSerializer(_MyParticipationMixin, serializers.ModelSerializer):
    """Full representation used by the event detail endpoint."""

    organizer = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "date",
            "format",
            "location",
            "access_type",
            "capacity",
            "organizer",
            "created_at",
            "updated_at",
            "my_participation",
        ]


class EventWriteSerializer(serializers.ModelSerializer):
    """Validation-only serializer for create/update; no business rules."""

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "date",
            "format",
            "location",
            "access_type",
            "capacity",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "title": {"allow_blank": False},
        }

    def validate_capacity(self, value):
        if value <= 0:
            raise serializers.ValidationError("capacity must be greater than zero.")
        return value

    def validate_date(self, value):
        # Whether `date` is in the future is a business/domain rule
        # (ADR 002) and is intentionally not enforced here.
        return value

    def validate_access_type(self, value):
        if self.instance is not None and value != self.instance.access_type:
            raise serializers.ValidationError("access_type is immutable after creation.")
        return value

    def validate(self, attrs):
        fmt = attrs.get("format", getattr(self.instance, "format", None))
        location = attrs.get("location", getattr(self.instance, "location", None))

        # DRF's CharField trims whitespace, so "" and " " both arrive here as
        # "" — treat blank the same as absent so it can't slip past this
        # check and hit the DB's offline_requires_location constraint as a
        # raw IntegrityError instead of a clean 400.
        if location == "":
            location = None
            attrs["location"] = None

        if fmt == EventFormat.OFFLINE and not location:
            raise serializers.ValidationError(
                {"location": "location is required for offline events."}
            )
        if fmt == EventFormat.ONLINE and location:
            raise serializers.ValidationError(
                {"location": "location must be null for online events."}
            )
        return attrs

    def create(self, validated_data):
        validated_data["organizer"] = self.context["request"].user
        return super().create(validated_data)


class EventParticipantFullSerializer(serializers.ModelSerializer):
    """Organizer/admin view: full participant data including email/status."""

    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = EventParticipant
        fields = ["id", "username", "email", "status", "updated_at"]


class EventParticipantPublicSerializer(serializers.ModelSerializer):
    """Confirmed-member view: usernames only, no status/email."""

    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = EventParticipant
        fields = ["username"]


class InviteSerializer(serializers.Serializer):
    username = serializers.CharField()


class RegisterActionSerializer(serializers.Serializer):
    """No body required."""


class AcceptActionSerializer(serializers.Serializer):
    """No body required."""


class RejectActionSerializer(serializers.Serializer):
    """No body required."""


class CancelActionSerializer(serializers.Serializer):
    """No body required."""
