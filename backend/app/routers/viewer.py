"""Viewer access demo endpoint.

Exposes the resolved access level (anonymous / registered / follower) for a
creator, exercising ``access.resolve_viewer_access``. Route handlers can depend
on the same resolver to gate content per viewer level.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..access import ViewerContext, resolve_viewer_access

router = APIRouter(prefix="/creators", tags=["viewer"])


@router.get("/{creator_id}/access")
def viewer_access(
    ctx: ViewerContext = Depends(resolve_viewer_access()),
) -> dict:
    """Return the current viewer's access level for this creator."""
    return {
        "level": ctx.level.value,
        "user_id": ctx.user.id if ctx.user else None,
        "subscription": ctx.subscription.status.value if ctx.subscription else None,
        "creator": {
            "id": ctx.creator.id,
            "username": ctx.creator.username,
        }
        if ctx.creator is not None
        else None,
    }
