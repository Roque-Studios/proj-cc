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
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` / `PAYPAL_WEBHOOK_ID` / `PAYPAL_ENVIRONMENT` / `PAYPAL_PRODUCT_ID` | empty / `sandbox` / empty | PayPal credentials — required when `PAYMENT_PROVIDER=paypal`. `PAYPAL_ENVIRONMENT` is `sandbox` or `live`; `PAYPAL_PRODUCT_ID` optionally pins the catalog product billing plans attach to |
| `WOMPI_CLIENT_ID` / `WOMPI_CLIENT_SECRET` / `WOMPI_ENVIRONMENT` / `WOMPI_API_BASE_URL` / `WOMPI_TOKEN_URL` / `WOMPI_WEBHOOK_URL` / `WOMPI_REDIRECT_URL` / `WOMPI_3DS_REDIRECT_URL` | empty / `sandbox` / Wompi API URLs / empty | Wompi credentials — required when `PAYMENT_PROVIDER=wompi`. Environment is per-app (each applicativo is marked sandbox/production in the panel), not a URL switch. `WOMPI_WEBHOOK_URL` is the backend's `POST /api/webhooks/wompi` endpoint — sent as each payment link's `configuracion.urlWebhook` (Wompi only notifies payment links through it, so a paid subscription never activates without it). `WOMPI_REDIRECT_URL` is where the customer returns after paying a subscription link; `WOMPI_3DS_REDIRECT_URL` is the legacy alias |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_TLS` | empty / `587` / … / `true` | Optional SMTP for payment-failure notifications. When `SMTP_HOST` is empty the notify task degrades to a structured log (dev/mock setups) |
| `ORIGINAL_MEDIA_STORAGE_PATH` / `MAX_MEDIA_SIZE_BYTES` / `ALLOWED_MEDIA_EXTENSIONS` | `/data/media/original` / `10485760` / `.jpg,.jpeg,.png,.webp,.gif` | Where **private unwatermarked originals** live (never served directly — only internal code reads them; `GET /content/{post_id}/media?media_id={id}` serves the original **watermarked on the fly** to the post's creator or an active follower), the per-file size cap, and allowed extensions |
| `BANNER_STORAGE_PATH` / `AVATAR_STORAGE_PATH` | `/data/media/banner` / `/data/media/avatar` | Where **public creator profile images** live — hero banners and avatars uploaded from the admin dashboard, served to any visitor via `GET /media/banner/{key}` / `GET /media/avatar/{key}` (content type follows the uploaded extension) |

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
`stripe`, `paypal` (httpx-based) and `wompi` (via the
`pywompi` package). `backend/app/services/subscriptions.py` is the only place
that orchestrates the subscription lifecycle, and it depends **only** on the
interface — so adding a gateway is a one-line registration in
`app/payments/factory.py` plus a provider class, with zero business-logic
changes. Set `PAYMENT_PROVIDER` to switch.

### Per-creator payment gateways (settings UI)

Gateways are **strictly per-creator**: each creator enables the gateways their
subscribers can pay with and enters that gateway's **own credentials** (the
`creator_gateway_config` table, one row per creator+gateway). There is **no
fallback to platform env keys for checkout** — a gateway only appears in a
subscriber's checkout once the creator enabled it with a complete config.

Required credentials per gateway: **Stripe** — secret key + webhook secret;
**PayPal** — client id, client secret, webhook id (+ environment, optional
plan id); **Wompi** — `WOMPI_CLIENT_ID` + `WOMPI_CLIENT_SECRET` + the
**webhook URL** (`POST /api/webhooks/wompi` — required, since Wompi only
notifies payment links through it), plus optional environment and redirect
URLs; **mock** — none (a zero-config dev gateway, backend-only, hidden from
the settings UI).

- `GET /api/creator/gateway-settings` — creator-only; returns every gateway's
  form metadata (labels, placeholders, allowed values) with per-field
  **`configured` booleans** — secret values are **never** returned. Stored
  values of **non-secret** fields (e.g. the environment select) are echoed
  via a per-field `value` so the form pre-fills them and a save never
  silently resets them.
- `PUT /api/creator/gateway-settings/{gateway}` — update the enabled flag and
  config. Enabling a gateway with incomplete required config is a `400`
  listing the missing fields; constrained values (environments) are
  validated. Config merges over stored values — empty strings
  keep the existing secret, so updates never wipe what the client can't see.
- `GET /api/creators/{creator_id}/gateways` — public; the gateways a
  subscriber can pay with (**only** enabled + configured ones), so checkout
  UI renders exactly what the creator accepts.
