"""One function per notification trigger (docs/email-integration-spec.md §3).

Each function renders subject/body from the plain-text templates in
templates/notifications/ and sends via Django's configured EMAIL_BACKEND.
Recipient is always the affected participant, never the organizer
(decision in §3).
"""

import django_rq
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from rq import Retry

# §5: fixed retries with backoff — 3 attempts, growing delay between each.
# Exhausted retries land in RQ's built-in FailedJobRegistry (no custom
# dead-letter handling); failures are visible via worker process logs only
# (no external alerting wired up).
EMAIL_RETRY_POLICY = Retry(max=3, interval=[10, 60, 300])


def send_on_commit(send_fn, *args, **kwargs):
    """Enqueue a notification send onto the RQ 'default' queue (§5), to be
    picked up by the `worker` service. django_rq's queue defers the actual
    enqueue until the enclosing DB transaction commits (its default
    "on_db_commit" mode) — so a rolled-back mutation never triggers an
    email. Call sites should use this instead of calling a send_* function
    directly."""
    django_rq.get_queue().enqueue(send_fn, *args, retry=EMAIL_RETRY_POLICY, **kwargs)


def _send(template_stem, to_email, context):
    subject = render_to_string(f"notifications/{template_stem}_subject.txt", context).strip()
    body = render_to_string(f"notifications/{template_stem}_body.txt", context).strip()
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email])


def send_email_verification(user, verification_link):
    _send(
        "email_verification",
        user.email,
        {"username": user.username, "verification_link": verification_link},
    )


def send_password_reset(user, reset_link):
    _send(
        "password_reset",
        user.email,
        {"username": user.username, "reset_link": reset_link},
    )


def send_registration_confirmed(participant):
    _send(
        "registration_confirmed",
        participant.user.email,
        {"username": participant.user.username, "event": participant.event},
    )


def send_invitation_received(participant, event_link=""):
    _send(
        "invitation_received",
        participant.user.email,
        {
            "username": participant.user.username,
            "event": participant.event,
            "organizer_username": participant.event.organizer.username,
            "event_link": event_link,
        },
    )


def send_invitation_accepted(participant):
    _send(
        "invitation_accepted",
        participant.user.email,
        {"username": participant.user.username, "event": participant.event},
    )


def send_invitation_rejected(participant):
    _send(
        "invitation_rejected",
        participant.user.email,
        {"username": participant.user.username, "event": participant.event},
    )


def send_participation_cancelled(participant):
    _send(
        "participation_cancelled",
        participant.user.email,
        {"username": participant.user.username, "event": participant.event},
    )


def send_reconfirmation_required(participant, changed_fields="", event_link=""):
    _send(
        "reconfirmation_required",
        participant.user.email,
        {
            "username": participant.user.username,
            "event": participant.event,
            "changed_fields": changed_fields,
            "event_link": event_link,
        },
    )


def send_reservation_expired(participant):
    _send(
        "reservation_expired",
        participant.user.email,
        {"username": participant.user.username, "event": participant.event},
    )
