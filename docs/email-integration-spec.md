# Email Integration Spec

This document records how email verification and email notifications
(required by ADR 002, API Layer) are implemented, and what's left to do
before a non-dev rollout. Dev/test functionality is done; the one
remaining task is picking a staging/prod transactional email provider
(§4).

## 1. Status summary

- **Email backend:** configured for dev (Mailpit via SMTP,
  `config/settings.py:153-165`). Staging/prod provider still to be
  chosen — see §4's to-do.
- **Async delivery:** RQ-based task queue configured (`RQ_QUEUES`,
  `config/settings.py:278-286`, `redis`/`worker` services in
  `docker-compose.yml`) — see §5.
- **Email verification:** fully implemented, including the login gate —
  see §2.
- **Password reset:** fully implemented — see §3.
- **Event/participation notifications:** every trigger in §3's table
  sends email through `apps/notifications/emails.py`.
- **Reconfirmation TTL expiration job:** implemented and scheduled via
  `CRONJOBS` — see §6.

## 2. Email verification

ADR 002 requires (see "Email verification" section of `002-api-layer.md`):

- Link-based verification.
- Tokens: single-use, cryptographically random, short-lived, stored
  server-side as a hash (or equivalent), invalidated after use, delivered
  and consumed over HTTPS.
- Login allowed only for verified users.

**Implemented:**

- `POST /api/v1/auth/email-verification/request/` and
  `.../confirm/` are wired in `apps/users/urls.py:12-21`, backed by
  `EmailVerificationRequestView`/`EmailVerificationConfirmView`
  (`apps/users/views.py:317-`, `346-`).
- `EmailVerificationToken` model (`apps/users/models.py:84`), storing a
  hashed token with `created_at`/`expires_at`/`used_at`.
- `User.is_email_verified` defaults to `False`
  (`apps/users/models.py:16`).
- The login gate: `LoginView.post` (`apps/users/views.py:127-141`)
  returns `403 email_not_verified` when `user.is_email_verified` is
  `False`, blocking unverified users.
- `EmailVerificationRequestView` generates a token and sends a
  verification email via `send_on_commit(send_email_verification, ...)`
  (`apps/users/views.py:330`), throttled by
  `EmailVerificationEmailThrottle`/`EmailVerificationIPThrottle`
  (`apps/users/throttles.py`).
- A separate GET, template-rendering `EmailVerificationConfirmPageView`
  exists per the ADR-deviation note below, alongside the JSON `POST`
  confirm endpoint.

**Design parameters:**

- Token TTL: **24 hours**.
- Rate limiting: per-email + per-IP throttle pattern
  (`EmailVerificationEmailThrottle`/`EmailVerificationIPThrottle`,
  `apps/users/throttles.py`, same architecture as
  `PasswordResetEmailThrottle`/`PasswordResetIPThrottle`).