- `POST /api/subscribe` accepts an optional `provider`; when omitted, a single
  enabled+configured gateway is used (none → `400`, several → `400` asking to
  specify one). The provider is built from the creator's stored config (see
  `app/payments/factory.build_provider_from_config`).
- **Webhooks** verify against **every registered credential set** for the
  gateway — the platform env config first (keeps mock/dev flows working),
  then each creator's stored config — so an event signed with a creator's own
  webhook secret reconciles. A forged event fails all candidates → `400`.

**Admin page (frontend)** — the admin dashboard lives at `/admin` (built
from the `roque-*` components: cards, switches, text fields, badges, toasts;
`/settings.html` remains a working alias):

1. Seed the creator (admin) account once — this platform treats the creator
   role as the admin role:

   ```bash
   docker compose exec api python -m app.seed_creator --email admin@you.io --password 'S3cret!'
   ```

2. Visit `http://localhost/admin` and sign in with the seeded account. The
   panel is gated to the creator role — a regular account is redirected away.
3. Toggle Stripe / PayPal / Wompi and enter each gateway's credentials in the
   **Settings** tab. A gateway's switch stays disabled until its required
   config is complete (the backend enforces the same rule). Secret fields are
   password-masked and their values are never shown back — a field marked
   "✓ saved" keeps its value when you leave it blank, and a new value
   replaces it. The settings tab renders the gateway cards even if the
   profile/messaging panels fail, and lazy profile creation is race-safe, so
   a first visit never blanks the page.

The frontend uses a token-based fetch client (`frontend/src/lib/api.ts`,
Bearer access token in localStorage); in dev, `yarn dev` proxies `/api/*` to
the backend (see `frontend/vite.config.ts`). Covered by
`backend/tests/test_gateway_settings.py` (guards, enable validation, secret
non-echo, merge semantics), `test_gateway_subscribe.py` (checkout listing +
strict per-creator resolution + factory mapping), and
`test_gateway_webhooks.py` (per-creator webhook secret matching, forged
rejection).

### Photo posts (creator uploads)

`POST /api/posts` — creator-only (`403` for registered users) multipart upload
with an optional `caption` and **one or more** image files (the admin
Content tab's Publish-post composer is the UI for it). Every file is
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

`app/watermark.py` is a traceable watermark service: every
`GET /content/{post_id}/media?media_id={id}` request re-encodes the private
original with the **requesting viewer's** identity — short hashes of their
user ref and the post id plus a UTC timestamp rendered as a small,
semi-transparent line in the image's **bottom-right corner** (subtle enough
not to spoil the photo, persistent enough to trace a leak). Because the
viewer is only known at request time, the watermark is applied on the fly at
serve time; no pre-watermarked copy is persisted, and the original bytes are
never exposed.

- **Traceable text** — the text (viewer/post hashes + UTC timestamp) carries
  the identity, so every viewer still receives byte-different output even
  though the corner placement is fixed; the same (image, viewer, timestamp)
  is always byte-identical (traceable, cacheable per viewer).
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
payment abstraction (`PaymentProvider.charge_one_time` — a one-time charge,
**entirely separate from the monthly subscription charge**) and records a
`PaidUnlock` row — one per (subscriber, post), idempotent: repeating an
already-paid unlock returns the existing row with `already_unlocked: true`
and never charges twice. After the unlock the feed returns the full post
(`unlocked: true`, media urls included) and the media endpoint serves the
watermarked blob. The post's creator always has full access (the unlock
endpoint rejects them with `409`). A failed charge returns `402` and grants
**nothing** — no `PaidUnlock` row is written.

