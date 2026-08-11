# Business Logic Spec

This document is a reference for the current, agreed business rules of the
event-manager application — consolidated from `docs/adr/001-django-models.md`,
`docs/adr/002-api-layer.md`, and `docs/adr/003-jwt-authentication.md` — plus
an explicit account of what is actually implemented in code today versus
still an open stub. Where ADRs disagreed across drafts, this document states
only the final, reconciled rule; see the ADRs themselves for the rationale
and history behind each decision.

## 1. Overview

Three core entities: `User` (custom, email-authenticated), `Event`
(organizer-owned, public or private, capacity-limited), and
`EventParticipant` (join table with a status state machine). The API is
versioned under `/api/v1/` and authenticates via JWT delivered through
HttpOnly cookies (with an `Authorization: Bearer` header fallback for
non-browser clients). PostgreSQL is required in all environments, not just
production, because several rules below depend on real row-level locking.

## 2. Domain model

### User (`accounts.User`, extends `AbstractUser`)

- `id`: `BigAutoField` (plain integer PK — not UUID; users are never
  addressed by id-in-URL, only by email).
- `email`: unique, login identifier (`USERNAME_FIELD`).
- `username`: required but **not unique** (display name only).
- `is_email_verified`: gates login (see §8).
- Hard delete only — no soft-delete/deactivate path.

### Event

- `id`: UUID PK (appears in public URLs; sequential ints would leak counts).
- `title` (required), `description` (optional), `date` (timestamptz).
- `format`: `online` | `offline`.
- `location`: **required** when `format=offline`, **must be null** when
  `format=online`. Enforced both in `Event.clean()` and a DB
  `CheckConstraint` (`offline_requires_location`).
- `access_type`: `public` | `private`. Required at creation, **immutable**
  after.
- `capacity`: integer, must be `> 0` (`PositiveIntegerField` +
  `CheckConstraint capacity_positive`).
- `organizer`: FK to `User`, `on_delete=PROTECT` — deleting a user who still
  organizes an event raises `ProtectedError`; callers must catch it and
  surface a clear error instead of a 500.
- `created_at`, `updated_at`.

### EventParticipant

- `id`: UUID PK.
- `event` FK (`on_delete=CASCADE`), `user` FK (`on_delete=CASCADE`).
- `status`: `invited | confirmed | rejected | cancelled |
  reconfirmation_required`.
- `updated_at`.
- `UniqueConstraint(event, user)` — one participation row per user per event.
- Index on `(event, status)` — backs the capacity count-and-decide (§5).

## 3. Participant state machine

```text
INVITED
  ├── accept  → CONFIRMED
  └── reject  → REJECTED

CONFIRMED
  ├── cancel  → CANCELLED
  └── event date/format/location changed → RECONFIRMATION_REQUIRED

RECONFIRMATION_REQUIRED
  ├── accept  → CONFIRMED
  └── cancel  → CANCELLED
```

- `CANCELLED` is terminal with respect to event-change side effects: a
  cancelled participant is **not** moved to `RECONFIRMATION_REQUIRED` by a
  later event edit.
- `cancel()` is only valid from `CONFIRMED` or `RECONFIRMATION_REQUIRED`;
  any other starting state raises `ValueError`.
- `accept`/`reject`/`cancel` are **idempotent for the same resulting
  state** at the API layer: a repeat call that matches the current state
  returns `200` with the current representation rather than erroring.
- `register` and `invite` are **not** idempotent resource-creation
  operations — repeat/conflicting calls return `409 Conflict`.

## 4. Registration & invitation flows

Two distinct entry points create `EventParticipant` rows; both must go
through model methods (`Event.register()`, `Event.invite()`,
`EventParticipant.accept()/reject()/cancel()`) — never raw
`EventParticipant.objects.create()/.update()` — since capacity,
terminal-state, locking, and date-window guarantees only hold inside them.

