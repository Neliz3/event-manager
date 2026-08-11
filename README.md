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
