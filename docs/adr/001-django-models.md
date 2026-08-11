# ADR 001: Core Django Models (Users, Events, Event Participants)

## Status

Accepted

## Context

The application needs three core entities: `users`, `events`, and
`event_participants`, linking organizers and participants to events.
This ADR captures the agreed schema and the Django-specific modeling
decisions (primary keys, delete behavior, capacity handling, and
registration flows) before implementation. It records decisions and
rationale, not the implementation — see `accounts/models.py` and
`events/models.py` for the actual code once scaffolded.

## Schema

```dbml
Table users {
  id bigint [pk, increment]
  username varchar(150) [not null]
  email varchar(254) [not null, unique]
  password varchar(128) [not null]
}

Table events {
  id uuid [pk]
  title varchar(255) [not null]
  description text
  date timestamp [not null]

  format varchar(10) [not null, note: 'enum: online, offline']
  location varchar(255) [note: 'required for offline events, null for online events']

  access_type varchar(10) [not null, note: 'public | private']
  capacity int [not null, note: 'must be > 0']
  organizer_id bigint [not null]

  created_at timestamp
  updated_at timestamp
}

Table event_participants {
  id uuid [pk]

  event_id uuid [not null]
  user_id bigint [not null]

  status varchar(24) [not null, note: 'invited | confirmed | rejected | cancelled | reconfirmation_required']

  updated_at timestamp

  indexes {
    (event_id, user_id) [unique]
    (event_id, status)
  }
}

Ref: events.organizer_id > users.id [delete: restrict]

Ref: event_participants.event_id > events.id [delete: cascade]
Ref: event_participants.user_id > users.id [delete: cascade]
```

