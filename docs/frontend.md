# Frontend (apps/webui)

A minimal, template-based UI for manually exercising the JSON API described
in [api-reference.md](api-reference.md): registration, email verification,
login, password change, and event listing/search. It exists to make those
flows clickable in a browser — it is not a product frontend, has no build
step, and adds no new business logic.

## Architecture

- **Django templates, no SPA framework.** Each route in `apps/webui/urls.py`
  maps to a plain `TemplateView` that renders a static page — the view layer
  does no API calls itself.
- **The page's own JavaScript calls the existing JSON API** via
  `fetch(..., { credentials: 'include' })`, sharing the browser session
  cookies with `/api/v1/...`. There is no server-side proxying or duplicated
  business logic (with the pre-existing exception of
  `PasswordResetConfirmPageView`, which predates this app — see below).
- **Styling:** [Materialize CSS](https://materializecss.com) loaded entirely
  from CDN (`cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/...`) plus the
  Google "Material Icons" font. No npm/build pipeline; a `<link>` and a
  `<script>` tag in `base.html` are the whole dependency.

## Cookie / CSRF contract

Auth is cookie-based JWT, not Django sessions (ADR 003). `access_token` and
`refresh_token` are `HttpOnly` and invisible to JS — the browser just sends
them automatically. State-changing requests (`POST`/`PATCH`/`DELETE`) must
also echo a `csrf_token` cookie value back as an `X-CSRF-Token` header
(double-submit check, enforced by `CookieCSRFPermission` in
`apps/users/permissions.py`).

`apps/webui/static/webui/js/api.js` centralizes this: its `apiFetch(url,
options)` helper reads the `csrf_token` cookie and attaches the header for
any non-`GET`/`HEAD` request, and always sets `credentials: 'include'`. Every
page script (`register.js`, `login.js`, `password_change.js`, `events.js`)
goes through `apiFetch` rather than calling `fetch` directly — new pages
should do the same instead of reimplementing the header logic.

`api.js` also drives the nav bar: on every page load it calls
`GET /api/v1/users/me/` and toggles the `[data-nav="logged-in"]` /
`[data-nav="logged-out"]` elements in `base.html` based on whether that
succeeds, and wires up `[data-action="logout"]` to
`POST /api/v1/auth/logout/`.

## Routes

| Path                          | View                            | Template                       | Calls |
|--------------------------------|----------------------------------|---------------------------------|-------|
| `/`                            | `HomeView`                       | `webui/home.html`               | — |
| `/register/`                   | `RegisterPageView`               | `webui/register.html`           | `POST /api/v1/auth/register/`, then `POST /api/v1/auth/email-verification/request/` (registration doesn't send a verification email on its own) |
| `/login/`                      | `LoginPageView`                  | `webui/login.html`              | `POST /api/v1/auth/login/`; on `403 email_not_verified`, offers a resend button hitting `/api/v1/auth/email-verification/request/` |
| `/password-reset/`             | `PasswordResetRequestPageView`   | `webui/password_reset_request.html` | `POST /api/v1/auth/password/reset/request/` (always 200; doesn't leak account existence) |
| `/account/password/`           | `PasswordChangePageView`         | `webui/account_password.html`   | `POST /api/v1/auth/password/change/` (this revokes all sessions server-side, so the page redirects to `/login/` on success) |
| `/account/profile/`            | `ProfilePageView`                | `webui/profile.html`            | `GET /api/v1/users/me/` to populate the form, `PATCH /api/v1/users/me/` to save the username (`id`/`email` are read-only) |
| `/events/`                     | `EventListPageView`              | `webui/events_list.html`        | `GET /api/v1/events/?search=...&organizer_username=...&date=...&capacity=...` — all four filters supported by `EventListCreateView.get_queryset()` (`apps/events/views.py`) are wired up: `search`/`organizer_username`/`capacity` are debounced text/number inputs, `date` reloads on change |
| `/events/new/`                 | `EventCreatePageView`            | `webui/event_form.html`         | `POST /api/v1/events/`, redirects to the new event's detail page |
| `/events/<uuid:event_id>/`     | `EventDetailPageView`            | `webui/event_detail.html`       | `GET /api/v1/events/{id}/`, plus organizer-only `DELETE .../`, `POST .../invite/`, `GET .../participants/`, and participant-only `POST .../register/\|accept/\|reject/\|cancel/` depending on `my_participation.status` |
| `/events/<uuid:event_id>/edit/`| `EventEditPageView`              | `webui/event_form.html`         | `GET /api/v1/events/{id}/` to prefill, `PATCH /api/v1/events/{id}/` to save (organizer only; `access_type` is immutable so the field is disabled) |

All routes are mounted at the URL root via `path('', include('apps.webui.urls'))`
in `config/urls.py`, ahead of `/api/v1/...`.

## API coverage gaps

None — the UI now covers all of `docs/api-reference.md`'s auth and events
surface described above (see Routes).

## Pre-existing server-rendered pages

Two page views predate `apps/webui` and were only restyled (Materialize
`<link>`/font added, content wrapped in `<main class="container">`) to match
— their view logic in `apps/users/views.py` was not touched:

- `EmailVerificationConfirmPageView` (`/auth/email-verification/confirm/`) —
  the target of the link inside the verification email.
- `PasswordResetConfirmPageView` (`/auth/password-reset/confirm/`) — the
  target of the link inside the password-reset email. Unlike the `webui`
  pages, this one re-implements the password-set logic directly in the view
  rather than calling the JSON API, since the whole point of the page is to
  work from a plain, unauthenticated `<form method="post">` (no JS/fetch,
  no CSRF cookie to read yet).

## Adding a new page

1. Add a `TemplateView` in `apps/webui/views.py` and a route in
   `apps/webui/urls.py`.
2. Add a template under `apps/webui/templates/webui/` that
   `{% extends 'webui/base.html' %}` and fills `{% block content %}` (and
   `{% block extra_js %}` if it needs its own script).
3. If it talks to the API, add a small script under
   `apps/webui/static/webui/js/` that uses `apiFetch` from `api.js` —
   don't hand-roll cookie/CSRF handling.
