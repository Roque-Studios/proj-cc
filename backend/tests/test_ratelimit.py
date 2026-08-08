"""Auth rate-limiting tests.

Acceptance: the unauthenticated auth endpoints (register, login, refresh,
forgot/reset-password, change-password) are throttled with fixed-window Redis
counters. Per-IP limits apply via a dependency; per-identity limits (e.g.
IP+email on login, email on forgot/reset) apply inline. Over-budget requests
get 429 with a ``Retry-After`` header. The store fails open: a broken store
must never lock everyone out of auth.
"""

from __future__ import annotations

import pytest

from app import ratelimit
from app.ratelimit import (
    InMemoryRateLimitStore,
    RedisRateLimitStore,
    check_rate_limit,
    client_ip,
    email_scope_key,
    rate_limit,
)
from tests.fake_redis import FakeRedis

# The PoW gate is off in tests (AUTH_POW_DIFFICULTY defaults to 0), so plain
# register/login calls flow straight to the rate limiter.


# --------------------------------------------------------------------------- #
# Store units
# --------------------------------------------------------------------------- #

def test_in_memory_store_counts_and_expires():
    store = InMemoryRateLimitStore()
    assert store.hit("k", window_seconds=60, max_requests=2) == (True, 0)
    assert store.hit("k", window_seconds=60, max_requests=2) == (True, 0)
    allowed, retry = store.hit("k", window_seconds=60, max_requests=2)
    assert allowed is False
    assert retry >= 1


def test_in_memory_store_consume_once():
    store = InMemoryRateLimitStore()
    assert store.consume_once("once", 60) is True
    assert store.consume_once("once", 60) is False


def test_redis_store_uses_fixed_window_and_retry_after(monkeypatch):
    fake = FakeRedis()
    store = RedisRateLimitStore("redis://x/4")
    monkeypatch.setattr(store, "_client", fake)
    assert store.hit("k", 60, 2) == (True, 0)
    assert store.hit("k", 60, 2) == (True, 0)
    allowed, retry = store.hit("k", 60, 2)
    assert allowed is False
    assert retry >= 1


def test_redis_store_fails_open(monkeypatch):
    class Boom:
        def incr(self, key):  # noqa: ARG002
            raise OSError("redis down")

        def expire(self, *a):  # noqa: ARG002
            raise AssertionError("should not be reached")

        def set(self, *a, **k):  # noqa: ARG002
            raise OSError("redis down")

        def ttl(self, *a):  # noqa: ARG002
            raise OSError("redis down")

    store = RedisRateLimitStore("redis://x/4")
    monkeypatch.setattr(store, "_client", Boom())
    # Fail-open: allowed despite the broken store.
    assert store.hit("k", 60, 1) == (True, 0)
    assert store.consume_once("k2", 60) is True


# --------------------------------------------------------------------------- #
# IP resolution
# --------------------------------------------------------------------------- #

def test_client_ip_prefers_trusted_forwarded_header(monkeypatch):
    class FakeRequest:
        def __init__(self, forwarded: str | None, host: str = "10.0.0.5"):
            self.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
            self.client = type("C", (), {"host": host})()

    monkeypatch.setattr(ratelimit.settings, "TRUST_PROXY_HEADERS", True)
    # nginx appends the real client IP as the last X-Forwarded-For entry.
    req = FakeRequest("1.2.3.4, 10.0.0.5")
    assert client_ip(req) == "10.0.0.5"

    monkeypatch.setattr(ratelimit.settings, "TRUST_PROXY_HEADERS", False)
    # Header ignored when untrusted — falls back to the socket address.
    assert client_ip(req) == "10.0.0.5"


def test_client_ip_ignores_forwarded_when_untrusted(monkeypatch):
    monkeypatch.setattr(ratelimit.settings, "TRUST_PROXY_HEADERS", False)

    class FakeRequest:
        def __init__(self):
            self.headers = {"X-Forwarded-For": "1.2.3.4"}
            self.client = type("C", (), {"host": "9.9.9.9"})()

    assert client_ip(FakeRequest()) == "9.9.9.9"


def test_client_ip_canonicalizes_mapped_ipv6(monkeypatch):
    # ::ffff:1.2.3.4 and 1.2.3.4 must key identically (no representation
    # rotation to multiply an IP budget).
    monkeypatch.setattr(ratelimit.settings, "TRUST_PROXY_HEADERS", True)

    class FakeRequest:
        def __init__(self, forwarded):
            self.headers = {"X-Forwarded-For": forwarded}
            self.client = type("C", (), {"host": "10.0.0.5"})()

    assert client_ip(FakeRequest("::ffff:1.2.3.4")) == "1.2.3.4"
    assert client_ip(FakeRequest("1.2.3.4")) == "1.2.3.4"