**Self-registration** — `register(user)`:
- Valid only for `access_type=PUBLIC` (`ValueError` otherwise).
- Rejects once `Event.date` is in the past.
- A prior `INVITED` row blocks it: raises `EventParticipant.InvitationPending`
  (API: `409`, code `invitation_pending`) rather than silently confirming.
- An existing `CONFIRMED` row raises `EventParticipant.AlreadyFinalized`.
- A prior `REJECTED` or `CANCELLED` row is allowed to proceed back to
  `CONFIRMED`, subject to the capacity check.

**Invitation** — `invite(user, *, by)`:
- Organizer-only (`PermissionError` if `by != event.organizer`).
- Available on **both** public and private events.
- Duplicate invite raises `EventParticipant.AlreadyInvited` (checked
  up front, and `IntegrityError` caught as a concurrency backstop).
- Not restricted by `Event.date` (organizers may record invites for past
  events administratively).
- Does **not** reserve capacity at invite time (organizers may over-invite
  by design) — capacity is enforced later, at `accept()`.

**Re-entry after `CANCELLED`:**
- Public event: `register()` may bring the participant back to `CONFIRMED`.
- Private event: `register()` is never valid; only a fresh `invite()` from
  the organizer can bring them back in. This must never be bypassed.

**Capacity enforcement:**
- Hard cap on *effective participation* = `CONFIRMED` +
  `RECONFIRMATION_REQUIRED` rows.
- Checked at both `register()` and `accept()` (not at `invite()`), each
  under `select_for_update()` + `transaction.atomic()`.
- `accept()` raises `EventParticipant.EventFull` (API: `409`, code
  `capacity_exceeded`) if the event filled up between invite and accept.

**Reconfirmation reservation:** when a `CONFIRMED` participant is moved to
`RECONFIRMATION_REQUIRED` because the organizer changed `date`, `format`,
or `location`:
- Their capacity slot is **not** released — it stays reserved.
- Reservation TTL: 24 hours from entering the state, capped at
  `min(created + 24h, event.date)`.
- Released by explicit `cancel()` or by TTL expiration.
- Resolved to a permanent `CONFIRMED` slot by `accept()`.
- The expiration mechanism itself (scheduled task vs. lazy check) is not
  built yet — see §9/§10.

## 5. Concurrency & data-integrity rules

- All capacity count-and-decide logic runs inside `transaction.atomic()`
  with `select_for_update()` on the `Event` row, so concurrent
  `register()`/`accept()`/`invite()` calls for the same event serialize
  instead of racing.
- This requires real row-level locking — enforced at runtime via a
  `connection.vendor != "postgresql"` guard (`ImproperlyConfigured`).
  SQLite is not used anywhere, including local dev, because it would
  silently make the lock a no-op.
- Three bugs a naive implementation would have, all fixed by the above plus
  explicit status checks:
  - **Capacity race** — two concurrent registrations both reading
    "room available" before either writes.
  - **Terminal-state overwrite** — a naive `update_or_create` silently
    flipping an already-`CONFIRMED` row back to `CONFIRMED`, bypassing
    cancellation semantics.
  - **Duplicate-invite crash** — a raw, unhandled `IntegrityError` on a
    repeated `invite()`; now caught and re-raised as `AlreadyInvited`.

## 6. API surface

Base path: `/api/v1/`.

### Authentication

| Method | Endpoint | Purpose | Success |
|---|---|---|---|
| POST | `/auth/register/` | Create account | 201 |
| POST | `/auth/login/` | Login, issue auth cookies | 200 |
| POST | `/auth/logout/` | Logout, revoke refresh-token family | 204 |
| POST | `/auth/refresh/` | Rotate refresh token, issue access token | 200 |
| POST | `/auth/email-verification/request/` | Request verification email | 200 |
| POST | `/auth/email-verification/confirm/` | Verify email via link | 200 |
| POST | `/auth/password/change/` | Change password | 200 |
| POST | `/auth/password/reset/request/` | Request password reset | 200 |
| POST | `/auth/password/reset/confirm/` | Complete password reset | 200 |

