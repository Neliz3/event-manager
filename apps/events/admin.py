from django.contrib import admin

from .models import Event, EventParticipant


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "format", "access_type", "date", "capacity")
    list_filter = ("format", "access_type")
    search_fields = ("title", "organizer__email")


@admin.register(EventParticipant)
class EventParticipantAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("event__title", "user__email")
