from rest_framework import permissions


class IsOrganizerOrReadOnly(permissions.BasePermission):
    """TODO: does not yet enforce private-event visibility (404 vs 403 per
    ADR 002) — only distinguishes safe methods from organizer-only writes.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and obj.organizer_id == request.user.id)


class CanViewParticipants(permissions.BasePermission):
    """TODO: stub — real rule is organizer/admin get full data, confirmed
    participants get username-only data, others get nothing (404/403 per
    ADR 002 private-event visibility rules).
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)
