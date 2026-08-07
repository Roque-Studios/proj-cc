"""Creator avatar upload/serve/delete tests.

Mirrors the banner flow: the avatar is public profile chrome (served to any
visitor via ``GET /media/avatar/{key}``), but only the owning creator can
upload/replace/remove it (``POST/DELETE /creator/avatar``). Files are validated
exactly like post media, and replacing/removing an avatar deletes the old
bytes.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.storage import get_avatar_storage


def _register(client, email: str, password: str = "AvatarCr123"):
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201


def _login(client, email: str, password: str = "AvatarCr123"):
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_creator(client, email: str = "avatarcr@example.com"):
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


def test_avatar_upload_requires_creator(client):
    _register(client, "plain@example.com")
    fan_headers = _login(client, "plain@example.com")
    resp = client.post(
        "/creator/avatar",
        headers=fan_headers,
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 403
    assert client.post(
        "/creator/avatar", files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")}
    ).status_code == 401


def test_avatar_upload_rejects_invalid_files(client):
    creator_headers = _make_creator(client)
    resp = client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("a.jpg", b"not-an-image", "image/jpeg")},
    )
    assert resp.status_code == 400
    # Content that doesn't match the extension.
    resp = client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("a.png", _jpeg_bytes(), "image/png")},
    )
    assert resp.status_code == 400
    assert client.get("/creator/profile", headers=creator_headers).json()[
        "avatar_url"
    ] is None


def test_avatar_upload_sets_public_url_and_serves(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    resp = client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("avatar.png", _png_bytes(), "image/png")},
    )
    assert resp.status_code == 200
    avatar_url = resp.json()["avatar_url"]
    assert avatar_url == f"/media/avatar/avatar_{creator_id}.png"

    served = client.get(avatar_url)
    assert served.status_code == 200
    assert served.content == _png_bytes()
    assert served.headers["Content-Type"] == "image/png"
    assert served.headers["Cache-Control"] == "no-store"


def test_avatar_replace_removes_old_file(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("a.png", _png_bytes(), "image/png")},
    )
    store = get_avatar_storage()
    assert store.exists(f"avatar_{creator_id}.jpg") is False
    assert store.exists(f"avatar_{creator_id}.png") is True


def test_avatar_delete_clears_url_and_file(client):
    creator_headers = _make_creator(client)
    creator_id = _creator_id(client, creator_headers)
    client.post(
        "/creator/avatar",
        headers=creator_headers,
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    resp = client.delete("/creator/avatar", headers=creator_headers)
    assert resp.status_code == 200
    assert resp.json()["avatar_url"] is None
    assert get_avatar_storage().exists(f"avatar_{creator_id}.jpg") is False
