"""Integration tests for the secure content-media endpoint.

Acceptance: ``/content/{post_id}/media?media_id={id}`` authenticates,
authorizes (follower / paid-unlock check), applies or retrieves the per-viewer
watermark, and streams the blob with no-store cache headers. Unauthorized
access is blocked (401 anonymous, 403 non-follower / expired), and for every
role that reaches the blob the response is the watermarked transform — the
original unwatermarked file is never exposed.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlalchemy import select

from app.models import PostMedia, Subscription, SubscriptionStatus, User
from app.storage import get_original_storage


def _real_jpeg(width: int = 320, height: int = 240) -> bytes:
    """A real decodable JPEG (served media is re-encoded, so it must decode).

    Large enough that the per-viewer watermark is visibly different — on tiny
    images the watermark text is wider than the image, so two viewers fetched
    in the same second would render byte-identical output.
    """
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 90, 40)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "ContentCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "ContentCr123") -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _make_creator(client, email: str = "cr@example.com") -> str:
    """Register + apply as a creator; returns the creator's access token."""
    _register(client, email)
    token = _login(client, email)
    assert client.post("/creator/apply", headers=_bearer(token)).status_code == 200
    return token


def _upload_post(client, creator_token: str, *, caption: str = "Secret") -> dict:
    """Upload a single-photo post; returns the post body (id, creator_id, media)."""
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        data={"caption": caption},
        files=[("files", ("photo.jpg", _real_jpeg(), "image/jpeg"))],
    )
    assert resp.status_code == 201
    return resp.json()


def _follow(
    db,
    subscriber: User,
    creator: User,
    *,
    status: SubscriptionStatus = SubscriptionStatus.active,
    days: int = 30,
) -> Subscription:
    sub = Subscription(
        subscriber_id=subscriber.id,
        creator_id=creator.id,
        status=status,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=days),
        payment_provider="mock",
        external_ref=f"sub_content_{subscriber.id}_{creator.id}",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _user_id(db, email: str) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def _media_url(post: dict) -> str:
    return post["media"][0]["media_url"]


def _make_follower(client, db, email: str = "fan@example.com") -> tuple[str, int]:
    """Register a fan, subscribe them to the default creator, return (token, id)."""
    _register(client, email)
    token = _login(client, email)
    with db:
        fan = db.get(User, _user_id(db, email))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator)
        fan_id = fan.id  # read before the with-block closes the session
    return token, fan_id


# --------------------------------------------------------------------------- #
# Unauthorized access is blocked
# --------------------------------------------------------------------------- #

def test_anonymous_is_rejected(client):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    assert client.get(_media_url(post)).status_code == 401


def test_anonymous_with_garbage_token_is_rejected(client):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    resp = client.get(f"{_media_url(post)}&token=not.a.jwt")
    assert resp.status_code == 401


