# API Reference

Hand-written companion to the generated OpenAPI docs (`/api/docs/`,
`/api/redoc/`, `/api/schema/` — see the [README](../README.md#api-docs)).
Those are the source of truth for exact request/response schemas; this file
is for browsing the whole surface at once and understanding the
business-rule behavior (status codes, idempotency, visibility rules) that a
bare schema doesn't explain.

All JSON endpoints are versioned under `/api/v1/`. Error responses use a
consistent shape unless noted otherwise:

```json
{ "error": { "code": "some_code", "message": "Human-readable explanation." } }
```

## Auth

Cookie-based JWT auth (ADR 003). `POST /auth/login/` sets three cookies:

| Cookie | HttpOnly | Purpose |
|---|---|---|
| `access_token` | yes | Short-lived JWT, sent automatically on every request |
| `refresh_token` | yes | Long-lived, scoped to `/api/v1/auth/refresh/` via `Path` |
| `csrf` | **no** | Double-submit CSRF token; JS reads it and echoes it back as `X-CSRF-Token` |

State-changing requests authenticated via cookie (not `Authorization:
Bearer`) must send `X-CSRF-Token` matching the `csrf` cookie
(`CookieCSRFPermission`), or they're rejected. Non-browser clients using an
`Authorization` header are exempt from this check.

Refresh-token reuse (a rotated-out token replayed) revokes the entire
token family, forcing re-login.

### `POST /api/v1/auth/register/`

Create an account. Public.

| Field | Type | Notes |
|---|---|---|
| `email` | string | unique |
| `username` | string | |
| `password` | string | validated against Django's password validators |

**201** → `{ id, email, username }`. Does not log the user in or send a
verification email itself — see `email-verification/request/`.

### `POST /api/v1/auth/login/`

Public. Body: `{ email, password }`.

- **200** → `{ id, email, username }` + sets auth cookies.
- **403** `email_not_verified` — credentials are correct but
  `is_email_verified` is `false`.
- **401** — invalid credentials (via DRF's standard `AuthenticationFailed`).

Throttled per-email and per-IP.

### `POST /api/v1/auth/logout/`

Auth required (cookie + CSRF). No body. Revokes the current refresh-token
family and blacklists the token. **204**, clears all three cookies.

### `POST /api/v1/auth/refresh/`

Public (reads the `refresh_token` cookie itself — no `Authorization`
needed). No body.

- **200** — rotates the refresh token, re-sets all three cookies.
- **401** — missing/invalid/unknown/revoked token, or **reuse detected**
  (a previously-rotated-out token replayed), which also revokes the whole
  family.

### `POST /api/v1/auth/email-verification/request/`

Public. Body: `{ email }`. Always **200** regardless of whether the email
is registered (doesn't leak account existence). If it is, queues a
verification email with a link to `/auth/email-verification/confirm/`.
Throttled per-email and per-IP.

### `POST /api/v1/auth/email-verification/confirm/`

Public. Body: `{ token }`.

- **200** — token valid; sets `is_email_verified = true`, invalidates the
  user's other outstanding verification tokens.
- **400** `invalid_token` — invalid, expired, or already used.

`GET /auth/email-verification/confirm/?token=...` is the server-rendered,
non-JSON equivalent the emailed link points to (not under `/api/v1/`).

### `POST /api/v1/auth/password/change/`

Auth required (cookie + CSRF). Body: `{ old_password, new_password }`.

- **200** — password changed; every refresh token family for the user is
  revoked, including the current session, and the caller's own auth
  cookies are cleared — the caller must log in again too, same as every
  other session.
- **400** `invalid_old_password` — `old_password` doesn't match.

### `POST /api/v1/auth/password/reset/request/`

Public. Body: `{ email }`. Same non-leaking **200**-always behavior as
email-verification request; queues a reset email linking to
`/auth/password-reset/confirm/`. Throttled per-email and per-IP.

### `POST /api/v1/auth/password/reset/confirm/`

Public. Body: `{ token, new_password }`.

- **200** — password changed, token consumed.
- **400** `invalid_token` — invalid, expired, or already used.

`GET`/`POST /auth/password-reset/confirm/?token=...` is the server-rendered
form the emailed link points to (not under `/api/v1/`) — unlike email
verification this needs an actual form since the user supplies a new
password.

### `GET/PATCH /api/v1/users/me/`

Auth required (cookie + CSRF for `PATCH`). Returns/updates
`{ id, email, username }`; `id` and `email` are read-only on `PATCH`.

## Events

### `GET /api/v1/events/`

List events visible to the requester. Public events are visible to
everyone; private events are visible only to their organizer or a
`CONFIRMED` participant. Anonymous users see only public events.

Query params: `organizer_username`, `date` (matches the date part of the
`date` field), `capacity` (exact match), `search` (case-insensitive
substring match against `title` or `description`).

Each item includes `my_participation` (the requester's own participant
`status`, or `null` if none) for authenticated requests; the field is
omitted entirely for anonymous requests.

### `POST /api/v1/events/`

Auth required. Body:

| Field | Type | Notes |
|---|---|---|
| `title` | string | required, non-blank |
| `description` | string | optional |
| `date` | datetime | required |
| `format` | `online` \| `offline` | required |
| `location` | string | required if `format=offline`, must be null if `online` |
| `access_type` | `public` \| `private` | required, **immutable** after creation |
| `capacity` | int | required, must be > 0 |

Creator becomes `organizer`. **201** → full event detail representation.

### `GET /api/v1/events/{id}/`

Detail view of one event, subject to the same public/private visibility
rule as the list — a private event you can't see returns **404**, not
403. Includes `my_participation`.

### `PATCH /api/v1/events/{id}/`

Organizer only (**403** otherwise). Same field rules as create, except
`access_type` cannot be changed.

Changing `date`, `format`, or `location` moves every currently `CONFIRMED`
participant to `reconfirmation_required` (with a
`reconfirmation_deadline`) and emails them — see [Database
schema](../README.md#database-schema) and
`docs/email-integration-spec.md` §6.

### `DELETE /api/v1/events/{id}/`

Organizer only.

### `POST /api/v1/events/{id}/register/`

Auth required. Self-registers the requester as a participant (status
`confirmed`, or `invited`/pending flows depending on event rules). No body.

- **201** → participant representation.
- **409** `invitation_pending` — an outstanding invite exists; accept/reject
  it first.
- **409** `already_finalized` — already confirmed.
- **409** `capacity_exceeded` — event is full.
- **400** `invalid_request` — other domain validation failure.

### `POST /api/v1/events/{id}/invite/`

Auth required (organizer, per business rule — `PermissionError` → **403**
otherwise). Body: `{ email }`.

- **201** → participant representation, status `invited`.
- **403** — requester isn't allowed to invite.
- **409** `already_invited` — that user already has an invite for this event.

### `POST /api/v1/events/{id}/accept/`

Auth required. Accepts the requester's own invitation/registration. No
body. Idempotent: calling again while already `confirmed` returns **200**
with the current state instead of erroring.

- **200** → participant representation.
- **409** `capacity_exceeded` — event filled up in the meantime.
- **400** `invalid_request` — invalid transition (e.g. no pending invite).

### `POST /api/v1/events/{id}/reject/`

Auth required. Rejects the requester's own invitation. No body. Idempotent
on `rejected`, same shape as `accept/`.

### `POST /api/v1/events/{id}/cancel/`

Auth required. Cancels the requester's own participation. No body.
Idempotent on `cancelled`, same shape as `accept/`.

### `GET /api/v1/events/{id}/participants/`

Auth required. Visibility (ADR 002 §7):

- **Organizer or staff** — full data (`id, username, email, status,
  updated_at`) for every participant; optional `?status=` filter.
- **Confirmed participant** — usernames only, of non-`cancelled`
  participants.
- **Anyone else authenticated** — **403**.
- **Event not visible to requester** (private, not organizer/confirmed) —
  **404**.

## Not part of the JSON API

Two server-rendered HTML pages exist purely as the landing targets for
emailed links, unauthenticated and outside `/api/v1/`:

- `GET /auth/email-verification/confirm/?token=...`
- `GET/POST /auth/password-reset/confirm/?token=...`

See `docs/email-integration-spec.md` for the full email-flow spec these
implement.
