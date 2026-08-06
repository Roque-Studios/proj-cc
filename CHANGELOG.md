# Changelog

## [0.11.0] - 2026-08-06
### Added
- Real-time DM delivery: `WS /api/ws/dms?token=<access JWT>` streams live messages between creators and followers; sends persist through the **same DM gate** as REST (non-followers/policy blocks get an `error` frame, nothing persisted), `4401` for missing/invalid/revoked tokens
- Shared `deps.user_from_access_token` — the REST bearer and WS query-token auth now use the same validation core (no drift between transports)
- `app/realtime.py` RealtimeManager: local-first delivery + best-effort Redis pub/sub relay (`dm:user:{id}` channels) for cross-worker (multi-gunicorn) delivery, bounded-deque dedupe so local push + relay feedback never double-deliver, 5 s per-socket send timeout, throttled outage logging, relay tracking pruned when a user has no live sockets
- `POST /messages` now pushes live to the recipient's connected sockets too; REST history (`GET /conversations/{id}/messages`) remains the fallback for disconnected recipients on reconnect
- nginx `Upgrade` headers + Vite dev proxy `ws: true` for the WS endpoint
- Coverage: `backend/tests/test_realtime.py` (12 tests: WS send → live receive, REST → live push, offline → REST on reconnect, auth guard, gate over WS, ping/pong, cross-manager relay, no double delivery, Redis-outage degradation, prune on disconnect) + `tests/fake_realtime.py` (in-memory pub/sub hub)

## [0.10.0] - 2026-08-06
### Added
- Creator messaging-settings toggle: `GET/PUT /creator/messaging-settings` (creator-only) for `allow_messages_from_all_followers` — takes effect immediately (the DM gate reads the profile per send), existing threads never interrupted
- Settings page (`settings.html`) Messaging card with an immediate-save switch (error reverts the toggle)
- Coverage: `backend/tests/test_messaging_settings.py` — endpoint guards, default-off, both acceptance states (flip on → blocked follower sends immediately; flip off → new threads blocked immediately), existing-thread and creator-outbound unaffected

## [0.9.0] - 2026-08-06
### Added
- DM data model: `Conversation` (unique creator+subscriber thread grouping) + `Message` (sender/recipient/body/read_at) + migration
- `creator_profile.allow_messages_from_all_followers` policy (default off) — the messaging gate: a subscriber can always continue an existing thread, but starting one requires an active subscription **and** (when the setting is off) is blocked with a clear 403; creators can always message subscribers (creator↔creator rejected)
- Shared `access.is_active_follower` helper — the single follower definition used by the content and DM gates
- `POST /messages`, `GET /conversations` (other party + last-message preview), `GET /conversations/{id}/messages` (participants only, 404 for outsiders)
- Coverage: `backend/tests/test_messages.py` (17 tests: gate block/clear error, setting-on new thread, existing-thread carve-out incl. lapsed subscription, follower requirement, creator↔creator rejection, thread grouping, read scoping)

## [0.8.0] - 2026-08-06
### Added
- Per-creator payment gateway settings: `CreatorGatewayConfig` model + migration; each creator enables/configures Stripe / PayPal / Wompi (plus the zero-config `mock` dev gateway) with **strictly per-creator credentials** — no env fallback for checkout
- `GET/PUT /creator/gateway-settings` (creator-only): enabling requires a complete config (`400` with missing fields), environments/payment-day validated, secret values never returned (per-field `configured` booleans only), partial updates preserve stored secrets
- Subscriber checkout gating: `GET /creators/{id}/gateways` lists only enabled + configured gateways; `POST /subscribe` takes an optional `provider` and resolves strictly from the creator's config (single-default; none/multiple → `400`)
- Webhook verification against every registered credential set for a gateway (platform env first, then per-creator configs) — events signed with a creator's own webhook secret reconcile; forged events fail all candidates → `400`
- `python -m app.seed_creator` CLI — create/promote the creator (admin) account for the settings UI
- Frontend admin page `settings.html` (private — only reachable by direct URL): token-based fetch API client (`src/lib/api.ts`), `roque-*` login page, and the gateway-settings view (per-gateway cards, switches disabled until config complete, save + toast, logout)
- Coverage: `tests/test_gateway_settings.py`, `test_gateway_subscribe.py`, `test_gateway_webhooks.py` (authz guards, enable validation, secret non-echo, merge semantics, checkout listing, strict subscribe resolution, per-creator webhook matching, factory mapping/plan resolution)