**Refunds revoke access.** The gateway's refund webhook (`charge.refunded` /
`PAYMENT.CAPTURE.REFUNDED` / mock `payment.refunded`) is normalized to
`payment.refunded` and reconciled by `BroadcastService.handle_refunded`
(route: `/webhooks/{provider}`), which stamps `refunded_at` on the matching
`PaidUnlock` — matched by external ref first (Stripe's `payment_intent`),
then by the `subscriber_id`/`post_id` charge metadata (PayPal sends the
capture id, not the order id we store). Once refunded the broadcast locks
again in the feed and media (`403`), and the subscriber can **re-purchase**
— the same row is reactivated in place with a fresh charge. Refund events are
idempotent per provider event id (same `processed_webhook_event` ledger as
subscriptions); a refund for an unknown charge is a no-op.

Access rules:

- anonymous → `401`; registered non-subscriber → `403`;
- subscriber without payment → locked preview in the feed, `403` on media;
- subscriber after unlock → full watermarked media (`Cache-Control: no-store`);
- subscriber after a refund → locked again until re-purchase;
- creator (owner) → full media without paying.

Covered by `backend/tests/test_broadcast.py` — the unit lock/unlock state
machine (charge recorded exactly once, failed charge grants nothing, refund
revokes + re-purchase reactivates, refund matched by ref or metadata,
idempotent refund redelivery) plus the end-to-end lock → unlock → full-access
integration flow and the **success / failure / refund** acceptance scenarios
for the one-time charge. Stripe's `charge.refunded` normalization is covered
in `backend/tests/test_stripe_integration.py` and PayPal's
`PAYMENT.CAPTURE.REFUNDED` in `backend/tests/test_webhook_renewal.py`.

### Creator content dashboard

`GET /api/creator/content` (creator-only) lists the creator's own
posts/broadcasts, newest first, with the engagement stats the dashboard
shows: `view_count` (media views served to **non-owners** — each GET counts,
including watermark-cache hits; HEAD probes, the owner's own previews and
unauthorized requests never do) and `unlock_count` (active one-time unlocks
of a paid broadcast; **refunded unlocks are excluded**). Media urls are always
included (the owner can fetch their own media), plus `media_count`,
`broadcast_price_cents` and `is_visible`.

- `PATCH /api/creator/content/{id}` — edit the caption (`null`/whitespace
  clears it) and/or flip `is_visible`; only the provided fields are applied.
- `DELETE /api/creator/content/{id}` — permanently deletes the post, its
  media rows, unlock rows and the private originals from storage (unlock rows
  are deleted explicitly, not just by the Postgres FK cascade, so the delete
  is correct on any backend).

Every route is **creator-only** (`403` for registered users, `401` anonymous)
and scoped to the caller: another creator's post is `404` — the same as a
missing post — so post ids can't be probed across creators.

**Visibility (soft-archive)** — a post with `is_visible=false` (default
`true`) stays fully editable in the dashboard but disappears from the
follower feed, and media/unlock requests `404` for everyone but its creator
(anonymous probes get the same `404` as a nonexistent post — a hidden post is
indistinguishable from a missing one). The creator's own media previews keep
working.

**Frontend** — the admin panel at `/settings.html` is now tabbed: **Settings**
(gateways + messaging) and a mobile-first **Content** tab (built from the
`roque-*` components — cards, badges, switches, textarea, dialog, toast): a
**publish composer** (caption + multi-photo picker with previews + optional
one-time unlock price for paid broadcasts, posted to `POST /posts`), a stats
bar (posts / views / unlocks), stacked post cards with watermarked thumbnails
(fetched with the access token via `?token=`, the `<img>` mechanism),
paid-broadcast + hidden badges, an edit dialog (caption + visibility), a
delete confirmation, and an immediate-save visibility switch that reverts on
error. Covered by `backend/tests/test_creator_content.py` (role gates,
listing + stats, unlock counts incl. refunds, view counting, caption edits,
visibility gating across feed/media/unlock, and delete cleaning rows +
storage).

### Subscriber management + revenue

`GET /api/creator/subscribers` (creator-only) lists the **owning creator's**
subscriptions, newest first, paginated (`page`, `page_size`, `total`,
`has_more`) and filterable by `?status=` (`active`, `trialing`, `incomplete`,
`past_due`, `canceled`, `expired`). Each row carries the subscriber's identity
and username, the status, subscription start date (`started_at`) and current
billing period, `cancel_at_period_end`, and the gateway provider. A different
creator (or a registered/anonymous user) gets `401`/`403`/`404` — revenue and
subscriber data are strictly the owning creator's.

The response also carries a global **revenue summary**: `monthly_revenue_cents`
(subscription payments), `one_time_revenue_cents` (broadcast unlocks) and
`total_revenue_cents`, plus subscriber counts per status. Revenue is summed
from the new **payment ledger** (`payment` table): every completed monthly
charge (each provider's payment-succeeded event / mock activation) and every
one-time unlock records one row **atomically with the transaction that
recorded the charge**; a refunded unlock marks its row `refunded` so it drops
out of the totals. The summary therefore always equals the sum of completed
payments in the DB by construction. `post_id` on the ledger deliberately has
no FK — revenue history survives a post being deleted.

Revenue-accuracy detail: only **recurring** charge events (flagged
`recurring` on the normalized event) may use the subscription email-fallback
reconciliation, so a gateway's one-time purchase webhook (e.g. a Wompi
`TransaccionCompra` event with a payer email but no subscription ref) can
never be reconciled against a subscription — no spurious monthly payment is
ever recorded for a one-time unlock.

**Frontend** — the admin panel at `/settings.html` gained a mobile-first
**Subscribers** tab (roque-* components): revenue summary cards (monthly /
one-time / total), status filter chips, paginated subscriber cards with status
badges, cancel-at-period-end indicator and `roque-pagination`. Covered by
`backend/tests/test_creator_subscribers.py` (role gates, own-only list,
pagination, status filtering, revenue == DB sum exercised end-to-end via real
subscription and unlock/refund flows) and the Wompi one-time-event regression
in `backend/tests/test_wompi_integration.py`.

### Public creator landing page

`GET /api/creators/{creator_id}/landing` (public, no auth needed) powers the
creator's public landing page at `/creator/{id}` (nginx serves `index.html`
there). Visiting the site root `/` with no creator id falls back to
`GET /api/creators/default/landing` — the **first/seed creator** — so a
single-creator platform can point its homepage straight at the creator (a
`404` before any creator exists shows an empty state). It returns the
creator's **public** identity — display name, bio, avatar, hero banner and
visible **post count** — plus their social accounts and the payment gateways
enabled for checkout, together with the **requesting viewer's** access level:

- **anonymous** → a hero with a **"Join free"** button — creating a free
  account (the existing `/login` register flow), after which the paywall is
  the subscription tier;
- **registered (non-follower)** → the same hero plus account context (who is
  logged in) and a **Subscribe** button that opens the hosted checkout (the
  existing `POST /subscribe` flow, resolving strictly from the creator's
  enabled gateways);
- **follower** → the hero with a subscriber welcome.

Every visitor also gets the **posts grid** below the hero: followers see the
full feed (`GET /creators/{id}/posts` returns full posts with watermarked
thumbnails), everyone else sees the same posts as **blurred previews** (see
*Blurred previews* below) — the real bytes are never exposed.

Classification is the shared viewer access resolver, so an expired
subscription reverts to the registered view. The landing endpoint itself
never leaks subscriber data — only public profile fields and enabled gateways.

**Hero banner & avatar** — the creator uploads both from the admin dashboard
(`POST /creator/banner` / `POST /creator/avatar`, creator-only, validated like
post media; the files land in the public profile-image stores and
`CreatorProfile.banner_url` / `avatar_url` point at `GET /media/banner/{key}` /
`GET /media/avatar/{key}`, served to any visitor). The `DELETE` variants
remove them (the frontend falls back to a gradient banner / initial-letter
avatar). The same admin card edits display name and bio via
`PUT /creator/profile`.

**Social accounts** — `CreatorProfile.social_links` (JSON dict of
`twitter` / `instagram` / `tiktok` / `other` handles or urls) is editable by
the creator via `PUT /creator/profile` (unknown platforms rejected, empty
values remove a link) and shown as link chips on the landing page. The
frontend only ever navigates to `http(s)` urls from stored values, so a
stored link can't be an XSS vector.

The page is built mobile-first from the `roque-*` components (avatar, card,
badge, button, icon) and is the Vite main entry (`index.html`) alongside
`settings.html` / `feed.html` / `checkout.html` / `chat.html`. Covered by `backend/tests/test_landing.py` (anonymous /
registered / follower / expired states, 404s, banner + post-count payload,
social-links roundtrip + validation + exposure) and the role-by-role feed
tests in `backend/tests/test_feed.py`.