### Users

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/users/me/` | Get current user |
| PATCH | `/users/me/` | Update current user |

### Events

| Method | Endpoint | Purpose | Success |
|---|---|---|---|
| GET | `/events/` | List events (filter + paginate) | 200 |
| POST | `/events/` | Create event | 201 |
| GET | `/events/{id}/` | Get event detail | 200 |
| PATCH | `/events/{id}/` | Update event | 200 |
| DELETE | `/events/{id}/` | Delete event | 204 |
| POST | `/events/{id}/register/` | Self-register (public events) | 201 |

### Invitations & participation

| Method | Endpoint | Purpose | Success |
|---|---|---|---|
| POST | `/events/{id}/invite/` | Organizer invites a user | 201 |
| POST | `/events/{id}/accept/` | Accept invite / reconfirm | 200 |
| POST | `/events/{id}/reject/` | Reject invitation | 200 |
| POST | `/events/{id}/cancel/` | Cancel participation | 200 |
| GET | `/events/{id}/participants/` | List participants (visibility rules apply) | 200 |

Admin-specific endpoints are not yet specced beyond what's above.

### Filtering & pagination

- Event list filters: `organizer_username`, `date`, `capacity`, `search`
  (case-insensitive substring match against `title` or `description`).
- Page-based pagination: `?page=2&page_size=20`.
- Participant list filter: `?status=confirmed`.

### Error format

Field-level validation errors (DRF style):

```json
{ "email": ["This field is required."] }
```

Business/domain errors:

```json
{ "error": { "code": "capacity_exceeded", "message": "Event capacity has been reached." } }
```

Known business error codes: `capacity_exceeded` (409), `invitation_pending`
(409), `email_not_verified` (403).

### HTTP semantics

`201` created · `200` successful update/action · `204` deletion/idempotent
logout with no body · `403` visible resource, insufficient permission ·
`404` used instead of `403` when private-resource existence must not leak ·
`409` valid request conflicting with current domain state.

## 7. Visibility & permissions

**Public events:** anonymous users may list/view; authenticated users may
additionally self-register; organizers manage their own events.

**Private events:** visible only to the organizer or a confirmed
participant. Not discoverable by anyone else → `404` (not `403`) on both
list and detail. An authenticated user who *can* see the event but lacks
permission for the attempted operation gets `403`.

**Participant list (`GET /events/{id}/participants/`):**
- Organizer/admin: full data, including email and status.
- Confirmed participant: usernames only, of confirmed/non-cancelled
  participants — no status, no email.
- Anonymous: no data.
- Private-event visibility rules apply before any participant data is
  exposed at all.

**Event representation `my_participation`:** authenticated users get
`{"my_participation": {"status": "confirmed"}}` or `{"my_participation":
null}`; anonymous users get the field omitted entirely.

## 8. Authentication & session security

- Access token: 15 min lifetime. Refresh token: 7 days.
- Cookies: `access_token`, `refresh_token`; `HttpOnly` always;
  `Secure` whenever `DEBUG` is off; `SameSite=Lax`.
- `refresh_token` is path-scoped to `/api/v1/auth/`; `access_token` uses the
  default path (`/`).
- Dual channel: `Authorization: Bearer` header checked first, cookie used
  only as fallback — lets non-browser clients skip cookies entirely.
- CSRF: `SameSite=Lax` is the primary control; a double-submit CSRF token
  is additionally required on cookie-authenticated, state-changing
  (`POST`/`PATCH`/`DELETE`) requests. Header-authenticated requests are
  exempt (not cookie-based, not CSRF-exposed).
- Signing: dedicated `SIGNING_KEY` (separate from Django's `SECRET_KEY`),
  algorithm pinned to `HS256`.
- Access-token revocation: **accepted risk** — no per-token denylist, so a
  leaked access token stays valid for up to its full 15-minute lifetime
  even after logout/revocation. This bounds the exposure window and is why
  the lifetime is kept short.
- Refresh-token reuse detection: refresh tokens belong to a "family"
  (one chain per login). Rotation marks the presented token's record
  `used`; a token presented again after being marked `used` is reuse —
  the whole family is revoked. Logout revokes the family outright, not just
  the presented token. `BLACKLIST_AFTER_ROTATION` is enabled as
  defense-in-depth on top of this.
- Login requires `user.is_email_verified`; unverified users get `403` with
  code `email_not_verified` and no token/cookie is issued.
- Rate limiting: `login`/`password/reset/request` throttled per-email plus
  a stricter per-IP secondary throttle; `refresh` and `register` throttled
  per-IP. All stricter than DRF's default throttle scope; `429` on trip.

## 9. Implementation status

| Feature | Status | Notes |
|---|---|---|
| User/Event/EventParticipant models, constraints, indexes | Implemented | `apps/users/models.py`, `apps/events/models.py` |
| `register()` model method (capacity, locking, terminal-state) | Implemented | `apps/events/models.py` |
| `EventRegisterView` (`POST /events/{id}/register/`) | **Stub — 501** | `apps/events/views.py::EventRegisterView` |
| `EventInviteView` (`POST /events/{id}/invite/`) | **Stub — 501** | `apps/events/views.py::EventInviteView` |
| `EventAcceptView` (`POST /events/{id}/accept/`) | **Stub — 501** | `apps/events/views.py::EventAcceptView` |
| `EventRejectView` (`POST /events/{id}/reject/`) | **Stub — 501** | `apps/events/views.py::EventRejectView` |
| `EventCancelView` (`POST /events/{id}/cancel/`) | **Stub — 501** | `apps/events/views.py::EventCancelView` |
| Private-event visibility (404 when undiscoverable) | **Partial** | Not applied in `EventListView`/`EventDetailView` querysets yet |
| Participant-list visibility tiers | **Partial (stub)** | `apps/events/permissions.py` doesn't yet distinguish organizer/admin vs. confirmed-member; `GET .../participants/` returns 501 pending this |
| `my_participation` on event representation | **Partial** | Serializer field always returns `None`; not wired to requesting user (`apps/events/serializers.py`) |
| `RECONFIRMATION_REQUIRED` TTL expiration mechanism | Not built | Counting rule is implemented; the timer/expiry job is not (§4) |
| JWT issuance, cookie delivery, dual auth channel | Implemented | `apps/users/authentication.py` |
| Dedicated `SIGNING_KEY` / pinned `HS256` | Implemented | `config/settings.py` |
| Refresh-cookie path scoping | Implemented | `apps/users/views.py` |
| CSRF double-submit (`CookieCSRFPermission`) | Implemented | `apps/users/permissions.py` |
| Refresh-token reuse detection / family revocation | Implemented | `RefreshTokenFamily`, `RefreshTokenRecord` in `apps/users/models.py` |
| `cleanup_expired_tokens` job | Implemented | Scheduled hourly via `django-crontab` |
| Email-verification login gate | Implemented | `LoginView` checks `is_email_verified` |
| `EmailVerificationRequestView` / `ConfirmView` | **Stub — 501** | `apps/users/views.py` |
| `PasswordChangeView` | **Stub — 501** | `apps/users/views.py` |
| `PasswordResetRequestView` / `ConfirmView` | **Stub — 501** | `apps/users/views.py` |
| Auth-endpoint rate limiting | Implemented | `apps/users/throttles.py` |

## 10. Open items / deferred

- **`RECONFIRMATION_REQUIRED` TTL expiration** — counting rule is final;
  the actual timer/lazy-expiry mechanism is an open implementation TODO.
- **Invite-time capacity** — intentionally uncapped; organizers may
  over-invite freely, capacity only bites at `accept()`.
- **`User` soft-delete** — considered and explicitly dropped; deletion is a
  real, irreversible `DELETE`. Revisit only if account recovery or
  delete-auditing becomes a real requirement.
- **`is_email_verified` default** — currently `True` as a stopgap because
  the verification send/confirm flow is still a 501 stub; flip to `False`
  once that flow ships, per ADR 003.
- **Admin-specific endpoints** — not specced beyond the inventory in §6
  until concrete admin use cases require them.
