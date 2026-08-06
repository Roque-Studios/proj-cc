# Changelog

## [0.4.0] - 2026-08-06
### Added
- Paid broadcasts: creators post a message + media with a one-time `price_cents`; subscribers see a locked preview (metadata only — no media urls) until they pay
- `POST /content/{post_id}/unlock` — one-time paid unlock through `PaymentProvider.charge_one_time`, recorded in the new `broadcast_unlock` table (idempotent — never charges twice)
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
