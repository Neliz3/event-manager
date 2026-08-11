# Event Manager

A Django REST API for managing events (conferences, meetups, etc.). Supports creating, viewing, updating, and deleting events, as well as handling user registrations for them.


## Setup

### 1. Local Env

```bash
cp .env.example .env
uv run python -c "from django.core.management.utils import get_random_secret_key; print (get_random_secret_key())"
```

Paste the output into `.env` as `DJANGO_SECRET_KEY=...`, and fill in
`POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`.

### 2. Run via Docker

```bash
docker compose up --build -d
docker compose run --rm app uv run python manage.py migrate
```

App: `http://localhost:8000`

```bash
docker compose logs -f app   # logs
docker compose down          # stop
```


### 3. Create superuser

```bash
docker compose exec app uv run python manage.py createsuperuser
```

### 4. Model changes / migrations

```bash
docker compose exec app python manage.py makemigrations users events
docker compose exec app python manage.py migrate
docker compose exec app python manage.py check
```

**Future improvement**: the `app` image doesn't currently install the
system `cron` package or run `crontab add`, so `django-crontab`'s
`CRONJOBS` setting is inert — `cleanup_expired_tokens` must be run
manually (above) until this is wired up. Add a separate `cron` service in
`docker-compose.yml` (same image, `command` running `crontab add && cron -f`
instead of `runserver`, with `cron` installed via `apt-get` in the
Dockerfile for that service) rather than bolting cron onto the `app`
container — avoids duplicate cleanup runs if `app` is ever scaled to
multiple replicas.

### 5. Tests

```bash
docker compose run --rm app python manage.py test
```

### 6. API docs (Swagger)

With the app running, open the Swagger UI in your browser:

`http://127.0.0.1:8000/api/docs/`

- `/api/docs/` — Swagger UI (interactive, "Try it out" buttons to actually call endpoints)
- `/api/redoc/` — ReDoc (read-only, nicer for browsing)
- `/api/schema/` — raw OpenAPI 3 YAML schema, auto-generated from your serializers/views

Auth is cookie-based (`access_token` / `refresh_token` are set as HttpOnly
cookies on login), so once you're logged in Swagger UI just works — no need
to paste a token into the "Authorize" button.

**Troubleshooting: Swagger UI shows no endpoints, just "401 Unauthorized"**

This means a stale/expired `access_token` cookie from a previous session is
being sent with the request for the schema itself, which fails
authentication before permissions are even checked. Fix:

1. Open DevTools → Application/Storage → Cookies → `http://127.0.0.1:8000`.
2. Delete `access_token` and `refresh_token` cookies.
3. Reload `http://127.0.0.1:8000/api/docs/`.