**Blurred previews (non-followers / locked broadcasts)** — `GET
/preview/{post_id}/media?media_id={id}` serves a **blurred, `PREVIEW`-stamped
transform** of a post's media to *any* visitor (no auth): the image is heavily
gaussian-blurred with a diagonal `PREVIEW` label (deterministic,
viewer-independent, cached once per media file), so visitors see the shape of
the content without anything usable leaking. Hidden posts and media that
doesn't belong to the post `404` exactly like the authenticated endpoint. The
feed wires these urls: every media item on a non-follower teaser (and on a
paid broadcast a follower hasn't unlocked) carries `preview_url` while the
real `media_url` stays withheld — the subscriber feed renders them as blurred
thumbnails (locked broadcasts show the one-time price + unlock CTA over the
blur).

### Subscriber feed view (frontend)

The mobile-first subscriber feed (`feed.html`, nginx `/feed/…`, built from the
`roque-*` components) consumes the follower-gated feed endpoint with
**infinite scroll** — an IntersectionObserver sentinel loads the next page as
it enters the viewport (page-key + post-id dedupe so a page can never
double-load, and a short retry backoff so a transient failure doesn't hammer
the API). Each post renders by its access state:

- **locked paid broadcast** — a styled lock preview with the one-time price
  and an **Unlock CTA**: `POST /content/{id}/unlock` (double-click guarded,
  errors toasted) then swaps the fresh post object in place, so the full
  broadcast — including every media file — renders immediately;
- **unlocked / regular post** — the full **watermarked media**, each `<img>`
  fetched through the secure content endpoint with the access token as
  `?token=` (the `<img>` authentication mechanism).

The reusable component is `roque-subscriber-feed`
(`src/components/feed/subscriber-feed.ts`); the `roque-subscriber-feed-page`
wrapper resolves the creator id from the URL and shows the login/subscribe
prompts for anonymous / registered non-followers (the endpoint's teaser data
only — urls withheld). The creator landing page's follower view reuses the
same component instead of its old inline simplified feed. Backend coverage of
the lock → unlock → full-access flow (incl. refunds re-locking) is in
`backend/tests/test_broadcast.py`; the feed role-by-role tests are in
`backend/tests/test_feed.py`.

### Sign in & accounts (shared `/login`)

One shared sign-in page (`login.html`, nginx `/login`) serves every role —
creators and followers use the same account system (`POST /auth/login`,
`GET /auth/me`). After sign-in the page redirects **by role**: creators go to
the `/admin` dashboard, everyone else returns to the page they came from
(an optional safe `?next=` target, or the site root). A small "create an
account" flow (`POST /auth/register`) is built into the same page, so a
new follower can register and subscribe without leaving the flow. The
landing page's "Log in to subscribe" CTA, the subscriber-feed prompt and the
anonymous checkout redirect all point here with `?next=` set, so a follower
signs in and lands right back where they were.

**Forgot your password?** Both sign-in pages link to the same reset flow
(`roque-password-reset`): `POST /auth/forgot-password` hands back a short-lived
reset code (emailed via SMTP when `SMTP_HOST` is set, otherwise returned as
`dev_token` so the flow works in dev), then `POST /auth/reset-password` sets the
new one. Reset codes are single-purpose JWTs (`type: "reset"`, 30 minutes) — an
access or refresh token can never be used as a reset code, and the endpoint
never reveals whether an email has an account.

In the Vite dev server the clean URLs fall back to the landing page (same as
`/checkout` / `/feed` / `/chat`) — use `/login.html` and `/admin.html`
directly in dev; nginx maps the clean `/login` / `/admin` URLs in production.

### Subscribe / checkout UI

The subscribe checkout (`checkout.html`, nginx `/checkout/…`, `roque-*`
components) is the payment entry point for visitors. `GET /api/creators/{id}/gateways`
returns **only the creator's enabled + configured** gateways, and the checkout
shows exactly that list as a picker; choosing one calls `POST /api/subscribe`
with the provider, which re-validates strictly against the creator's config.
The page shows the real monthly tier price and handles every state:

- **already a follower** — a success panel (no payment form);
- **pending payment** — an incomplete row is surfaced with its hosted
  checkout url (resume) plus a retry form;
- **no gateways enabled** — a clear "subscriptions unavailable" state;
- **payment started** — the user is redirected to the hosted checkout;
  on return, the page polls `GET /api/subscribe/status` (every 2 s, up to
  ~30 s) to reconcile the final state: the webhook-driven transition to
  `active`/`trialing` shows success, while `canceled`/`expired`/a
  still-`incomplete` row shows a clear payment-not-completed state.

`GET /api/subscribe/status` (authenticated) returns the viewer's subscription
row for a creator in any status — `incomplete` with its `checkout_url`,
`active`, `trialing`, `past_due`, `canceled` — plus their access level and the
tier price, which is what makes the return-reconciliation possible.

The landing page and subscriber-feed page route their Subscribe CTAs here.
Covered by `backend/tests/test_subscribe_status.py` and the existing
`test_subscribe.py` / `test_gateway_subscribe.py` suites.

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

### PayPal subscriptions (sandbox & live)

Set `PAYMENT_PROVIDER=paypal` with `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`
/ `PAYPAL_WEBHOOK_ID` from a PayPal REST app, and `PAYPAL_ENVIRONMENT`
(`sandbox` — `api-m.sandbox.paypal.com` — or `live` — `api-m.paypal.com`).
The provider speaks the **Billing Subscriptions API** (`httpx`, no SDK):
OAuth2 client-credentials auth, hosted subscription approval links, cancel,
and POST-back webhook verification.

**Billing plan bootstrap** — PayPal requires the monthly billing plan to exist
at the gateway before it accepts subscriptions. Create it once per environment:

```bash
docker compose exec api python -m app.payments.bootstrap_paypal
# -> Created billing plan P-XXXXXXXXXX (status ACTIVE).
# -> Set SUBSCRIPTION_TIER_PLAN_ID=P-XXXXXXXXXX and restart the API.
```

This creates the catalog product (unless `PAYPAL_PRODUCT_ID` is set) and an
**ACTIVE fixed-price monthly plan** priced at `SUBSCRIPTION_TIER_PRICE_CENTS`;
the printed plan id is what `SUBSCRIPTION_TIER_PLAN_ID` must hold (PayPal plan
ids look like `P-...`). In production, create the plan in the live app and
repeat with the live credentials.

**Webhooks** — register `POST /api/webhooks/paypal` as the webhook URL in the
PayPal app (sandbox or live) for `BILLING.SUBSCRIPTION.APPROVED` /
`ACTIVATED` / `CANCELLED` / `SUSPENDED` and `PAYMENT.SALE.COMPLETED` /
`DENIED`, and set `PAYPAL_WEBHOOK_ID` from that webhook. Signatures are
verified by POSTing the event back to PayPal's verify endpoint.

**Lifecycle** — subscribing returns the hosted **approve link** (the local row
is `incomplete` until then); the buyer's approval fires `APPROVED`, which
activates the subscription (checkout url cleared). Renewals reconcile **by
`billing_agreement_id`** — the `PAYMENT.SALE.*` resource is the sale (its own
id differs from the stored ref), so this is what keeps renewals matching the
local row; `SALE.COMPLETED` → `active`, `SALE.DENIED` → `past_due` with the
grace-period notification. PayPal events don't carry a billing period, so the
provider approximates a 30-day cycle from the event's `create_time` to keep
period-based access expiry working.

**Integration tests** — `backend/tests/test_paypal_integration.py` simulates
the sandbox Billing Subscriptions API with `httpx.MockTransport` (no network):
plan bootstrap, subscribe → pending row + approve link, `APPROVED` → `active`,
renewal success/failure reconciled by `billing_agreement_id` (regression-tested),
idempotent redelivery, cancel, and verification failures. A **real-sandbox**
test (`test_paypal_sandbox_subscribe_flow`) runs only when `PAYPAL_CLIENT_ID` /
`PAYPAL_CLIENT_SECRET` / `PAYPAL_WEBHOOK_ID` are set **and**
`RUN_PAYPAL_SANDBOX=1`: it bootstraps a plan and subscribes against the real
sandbox, printing the approve link (the buyer's approval is a hosted,
human step — webhook reconciliation is covered by the simulated tests).

### Wompi subscriptions

Set `PAYMENT_PROVIDER=wompi` with `WOMPI_CLIENT_ID` / `WOMPI_CLIENT_SECRET`
from a Wompi Commerce applicativo. Wompi authenticates with
**OAuth2 client credentials**
and is integrated through the `pywompi` package: the provider uses its generic
authenticated `request(method, path, json=)` for the endpoints below and its
`parse_event()` for webhook validation. The environment (sandbox vs
production) is a property of the applicativo — the credentials you configure —
not a URL switch; `WOMPI_API_BASE_URL` / `WOMPI_TOKEN_URL` overrides exist for
test accounts.

**Subscriptions via hosted payment links (with 3DS)** — Wompi's recurring-link
endpoint (`EnlacePagoRecurrente`) was creating bad states with no fix ETA, so
`create_subscription` uses `pywompi.WompiClient.create_payment_link`
(`POST /EnlacePago`) instead: it creates a hosted **one-time payment link**
whose payload is `identificadorEnlaceComercio` = the **creator id** (our
merchant reference, echoed back by webhooks), `nombreProducto` =
"subscription to <creator username>" (the creator tag), and `monto` =
`SUBSCRIPTION_TIER_PRICE_CENTS`. The link's `configuracion` carries
**`urlWebhook`** = `WOMPI_WEBHOOK_URL` (`POST /api/webhooks/wompi` — without
it Wompi never notifies us, so a paid subscription would never activate) and
**`urlRedirect`** = the per-checkout success url (falling back to
`WOMPI_REDIRECT_URL`) so the customer lands back on the checkout page after
paying; `urlRetorno` mirrors the cancel url when provided. The subscriber
completes the hosted page (3DS is handled there); the link id doubles as our
`external_ref`. Because a one-time link never auto-charges, cancellation is
**local-only** (the row is marked `canceled`; there is no recurring charge at
Wompi to disable) — and renewals need a **fresh link each month** (a later
paid link reconciles on the same row via webhook: it stays active and records
another payment).

**Webhooks (`wompi_hash` signature)** — Wompi signs every webhook with the
`wompi_hash` header: the HMAC-SHA256 of the **raw body** (byte for byte)
keyed with your API Secret. `POST /api/webhooks/wompi` validates via
`pywompi.parse_event` before touching anything; a bad signature is a `400`.
(Behind nginx, keep `underscores_in_headers on;` — the default ignores
header names containing underscores, so `wompi_hash` would be dropped and
**every** webhook would fail as a "possible spoof".)
Payment-link transactions arrive as a **flat payload**
(`ResultadoTransaccion` = "ExitosaAprobada" on success, `EnlacePago.Id` =
the link id we stored, `EnlacePago.IdentificadorEnlaceComercio` = our creator
id, `cliente.Email` = the payer) — Wompi does **not** send the legacy nested
`data.transaccion.estado` shape for payment links, so the flat form is
normalized first (the legacy shape is still parsed for compatibility).
`APROBADA`/"Exitosa…" activates the pending subscription — and later payments
reconcile — by matching the **echoed link id** directly, else the **merchant
reference + payer email** (creator id + `cliente.Email`), pinned so a
subscriber with rows for several creators activates the right one.
`external_ref` deliberately stays the link id. A rejected payment on an
active subscription moves it to `past_due` (grace-period notification).

**One-time charges** — `charge_one_time` uses
`POST /TransaccionCompra/TokenizadaSin3Ds` with a card token from client-side
tokenization (the Wompi JS SDK with your public key; pass it as
`payment_method_token`). Cards that require 3DS go through
`charge_one_time_3ds` (`POST /TransaccionCompra/3Ds`), which returns
`urlCompletarPago3Ds` to redirect the customer to; the outcome arrives by
webhook (`WOMPI_REDIRECT_URL` / `WOMPI_3DS_REDIRECT_URL` is the return URL).

Covered by `backend/tests/test_wompi_integration.py`, which fakes the sandbox
API through `pywompi.WompiClient(http_client=httpx.MockTransport(...))` (no
network): subscribe → pending row + hosted payment link (merchant ref +
product name asserted), signature validation (valid/forged/unknown-state),
`APROBADA` → active via link-ref and via (creator, email) matching —
including pinning the right creator when a subscriber has rows for several
creators — renewals, `RECHAZADA` → `past_due`, local-only cancel,
tokenized/3DS one-time charges, and config guards. A real-sandbox test
(`test_wompi_sandbox_subscribe_flow`) runs when `WOMPI_CLIENT_ID` /
`WOMPI_CLIENT_SECRET` are set **and** `RUN_WOMPI_SANDBOX=1`.

> **License note:** `pywompi` is GPL-3.0-or-later — keep that in mind if you
distribute this project as a closed product.

### Direct messages (creator ↔ subscriber, 1:1 threads)

Creator-to-subscriber DMs with **thread grouping**: a `Conversation` is the
unique `(creator_id, subscriber_id)` pair — every message between the same two
people lands in that one thread, so starting a "new" thread for an existing
pair is impossible (the unique constraint makes it idempotent). `Message`
rows carry `sender_id` / `recipient_id`, the body, and a `read_at` marker.

**The messaging gate** — a creator's `allow_messages_from_all_followers`
setting (on `creator_profile`, default **off**) controls who can start a
thread:

- the **creator may always message** a subscriber — this is what creates the
  "existing thread" a subscriber can later reply into;
- a **subscriber** may always **continue an existing conversation** (the
  acceptance carve-out: the block only applies when the sender isn't already
  in a thread);
- a subscriber with **no existing thread** must be an **active follower**
  (active/trialing subscription with a current period) and, when the creator's
  setting is off, is **blocked with a clear error** — `403 "This creator has
  messaging turned off — you can only message them if you already have an
  existing conversation"`;
- DMs are strictly creator ↔ subscriber: a creator messaging another creator
  is rejected (`400`), and the follower definition is the shared
  `access.is_active_follower` helper used by every content gate.

**The toggle** — `GET/PUT /api/creator/messaging-settings` (creator-only)
reads/writes `allow_messages_from_all_followers`. Toggling takes effect
**immediately**: the DM service reads the profile on every send, so the next
message attempt reflects the new policy (no cache, no propagation delay), and
existing conversations are never interrupted — continuing a thread is always
allowed. The setting lives on the profile but is intentionally exposed only
through these dedicated endpoints, not `GET/PUT /api/creator/profile`.

**Endpoints** — `POST /messages` (`{recipient_id, body}`, `201`; blocked with
a `403` as above; unknown recipient `404`); `GET /conversations` (the
requester's threads, newest first, with the other party + a last-message
preview); `GET /conversations/{id}/messages?limit=50&before_id={id}` —
**paginated** history, newest-first pages with an id cursor (`before_id` +
`has_more`), so a chat client loads the latest page and scrolls back
through older ones; participants only, an outsider gets the same `404` as a
missing thread, so conversation ids don't leak.

`GET /messages/status?recipient_id={id}` (authenticated) powers the chat UI's
**composer gate**: it returns whether the caller may message the recipient
(`can_message`) with a human `reason` when not — e.g. "This creator has
messaging turned off…" — plus context (recipient is creator?, active
follower?, existing thread?) so the UI can explain *why* instead of showing a
dead input box.

