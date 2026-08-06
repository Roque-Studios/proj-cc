# A singel content creation platform
This is a full stack project ready to be deployed in any server with docker compose, to manage your own content page.
Think this as a Solo Patreon page.

## Features
- Admin panel to manage content.
- Subscription based mechanism

## Running

```bash
cp .env.example .env
# Generate a strong SECRET_KEY and paste it into .env:
python -c "import secrets; print(secrets.token_hex(32))"
docker compose up -d --build
```

## Configuration & environment variables

All configuration happens through environment variables, loaded from `.env`
(see `.env.example` for the full template). The app **fails fast at startup with
a clear error** if any required variable is missing or empty.

### Required

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string (e.g. `postgresql://user:pass@db:5432/db`) |
| `REDIS_URL` | Redis connection string — Celery broker (DB 0); base for result backend (DB 1) and media cache (DB 2) |
| `SECRET_KEY` | JWT signing secret — generate with `python -c "import secrets; print(secrets.token_hex(32))"`; must be non-empty |
| `CC_VERSION` | Semantic version reported by the API (e.g. `0.1.0`) |

### Optional

| Variable | Default | Description |
| --- | --- | --- |
| `ENVIRONMENT` | `dev` | `dev` or `prod`. In `prod`, an insecure placeholder `SECRET_KEY` refuses to start |
| `ALLOWED_ORIGINS` | localhost origins | Comma-separated CORS origins |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | `REDIS_URL` (DB 0 / 1) | Celery broker / result backend overrides |
| `WATERMARK_CACHE_REDIS_URL` / `WATERMARK_CACHE_TTL_SECONDS` | `REDIS_URL` DB 2 / `3600` | Redis cache for watermarked media and its TTL |
| `LOG_LEVEL` | `INFO` | Python log level |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_MINUTES` | `21600` / `10080` | JWT token lifetimes (minutes) |
| `SENTRY_DSN` | unset | Sentry error tracking DSN |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | dev defaults | Bootstrap admin credentials — **override in production** |

> Payment gateway keys and media storage paths will be added as environment
> variables when those features are implemented.

### Security

- **Never commit a real `.env`.** Only `.env.example` (with placeholders) is
  tracked; `.env` and `.env.*` variants are gitignored.
- Pass real secrets through your deployment mechanism (secrets manager, CI/CD
  variables, etc.) rather than files in the repo.
- `SECRET_KEY` must be a fresh random value per environment — the app refuses to
  start in `prod` with a placeholder, and refuses to start anywhere with an empty value.

## Tests

```bash
# Backend unit tests (run inside the api container; uses an isolated SQLite DB):
docker compose exec api python -m pytest /app/tests -v
```

Covers the auth endpoints (registration, duplicate-email rejection, password
complexity, login/JWT, wrong-credentials 401, refresh, protected `/auth/me`).

## Background jobs & caching (Celery + Redis)

The `redis` service is used as the Celery **broker** (DB 0), **result backend** (DB 1),
the **cache for watermarked media** (DB 2) and the **JWT token revocation list**
(DB 3 — revoked refresh/access tokens live there until they expire).
The Celery worker runs as its own compose service:

```bash
docker compose up -d --build worker
# tail logs to confirm "celery@... ready" and "Connected to redis://..."
docker compose logs -f worker
```

API and worker share the same env config; the API dispatches tasks from `app.tasks`.

### Sanity checks

```bash
# 1) Test task enqueued and completed via the Redis broker/result backend
docker compose exec api python -c \
  "from app.tasks import debug_add; r = debug_add.delay(2, 3); print('result:', r.get(timeout=15))"

# 2) Cache keys expire per TTL config
docker compose exec api python -c "
from app import cache
cache.set_watermarked_media('demo', b'watermarked-bytes', ttl_seconds=2)
print('ttl after set:', cache.get_cached_media_ttl('demo'))
import time; time.sleep(3)
print('after 3s:', cache.get_cached_watermarked_media('demo'))
"
```