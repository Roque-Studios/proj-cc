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
| `ORIGINAL_MEDIA_STORAGE_PATH` / `MAX_MEDIA_SIZE_BYTES` / `ALLOWED_MEDIA_EXTENSIONS` | `/data/media/original` / `10485760` / `.jpg,.jpeg,.png,.webp,.gif` | Where **private unwatermarked originals** live (never served directly — only internal code reads them; `GET /content/{post_id}/media?media_id={id}` serves the original **watermarked on the fly** to the post's creator or an active follower), the per-file size cap, and allowed extensions |

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

### Photo posts (creator uploads)

`POST /api/posts` — creator-only (`403` for registered users) multipart upload
with an optional `caption` and **one or more** image files. Every file is
validated before anything is persisted: extension in the allowed set, declared
`Content-Type` is `image/*`, **magic bytes** match (spoof-proof, and the
sniffed type is authoritative over the extension), and the size cap
(`MAX_MEDIA_SIZE_BYTES`) is enforced with a bounded chunked read (`413` on
exceed). A post requires at least one file (`400`).

Uploads are stored **only** as private originals under
`ORIGINAL_MEDIA_STORAGE_PATH` (a named docker volume, `media_data`) keyed by an
unguessable uuid; nothing is persisted as a public copy. The `Post` /
`PostMedia` models scope posts to their creator. Covered by
`backend/tests/test_posts.py`.

### Original-media storage (private)

The unwatermarked **originals** are stored separately behind a storage
abstraction (`app/storage.py` — `MediaStorage` / `DiskMediaStorage`, swap for
S3/GCS later) under `ORIGINAL_MEDIA_STORAGE_PATH` (`/data/media/original`).
They are **never served**: no FastAPI route reads that store, no nginx location
proxies it, and only internal service code can read originals. Covered by
`backend/tests/test_storage.py` (roundtrip, traversal guard, privacy checks).

### Image watermarking (per-viewer, on the fly)

`app/watermark.py` is an OnlyFans-style traceable watermark service: every
`GET /content/{post_id}/media?media_id={id}` request re-encodes the private
original with the **requesting viewer's** identity — short hashes of their
user ref and the post id plus a UTC timestamp tiled diagonally across the
image (white text, dark outline + shadow, legible over arbitrary content).
Because the viewer is only known at request time, the watermark is applied on
the fly at serve time; no pre-watermarked copy is persisted, and the original
bytes are never exposed.

- **Deterministic placement** — the layout is seeded from a hash of the image
  bytes + viewer ref: the same (image, viewer) always yields byte-identical
  output (traceable, cacheable per viewer), while different viewers get
  different placements and text.
- **Identity source** — the media endpoint accepts the access JWT via either
  the `Authorization` header or a `?token=` query parameter (`<img>` tags
  can't send an `Authorization` header). The viewer ref is echoed in the
  `X-Watermark` response header.
- **Format-preserving** — JPEG/PNG/WEBP re-encode in-kind; animated GIFs are
  rasterized to their first frame and served as PNG. Responses are always
  `Cache-Control: no-store` (the watermark is volatile) with
  `X-Content-Type-Options: nosniff`.
- **Validation pairing** — uploads must actually decode (Pillow) at ingestion,
  so header-spoofed/truncated files are rejected with `400` instead of failing
  at serve time.

Covered by `backend/tests/test_watermark.py` (text format & decode-back
pairing, byte-determinism, per-viewer/per-timestamp differences, ink coverage
legibility, format handling, `render_served_media` wiring).

### Secure content-media endpoint (auth + authz + watermark)

`GET/HEAD /content/{post_id}/media?media_id={id}` streams one media file of a
post to an **authorized** viewer only:

1. **Authenticate** — the access JWT must be present (Bearer header or
   `?token=` for `<img>` tags); missing/invalid/revoked → `401`.
2. **Authorize** — the post's creator, or an **active follower** (active or
   trialing subscription whose current period hasn't ended) → `200`; a
   registered non-follower or expired subscription → `403`; unknown post →
   `404`; a media id that doesn't belong to the post → `404`.
3. **Watermark** — the private original is watermarked on the fly for that
   viewer (`media.serve_media`; a Redis cache hit skips the re-encode and
   reports `X-Watermark-Cache: hit`). The response is the transformed blob,
   **never the original**.
4. **Stream** — `Cache-Control: no-store` (also enforced by nginx), plus
   `X-Watermark` (viewer ref) and `X-Content-Type-Options: nosniff` headers.

The storage key is never part of any URL, and the legacy unauthenticated
`/media/{key}` route has been **removed** — there is no way to fetch media
without passing the authorization check. Covered end-to-end for every viewer
role (anon `401`, non-follower `403`, expired `403`, follower / trialing /
creator `200`, cross-post media `404`, cache hit on repeat) by
`backend/tests/test_content_media.py`.

### Watermark traceability (abuse investigation)

Every served watermark embeds a truncated sha256 hash of the **viewer ref**
(`user:{id}`) and the **post id** (`post:{id}`) plus the capture timestamp,
so a leaked screenshot can be traced back to who fetched it, when, and from
which post:

```
a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00 UTC   # viewer_hash post_hash when UTC
```

`GET /admin/watermark-trace?text=<the watermark text line>` decodes it
(`app/watermark_trace.py`): the one-way hashes are resolved by enumerating
the sequential id spaces (O(max_id) sha256 per hash — fine for an
investigation tool; keep it off hot paths) and the tool returns the
originating `user_id` / `user_email` and `post_id` / `post_caption`, plus
the capture time. Deleted rows still resolve while a later id keeps the
enumeration bound above them (email/caption are then null); only tail
deletions of the highest ids drop out of reach. A truncated-hash collision
(40 bits — exponentially unlikely) is surfaced via the `user_matches` /
`post_matches` counts. Legacy watermarks (rendered before the post identity
existed) decode to the user with a `null` post.

**Access is restricted to the admin role** — which on this single-operator
platform *is* the creator role (`deps.require_admin`; no separate admin role
exists): anonymous → `401`, registered users → `403`, creators → `200`.
Malformed text → `400`; a viewer hash matching no known user → `404`.
Covered by `backend/tests/test_watermark_trace.py` (parse/legacy handling,
hash round-trip resolving user+post, deleted-post trace survival, and the
endpoint's role gates).

### Watermark cache (Redis + TTL)

Watermarked output is **viewer-specific**, so renders are cached in Redis keyed
by **(viewer, media)** (`watermarked:media:{user_ref}:{media_id}`) with TTL
`WATERMARK_CACHE_TTL_SECONDS` (default `3600`, DB 2). The second request for
the same viewer + media is served straight from Redis instead of re-encoding
(`X-Watermark-Cache: hit` response header; a miss logs `watermark_cache_miss`
with the render duration in `render_ms`) — most valuable for large/video
media. Because the identity is part of the cache key, one viewer's watermark is
never served to another, and cached bytes carry the first render's timestamp,
so staleness is bounded by the TTL.

The cache is **best-effort**: a Redis outage degrades to a miss (render and
serve) — media serving never fails because of the cache. Entries can be
invalidated with `cache.delete_watermarked_media(user_ref, media_id)`.
Covered by `backend/tests/test_cache.py` (hit/miss, per-viewer separation, TTL
eviction, a render spy proving the second request skips the pipeline, and
Redis-outage degradation), running against an in-memory fake Redis with real
wall-clock TTL expiry.

### Follower feed (access-gated)

`GET /api/creators/{creator_id}/posts?page=1&page_size=20` returns the creator's
posts, newest first, paginated (`page`, `page_size`, `total`, `has_more`). The
viewer is classified with `access.resolve_viewer_access`:

- **active follower** (active/trialing subscription with a current period) →
  full posts with media urls (`teaser: false`); the urls point at the
  auth-gated `/content/{post_id}/media?media_id={id}` endpoint;
- **anonymous or registered non-follower** → a teaser (`teaser: true`):
  captions + media counts, but **media urls are withheld** so the feed never
  leaks links to locked content;
- unknown / non-creator id → `404`.

Covered end-to-end across all three access levels (plus expired-subscription
and pagination) by `backend/tests/test_feed.py`.

### Paid broadcasts & one-time unlocks

A creator can post a **paid broadcast** by passing `price_cents` to
`POST /posts` (the same validated media upload as a regular post). The
broadcast is delivered to all subscribers as a **locked preview**: the feed
shows the caption, media count and the one-time price
(`broadcast_price_cents`) with `unlocked: false` and **media urls withheld**
— the client renders the blurred/locked card from that metadata.

`POST /content/{post_id}/unlock` charges the one-time price through the
payment abstraction (`PaymentProvider.charge_one_time`, same gateway as
subscriptions) and records a `BroadcastUnlock` row — one per (subscriber,
post), idempotent: repeating an already-paid unlock returns the existing row
with `already_unlocked: true` and never charges twice. After the unlock the
feed returns the full post (`unlocked: true`, media urls included) and the
media endpoint serves the watermarked blob. The post's creator always has
full access (the unlock endpoint rejects them with `409`).

Access rules:

- anonymous → `401`; registered non-subscriber → `403`;
- subscriber without payment → locked preview in the feed, `403` on media;
- subscriber after unlock → full watermarked media (`Cache-Control: no-store`);
- creator (owner) → full media without paying.

Covered by `backend/tests/test_broadcast.py` — the unit lock/unlock state
machine (charge recorded exactly once, failed charge grants nothing, regular
posts rejected) plus the end-to-end lock → unlock → full-access integration
flow across the feed, media and unlock endpoints.

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

# 2) Watermark cache: keys are per (viewer, media) and expire per TTL config
docker compose exec api python -c "
from app import cache
cache.set_watermarked_media('anon', 'demo', b'watermarked-bytes', ttl_seconds=2)
print('ttl after set:', cache.get_cached_media_ttl('anon', 'demo'))
import time; time.sleep(3)
print('after 3s:', cache.get_cached_watermarked_media('anon', 'demo'))
"
```