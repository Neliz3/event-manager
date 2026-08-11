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

### 5. API docs (Swagger)

With the app running, open the Swagger UI in your browser:

`http://127.0.0.1:8000/api/docs/`

- `/api/docs/` — Swagger UI (interactive, "Try it out" buttons to actually call endpoints)
- `/api/redoc/` — ReDoc (read-only, nicer for browsing)
- `/api/schema/` — raw OpenAPI 3 YAML schema, auto-generated from your serializers/views
