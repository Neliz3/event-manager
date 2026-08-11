# ADR 002: API Layer

## Status

Accepted

## Context

The API layer exposes the core application entities and business flows
for users, events, and event participants.

The API uses a versioned URL prefix:

``` text
/api/v1/
```

Authentication uses JWT with HttpOnly cookies. Login is allowed only for
users whose email has been verified.

The API contract follows REST-oriented HTTP semantics where practical,
while keeping domain-specific state transitions explicit through action
endpoints.

## API conventions

### Versioning

All endpoints are currently under:

``` text
/api/v1/
```

### Authentication

-   Access and refresh tokens are JWTs.
-   Tokens are delivered/stored through HttpOnly cookies.
-   HTTPS is required.
-   Refresh tokens are rotated.
-   Refresh-token reuse detection revokes the corresponding token
    family.
-   Only verified users may log in.

### Error formats

Validation errors use Django REST Framework-style field-level errors:

``` json
{
  "email": ["This field is required."]
}
```

Business/domain errors use:

``` json
{
  "error": {
    "code": "capacity_exceeded",
    "message": "Event capacity has been reached."
  }
}
```

### HTTP semantics

-   `201 Created` when a new resource is created.
-   `200 OK` for successful updates/actions on an existing resource.
-   `204 No Content` for successful deletion/logout where no response
    body is needed.
-   `403 Forbidden` when the resource is visible but the authenticated
    user lacks permission.
-   `404 Not Found` is used when private-resource visibility must not
    reveal whether the resource exists.
-   `409 Conflict` is used for valid requests that conflict with the
    current domain state.

## Endpoint inventory

### Authentication

  --------------------------------------------------------------------------------------------------
  Method            Endpoint                                     Purpose           Success
  ----------------- -------------------------------------------- ----------------- -----------------
  POST              `/api/v1/auth/register/`                     Create account    201

  POST              `/api/v1/auth/login/`                        Login and issue   200
                                                                 authentication    
                                                                 cookies           

  POST              `/api/v1/auth/logout/`                       Logout and revoke 204
                                                                 refresh-token     
                                                                 family            

  POST              `/api/v1/auth/refresh/`                      Rotate refresh    200
                                                                 token and issue   
                                                                 access token      

  POST              `/api/v1/auth/email-verification/request/`   Request           200
                                                                 verification      
                                                                 email             

  POST              `/api/v1/auth/email-verification/confirm/`   Verify email      200
                                                                 using             
                                                                 verification link 

  POST              `/api/v1/auth/password/change/`              Change password   200

  POST              `/api/v1/auth/password/reset/request/`       Request password  200
                                                                 reset             

  POST              `/api/v1/auth/password/reset/confirm/`       Complete password 200
                                                                 reset             
  --------------------------------------------------------------------------------------------------

### Users

  Method   Endpoint              Purpose
  -------- --------------------- ---------------------
  GET      `/api/v1/users/me/`   Get current user
  PATCH    `/api/v1/users/me/`   Update current user

### Events

  ---------------------------------------------------------------------------------------------
  Method            Endpoint                                Purpose           Success
  ----------------- --------------------------------------- ----------------- -----------------
  GET               `/api/v1/events/`                       List events with  200
                                                            filtering and     
                                                            pagination        

  POST              `/api/v1/events/`                       Create event      201

  GET               `/api/v1/events/{event_id}/`            Get event detail  200

  PATCH             `/api/v1/events/{event_id}/`            Update event      200

  DELETE            `/api/v1/events/{event_id}/`            Delete event      204

  POST              `/api/v1/events/{event_id}/register/`   Self-register for 201
                                                            a public event    
  ---------------------------------------------------------------------------------------------

