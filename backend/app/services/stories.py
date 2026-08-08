"""24-hour story service: the shared query helpers.

Stories are ephemeral — ``expires_at`` gates every read (follower listing,
media serving) and the public ``has_active_story`` flag that turns the avatar
indicator green. The queries live here so the story router and the landing-page
builder agree on what "an active story" means (one query, one definition).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..media import delete_original
from ..models import Story

# Stories disappear 24 hours after they are created.
STORY_TTL = timedelta(hours=24)


class StoryService:
    """Query helpers for 24-hour stories (shared by routes + landing builder)."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Normalize a stored story timestamp for comparison.

        SQLite returns naive datetimes for ``DateTime(timezone=True)``
        columns; Postgres returns aware ones. Both are stored as UTC, so a
        naive value is treated as UTC before any ``<``/``>`` comparison (the
        same convention ``app.access`` uses for subscription periods).
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def has_active_story(self, creator_id: int) -> bool:
        """True when the creator has at least one not-yet-expired story."""
        return (
            self.db.scalar(
                select(Story.id)
                .where(
                    Story.creator_id == creator_id,
                    Story.expires_at > self._now(),
                )
                .limit(1)
            )
            is not None
        )

    def active_stories(self, creator_id: int) -> list[Story]:
        """The creator's live (unexpired) stories, newest first, media loaded."""
        return list(
            self.db.scalars(
                select(Story)
                .options(selectinload(Story.media))
                .where(
                    Story.creator_id == creator_id,
                    Story.expires_at > self._now(),
                )
                .order_by(Story.created_at.desc(), Story.id.desc())
            ).all()
        )

    # How many of the creator's own stories the dashboard lists (newest first).
    # Expired ones remain visible for this window so the UI can show what
    # auto-expired; the Celery sweep purges them from the DB/storage.
    DASHBOARD_STORY_LIMIT = 50

    def all_stories(self, creator_id: int) -> list[Story]:
        """The creator's own stories, newest first, capped for the dashboard."""
        return list(
            self.db.scalars(
                select(Story)
                .options(selectinload(Story.media))
                .where(Story.creator_id == creator_id)
                .order_by(Story.created_at.desc(), Story.id.desc())
                .limit(self.DASHBOARD_STORY_LIMIT)
            ).all()
        )

    def purge_expired(self) -> int:
        """Delete expired stories + their private originals; returns the count.

        Housekeeping only — every read path already filters ``expires_at``, so
        expired stories are invisible; this removes the rows and the storage
        bytes so a 24-hour story truly disappears. Safe to run on any schedule
        (idempotent; no-op when nothing is expired).
        """
        now = self._now()
        expired = list(
            self.db.scalars(
                select(Story)
                .options(selectinload(Story.media))
                .where(Story.expires_at <= now)
            ).all()
        )
        if not expired:
            return 0
        for story in expired:
            for media in story.media:
                try:
                    delete_original(media.storage_key)
                except Exception:  # noqa: BLE001 — a storage miss must not
                    # abort the sweep; the row is still removed.
                    continue
            self.db.delete(story)
        self.db.commit()
        return len(expired)
