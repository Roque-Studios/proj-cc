"""Public creator landing page endpoint.

``GET /creators/{creator_id}/landing`` is the single payload behind the public
creator landing page: the creator's public identity (display name, bio,
avatar) with their social accounts, the **requesting viewer's** access level
for this creator, and the payment gateways the subscribe CTA can offer.

The viewer state drives the page's role-based content:

- ``anonymous`` — the landing page shows the subscribe prompt only (a login
  link, since subscribing requires an account);
- ``registered`` — the same prompt plus account context (who is logged in),
  with the enabled gateways listed for the subscribe action;
- ``follower`` — the same profile, and the frontend loads the full feed
  (``GET /creators/{creator_id}/posts`` already returns full posts for
  followers and teasers for everyone else).

The endpoint itself is public (no auth required): anonymous requests simply
resolve to the anonymous level. It never leaks subscriber data — only the
public profile fields and the creator's *enabled* gateways.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..access import ViewerAccessLevel, ViewerContext, resolve_viewer_access
from ..database import get_db
from ..gateways import GATEWAYS
from ..models import User, UserRole
from ..schemas import (
    CheckoutGatewayOut,
    CreatorLandingOut,
    CreatorLandingProfileOut,
    SocialLinkOut,
    ViewerLandingOut,
)
from ..services.gateways import enabled_configured_gateways

router = APIRouter(prefix="/creators", tags=["public"])

_SOCIAL_LABELS = {
    "twitter": "X / Twitter",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "other": "Website",
}


def _social_links(profile) -> list[SocialLinkOut]:
    """The creator's configured social accounts as ordered link chips."""
    links = profile.social_links or {}
    return [
        SocialLinkOut(platform=platform, label=_SOCIAL_LABELS[platform], value=value)
        for platform, value in links.items()
        if value and platform in _SOCIAL_LABELS
    ]


@router.get("/{creator_id}/landing", response_model=CreatorLandingOut)
def creator_landing(
    creator_id: int,
    ctx: ViewerContext = Depends(resolve_viewer_access()),
    db: Session = Depends(get_db),
):
    """Public landing payload for a creator, shaped for the requesting viewer."""
    creator = db.get(User, creator_id)
    if creator is None or creator.role != UserRole.creator or not creator.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Creator not found",
        )

    profile = creator.creator_profile
    gateways = [
        CheckoutGatewayOut(gateway=gateway, label=GATEWAYS[gateway].label)
        for gateway, _row in enabled_configured_gateways(db, creator_id)
    ]

    level = ctx.level.value
    subscription = None
    if ctx.level == ViewerAccessLevel.follower and ctx.subscription is not None:
        subscription = ctx.subscription.status.value

    return CreatorLandingOut(
        profile=CreatorLandingProfileOut(
            id=creator.id,
            username=creator.username,
            display_name=profile.display_name if profile else None,
            bio=profile.bio if profile else None,
            avatar_url=profile.avatar_url if profile else None,
        ),
        social_links=_social_links(profile) if profile else [],
        viewer=ViewerLandingOut(
            level=level,
            user_id=ctx.user.id if ctx.user else None,
            username=ctx.user.username if ctx.user else None,
            subscription=subscription,
        ),
        gateways=gateways,
    )
