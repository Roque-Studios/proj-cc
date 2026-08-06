"""Admin endpoints.

On this single-operator platform the **creator role is the admin role**
(product decision — see ``deps.require_admin``): the platform owner is a
creator, so admin tooling is gated behind that role. No separate admin role
exists.

``GET /admin/watermark-trace`` is the abuse-investigation tool: it decodes a
leaked watermark's hashed identity back to the originating user and post
(``app.watermark_trace``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import WatermarkTraceOut
from ..watermark_trace import WatermarkTraceError, lookup_trace

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/watermark-trace", response_model=WatermarkTraceOut)
def watermark_trace(
    text: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "The watermark text line read off the leaked image, e.g. "
            "\"a1b2c3d4e5 f6a7b8c9d0 2026-08-06T12:00:00 UTC\""
        ),
    ),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Decode a watermark text line back to the viewer (user) and the post.

    Admin-only (the creator role on this platform). Returns the originating
    user id/email and post id/caption plus the capture timestamp embedded in
    the watermark. ``400`` for malformed watermark text; ``404`` when the
    viewer hash matches no known user.
    """
    try:
        result = lookup_trace(db, text)
    except WatermarkTraceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if result.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user matches the watermark's viewer hash",
        )
    return WatermarkTraceOut(
        viewer_hash=result.viewer_hash,
        post_hash=result.post_hash,
        fetched_at=result.fetched_at,
        user_id=result.user_id,
        user_email=result.user_email,
        post_id=result.post_id,
        post_caption=result.post_caption,
        user_matches=result.user_matches,
        post_matches=result.post_matches,
    )
