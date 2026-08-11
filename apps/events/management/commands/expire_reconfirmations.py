from django.core.management.base import BaseCommand

from apps.events.models import expire_reconfirmations


class Command(BaseCommand):
    """Release RECONFIRMATION_REQUIRED participant rows whose 24h deadline
    has passed, freeing their capacity hold and emailing the participant.

    Scheduled via django-crontab (see CRONJOBS in config/settings.py);
    run `manage.py crontab add` once per deployment to register it.
    """

    help = "Expire RECONFIRMATION_REQUIRED participants past their reconfirmation deadline."

    def handle(self, *args, **options):
        count = expire_reconfirmations()
        self.stdout.write(
            self.style.SUCCESS(f"Expired {count} reconfirmation-required participant(s).")
        )