- Link target: **server-rendered Django template**, not a separate
  frontend. The link in the email points directly at the Django server
  (`GET /api/v1/auth/email-verification/confirm/?token=<raw_token>` or
  an equivalent non-API-versioned path); the view verifies the token
  and renders a plain template ("Email verified" / "Link expired or
  already used"). No SPA/frontend project exists yet, so this avoids
  building one just to host a confirmation page — CORS/base-URL
  complexity is deferred until a real frontend exists, at which point
  this can be swapped without touching the token/model layer.

> **Note — deviates from ADR 002 as written:** ADR 002 lists
> `email-verification/confirm/` (and `password/reset/confirm/`) as
> `POST` JSON endpoints returning `200`. The server-rendered-template
> decision above means the *link the user clicks* hits a `GET`,
> template-rendering endpoint instead. The existing `POST .../confirm/`
> JSON endpoint is kept as-is (for non-browser/API clients and a future
> frontend to call directly), with a **separate** `GET`
> template-rendering route for the emailed link, which internally
> reuses the same confirm logic. ADR 002 should be updated to document
> this additional route.

## 3. Email notifications

ADR 002 doesn't fully enumerate notification events, but implies emails
accompany at least: registration, invitation, and reconfirmation-required
transitions (event date/format/location changes), plus password reset.

**By trigger:**

| Trigger | Code location | Status |
|---|---|---|
| Self-registration confirmed | `Event.register()` / `apps/events/views.py` (`send_registration_confirmed`) | Sends email |
| Organizer invites a user | `Event.invite()` / `apps/events/views.py` (`send_invitation_received`) | Sends email |
| `accept` | `apps/events/views.py` (`send_invitation_accepted`) | Sends email |
| `reject` | `apps/events/views.py` (`send_invitation_rejected`) | Sends email |
| `cancel` | `apps/events/views.py` (`send_participation_cancelled`) | Sends email |
| Event change → `RECONFIRMATION_REQUIRED` | `apps/events/views.py` (`send_reconfirmation_required`) | Sends email |
| Reconfirmation deadline expires | `apps/events/models.py` (`send_reservation_expired`) | Sends email |
| `POST /auth/password/reset/request/` | `apps/users/views.py` (`PasswordResetRequestView`) | Sends email |
| `POST /auth/password/reset/confirm/` | `apps/users/views.py` (`PasswordResetConfirmView`) | No email (confirmation is the response itself) |
| `POST /auth/password/change/` | `apps/users/views.py` (`PasswordChangeView`) | No email by design |

**Design parameters:**

- Recipient scope: **participant only**. The affected participant is
  emailed for every trigger in the table above (invited, registered,
  accepted/rejected/cancelled, reconfirmation-required). The organizer is
  **not** emailed on any of these — they see state changes via the API
  (participants list / event detail), not email. This keeps the
  notification layer to one recipient path per trigger; revisit if
  organizer-facing email becomes a real need later.
- Password reset token TTL: **1 hour** — shorter than the 24h email
  verification TTL, since a reset token is higher-stakes (account
  takeover risk if leaked/intercepted) than a verification token.
- Password reset rate limiting: `PasswordResetEmailThrottle`/
  `PasswordResetIPThrottle` (`apps/users/throttles.py`), wired into
  `PasswordResetRequestView`.

**Implemented:**

- Notification layer: `apps/notifications/emails.py`, one function per
  trigger, called via `send_on_commit(...)` (RQ-backed
  `transaction.on_commit` wrapper) from the model/view layer.
- Password reset token model: `PasswordResetToken`
  (`apps/users/models.py:94`, `expires_at = created_at + 1h`) +
  `PasswordResetRequestView`/`PasswordResetConfirmView`
  (`apps/users/views.py:424,453`).
- Delivery is async via RQ — see §5.

**Reset confirm page — differs from verification, needs a form:**

Unlike email verification, the reset link can't be a plain click-to-confirm
GET, because the user must submit a new password, not just prove token
possession. It's a **server-rendered Django form**, same
no-separate-frontend approach as §2's verification page, extended with an
HTML form instead of a bare confirmation:

- `GET /api/v1/auth/password-reset/confirm/?token=<raw_token>` (or an
  equivalent non-API-versioned path) — validates the token exists and
  isn't expired/used, renders an HTML form with "new password" +
  "confirm password" fields. If the token is invalid/expired, renders an
  error template instead of the form.
- `POST` to the same view (form submission, not JSON) — re-validates the
  token, validates the two password fields match and pass Django's
  password validators, calls `user.set_password(...)`, marks the token
  used, renders a "Password changed" template.
- The existing JSON `POST /api/v1/auth/password/reset/confirm/` endpoint
  (per ADR 002) is kept as-is for non-browser/API clients and a future
  frontend, same pattern as the verification deviation note in §2 — both
  routes share the same underlying confirm logic/service function.

### 3a. Email copy (plain text)

Draft copy for each email type below — plain-text only for now (no HTML
multipart), placeholders in `{braces}`. Subject lines are kept short and
scannable; body includes event context so the participant doesn't need
to click through to know what happened.

**1. Email verification** (§2)

```
Subject: Verify your email address

Hi {username},

Please verify your email address to activate your account:

{verification_link}

This link expires in 24 hours. If you didn't create an account, you
can ignore this email.
```

**2. Password reset request** (§3)

```
Subject: Reset your password

Hi {username},

We received a request to reset your password. Click the link below to
choose a new one:

{reset_link}

This link expires in 1 hour. If you didn't request this, you can
safely ignore this email — your password won't change.
```

**3. Registration confirmed** (self-registered on a public event)

```
Subject: You're confirmed for {event.title}

Hi {username},

You're confirmed for {event.title} on {event.date}.
{if event.format == offline}Location: {event.location}{else}This is an
online event.{endif}

See you there!
```

**4. Invitation received** (organizer invited)

```
Subject: You're invited to {event.title}

Hi {username},

{organizer.username} has invited you to {event.title} on {event.date}.

Accept or decline: {event_link}

If you don't respond, your invitation stays pending.
```

**5. Invitation accepted / participation confirmed**

```
Subject: You're confirmed for {event.title}

Hi {username},

Your spot for {event.title} on {event.date} is confirmed.
{if event.format == offline}Location: {event.location}{else}This is an
online event.{endif}
```

**6. Invitation rejected (confirmation of your own action)**

```
Subject: You declined {event.title}

Hi {username},

You've declined the invitation to {event.title}. If this was a
mistake, contact the organizer to be invited again.
```

**7. Participation cancelled (confirmation of your own action)**

```
Subject: Your spot for {event.title} is cancelled

Hi {username},

You've cancelled your participation in {event.title} on {event.date}.
{if event.access_type == public}You can register again any time before
the event.{else}You'll need a new invitation from the organizer to
join again.{endif}
```

**8. Reconfirmation required (event details changed)**

```
Subject: Action needed: {event.title} details changed

Hi {username},

The organizer changed the {date|format|location} for {event.title}.
Your confirmed spot now needs reconfirmation:

{event_link}

Please reconfirm or cancel within 24 hours, or your reservation may be
released to other participants.
```

Notes:
- `{event_link}` should point at the event detail page (frontend, once
  it exists) or the API resource, consistent with whatever link-target
  decision is made for that context — this doc doesn't have a frontend
  event-detail page decided yet, so treat as a placeholder.
- The conditional `{if ...}` blocks are pseudo-syntax for the spec —
  actual implementation renders these via Django template tags in the
  real `.txt` email templates (`apps/notifications/templates/...`).
- Reconfirmation email's "24 hours" note refers to the reservation TTL
  from §6, not the email-verification TTL from §2 — same number,
  different mechanism, worth calling out to avoid confusion during
  implementation.

## 4. Email backend

**Dev: done. Staging/prod: open TODO.**

### Dev: Mailpit via docker-compose (done)

`mailpit` service (`axllent/mailpit` image) runs alongside `db`/`app` in
`docker-compose.yml`:

```yaml
mailpit:
  image: axllent/mailpit:latest
  restart: unless-stopped
  ports:
    - "8025:8025"  # web UI — view caught emails at http://localhost:8025
    - "1025:1025"  # SMTP endpoint the app sends to
```

`app` depends on `mailpit` and uses Django's normal SMTP backend pointed
at it (`config/settings.py:153-165`). This gives clickable
verification/reset links in a real inbox UI during manual dev, without
touching a real provider or the network. Automated tests use Django's
`locmem` backend (Django's default for `TestCase`) so they can assert on
`django.core.mail.outbox` without depending on Mailpit being up.

### Staging/prod: transactional provider (TODO — blocks non-dev rollout)

- Settings needed: `EMAIL_BACKEND` (or Anymail backend), `EMAIL_HOST`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
  `DEFAULT_FROM_EMAIL`, plus a provider API key.
- **Action item: pick a provider before staging/prod rollout.**
  Candidates and tradeoffs:
  - **Amazon SES** — cheapest at scale, natural fit if AWS infra is used
    elsewhere.
  - **Postmark** — best deliverability reputation for transactional
    (non-marketing) mail, simple pricing.
  - **SendGrid** — generous free tier, widely used, most integrations.
  - `django-anymail` is recommended as the integration layer regardless
    of which is picked, so switching providers later doesn't touch
    call-site code.

## 5. Delivery mechanism (sync vs async)

**Implemented: RQ (Redis Queue), not Celery.**

`RQ_QUEUES` is configured (`config/settings.py:278-286`), `redis`/`worker`
services exist in `docker-compose.yml`, and every notification call site
uses `send_on_commit(...)` (`apps/notifications/emails.py`), which wraps
`transaction.on_commit(...)` around an RQ `enqueue(..., retry=Retry(max=3,
interval=[10, 60, 300]))` call, avoiding the risk of emailing on a
rolled-back transaction or blocking the request/response cycle.
`CRONJOBS` (`config/settings.py:256-259`) has two entries:
`cleanup_expired_tokens` and `expire_reconfirmations` (the §6 TTL job,
enqueuing email via the same RQ path).

**Rationale for RQ over Celery:**

- The async need is narrow — send-email-after-commit, plus one periodic
  job for §6's TTL expiration. Celery's extra machinery (broker tuning,
  beat scheduler, Flower monitoring) isn't justified at this scope; RQ's
  `job.delay()`-style API gets there with far less setup and fewer
  moving parts to operate.
- Redis is the only new piece of infra either option needs, so the
  choice is really "how much framework on top of Redis," and RQ is the
  lighter one for a project this size.
- The one thing RQ lacks out of the box is a periodic/cron scheduler —
  needed for §6. Uses the project's existing `CRONJOBS` mechanism
  (`django-crontab`, already used for `cleanup_expired_tokens`) to
  enqueue an expiration-check RQ job on a schedule, rather than adding
  `rq-scheduler`. This avoids a second scheduling system alongside one
  already in place and proven.
- If the app's async needs grow substantially beyond email + this one
  periodic job (e.g. multi-queue priority, complex retry/routing), it's
  worth revisiting Celery then — but that isn't justified today.

**Worker deployment:** `redis` and `worker` services run alongside
`db`/`app`/`mailpit` in `docker-compose.yml`. `worker` uses the same
application image as `app`, but runs `python manage.py rqworker default`
instead of the web server, keeping dev/staging/prod symmetric with how
`db`/`app` are already run:

```yaml
redis:
  image: redis:7-alpine
  restart: unless-stopped

worker:
  build: .
  command: python manage.py rqworker default
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_started
  environment:
    # same DB/Redis/email env vars as `app`
  volumes:
    - .:/app
```

`RQ_QUEUES` in `settings.py` points at the `redis` service (`REDIS_URL`
env var, defaulting to `redis://redis:6379/0` in compose).

**Retry/failure policy:**

- Retries: fixed retries with backoff, via RQ's built-in `Retry` —
  `Retry(max=3, interval=[10, 60, 300])` (3 attempts, growing delay
  between each). Covers transient provider/network blips without extra
  infra.
- Dead-letter handling: none custom — jobs that exhaust all retries fall
  into RQ's built-in `FailedJobRegistry` automatically. Inspectable via
  `rq info` / `python manage.py rqstats` if/when needed; no dedicated
  Django model or admin view for this now.
- Alerting: **log only**. Failures (each retry, and final exhaustion)
  go through Django's normal logging, visible in worker process logs.
  No external alerting service (e.g. Sentry) wired up — matches the
  project's current lack of any monitoring infra. Revisit if/when
  monitoring is introduced more broadly, not as an email-specific
  addition.

## 6. RECONFIRMATION_REQUIRED TTL expiration job

ADR 001/002 specify a 24-hour reservation TTL for
`RECONFIRMATION_REQUIRED` capacity holds, with expiration releasing the
slot.

**Implemented.** `expire_reconfirmations()`
(`apps/events/models.py:316-353`) runs the expiration check, releases the
slot, and calls `send_on_commit(send_reservation_expired, participant)`
(line 353). It's invoked via the `expire_reconfirmations` management
command, scheduled every 15 minutes through `CRONJOBS`
(`config/settings.py:258`). Covered by
`apps/events/tests/test_notifications.py`.

## 7. Functionality checklist

- [x] Email backend for dev (Mailpit)
- [x] Email verification: token model, request/confirm views, login gate
- [x] Password reset: token model, request/confirm views/pages
- [x] Event/participation notification emails: register, invite,
      accept/reject/cancel, reconfirmation-required
- [x] Async delivery via RQ, with retry policy
- [x] RECONFIRMATION_REQUIRED TTL expiration job, including its
      notification email
- [ ] **Pick and configure a staging/prod email provider** (§4: SES vs.
      Postmark vs. SendGrid, via `django-anymail`)

## 8. API endpoints reference

Brief request/response shapes for the email-related endpoints; see
[api-reference.md](api-reference.md) for the rest of the API and its
conventions (auth, error shape, CSRF).

| Endpoint | Auth | Body | Response |
|---|---|---|---|
| `POST /api/v1/auth/email-verification/request/` | Public | `{ email }` | **200** always (no account-existence leak); queues verification email if the address is registered |
| `POST /api/v1/auth/email-verification/confirm/` | Public | `{ token }` | **200** verified / **400** `invalid_token` |
| `GET /auth/email-verification/confirm/?token=...` | Public | — (query param) | HTML page: "Email verified" / "Link expired or already used" — the emailed link's landing target, not under `/api/v1/` |
| `POST /api/v1/auth/password/reset/request/` | Public | `{ email }` | **200** always (no leak); queues reset email if registered |
| `POST /api/v1/auth/password/reset/confirm/` | Public | `{ token, new_password }` | **200** password changed / **400** `invalid_token` |
| `GET/POST /auth/password-reset/confirm/?token=...` | Public | form: new password + confirm | HTML form → "Password changed" — the emailed link's landing target, not under `/api/v1/` |
| `POST /api/v1/auth/password/change/` | Cookie + CSRF | `{ old_password, new_password }` | **200**, no email sent (see §3) |
