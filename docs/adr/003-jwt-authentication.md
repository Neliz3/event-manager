# ADR 003: JWT Authentication Implementation

## Status

Accepted

## Context

ADR 002 states the authentication contract at the API-contract level:
JWTs delivered via HttpOnly cookies, HTTPS required, refresh tokens
rotated, reuse detection revokes the token family, and only verified
users may log in.

The current implementation (`config/settings.py`, `SIMPLE_JWT`,
`AUTH_COOKIE_*`; `apps/users/authentication.py`; `apps/users/views.py`)
settles several details ADR 002 never spelled out, and leaves some of
ADR 002's own claims unimplemented. This ADR records the implementation
decisions and the remaining gaps.

## Decisions

### Token lifetimes

-   Access token: 15 minutes.
-   Refresh token: 7 days.

### Cookie delivery

-   Cookie names: `access_token`, `refresh_token`.
-   `HttpOnly`: always set.
-   `Secure`: set whenever `DEBUG` is off (i.e. in any non-local
    environment).
-   `SameSite`: `Lax`.

### Dual authentication channel

`CookieJWTAuthentication` (`apps/users/authentication.py`) first checks
the standard `Authorization: Bearer` header, and falls back to the
`access_token` cookie only when no header is present. This lets
non-browser clients/tooling authenticate without cookies, alongside the
cookie-based flow browsers use. ADR 002 does not mention this fallback;
it is now the documented behavior.

### CSRF

Because the access token travels in an `HttpOnly` cookie, browser
requests are authenticated without JavaScript attaching a header,
which reopens CSRF exposure that `Authorization`-header-based JWT
normally avoids. Mitigation relies on `SameSite=Lax`, which blocks the
cookie from being sent on cross-site POST/PUT/PATCH/DELETE requests
from other origins. This ADR treats `SameSite=Lax` as the primary CSRF
control and does not add a separate CSRF token for the JWT cookie
endpoints.

### Signing

No explicit `SIGNING_KEY` or algorithm is configured today, so
simplejwt defaults apply: HMAC signing (`HS256`) using Django's
`SECRET_KEY`. This is changed by this ADR (see "Decision: dedicated
signing key and pinned algorithm" below).

## Decision: dedicated signing key and pinned algorithm

Two changes to the signing configuration:

-   `ALGORITHM` is set explicitly to `HS256` in `SIMPLE_JWT`, rather
    than left on the implicit default. Per RFC 8725 §3.1, the
    algorithm should never be left for a library default to decide;
    pinning it prevents a future config change from silently
    downgrading it.
-   A dedicated `SIGNING_KEY` is introduced for JWT signing, separate
    from Django's `SECRET_KEY`. Reusing `SECRET_KEY` couples JWT
    signing to every other subsystem that uses it (sessions, CSRF,
    password-reset/email-verification tokens): rotating it for any
    unrelated reason invalidates all live JWTs, and compromising the
    JWT signing key compromises everything else keyed off
    `SECRET_KEY`. A separate `SIGNING_KEY` lets JWT signing be rotated
    independently.

Implementation TODO: add `SIGNING_KEY` to `SIMPLE_JWT` in
`config/settings.py`, sourced the same way `SECRET_KEY` is (env var /
secrets manager), and set `ALGORITHM: 'HS256'` alongside it.

## Decision: refresh-cookie path scoping

The `refresh_token` cookie is scoped with `Path=/api/v1/auth/` so the
browser only attaches it to the auth endpoints that need it
(`refresh`, `logout`), not to every request on the domain. The
`access_token` cookie keeps the default path (`/`), since it is
required on all authenticated endpoints.

Rationale: the refresh token is long-lived (7 days) and high-value —
its compromise defeats rotation entirely. Sending it only on the
endpoints that consume it reduces the surface that could leak it
(logging, unrelated endpoint bugs, open redirects, etc.) compared to
attaching it to every request by default.

Implementation TODO: pass `path=settings.AUTH_COOKIE_REFRESH_PATH` (or
equivalent) when setting the refresh cookie in
`apps/users/views.py::_set_auth_cookies`, and when clearing it in
`_clear_auth_cookies`.

## Decision: CSRF defense-in-depth beyond `SameSite`

`SameSite=Lax` is retained as the primary control, but is no longer
treated as sufficient on its own. `SameSite=Lax` still permits the
cookie on top-level cross-site GET navigation, and its enforcement
depends on browser compliance (older browsers, some in-app webviews).
Per OWASP guidance for cookie-delivered auth, a second, independent
control is added: a double-submit CSRF token required on state-
changing requests (`POST`/`PATCH`/`DELETE`) to any `/api/v1/` endpoint
authenticated via the cookie.

This does not apply to requests authenticated via the `Authorization`
header (non-browser clients), which are not subject to CSRF.

Implementation TODO: enable DRF's CSRF enforcement path (or an
equivalent double-submit token check) for cookie-authenticated,
state-changing requests; exempt header-authenticated requests.

