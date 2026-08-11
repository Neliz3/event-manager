# Email Integration Spec (Gap Analysis)

This document records what ADR 002 (API Layer) requires for email
verification and email notifications versus what is actually implemented
in code today. It is a gap list to drive future work, not a record of
decisions already made — unlike the ADRs, nothing here is "Accepted" yet.

## 1. Current state summary

No email backend is configured anywhere in the project (`config/settings.py`
has no `EMAIL_BACKEND`/`EMAIL_*` settings), and there is no `send_mail`,
`EmailMessage`, or task-queue (Celery/etc.) usage anywhere under `apps/`.
Every endpoint that should trigger an email is either a `501` stub or
silently omits the notification.

## 2. Email verification — gap detail

ADR 002 requires (see "Email verification" section of `002-api-layer.md`):

- Link-based verification.
- Tokens: single-use, cryptographically random, short-lived, stored
  server-side as a hash (or equivalent), invalidated after use, delivered
  and consumed over HTTPS.
- Login allowed only for verified users.

**Implemented:**

- `POST /api/v1/auth/email-verification/request/` and
  `.../confirm/` are wired in `apps/users/urls.py:12-21`.
- The login gate exists: `LoginView.post`
  (`apps/users/views.py:127-141`) returns `403 email_not_verified` when
  `user.is_email_verified` is `False`.

**Not implemented:**

- Both verification views subclass `_NotImplementedAuthView`
  (`apps/users/views.py:220-232`) and return `501 Not Implemented` after
  only validating the input serializer — no token issuance, no
  verification, no side effect.
- No verification-token model exists (`apps/users/models.py` only has
  `User`, `RefreshTokenFamily`, `RefreshTokenRecord`).
- `User.is_email_verified` defaults to `True`
  (`apps/users/models.py:13`), with a TODO comment noting it should
  default to `False` once verification is real. As a result the login
  gate above is currently inert — no user is ever actually blocked.
- No email is ever sent for verification.

**Decided parameters:**

- Token TTL: **24 hours**.
- Rate limiting: reuse the existing per-email + per-IP throttle pattern
  (`PerEmailThrottle`/`LoginIPThrottle` subclasses in
  `apps/users/throttles.py`, same architecture as
  `PasswordResetEmailThrottle`/`PasswordResetIPThrottle`) — add
  `EmailVerificationEmailThrottle`/`EmailVerificationIPThrottle` with
  their own `DEFAULT_THROTTLE_RATES` scope/rate.