def test_registered_non_follower_is_forbidden(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    _register(client, "fan@example.com")
    fan_token = _login(client, "fan@example.com")

    # Both auth channels are equally rejected.
    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 403
    assert client.get(f"{_media_url(post)}&token={fan_token}").status_code == 403


def test_expired_subscription_is_forbidden(client, db_session):
    """Active status but the current period has ended -> not a follower -> 403."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    _register(client, "exfan@example.com")
    fan_token = _login(client, "exfan@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "exfan@example.com"))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator, days=-5)

    assert client.get(_media_url(post), headers=_bearer(fan_token)).status_code == 403


def test_legacy_media_key_path_is_closed(client):
    """The old unauthenticated /media/{key} route no longer exists."""
    assert client.get("/media/whatever.jpg").status_code == 404
    assert client.get("/media/original/whatever.jpg").status_code == 404


# --------------------------------------------------------------------------- #
# Authorized roles get the watermarked blob (full request -> blob path)
# --------------------------------------------------------------------------- #

def test_follower_gets_watermarked_blob(client, db_session):
    original = _real_jpeg()
    creator_token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    assert resp.status_code == 201
    post = resp.json()
    fan_token, fan_id = _make_follower(client, db_session)

    served = client.get(_media_url(post), headers=_bearer(fan_token))
    assert served.status_code == 200
    # The full path: request -> authenticated -> authorized -> watermarked blob.
    assert served.headers["content-type"].startswith("image/jpeg")
    assert "no-store" in served.headers.get("cache-control", "")
    assert served.headers.get("x-content-type-options") == "nosniff"
    assert served.headers.get("x-watermark") == f"user:{fan_id}"
    assert served.headers.get("x-watermark-cache") == "miss"
    # Blob is a real decodable image and is NOT the original upload bytes.
    assert served.content != original
    img = Image.open(io.BytesIO(served.content))
    img.load()
    assert img.format == "JPEG"


def test_follower_via_query_token_for_img_tags(client, db_session):
    """<img> tags can't send an Authorization header — ?token= must work."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    fan_token, _ = _make_follower(client, db_session)

    served = client.get(f"{_media_url(post)}&token={fan_token}")
    assert served.status_code == 200
    assert served.headers.get("x-watermark").startswith("user:")


def test_trialing_subscriber_passes_paid_unlock_check(client, db_session):
    """A trialing subscription counts as the paid unlock for access purposes."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    _register(client, "trial@example.com")
    trial_token = _login(client, "trial@example.com")
    with db_session as db:
        fan = db.get(User, _user_id(db, "trial@example.com"))
        creator = db.get(User, _user_id(db, "cr@example.com"))
        _follow(db, fan, creator, status=SubscriptionStatus.trialing, days=7)

    resp = client.get(_media_url(post), headers=_bearer(trial_token))
    assert resp.status_code == 200
    assert resp.headers.get("x-watermark").startswith("user:")


def test_creator_can_fetch_own_media(client):
    """The post's creator always has access — no subscription needed."""
    creator_token = _make_creator(client)
    original = _real_jpeg()
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    post = resp.json()

    served = client.get(_media_url(post), headers=_bearer(creator_token))
    assert served.status_code == 200
    assert served.headers.get("x-watermark").startswith("user:")
    assert served.content != original  # still watermarked for the owner


def test_second_request_served_from_cache(client, db_session):
    """Repeated (viewer, media) pairs hit the per-viewer watermark cache."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    fan_token, _ = _make_follower(client, db_session)

    first = client.get(_media_url(post), headers=_bearer(fan_token))
    assert first.headers.get("x-watermark-cache") == "miss"

    second = client.get(_media_url(post), headers=_bearer(fan_token))
    assert second.headers.get("x-watermark-cache") == "hit"
    assert second.content == first.content  # identical bytes, no re-render


def test_head_request_returns_headers_without_body(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    fan_token, _ = _make_follower(client, db_session)

    served = client.head(_media_url(post), headers=_bearer(fan_token))
    assert served.status_code == 200
    assert served.content == b""
    assert "no-store" in served.headers.get("cache-control", "")


# --------------------------------------------------------------------------- #
# 404s and the original-file guarantee
# --------------------------------------------------------------------------- #

def test_unknown_post_404(client):
    assert client.get("/content/999999/media?media_id=1").status_code == 404


def test_unknown_media_id_404(client, db_session):
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    fan_token, _ = _make_follower(client, db_session)

    url = f"/content/{post['id']}/media?media_id=999999"
    assert client.get(url, headers=_bearer(fan_token)).status_code == 404


def test_media_from_another_post_404(client):
    """A media id that belongs to a different post must not be reachable here."""
    creator_token = _make_creator(client)
    first = _upload_post(client, creator_token, caption="First")
    second = _upload_post(client, creator_token, caption="Second")

    url = f"/content/{first['id']}/media?media_id={second['media'][0]['id']}"
    # Even the creator (who owns both posts) gets 404 — the media isn't in this
    # post, so the id can't be used to tunnel across posts.
    assert client.get(url, headers=_bearer(creator_token)).status_code == 404


def test_media_missing_on_disk_404(client, db_session):
    """A DB row whose private original is gone returns 404, not a 500."""
    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token)
    with db_session as db:
        row = db.get(PostMedia, post["media"][0]["id"])
        get_original_storage().delete(row.storage_key)
    fan_token, _ = _make_follower(client, db_session)

    resp = client.get(_media_url(post), headers=_bearer(fan_token))
    assert resp.status_code == 404


def test_served_media_embeds_post_id_for_traceability(client, db_session, monkeypatch):
    """The content endpoint renders with post_id so leaks trace back to the post."""
    from app import media as media_module

    creator_token = _make_creator(client)
    post = _upload_post(client, creator_token, caption="Trace me")
    fan_token, _ = _make_follower(client, db_session)

    captured = {}
    real_render = media_module.render_served_media

    def spy_render(original, user_ref, timestamp=None, **kwargs):
        captured["post_id"] = kwargs.get("post_id")
        return real_render(original, user_ref, timestamp, **kwargs)

    monkeypatch.setattr(media_module, "render_served_media", spy_render)
    served = client.get(_media_url(post), headers=_bearer(fan_token))
    assert served.status_code == 200
    assert captured["post_id"] == post["id"]


def test_response_never_exposes_original_for_any_role(client, db_session):
    """Every role that reaches the blob gets the transform, never the original."""
    original = _real_jpeg()
    creator_token = _make_creator(client)
    resp = client.post(
        "/posts",
        headers=_bearer(creator_token),
        files=[("files", ("photo.jpg", original, "image/jpeg"))],
    )
    post = resp.json()
    with db_session as db:
        row = db.get(PostMedia, post["media"][0]["id"])
        assert get_original_storage().read(row.storage_key) == original  # intact

    # The owner's copy is watermarked…
    owner = client.get(_media_url(post), headers=_bearer(creator_token))
    assert owner.status_code == 200
    assert owner.content != original

    # …and so is the follower's.
    fan_token, _ = _make_follower(client, db_session)
    follower = client.get(_media_url(post), headers=_bearer(fan_token))
    assert follower.status_code == 200
    assert follower.content != original

    # Different viewers see different watermarked bytes (per-viewer tracing).
    assert follower.content != owner.content