Note: `users.id` uses `bigint [pk, increment]` (decision #11);
`events` and `event_participants` keep `uuid` PKs (decision #1).
`users.username` is not unique (decision #4).

## Decisions

### 1. UUID primary keys for `Event` and `EventParticipant`

Both tables use UUID primary keys instead of Django's default
auto-incrementing integer. These IDs are expected to appear in public
URLs (e.g. `/events/{id}/register`), and a sequential integer there
would leak row counts and invite enumeration. `User` is the exception
— see decision #11.

```python
id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
```

`DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"` stays the
project-wide fallback in `settings.py` for any model that doesn't
declare its own PK explicitly (this is what `User` relies on).

### 2. Custom `User` model

Django projects should always start with a custom user model, since
swapping it later requires a full data migration. `User` extends
`AbstractUser`, with `email` as the login identifier (see #4) and
`username` kept as a non-unique display field.

### 3. `organizer_id` uses `on_delete=PROTECT`

Deleting a user who still organizes at least one event must raise
`django.db.models.ProtectedError` rather than cascading or silently
orphaning events. Application code (views/serializers) that deletes
users must catch `ProtectedError` and surface a clear error ("this
user still organizes events; reassign or remove them first") instead
of letting it bubble up as a 500.

```python
organizer = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.PROTECT,
    related_name="organized_events",
)
```

Note: soft deletion for `User` (deactivate instead of delete) was
considered and explicitly dropped for now — `User` uses plain hard
delete, same as Django's default. Revisit if/when a real requirement
for account recovery or delete-auditing shows up.

### 4. Login by email; `username` is not unique

`AUTH_USER_MODEL` authenticates by email, not username. `username`
remains as a required display name (schema: `not null`), but the
uniqueness constraint moves entirely to `email`; `EmailField` already
carries `unique=True`, so no extra `CheckConstraint` is needed.

```python
username = models.CharField(max_length=150)  # no unique=True
email = models.EmailField(max_length=254, unique=True)

USERNAME_FIELD = "email"
REQUIRED_FIELDS = ["username"]  # still asked for on createsuperuser
```

### 5. Conditional `location` requirement

`location` is required when `format = "offline"` and must be null when
`format = "online"`. Enforced two ways:

- `Event.clean()` raises `ValidationError` on mismatched
  format/location combinations (form/admin/serializer validation
  path).
- A DB-level `CheckConstraint` named `offline_requires_location` as a
  backstop for direct DB writes that bypass model validation.

### 6. `capacity > 0`

Modeled as `PositiveIntegerField` plus a `CheckConstraint` named
`capacity_positive`.

### 7. `event_participants` foreign keys

- `event_id` → `Event`, `on_delete=CASCADE`: deleting an event removes
  its participation rows only, not the `User` accounts referenced by
  them.
- `user_id` → `User`, `on_delete=CASCADE`: deleting a user removes
  their participation records (and only that — the `PROTECT` on
  `organizer` is what stops deletion of users who still organize
  events; see decision #3).
- `UniqueConstraint(fields=["event", "user"], name="unique_event_participant")`
  prevents duplicate participation rows for the same user/event pair.

A `CONFIRMED` participant — whether they got there via
`invite()`+`accept()` on a private event, or `register()` on a public
one — can back out via `EventParticipant.cancel()`, moving them to
`CANCELLED`. On a private event this is effectively terminal, since
only a new organizer `invite()` can bring the participant back in; on
a public event `register()` may bring them back to `CONFIRMED` (see
decision #8/#9). `cancel()` is only valid from `CONFIRMED` or
`RECONFIRMATION_REQUIRED`; anything else raises `ValueError`.

A `CONFIRMED` participant can also be moved to `RECONFIRMATION_REQUIRED`
when the organizer changes `date`, `format`, or `location` on the event
(see decision #12 for the capacity implications). From
`RECONFIRMATION_REQUIRED`, `accept()` returns the participant to
`CONFIRMED` and `cancel()` moves them to `CANCELLED`, mirroring the
transitions available from `INVITED`. A participant already in
`CANCELLED` is not moved to `RECONFIRMATION_REQUIRED` by a subsequent
event change — `CANCELLED` stays terminal for that purpose.

### 8. Registration / invitation flow (private vs. public events)

Two distinct flows produce `EventParticipant` rows, and both must be
implemented as model methods rather than left to ad-hoc view code, so
capacity and status rules stay in one place. `register()` remains
self-registration and is restricted to `access_type=PUBLIC`; `invite()`
is organizer-initiated and is available on both `access_type=PUBLIC`
and `access_type=PRIVATE` events, so an organizer can proactively add a
participant to a public event without waiting for them to self-register.

**Invite-based (both public and private events):**

```
Organizer
    |
    | invite(user)
    ↓
EventParticipant(status=INVITED)
    |
    +---- user accepts ----> CONFIRMED
    |
    +---- user rejects ----> REJECTED
```

Only the organizer can create the initial `INVITED` row
(`invite(user, *, by)` — raises `PermissionError` if
`by != self.organizer`, and `EventParticipant.AlreadyInvited` on a
duplicate invite). Unlike an earlier draft of this ADR, `invite()` is
no longer restricted to `access_type=PRIVATE` events — organizers may
invite users to public events too, as an alternative entry point
alongside self-registration. The invited user then transitions it to
`CONFIRMED` or `REJECTED` via `accept()`/`reject()` (each only valid
from `INVITED`). No capacity check is applied at invite time in this
ADR (organizers are expected to manage this manually) — flag as an
open question if invite floods beyond capacity need to be prevented
too.

**Re-entering after `CANCELLED`:**

Whether a `CANCELLED` participant can become active again depends on
`access_type`:

- **Public events**: `register(user)` is allowed to bring a `CANCELLED`
  participant back to `CONFIRMED` (subject to the normal capacity
  check), the same as it does for a prior `REJECTED` row — see
  decision #9.
- **Private events**: `register()` is never valid (per the `ValueError`
  above), so a `CANCELLED` participant on a private event can only
  re-enter via a new `invite()` from the organizer. This must not be
  bypassed — there is no self-service path back into a private event.

**Public events — self-registration with capacity check:**

```
User -> POST /events/{id}/register

             ↓

access_type == PUBLIC?  -- no --> rejected ("not self-serve")
        |
       yes
        ↓
capacity available?  (capacity > count of CONFIRMED participants)
       /          \
     yes           no
      ↓             ↓
 CONFIRMED     rejected ("event full")
```

`register(user)` is only valid for `access_type=PUBLIC` events
(`ValueError` otherwise).

### 9. Fixing the capacity race condition, terminal-state overwrite, and duplicate-invite crashes

Three related bugs in a naive version of `register()`/`invite()`,
fixed together since they touch the same methods:

- **Race condition**: two concurrent `POST /events/{id}/register`
  requests, both reading "74 confirmed of 100" before either writes,
  can both decide there's room and both insert a `CONFIRMED` row —
  overshooting capacity. Fixed by locking the `Event` row with
  `select_for_update()` and doing the count-and-decide inside the same
  `transaction.atomic()` block, so concurrent registrations for the
  same event serialize instead of racing. This requires a database
  with real row locks — see decision #10 (PostgreSQL); SQLite doesn't
  honor `SELECT ... FOR UPDATE` the same way and would silently make
  the lock a no-op, so the race would resurface undetected in any test
  run against SQLite. Guarded explicitly at call time with a
  `connection.vendor != "postgresql"` check that raises
  `ImproperlyConfigured`, rather than left as a paper requirement.

- **Terminal-state overwrite**: a naive `register()` using
  `update_or_create` would silently flip an already-`CONFIRMED`
  participant back to `CONFIRMED` on a repeat call, bypassing
  cancellation semantics. Fixed by checking the existing status first:
  `CONFIRMED` is terminal for self-service re-registration and raises
  `EventParticipant.AlreadyFinalized` instead of silently mutating;
  a missing record, a prior `REJECTED` (event was full last time,
  capacity may have opened up since), or — per decision #8's
  re-entering rule — a prior `CANCELLED` row are all allowed to
  proceed back to `CONFIRMED` via `register()`. `CANCELLED` is only
  terminal in the sense that `register()` is never reachable at all on
  private events; it is not terminal on public events. An existing
  `INVITED` row is also not silently overwritten: `register()` must
  not confirm a user out from under a pending invite, so it raises
  `EventParticipant.InvitationPending` instead — the user is expected
  to `accept()`/`reject()` the invite first.

- **Duplicate invite crash**: calling `invite()` twice for the same
  user hit the DB's unique constraint and raised a raw
  `django.db.utils.IntegrityError` — unhandled and non-descriptive for
  callers. Fixed by checking for an existing row first and raising
  `EventParticipant.AlreadyInvited` instead.

  This check-then-insert is only safe against *sequential* duplicate
  invites. Two concurrent `invite()` calls for the same user/event can
  both pass the "no existing row" check before either writes, and the
  second still hits the unique constraint — reintroducing the raw
  `IntegrityError` this fix was meant to remove, just under
  concurrency. `invite()` must take the same lock `register()` does
  (`select_for_update()` on the `Event` row inside
  `transaction.atomic()`) so the check and the write are atomic
  together, plus catch `IntegrityError` as a backstop and re-raise it
  as `AlreadyInvited`.

### 10. PostgreSQL as the primary database

The project targets PostgreSQL in all environments (not SQLite), so
`CheckConstraint`, `UniqueConstraint`, and `select_for_update()` behave
consistently between dev and prod — SQLite has partial/older support
for check constraints and doesn't provide real row-level locking,
which would silently defeat decision #9's fix anywhere the race
actually matters (e.g. local dev testing of concurrent registration).
Enforced at the model layer by the `connection.vendor` guard in
`Event.register()` rather than left as an unenforced deployment
assumption; the `DATABASES` setting itself is deployment configuration
and out of scope here.

### 11. `AbstractUser` id stays `AutoField` (bigint) for `User`

`User` keeps Django's default `id` provided by `AbstractUser`/`Model`,
i.e. `BigAutoField` via `DEFAULT_AUTO_FIELD`. `Event` and
`EventParticipant` use UUID (decision #1). Rationale: no strong
exposure requirement was identified for `users.id` specifically — auth
flows address users by email, not by id-in-URL — and it avoids
fighting `AbstractUser`'s built-in `id` field (permissions,
`contenttypes`, `createsuperuser`, etc. are all validated against
Django's default integer PK conventions without any extra care
needed).

### 12. Capacity is enforced at `accept()`, not just `register()`

Decision #8 left invite-time capacity as an open question ("no capacity
check is applied at invite time... flag as an open question if invite
floods beyond capacity need to be prevented too"). That only covers
*invite floods*; it left a separate gap unaddressed: nothing stopped
`accept()` from confirming more participants than `capacity` once
invited, even without a flood, so a private event could silently
overbook through invite→accept while `register()` stayed race-safe.

Resolved: `capacity` is a hard cap on effective participation
regardless of path, where "effective participation" is `CONFIRMED`
plus `RECONFIRMATION_REQUIRED` — see below. `accept()` must do the same
locked count-and-decide `register()` does (`select_for_update()` on
the `Event` row inside `transaction.atomic()`, count existing
`CONFIRMED` + `RECONFIRMATION_REQUIRED` rows, reject if at capacity)
and raise a new `EventParticipant.EventFull` if the event is full.
Organizers can still over-invite freely (invite-time itself stays
uncapped, per decision #8) but an invitee's `accept()` can now fail
with `EventFull` if the event filled up in the meantime.

Per ADR 002's error-format contract, `EventFull` is surfaced to API
clients as the `capacity_exceeded` business error code (`409
Conflict`); `EventParticipant.InvitationPending` (above) is surfaced
as `invitation_pending`, also `409 Conflict`. These two are named
explicitly here because ADR 002 gives them explicit codes; the other
model exceptions (`AlreadyFinalized`, `AlreadyInvited`) still map to
`409 Conflict` but ADR 002 does not assign them dedicated codes.

**`RECONFIRMATION_REQUIRED` reserves capacity too:** when a `CONFIRMED`
participant is moved to `RECONFIRMATION_REQUIRED` (decision #7/#8,
triggered by an organizer changing `date`, `format`, or `location`),
their slot is not released — they keep temporarily holding one
capacity unit rather than freeing it up for someone else to grab
before they've had a chance to reconfirm. The reservation:

- lasts up to 24 hours from the moment the participant entered
  `RECONFIRMATION_REQUIRED`;
- cannot outlive `event.date` — it expires at `min(created + 24h,
  event.date)`;
- is released either by an explicit `cancel()` (→ `CANCELLED`) or by
  expiration of the TTL, at which point the slot becomes available to
  other participants/registrants again;
- is resolved to a permanent `CONFIRMED` slot by `accept()`.

The actual TTL/expiration mechanism (a scheduled task or lazy
expiration check) is not designed by this ADR and is left as an
implementation TODO — this decision only fixes the *counting* rule
that `RECONFIRMATION_REQUIRED` rows must be included in the
count-and-decide alongside `CONFIRMED` rows.

This count-and-decide now runs on every `register()` and `accept()`
call, so it needs an index behind it: the only index defined in the
schema is the unique `(event_id, user_id)`, which doesn't cover
`status`. Added a non-unique index on `(event, status)` so the
effective-capacity count stays index-backed as participant volume per
event grows; cheap to add now, so it isn't deferred as a "revisit at
scale" item.

### 13. Registration/acceptance window relative to `Event.date`

Not addressed by the original schema or flows: nothing prevented
`register()`, `invite()`, or `accept()` from succeeding after
`Event.date` had already passed. Resolved by decision, not by omission:
`register()` and `accept()` both reject once `Event.date` is in the
past (raising `ValueError`); `invite()` is unrestricted by date, since
an organizer may still want to record an invite for a past event
administratively. Revisit if multi-day or ongoing events need
registration to stay open past the listed start `date`.

## Settings

Model-relevant wiring only; broader `DATABASES`/deployment
configuration is out of scope for this ADR.

```python
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
```

## Consequences

- Views/serializers that delete a `User` must catch `ProtectedError`
  for organizers who still have events.
- `Event.register()` relies on `select_for_update()`, which requires
  PostgreSQL (or another DB with real row locks) to actually prevent
  overbooking; this is also why SQLite is not used, even for local dev.
- Invites (now allowed on both public and private events, decision #8)
  currently bypass the capacity check by design (organizer discretion)
  — but `accept()` does not bypass it (decision #12): an invited user
  can still be rejected with `EventFull` at accept time even though the
  invite itself succeeded. `invite()` also must take the same
  `select_for_update()` + `transaction.atomic()` lock as
  `register()`/`accept()` (decision #9); the original check-then-insert
  alone is not concurrency-safe.
- `register()` and `accept()` both reject once `Event.date` has passed
  (decision #13); `invite()` does not.
- `RECONFIRMATION_REQUIRED` participants count toward effective
  capacity alongside `CONFIRMED` (decision #12); the reservation
  releases on `cancel()` or after its TTL expires, but this ADR does
  not design the expiration mechanism itself.
- `register()` raises `EventParticipant.InvitationPending` rather than
  silently confirming a user who already has an `INVITED` row
  (decision #9); per ADR 002 this maps to the `invitation_pending`
  API error code, `409 Conflict`. `EventFull` maps to
  `capacity_exceeded`, also `409 Conflict`.
- A `CANCELLED` participant can re-enter a public event via `register()`
  again, but on a private event can only re-enter via a fresh
  `invite()` from the organizer (decision #8) — this distinction must
  not be bypassed by view/serializer code.
- All `EventParticipant` status transitions (`register`, `invite`,
  `accept`, `reject`, `cancel`) must go through the model methods on
  `Event`/`EventParticipant`, never raw `EventParticipant.objects
  .create()/.update()` calls — the capacity, terminal-state, locking,
  and date-window guarantees above only hold inside those methods.
- `User` deletion is a real, irreversible `DELETE` (no soft-delete) —
  callers deleting users should be deliberate; there's no recovery path
  once a user is gone (besides the `PROTECT` guard on organizers).
- `accounts` app must be migrated before `events`, since `events`
  depends on `AUTH_USER_MODEL`.
