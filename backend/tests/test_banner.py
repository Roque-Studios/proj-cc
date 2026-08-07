"""Creator hero banner upload/serve/delete tests.

The hero banner is public profile chrome (served to any visitor via
``GET /media/banner/{key}``), but only the owning creator can upload/replace/
remove it (``POST/DELETE /creator/banner``). Files are validated exactly like
post media, and replacing/removing a banner deletes the old bytes.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.storage import get_banner_storage


def _register(client, email: str, password: str = "BannerCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "BannerCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "bannercr@example.com"):
    _register(client, email)
    headers = _login(client, email)
    assert client.post("/creator/apply", headers=headers).status_code == 200
    return headers


def _creator_id(client, creator_headers) -> int:
    return client.get("/creator/profile", headers=creator_headers).json()["user_id"]


def _jpeg_bytes(color=(120, 80, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes(color=(30, 30, 200)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #


def test_banner_upload_requires_creator(client):
    _register(client, "plain@example.com")
    fan_headers = _login(client, "plain@example.com")
    resp = client.post(
        "/creator/banner",
        headers=fan_headers,
        files={"file": ("b.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 403
    assert client.post(
        "/creator/banner", files={"file": ("b.jpg", _jpeg_bytes(), "image/jpeg")}
    ).status_code == 401


def test_banner_upload_rejects_invalid_files(client):
    creator_headers = _make_creator(client)
    # Not an image at all.
    resp = client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("b.jpg", b"not-an-image", "image/jpeg")},
    )
    assert resp.status_code == 400
    # An image whose content doesn't match its extension.
    resp = client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("b.png", _jpeg_bytes(), "image/png")},
    )
    assert resp.status_code == 400
    # No banner_url was set by any failed upload.
    assert client.get("/creator/profile", headers=creator_headers).json()[
        "banner_url"
    ] is None


def test_banner_upload_sets_public_url_and_serves(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    resp = client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("banner.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 200
    banner_url = resp.json()["banner_url"]
    assert banner_url == f"/media/banner/banner_{creator_id}.jpg"

    served = client.get(banner_url)
    assert served.status_code == 200
    assert served.content == _jpeg_bytes()
    assert served.headers["Cache-Control"] == "no-store"


def test_banner_replace_removes_old_file(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("a.jpg", _jpeg_bytes((200, 30, 30)), "image/jpeg")},
    )
    client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("b.png", _png_bytes(), "image/png")},
    )
    # The .png replacement overwrote the same banner_{id} key, and the old
    # .jpg is gone — the store holds exactly one file.
    store = get_banner_storage()
    assert store.exists(f"banner_{creator_id}.jpg") is False
    assert store.exists(f"banner_{creator_id}.png") is True
    # The served content type follows the stored extension.
    served = client.get(f"/media/banner/banner_{creator_id}.png")
    assert served.status_code == 200
    assert served.headers["Content-Type"] == "image/png"


def test_banner_delete_clears_url_and_file(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    client.post(
        "/creator/banner",
        headers=creator_headers,
        files={"file": ("b.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    resp = client.delete("/creator/banner", headers=creator_headers)
    assert resp.status_code == 200
    assert resp.json()["banner_url"] is None
    assert get_banner_storage().exists(f"banner_{creator_id}.jpg") is False