## [0.7.0] - 2026-08-06
### Added
- Wompi (El Salvador) payment integration via `pywompi` (OAuth2 client-credentials auth): per-subscription recurring payment links (`EnlacePagoRecurrente`, monthly charge on `WOMPI_DIA_DE_PAGO`, hosted checkout with 3DS handled on Wompi's page)
- Webhook validation with Wompi's `wompi_hash` header (HMAC-SHA256 of the raw body with the API secret) via `pywompi.parse_event`; `APROBADA` activates the subscription (email-matched across non-terminal statuses; the link id stays the cancel ref), `RECHAZADA` → `past_due`
- One-time charges: tokenized card without 3DS (`TransaccionCompra/TokenizadaSin3Ds`) + 3DS redirect flow (`TransaccionCompra/3Ds` → `urlCompletarPago3Ds`)
- `WebhookEvent.customer_email` + email-fallback reconciliation in `SubscriptionService` (gateway-agnostic; also benefits future gateways whose events don't reference stored refs); `ChargeRequest.payment_method_token`
- `WOMPI_*` env config; provider registered in the factory
- Coverage: `backend/tests/test_wompi_integration.py` (fake sandbox via pywompi's injectable http_client: signature validation, subscribe → activate, renewals, cancel, one-time/3DS) + opt-in real-sandbox test (`RUN_WOMPI_SANDBOX=1`)

## [0.6.0] - 2026-08-06
### Added
- PayPal Subscriptions integration (sandbox & live via `PAYPAL_ENVIRONMENT`): Billing Subscriptions API for recurring monthly charges, hosted approval link, cancel, POST-back webhook verification
- Renewal webhooks (`PAYMENT.SALE.COMPLETED`/`DENIED`) reconcile by `billing_agreement_id` — the sale's own id differs from the stored ref, so this keeps renewals matching the local row
- Approximate 30-day billing period stamped from event `create_time` (PayPal events don't carry periods), keeping period-based access expiry working
- `python -m app.payments.bootstrap_paypal` — creates the catalog product + ACTIVE monthly billing plan; prints the `P-...` id to set as `SUBSCRIPTION_TIER_PLAN_ID`
- `PAYPAL_PRODUCT_ID` env to pin the catalog product; provider gained a `transport` hook (httpx.MockTransport) for tests
- Coverage: `backend/tests/test_paypal_integration.py` (sandbox-faithful simulation: plan bootstrap, subscribe → approve → active, renewal success/failure, idempotency, cancel, verification failures) + opt-in real-sandbox test (`RUN_PAYPAL_SANDBOX=1`)

## [0.5.0] - 2026-08-06
### Added
- One-time payment flow for paid broadcast unlocks, separate from the monthly subscription charge: a successful `charge_one_time` creates a `PaidUnlock` record granting access to that specific post only
- Refund handling: `charge.refunded` / `PAYMENT.CAPTURE.REFUNDED` / mock `payment.refunded` webhooks normalize to `payment.refunded`, stamp `paid_unlock.refunded_at`, and revoke access until the subscriber re-purchases (same row reactivated in place)
- Webhook router verifies signatures once and dispatches by event type; `SubscriptionService.handle_webhook` accepts a pre-verified event
- Gateway coverage: Stripe refund events carry the `payment_intent` ref; PayPal refund events carry the charge metadata (`custom_id`) for matching
- Coverage: `backend/tests/test_broadcast.py` success/failure/refund integration + `test_stripe_integration.py` / `test_webhook_renewal.py` mapping tests

## [0.4.0] - 2026-08-06
### Added
- Paid broadcasts: creators post a message + media with a one-time `price_cents`; subscribers see a locked preview (metadata only — no media urls) until they pay
- `POST /content/{post_id}/unlock` — one-time paid unlock through `PaymentProvider.charge_one_time`, recorded in the `paid_unlock` table (idempotent — never charges twice)
- Feed/media lock states: `broadcast_price_cents` + `unlocked` on posts; locked broadcasts withhold media urls; the media endpoint denies locked content with `403`
- Coverage: `backend/tests/test_broadcast.py` (unit lock/unlock state machine + end-to-end integration flow)

## [0.3.0] - 2026-08-05
### Added
- Post model & creation endpoint
- Feed endpoint for followers
- Media storage layer (original, unwatermarked)
- Image watermarking service (Pillow)
- Watermark cache layer (Redis/disk + TTL)
- Secure media-serving endpoint
- Watermark traceability lookup tool

## [0.2.0] - 2026-08-05
### Added
- Subscription data model
- Determine viewer access level (anon/registered/follower)
- Payment gateway abstraction layer
- Stripe integration (subscriptions)
- Subscribe to creator (monthly, single tier)
- Webhook handler — payment renewal & failure


## [0.1.0] - 2026-08-05
### Added
- Redis service for Celery broker + cache
- Nginx reverse proxy + static frontend serving
- Environment & secrets management
- User registration & login
- Role model: registered vs creator
- Session/JWT refresh & logout

### Fixed
-
