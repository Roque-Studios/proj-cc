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
from .deps import _bearer, resolve_authenticated_user
from .models import Subscription, SubscriptionStatus, User, UserRole

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


def resolve_viewer_access(creator_id: int | None = None) -> Callable[..., ViewerContext]:
    """Dependency factory classifying the request for a given creator."""

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        db: Session = Depends(get_db),
    ) -> ViewerContext:
        user = resolve_authenticated_user(credentials, db)
        if user is None:
            return ViewerContext(level=ViewerAccessLevel.anonymous)

        target_creator_id = _resolve_creator_id(request, creator_id)
        if target_creator_id is not None:
            creator = db.get(User, target_creator_id)
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
                        Subscription.creator_id == target_creator_id,
                        Subscription.status.in_(_FOLLOWER_STATUSES),
                    )
                )
            if subscription is not None and _period_is_current(subscription):
                return ViewerContext(
                    level=ViewerAccessLevel.follower,
                    user=user,
                    creator=creator,
                    subscription=subscription,
                )

        return ViewerContext(level=ViewerAccessLevel.registered, user=user)

    return dependency
