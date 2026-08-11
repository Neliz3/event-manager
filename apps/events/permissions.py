from rest_framework import permissions

from .models import EventParticipant


class IsOrganizerOrReadOnly(permissions.BasePermission):
    """TODO: does not yet enforce private-event visibility (404 vs 403 per
    ADR 002) — only distinguishes safe methods from organizer-only writes.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and obj.organizer_id == request.user.id)


class CanViewParticipants(permissions.BasePermission):
    """Organizer/admin get full data, confirmed participants get
    username-only data, everyone else is forbidden (§7). Private-event
    404-vs-403 discoverability is enforced separately by the event queryset.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, event):
        user = request.user
        if user.is_staff or event.organizer_id == user.id:
            return True
        return event.participants.filter(
            user=user, status=EventParticipant.Status.CONFIRMED
        ).exists()
