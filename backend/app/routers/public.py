"""Public creator landing page endpoints.

``GET /creators/{creator_id}/landing`` is the single payload behind the public
creator landing page: the creator's public identity (display name, bio,
avatar) with their social accounts, the **requesting viewer's** access level
for this creator, and the payment gateways the subscribe CTA can offer.

``GET /creators/default/landing`` serves the same payload for the first
(seed) creator — the site-root default when no creator id is in the URL.

The viewer state drives the page's role-based content:

- ``anonymous`` — the landing page shows the subscribe prompt only (a login
  link, since subscribing requires an account);
- ``registered`` — the same prompt plus account context (who is logged in),
  with the enabled gateways listed for the subscribe action;
- ``follower`` — the same profile, and the frontend loads the full feed
  (``GET /creators/{creator_id}/posts`` already returns full posts for
  followers and teasers for everyone else).

The endpoints are public (no auth required): anonymous requests simply
resolve to the anonymous level. They never leak subscriber data — only the
public profile fields and the creator's *enabled* gateways.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import (
    ViewerAccessLevel,
    ViewerContext,
    resolve_viewer_access,
    resolve_viewer_context,
)
from ..database import get_db
from ..gateways import GATEWAYS
from ..legal import DEFAULT_PRIVACY, DEFAULT_TOS
from ..models import Post, User, UserRole
from ..schemas import (
    CheckoutGatewayOut,
    CreatorLandingOut,
    CreatorLandingProfileOut,
    SocialLinkOut,
    ViewerLandingOut,
)
from ..services.gateways import enabled_configured_gateways
from ..services.stories import StoryService

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


def _landing_payload(
    creator: User,
    ctx: ViewerContext,
    db: Session,
) -> CreatorLandingOut:
    """Build the landing payload for one creator, shaped for the viewer."""
    profile = creator.creator_profile
    gateways = [
        CheckoutGatewayOut(gateway=gateway, label=GATEWAYS[gateway].label)
        for gateway, _row in enabled_configured_gateways(db, creator.id)
    ]

    level = ctx.level.value
    subscription = None
    if ctx.level == ViewerAccessLevel.follower and ctx.subscription is not None:
        subscription = ctx.subscription.status.value

    # Visible posts only — hidden (soft-archived) posts never count toward the
    # hero's post count (same rule as the feed).
    post_count = (
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(Post.creator_id == creator.id, Post.is_visible.is_(True))
        )
        or 0
    )

    # The effective legal documents — the creator's own text, falling back to
    # the ``app.legal`` platform defaults when unset/blank (a creator can never
    # leave subscribers without a policy).
    tos_text = (
        (profile.tos_text or DEFAULT_TOS).strip() or DEFAULT_TOS
        if profile
        else DEFAULT_TOS
    )
    privacy_text = (
        (profile.privacy_text or DEFAULT_PRIVACY).strip() or DEFAULT_PRIVACY
        if profile
        else DEFAULT_PRIVACY
    )

    return CreatorLandingOut(
        profile=CreatorLandingProfileOut(
            id=creator.id,
            username=creator.username,
            display_name=profile.display_name if profile else None,
            bio=profile.bio if profile else None,
            avatar_url=profile.avatar_url if profile else None,
            banner_url=profile.banner_url if profile else None,
            post_count=post_count,
            # Public signal that the creator has a live story — turns the
            # avatar indicator green. The story *content* stays follower-only;
            # the badge itself is public (like an online presence dot).
            has_active_story=StoryService(db).has_active_story(creator.id),
            # The effective legal documents — public, subscribers read them
            # pre-checkout and the /legal page renders them.
            tos_text=tos_text,
            privacy_text=privacy_text,
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


@router.get("/default/landing", response_model=CreatorLandingOut)
def default_creator_landing(
    request: Request,
    db: Session = Depends(get_db),
):
    """Landing payload for the first (seed) creator — the site-root default.

    The site root ``/`` (no creator id in the URL) shows this creator's
    landing page; ``404`` when no creator account exists yet.
    """
    creator = db.scalar(
        select(User)
        .where(User.role == UserRole.creator, User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    )
    if creator is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No creator configured yet",
        )
    ctx = resolve_viewer_context(request, creator.id, db)
    return _landing_payload(creator, ctx, db)


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
    return _landing_payload(creator, ctx, db)