## Decision: access-token revocation window is an accepted risk

Revoking a JWT before its own expiry is not possible without a
per-token denylist check on every request, which this design does not
add for access tokens (only for refresh tokens, via the reuse-
detection design below). This is an explicit, accepted tradeoff:

-   Logout and reuse-detection revoke the refresh token (family); they
    do **not** invalidate any access token already issued from it.
-   A stolen/leaked access token remains usable for up to its full
    lifetime (15 minutes) regardless of logout or revocation.
-   This bound is why the access-token lifetime is kept short (15
    minutes) — it is the exposure window this risk accepts.

## Decision: refresh-token reuse detection / token-family revocation

ADR 002 states reuse detection revokes the corresponding token family.
This is currently unimplemented — `BLACKLIST_AFTER_ROTATION` is
`False`, so a rotated-out refresh token stays valid until it expires,
and `LogoutView` blacklists only the single refresh token presented,
not its family. This ADR resolves the gap with two new models and a
defined business flow, matching the hash-and-lookup pattern already
used for email verification (ADR 002, "Email verification").

### Data model

```dbml
Table refresh_token_families {
  id bigint [pk, increment]
  user_id bigint [not null]
  created_at timestamp [not null]
  revoked_at timestamp [note: 'null while live']
}

Table refresh_token_records {
  id bigint [pk, increment]
  family_id bigint [not null]
  jti_hash char(64) [not null, unique, note: 'sha256(jti), never the raw token']
  issued_at timestamp [not null]
  expires_at timestamp [not null]
  used_at timestamp [note: 'null until consumed by rotation']

  indexes {
    jti_hash [unique]
    (family_id, used_at)
  }
}

Ref: refresh_token_families.user_id > users.id [delete: cascade]
Ref: refresh_token_records.family_id > refresh_token_families.id [delete: cascade]
```

