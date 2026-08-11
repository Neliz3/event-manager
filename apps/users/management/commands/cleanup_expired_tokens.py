from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.users.models import RefreshTokenFamily, RefreshTokenRecord


class Command(BaseCommand):
    """Prune expired refresh-token records and their now-empty families.

    Scheduled hourly via django-crontab (see CRONJOBS in config/settings.py);
    run `manage.py crontab add` once per deployment to register it.
    """

    help = "Delete expired RefreshTokenRecord rows and orphaned RefreshTokenFamily rows."

    def handle(self, *args, **options):
        now = timezone.now()

        expired_records, _ = RefreshTokenRecord.objects.filter(
            expires_at__lt=now
        ).delete()
        empty_families, _ = RefreshTokenFamily.objects.filter(
            records__isnull=True
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {expired_records} expired refresh token record(s) "
                f"and {empty_families} empty family/families."
            )
        )