### Invitations and participation

  -------------------------------------------------------------------------------------------------
  Method            Endpoint                                    Purpose           Success
  ----------------- ------------------------------------------- ----------------- -----------------
  POST              `/api/v1/events/{event_id}/invite/`         Organizer invites 201
                                                                a user            

  POST              `/api/v1/events/{event_id}/accept/`         Accept invitation 200
                                                                or reconfirm      
                                                                participation     

  POST              `/api/v1/events/{event_id}/reject/`         Reject invitation 200

  POST              `/api/v1/events/{event_id}/cancel/`         Cancel            200
                                                                participation     

  GET               `/api/v1/events/{event_id}/participants/`   List participants 200
                                                                according to      
                                                                access rules      
  -------------------------------------------------------------------------------------------------

Admin-specific endpoints are not expanded here until concrete admin use
cases require them.

## Event list filters and pagination

`GET /api/v1/events/` supports the filters already agreed for the API:

- `organizer_username`
- `date`
- `capacity`

Example:

```text
GET /api/v1/events/?organizer_username=alex&date=2026-09-01&capacity=50
```

The event list uses page-based pagination:

```text
GET /api/v1/events/?page=2&page_size=20
```

Private events returned by list/search must follow the same visibility rules as
event detail: a user may discover a private event only when they have access
as its organizer or confirmed participant.

For participant listing, a `status` filter is supported:

```text
GET /api/v1/events/{event_id}/participants/?status=confirmed
```

The exact allowed filter operators and pagination metadata shape are part of
the final response-schema definition.

## Event representation

Event list responses use a compact representation. Event detail
responses use the full representation.

For authenticated users, event representations include:

``` json
{
  "my_participation": {
    "status": "confirmed"
  }
}
```

If the authenticated user has no participation record:

``` json
{
  "my_participation": null
}
```

For anonymous users, `my_participation` is omitted.

`created_at` is not conditionally exposed based on how the user accesses
the event.

## Event validation

-   `title` is required and must be non-empty.
-   `description` is optional.
-   `access_type` is required on creation and is immutable after
    creation.
-   `access_type` is `public` or `private`.
-   `capacity` is required and must be an integer greater than zero.
-   `date` is an ISO 8601 datetime with a required timezone.
-   Whether `date` is in the future is a business/domain rule.
-   `format` is `online` or `offline`.
-   `offline` events require `location`.
-   `online` events require `location = null`.
-   Username validation is provided by the existing model/serializer
    validators rather than duplicated in this API contract.

## Event visibility and permissions

### Public events

-   Anonymous users may list and view public events.
-   Authenticated users may additionally self-register.
-   Organizers may manage their own events.
-   Admins have the permissions defined by the admin scope.

### Private events

A private event is visible only to users who have access as an organizer
or confirmed participant, subject to the participant visibility rules.

If the user is not allowed to discover a private event, the API returns
`404`.

If an authenticated user can see the event but lacks permission for an
operation, the API returns `403`.

## Participant visibility

For `GET /events/{event_id}/participants/`:

-   Organizer and admin receive full participant data, including email
    and status.
-   A confirmed participant receives only usernames of
    confirmed/non-cancelled participants.
-   Participant status and email are not exposed to ordinary confirmed
    members.
-   Anonymous users do not receive participant data.
-   Private-event access rules apply before participant data is exposed.

## Participation state machine

The API recognizes these states:

``` text
INVITED
CONFIRMED
REJECTED
CANCELLED
RECONFIRMATION_REQUIRED
```

Primary transitions:

``` text
INVITED
  ├── accept  → CONFIRMED
  └── reject  → REJECTED

CONFIRMED
  └── cancel  → CANCELLED

CONFIRMED
  └── event date/format/location changed
                  ↓
       RECONFIRMATION_REQUIRED
          ├── accept → CONFIRMED
          └── cancel → CANCELLED
```

`CANCELLED` participants do not become `RECONFIRMATION_REQUIRED` when
the event is subsequently changed.

For public events, a cancelled user may register again.

For private events, a cancelled user may participate again only after
receiving a new invitation.

`REJECTED` may participate again when the corresponding flow permits it.

## Registration

`POST /events/{event_id}/register/` is available for public events.

