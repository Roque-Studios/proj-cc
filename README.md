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
| `PAYMENT_PROVIDER` | `mock` | Active payment gateway: `mock`, `stripe`, or `paypal`. Switching is a config change only — the subscription business logic is gateway-agnostic |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_API_BASE` | empty / Stripe base | Stripe credentials — required when `PAYMENT_PROVIDER=stripe` |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` / `PAYPAL_WEBHOOK_ID` / `PAYPAL_ENVIRONMENT` | empty / `sandbox` | PayPal credentials — required when `PAYMENT_PROVIDER=paypal` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_TLS` | empty / `587` / … / `true` | Optional SMTP for payment-failure notifications. When `SMTP_HOST` is empty the notify task degrades to a structured log (dev/mock setups) |

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
complexity, login/JWT, wrong-credentials 401, refresh, protected `/auth/me`),
the role/creator model, token revocation, the subscription model, viewer
access-level classification, and the payment gateway abstraction (mocked
provider; business logic independent of any real gateway).

## Payment gateway abstraction

All payment flows go through the `PaymentProvider` interface in
`backend/app/payments/base.py` (create customer, create/cancel subscription,
verify webhook, charge one-time). Implementations: `mock` (in-memory, default),
`stripe`, and `paypal` (httpx-based). `backend/app/services/subscriptions.py`
is the only place that orchestrates the subscription lifecycle, and it depends
**only** on the interface — so adding a gateway is a one-line registration in
`app/payments/factory.py` plus a provider class, with zero business-logic
changes. Set `PAYMENT_PROVIDER` to switch.

### Stripe subscriptions

`POST /api/webhooks/stripe` receives signed Stripe events (set it as your
Stripe webhook endpoint, `invoice.paid` / `invoice.payment_failed` events are
reconciled against local subscriptions). Stripe checkout collects the payment
method on the hosted page; the customer is created once per user and cached on
`user.payment_customer_id`. Provider webhooks are also available at
`/api/webhooks/mock` (dev) and `/api/webhooks/paypal`.

Stripe integration is covered by `backend/tests/test_stripe_integration.py`,
which simulates the Stripe API with `httpx.MockTransport` (no network): the
test-mode subscription flow, `invoice.paid` → `active`, and
`invoice.payment_failed` → `past_due`.

### Webhook handling — renewal, failure & idempotency

Every provider webhook is **signature-verified** per gateway before anything
else (bad signature / malformed body → `400`, unknown provider → `404`,
unconfigured provider → `503`). Verified events are reconciled via
`SubscriptionService.handle_webhook`:

- **Renewal success** (`invoice.paid` / `payment.succeeded` /
  `PAYMENT.SALE.COMPLETED`) → status `active`, period dates applied, checkout
  url cleared.
- **Renewal failure** (`invoice.payment_failed` / `payment.failed` /
  `PAYMENT.SALE.DENIED`) → status `past_due` (the grace period) and a
  notification task is enqueued — **exactly once**, on the transition into
  `past_due`. With SMTP configured the worker emails the subscriber; otherwise
  it logs the notification (see `tasks.notify_payment_failed`).
- **Idempotent processing** — each processed `(provider, event_id)` is recorded
  in the `processed_webhook_event` ledger **in the same transaction** as the
  reconciliation. A provider redelivery of the same event id is acknowledged
  with `"duplicate": true` and re-applies nothing (no duplicate renewal, no
  double notification). Unverified events can never pollute the ledger.

Gateway refs are unique per provider (`uq_subscription_provider_ref`), so
reconciliation by `external_ref` is unambiguous. Webhook behavior is covered
by `backend/tests/test_webhook_renewal.py` with mocked payloads for mock /
Stripe (signed) / PayPal (mocked verification endpoint) gateways.

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