- Link target: **server-rendered Django template**, not a separate
  frontend. The link in the email points directly at the Django server
  (e.g. `GET /api/v1/auth/email-verification/confirm/?token=<raw_token>`
  or an equivalent non-API-versioned path); the view verifies the token
  and renders a plain template ("Email verified" / "Link expired or
  already used"). No SPA/frontend project exists yet, so this avoids
  building one just to host a confirmation page — CORS/base-URL
  complexity is deferred until a real frontend exists, at which point
  this can be swapped without touching the token/model layer.

**To close the gap:**

1. Add an `EmailVerificationToken` model: FK to `User`, hashed token
   value, `created_at`, `expires_at` (`created_at + 24h`), `used_at`
   (nullable).
2. Flip `User.is_email_verified` default to `False`.
3. Implement `EmailVerificationRequestView`: generate a cryptographically
   random token, store its hash, send a verification email containing an
   HTTPS link with the raw token; throttle via
   `EmailVerificationEmailThrottle`/`EmailVerificationIPThrottle`.
4. Implement `EmailVerificationConfirmView` (GET, template-rendering, not
   JSON): look up by hashed token, check not expired/used, mark
   `is_email_verified = True`, mark token used, invalidate other
   outstanding tokens for that user, render result template.
5. Wire an email backend (see §4).

> **Note — deviates from ADR 002 as written:** ADR 002 lists
> `email-verification/confirm/` (and `password/reset/confirm/`) as
> `POST` JSON endpoints returning `200`. The server-rendered-template
> decision above means the *link the user clicks* hits a `GET`,
> template-rendering endpoint instead. Recommend keeping the existing
> `POST .../confirm/` JSON endpoint as-is (for non-browser/API clients
> and a future frontend to call directly), and adding a **separate**
> `GET` template-rendering route for the emailed link, which internally
> reuses the same confirm logic. ADR 002 should be updated to document
> this additional route once implemented.

## 3. Email notifications — gap detail

ADR 002 doesn't fully enumerate notification events, but implies emails
accompany at least: registration, invitation, and reconfirmation-required
transitions (event date/format/location changes), plus password reset.
All of these now send email; the table below is kept as a record of
trigger → code location, not a "still missing" list.

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
| `POST /auth/password/reset/request/` | `apps/users/views.py` (`PasswordResetRequestView`) | Implemented; sends email |
| `POST /auth/password/reset/confirm/` | `apps/users/views.py` (`PasswordResetConfirmView`) | Implemented; no email (confirmation is the response itself) |
| `POST /auth/password/change/` | `apps/users/views.py` (`PasswordChangeView`) | Implemented; no email is sent on this trigger by design |

**Decided parameters:**

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
- Password reset rate limiting: reuse
  `PasswordResetEmailThrottle`/`PasswordResetIPThrottle`
  (`apps/users/throttles.py:34,50`) as-is — they already exist and just
  need wiring into the currently-`501`-stub reset views.

**To close the gap:**

1. Choose and configure an email backend (see §4).
2. Add a small notification layer (e.g. `apps/notifications/emails.py`)
   with one function per trigger, called from the model/view layer after
   the DB state change commits (ideally via `transaction.on_commit` to
   avoid emailing on a rolled-back transaction). Each function takes the
   participant (not the organizer) as its recipient.
3. Implement password reset token model (mirrors
   `EmailVerificationToken` from §2, but `expires_at = created_at + 1h`)
   + views, reusing the existing `PasswordResetEmailThrottle`/
   `PasswordResetIPThrottle`, including the reset email itself.
4. Decide synchronous vs. async sending — see §5.

**Reset confirm page — differs from verification, needs a form:**

Unlike email verification, the reset link can't be a plain click-to-confirm
GET, because the user must submit a new password, not just prove token
possession. Decided: a **server-rendered Django form**, same
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

**Decided: Mailpit for dev, SMTP via a transactional provider (SES,
SendGrid, Postmark, etc. — likely fronted by `django-anymail`) for
staging/prod.**

### Dev: Mailpit via docker-compose

Add a `mailpit` service (`axllent/mailpit` image) to `docker-compose.yml`,
alongside the existing `db`/`app` services:

```yaml
mailpit:
  image: axllent/mailpit:latest
  restart: unless-stopped
  ports:
    - "8025:8025"  # web UI — view caught emails at http://localhost:8025
    - "1025:1025"  # SMTP endpoint the app sends to
```

`app` should `depends_on: mailpit` and use Django's normal SMTP backend
pointed at it:

```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mailpit")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 1025))
EMAIL_USE_TLS = False
```

This gives clickable verification/reset links in a real inbox UI during
manual dev, without touching a real provider or the network. Automated
tests should still use Django's `locmem` backend
(`django.core.mail.backends.locmem.EmailBackend`, Django's default for
`TestCase`) so they can assert on `django.core.mail.outbox` without
depending on Mailpit being up.

### Staging/prod: transactional provider

- Settings needed: `EMAIL_BACKEND` (or Anymail backend), `EMAIL_HOST`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
  `DEFAULT_FROM_EMAIL`, plus a provider API key.
- **Provider choice is still open — explicitly deferred.** Candidates
  and tradeoffs, for whoever makes this call before staging/prod
  rollout:
  - **Amazon SES** — cheapest at scale, natural fit if AWS infra is used
    elsewhere.
  - **Postmark** — best deliverability reputation for transactional
    (non-marketing) mail, simple pricing.
  - **SendGrid** — generous free tier, widely used, most integrations.
  - `django-anymail` is recommended as the integration layer regardless
    of which is picked, so switching providers later doesn't touch
    call-site code.

## 5. Delivery mechanism (sync vs async)

Sending email inline in the request/response cycle blocks the API and
risks partial failure (DB committed, email failed silently or vice
versa). Recommend:

- Introduce a task queue for outbound email, OR
- At minimum, wrap sends in `transaction.on_commit(...)` and catch/log
  failures without failing the triggering request.

No task queue currently exists in the project (`config/settings.py` has
no Celery/broker config); `CRONJOBS` (`config/settings.py:238-239`) only
runs `cleanup_expired_tokens` and is unrelated to email sending.

**Decided: RQ (Redis Queue), not Celery.** Rationale:

- Current async need is narrow — send-email-after-commit, plus the one
  future periodic job for §6's TTL expiration. Celery's extra machinery
  (broker tuning, beat scheduler, Flower monitoring) isn't justified at
  this scope; RQ's `job.delay()`-style API gets there with far less
  setup and fewer moving parts to operate.
- Redis is the only new piece of infra either option needs, so the
  choice is really "how much framework on top of Redis," and RQ is the
  lighter one for a project this size.
- The one thing RQ lacks out of the box is a periodic/cron scheduler —
  needed for §6. **Decided: use the project's existing `CRONJOBS`
  mechanism** (`django-crontab`, already used for
  `cleanup_expired_tokens` per `config/settings.py:238-239`) to enqueue
  an expiration-check RQ job on a schedule, rather than adding
  `rq-scheduler`. This avoids introducing a second scheduling system
  when one is already in place and proven.
- If the app's async needs grow substantially beyond email + this one
  periodic job (e.g. multi-queue priority, complex retry/routing), it's
  worth revisiting Celery then — but that isn't justified today.

**Worker deployment:** add a `redis` service and a `worker` service to
`docker-compose.yml`, alongside the existing `db`/`app`/`mailpit`
services. `worker` uses the same application image as `app`, but runs
`python manage.py rqworker default` instead of the web server, keeping
dev/staging/prod symmetric with how `db`/`app` are already run (rather
than a separately-managed process outside docker-compose).

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

`RQ_QUEUES` in `settings.py` should point at the `redis` service
(`REDIS_URL` env var, defaulting to `redis://redis:6379/0` in compose).

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

## 6. Related but distinct gap: RECONFIRMATION_REQUIRED TTL

Not an email gap per se, but adjacent: ADR 001/002 specify a 24-hour
reservation TTL for `RECONFIRMATION_REQUIRED` capacity holds, with
expiration releasing the slot. This timer/expiration mechanism is not
implemented (`apps/events/models.py:293` notes it as a known TODO), and
there is no scheduled job for it. When implemented, expiration should
likely also trigger a notification email ("your reservation expired").

## 7. Suggested implementation order

1. Pick and configure an email backend (§4) — unblocks everything else.
2. Email verification (§2): token model, request/confirm views, flip
   `is_email_verified` default.
3. Password reset (§3): token model, request/confirm views.
4. Event/participation notification emails (§3): register, invite,
   accept/reject/cancel, reconfirmation-required.
5. RECONFIRMATION_REQUIRED TTL expiration job (§6), including its
   notification email.