A successful first registration creates an `EventParticipant` and
returns `201 Created`.

If the user has a pending invitation, registration is not performed:

``` json
{
  "error": {
    "code": "invitation_pending",
    "message": "You have a pending invitation. Accept it to join the event."
  }
}
```

The response status is `409 Conflict`.

Repeated/conflicting registration attempts use `409 Conflict` according
to the current participant state.

## Invitations

An organizer may invite users to both public and private events.

For an invitation:

``` text
INVITED → CONFIRMED
INVITED → REJECTED
```

An invited user does not need to call `register`; they only need to
accept or reject the invitation.

Duplicate invitations return `409 Conflict`.

Invitation creation itself does not reserve capacity.

Acceptance is subject to the event capacity rule.

## Idempotency

The following participant actions are idempotent for the same resulting
state:

-   `accept`
-   `reject`
-   `cancel`

A repeated action returns `200` with the current participant
representation when the action is already reflected by the current
state.

`register` and `invite` are not treated as idempotent resource creation
operations; duplicate or conflicting state returns `409 Conflict`.

## Capacity

Capacity is a hard limit on confirmed participation.

Registration and invitation acceptance must enforce capacity safely.

`accept` may fail with a business error when the event has become full
since the invitation was created.

## Event changes and re-confirmation

Changes to any of the following fields invalidate existing
confirmations:

-   `date`
-   `format`
-   `location`

The affected participant transitions:

``` text
CONFIRMED → RECONFIRMATION_REQUIRED
```

The participant must explicitly reconfirm.

An already-cancelled participant is not affected.

## ADR/model synchronization (resolved)

The four items below were previously open TODOs against the
model/domain ADR (ADR 001). ADR 001 has since been updated to match
this contract; kept here as a record of what was synchronized.

### 1. `RECONFIRMATION_REQUIRED` added

`RECONFIRMATION_REQUIRED` is part of the `EventParticipant` status
definition in ADR 001, with `accept → CONFIRMED` / `cancel →
CANCELLED` transitions documented there.

### 2. Capacity semantics updated

ADR 001 documents the temporary capacity reservation for
`RECONFIRMATION_REQUIRED`:

-   `RECONFIRMATION_REQUIRED` temporarily reserves one capacity slot.
-   Reservation TTL is 24 hours.
-   Reservation cannot extend beyond `event.date`.
-   `accept` changes the state to `CONFIRMED`.
-   `cancel` or reservation expiration releases the capacity.
-   Effective capacity usage includes both `CONFIRMED` and
    `RECONFIRMATION_REQUIRED` reservations.

The reservation/expiration mechanism itself remains a business-logic
implementation TODO. The API exposes the resulting participant state
but does not implement timers.

### 3. Organizer invitations allowed for public events

ADR 001's `invite()` is no longer private-event-only; an organizer may
invite a user to both public and private events.

Public events therefore support both:

``` text
self-registration:
user → register → CONFIRMED
```

and:

``` text
organizer invitation:
organizer → invite → INVITED → accept → CONFIRMED
```

### 4. Cancelled-user re-registration semantics updated

ADR 001 now reflects:

``` text
Public event:
CANCELLED → register → participation can become active again

Private event:
CANCELLED → only a new organizer invitation can re-enter the flow
```

This does not bypass the private-event invitation requirement.

## Email verification

Email verification is link-based.

Verification tokens must be:

1.  single-use;
2.  cryptographically random;
3.  short-lived;
4.  stored server-side as a hash or otherwise securely protected;
5.  invalidated after use;
6.  delivered and consumed over HTTPS.

Login is allowed only for verified users.

## Pagination

The event list uses page-based pagination:

``` text
GET /api/v1/events/?page=2&page_size=20
```

Pagination metadata and exact response envelope are part of the final
response-schema definition.

## Final consistency status

The API decisions are considered internally consistent, and are now
synchronized with the domain/model ADR (ADR 001) — see the "ADR/model
synchronization (resolved)" section above.