Deleting a `User` cascades to their `refresh_token_families` (and
transitively their `refresh_token_records`) — unlike `organizer_id` on
`Event` (ADR 001 decision #3), there's no reason to block user deletion
over live sessions; they simply stop being valid.

Two new models are needed:

-   **Refresh token family** — represents one chain of rotated refresh
    tokens issued from a single login. Fields: owning user, created
    timestamp, and a revoked timestamp (null while live). A family is
    the unit of revocation: revoking it invalidates every token ever
    issued within it, past or future.
-   **Refresh token record** — represents one issued refresh token.
    Fields: the family it belongs to, a hash of the token's `jti`
    (never the raw token — same principle as the email-verification
    tokens), issued/expiry timestamps, and a "used" timestamp (null
    until consumed by rotation).

Both tables need periodic cleanup of rows past their expiry; the
cleanup mechanism itself is a further implementation TODO, not
specced here.

### Business logic

-   **Login** creates a new family for the user and a record for the
    refresh token it issues.
-   **Refresh** looks up the presented token's record by its hashed
    `jti`:
    -   no matching record → reject; the token wasn't issued by this
        system or its record has already been pruned.
    -   family already revoked → reject, no rotation.
    -   record already marked used → **reuse detected**: this is the
        signal that a stolen refresh token was replayed after the
        legitimate client already rotated past it, so the whole family
        is revoked and the request rejected.
    -   otherwise: the presented record is marked used, a new refresh
        token is issued into the *same* family with its own record,
        and a new access token is returned as today.
-   **Logout** looks up the presented token's family and revokes it
    outright — this is what makes logout invalidate every token in the
    chain, not just the one cookie in hand, closing the gap called out
    against ADR 002. A missing/already-invalid cookie is still a
    no-op `204` (idempotent logout, per ADR 002 HTTP semantics).

With this in place, a rotated-out token is rejected by the
family/used-state check before simplejwt's own blacklist is even
consulted. That makes `BLACKLIST_AFTER_ROTATION` safe to turn on as a
defense-in-depth double-check rather than the sole mechanism — it
should be enabled once this model ships.

Implementation TODO: add the two models and their migration, wire
login/refresh/logout to them, and add the periodic-cleanup job for
expired rows.

## Decision: email-verification gate on login

ADR 002 states only verified users may log in; `LoginView` does not
currently enforce this. Resolved: login checks `user.is_email_verified`
(existing field, per ADR 001's `User` model) after credentials are
validated and before any token is issued.

-   Unverified user, correct credentials → reject. This is a business
    error, not a validation error, and follows ADR 002's error-format
    contract: `403 Forbidden` with an `email_not_verified` code, rather
    than `401`, since the credentials themselves were correct — the
    account just isn't allowed to authenticate yet.
-   No token/cookie of any kind is issued in this case — the check
    happens before refresh-family creation (see the reuse-detection
    decision above), so a rejected login leaves no session state
    behind.
-   The response should point the client at the existing
    `/api/v1/auth/email-verification/request/` endpoint (ADR 002) to
    resend the verification email, rather than just stating the
    account is unverified.

Implementation TODO: add the check to `LoginView` in
`apps/users/views.py`, before refresh-token issuance; add the
`email_not_verified` business error code alongside the existing
`capacity_exceeded`/`invitation_pending` codes (ADR 002).

## Decision: rate limiting on auth endpoints

No throttling exists today on any auth endpoint, leaving
`login`/`refresh`/`register`/`password/reset/*` open to credential
stuffing, brute force, and token-guessing. Resolved with DRF throttle
classes, scoped per endpoint rather than one global rate:

-   `login` and `password/reset/request` are throttled **per
    identifying input** (the submitted email), not just per IP, since
    both are guessable-credential attacks against a specific account
    that a shared-IP/NAT attacker could otherwise dodge by rotating
    source IPs alone — and per-IP alone would let an attacker spread
    the same account's attempts across many IPs. A stricter secondary
    per-IP throttle still applies underneath, to blunt broad,
    multi-account sweeps from one source.
-   `refresh` is throttled per-IP; it's already bound to a valid
    refresh token, so the risk here is volumetric abuse rather than
    credential guessing.
-   `register` is throttled per-IP, to slow bulk account creation.
-   All auth throttles are stricter than DRF's general-purpose default
    throttle scope, and return `429 Too Many Requests`.
-   Exact rates (requests/window) are left as an implementation detail
    tuned during rollout, not fixed by this ADR.

Implementation TODO: add scoped `DEFAULT_THROTTLE_CLASSES`/
`DEFAULT_THROTTLE_RATES` entries (or per-view `throttle_classes`) for
the endpoints above in `config/settings.py` / `apps/users/views.py`.

## Consequences

-   The cookie/header dual-auth behavior, cookie attributes, signing
    key/algorithm, refresh-cookie path scoping, CSRF defense-in-depth,
    access-token revocation tradeoff, refresh-token reuse detection,
    email-verification gate, and rate limiting are now all documented
    decisions rather than undocumented behavior or open TODOs.
-   All "Implementation TODO" items across these decisions are now
    implemented: the two new models (`RefreshTokenFamily`,
    `RefreshTokenRecord` in `apps/users/models.py`), the `SIGNING_KEY`/
    `ALGORITHM` settings, refresh-cookie path scoping, the
    `CookieCSRFPermission` double-submit check, the login verification
    gate, the per-email/per-IP throttles, and the `cleanup_expired_tokens`
    management command (scheduled hourly via `django-crontab`).
-   One deviation from this ADR's original wording: `is_email_verified`
    was added with `default=True` rather than `False`, since email
    verification send/confirm is still an unimplemented `501` stub —
    defaulting to `False` would have locked out all new registrations.
    Flip the default once that stub ships.
