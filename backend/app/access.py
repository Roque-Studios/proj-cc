"""Viewer access-level resolution: anonymous / registered / follower.

A request is classified against a *specific* creator:

- ``anonymous`` — no token, an invalid/expired/revoked token, or an inactive user;
- ``registered`` — a valid token for an active user;
- ``follower``   — a valid token AND an active (or trialing) subscription to the
  creator whose current period has not ended. An expired subscription (status or
  period) classifies as ``registered``, not ``follower``.

``resolve_viewer_access`` is a dependency factory: pass ``creator_id`` directly
or let it read ``creator_id`` from the route path parameters.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .deps import resolve_authenticated_user
from .models import BlockedUser, Subscription, SubscriptionStatus, User, UserRole

_FOLLOWER_STATUSES = (SubscriptionStatus.active, SubscriptionStatus.trialing)


class ViewerAccessLevel(enum.Enum):
    anonymous = "anonymous"
    registered = "registered"
    follower = "follower"


@dataclass
class ViewerContext:
    """Resolved viewer context for a specific creator."""

    level: ViewerAccessLevel
    user: User | None = None
    creator: User | None = None
    subscription: Subscription | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.level == ViewerAccessLevel.anonymous

    @property
    def is_registered(self) -> bool:
        return self.level == ViewerAccessLevel.registered

    @property
    def is_follower(self) -> bool:
        return self.level == ViewerAccessLevel.follower


def _period_is_current(subscription: Subscription) -> bool:
    """True if the subscription's current period hasn't ended (None = open-ended)."""
    period_end = subscription.current_period_end
    if period_end is None:
        return True
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)  # assume UTC
    return period_end > datetime.now(timezone.utc)


def is_blocked(db: Session, creator_id: int, user_id: int) -> bool:
    """True when the creator has blocked this user.

    The single definition of "blocked" shared by every access gate. A blocked
    user is demoted from ``follower`` everywhere: the content/media/story
    gates (they resolve as ``registered``), the DM gate, and the subscribe
    endpoint (``403``). Idempotent by the unique (creator, user) pair.
    """
    return (
        db.scalar(
            select(BlockedUser.id).where(
                BlockedUser.creator_id == creator_id,
                BlockedUser.user_id == user_id,
            )
        )
        is not None
    )


def is_active_follower(db: Session, subscriber_id: int, creator_id: int) -> bool:
    """True when the user is an active (or trialing) follower of the creator.

    The single definition of "follower" shared by every access gate (content
    media, DMs, ...): an active/trialing subscription whose current period
    hasn't ended. Kept in one place so the definition can't drift.
    """
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.subscriber_id == subscriber_id,
            Subscription.creator_id == creator_id,
            Subscription.status.in_(_FOLLOWER_STATUSES),
        )
    )
    return subscription is not None and _period_is_current(subscription)


def _resolve_creator_id(request: Request, creator_id: int | None) -> int | None:
    if creator_id is not None:
        return creator_id
    raw = request.path_params.get("creator_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _credentials_from(request: Request) -> HTTPAuthorizationCredentials | None:
    """Bearer credentials from the ``Authorization`` header or ``?token=``.

    ``<img>`` tags cannot send an ``Authorization`` header, so media endpoints
    accept the access token as a ``?token=`` query parameter as well. Header
    credentials win when both are present.
    """
    auth = request.headers.get("Authorization")
    if auth:
        scheme, _, param = auth.partition(" ")
        if scheme.lower() == "bearer" and param:
            return HTTPAuthorizationCredentials(scheme="Bearer", credentials=param)
    token = request.query_params.get("token")
    if token:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return None


def resolve_viewer_context(
    request: Request,
    creator_id: int | None,
    db: Session,
) -> ViewerContext:
    """Classify the request against a specific creator (no dependency magic).

    Shared by the ``resolve_viewer_access`` dependency factory and routes that
    resolve the target creator themselves (e.g. the content-media endpoint,
    where the path carries ``post_id`` rather than ``creator_id``).
    """
    user = resolve_authenticated_user(_credentials_from(request), db)
    if user is None:
        return ViewerContext(level=ViewerAccessLevel.anonymous)

    if creator_id is not None:
        creator = db.get(User, creator_id)
        is_real_creator = (
            creator is not None
            and creator.is_active
            and creator.role == UserRole.creator
        )
        subscription = None
        if is_real_creator:
            subscription = db.scalar(
                select(Subscription).where(
                    Subscription.subscriber_id == user.id,
                    Subscription.creator_id == creator_id,
                    Subscription.status.in_(_FOLLOWER_STATUSES),
                )
            )
        # A blocked user is demoted to ``registered`` regardless of their
        # subscription row: the creator banned them, so follower access
        # (content, stories, media, engagement, unlocks) stops immediately.
        if is_real_creator and is_blocked(db, creator_id, user.id):
            return ViewerContext(level=ViewerAccessLevel.registered, user=user)
        if subscription is not None and _period_is_current(subscription):
            return ViewerContext(
                level=ViewerAccessLevel.follower,
                user=user,
                creator=creator,
                subscription=subscription,
            )

    return ViewerContext(level=ViewerAccessLevel.registered, user=user)


def resolve_viewer_access(creator_id: int | None = None) -> Callable[..., ViewerContext]:
    """Dependency factory classifying the request for a given creator."""

    def dependency(
        request: Request,
        db: Session = Depends(get_db),
    ) -> ViewerContext:
        target_creator_id = _resolve_creator_id(request, creator_id)
        return resolve_viewer_context(request, target_creator_id, db)

    return dependency