def test_client_ip_rejects_junk_forwarded_entry(monkeypatch):
    # A spoofed header whose last entry isn't a real IP must not become the
    # rate-limit identity — fall back to the socket peer.
    monkeypatch.setattr(ratelimit.settings, "TRUST_PROXY_HEADERS", True)

    class FakeRequest:
        def __init__(self, forwarded):
            self.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
            self.client = type("C", (), {"host": "9.9.9.9"})()

    assert client_ip(FakeRequest("not-an-ip")) == "9.9.9.9"
    assert client_ip(FakeRequest("1.2.3.4, garbage")) == "9.9.9.9"
    assert client_ip(FakeRequest(None)) == "9.9.9.9"


def test_email_scope_key_hashes():
    key = email_scope_key("user@example.com")
    assert key.startswith("e:")
    assert len(key) == len("e:") + 16
    assert key != email_scope_key("other@example.com")


# --------------------------------------------------------------------------- #
# Dependency + inline limits through the API
# --------------------------------------------------------------------------- #

def test_register_rate_limited_per_ip(client, _in_memory_rate_limits):
    for i in range(5):
        resp = client.post(
            "/auth/register",
            json={"email": f"rl{i}@example.com", "password": "StrongPass1"},
        )
        assert resp.status_code == 201, resp.text
    # 6th registration within the hour window is throttled.
    resp = client.post(
        "/auth/register",
        json={"email": "rl6@example.com", "password": "StrongPass1"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert resp.headers["Retry-After"].isdigit()


def test_login_rate_limited_per_ip(client, _in_memory_rate_limits):
    # Distinct emails so only the per-IP budget (20/5 min) is exercised — the
    # per-(IP, email) budget (5/15 min) would trip first on one email.
    for i in range(20):
        resp = client.post(
            "/auth/login",
            json={"email": f"nobody{i}@example.com", "password": "WrongPass1"},
        )
        assert resp.status_code == 401, resp.status_code
    resp = client.post(
        "/auth/login",
        json={"email": "last@example.com", "password": "WrongPass1"},
    )
    assert resp.status_code == 429


def test_login_rate_limited_per_ip_email_pair(client, _in_memory_rate_limits):
    # 5 failed attempts for one (IP, email) pair trip the per-identity budget.
    for _ in range(5):
        resp = client.post(
            "/auth/login",
            json={"email": "victim@example.com", "password": "WrongPass1"},
        )
        assert resp.status_code == 401
    resp = client.post(
        "/auth/login",
        json={"email": "victim@example.com", "password": "WrongPass1"},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # A different email from the same IP is still allowed (IP budget not hit).
    resp = client.post(
        "/auth/login",
        json={"email": "other@example.com", "password": "WrongPass1"},
    )
    assert resp.status_code == 401


def test_forgot_password_rate_limited_per_email(client, _in_memory_rate_limits):
    for _ in range(3):
        resp = client.post(
            "/auth/forgot-password", json={"email": "victim@example.com"}
        )
        assert resp.status_code == 200
    resp = client.post("/auth/forgot-password", json={"email": "victim@example.com"})
    assert resp.status_code == 429


def test_refresh_and_reset_and_change_rate_limited(client, _in_memory_rate_limits):
    # refresh: 30/10min per IP — hammer with garbage tokens.
    for _ in range(30):
        client.post(
            "/auth/refresh",
            json={"refresh_token": "not-a-real-token-but-long-enough-xyz"},
        )
    resp = client.post(
        "/auth/refresh",
        json={"refresh_token": "not-a-real-token-but-long-enough-xyz"},
    )
    assert resp.status_code == 429

    # reset-password: 20/hour per IP (tokens invalid → 400, still counted).
    for _ in range(20):
        client.post(
            "/auth/reset-password",
            json={"token": "garbage", "new_password": "NewPassw0rd"},
        )
    resp = client.post(
        "/auth/reset-password",
        json={"token": "garbage", "new_password": "NewPassw0rd"},
    )
    assert resp.status_code == 429


def test_inline_check_rate_limit_raises_429(_in_memory_rate_limits):
    for _ in range(2):
        check_rate_limit("unit", "identity", window_seconds=60, max_requests=2)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        check_rate_limit("unit", "identity", window_seconds=60, max_requests=2)
    assert excinfo.value.status_code == 429


def test_fail_open_never_locks_auth(client, monkeypatch, _in_memory_rate_limits):
    class BoomStore:
        def hit(self, *a, **k):  # noqa: ARG002
            raise OSError("redis down")

        def consume_once(self, *a, **k):  # noqa: ARG002
            raise OSError("redis down")

    monkeypatch.setattr(ratelimit, "_store", BoomStore())
    resp = client.post(
        "/auth/register",
        json={"email": "failopen@example.com", "password": "StrongPass1"},
    )
    assert resp.status_code == 201
    resp = client.post(
        "/auth/login",
        json={"email": "failopen@example.com", "password": "StrongPass1"},
    )
    assert resp.status_code == 200


def test_rate_limit_dependency_builder(client):
    # Sanity: the dependency factory returns a FastAPI Depends object.
    dep = rate_limit("x", window_seconds=60, max_requests=5)
    assert dep is not None