Covered by `backend/tests/test_messages.py` (the gate: setting off + no
thread → blocked with the clear error; setting on → new thread; existing
thread → allowed even with the setting off or a lapsed subscription; the
follower requirement; creator↔creator rejection; thread grouping per pair;
and the read endpoints' participant scoping) and
`backend/tests/test_messaging_settings.py` (the toggle: endpoint guards,
default off, and both acceptance states — flipping on lets a blocked follower
through on the very next attempt, flipping off blocks a new thread
immediately, while existing threads stay unaffected). The settings page at
`/settings.html` has a Messaging card with the switch.

### Real-time delivery (WebSocket)

`WS /api/ws/dms?token=<access JWT>` streams DMs live between a creator and
their followers (the query token because browsers can't send
`Authorization` headers on WebSockets; missing/invalid/revoked token →
close `4401`). The auth check is the **shared** `deps.user_from_access_token`
used by the REST bearer path, so the two transports can't drift on what
counts as authenticated.

**Frames** — client → server: `{"type": "send", "recipient_id", "body"}`
(persisted through the **same DM gate** as `POST /messages`, so a
non-follower or a policy-blocked sender gets an `error` frame and nothing is
written) and `{"type": "ping"}`. Server → client: `ack` (after a send
persists), `message` (a new message in one of your conversations — from any
device, worker, or the REST endpoint), `pong`, and `error` with a clear
`detail`.

**Delivery model — local-first with a best-effort Redis relay**
(`app/realtime.py`). Every process (gunicorn worker) keeps a local registry
of its live sockets and delivers to them first; then it publishes the frame
on the recipient's Redis channel (`dm:user:{id}`) so sockets in **other
workers** get it too. A per-process relay subscribes to the channels of the
users connected locally and forwards what arrives. To avoid the local push
and the relay both delivering in the same process, delivered message ids are
remembered (a bounded deque) and the relay skips them. A single slow/hung
client socket is bounded with a 5 s send timeout so it can't stall the
recipient's other sockets or the REST send that awaits the push.

Resilience mirrors the cache layer: Redis is **best-effort** — a publish or
relay failure is caught, logged once (throttled during sustained outages),
and degrades to local-only delivery; **disconnected recipients get the
message via the paginated `GET /conversations/{id}/messages` on reconnect**
(the persisted REST layer is the polling fallback). Users with no live
sockets stop being tracked by the relay (no unbounded subscription growth).

The reverse proxy is wired for upgrades: nginx forwards the `Upgrade`
handshake on `/api/*` (dev: the Vite proxy sets `ws: true`). Covered by
`backend/tests/test_realtime.py` — WS send → live receive, REST send → live
push, offline → REST fetch on reconnect, `4401` auth guard, the DM gate over
WS (follower + policy block, nothing persisted), ping/pong, cross-manager
relay delivery (the multi-worker case), same-process no-double-delivery,
Redis-outage degradation, and relay tracking pruned on disconnect — plus the
in-memory pub/sub stand-in `backend/tests/fake_realtime.py`.

### DM / chat UI (mobile-first)

The chat page (`chat.html`, nginx `/chat/…`, `roque-*` components, mobile
first) is the message client for creators and subscribers:

- **Inbox** — `GET /conversations` renders the requester's threads with the
  other party (avatar) and a last-message preview, most recent first;
  `chat.html?conversation={id}` deep-links straight into a thread.
- **Thread + pagination** — opening a thread loads the **latest page** of
  history (`GET /conversations/{id}/messages?limit=50`) and opens the
  WebSocket; scrolling to the top fetches older pages via the `before_id`
  cursor with the scroll anchor preserved (newer messages stay put).
- **Real-time** — sends go through the open socket (`send` frame) with an
  optimistic bubble that the persisted `ack` replaces (id-deduped, so a
  local push and the relay can never double-render); if the socket isn't
  open yet, sends fall back to `POST /messages` (which still pushes live to
  the recipient). A 3 s auto-reconnect with a 30 s `ping` keepalive keeps the
  live channel up; a rejected send drops its phantom bubble and toasts the
  backend's `error` detail.
- **Disabled-messaging state** — the composer is gated by
  `GET /messages/status`: when `can_message` is false the input is replaced
  by a panel explaining the policy (`reason`), e.g. a creator who turned
  messaging off with no existing thread.

The reusable component is `roque-dm-chat` (`src/pages/chat.ts`). Backend
coverage for the pagination (`test_messages.py`) and the WS contract
(`test_realtime.py`) is listed above.

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