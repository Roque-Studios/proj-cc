"""Creator endpoints: self-serve application and creator profile management.

``POST /creator/apply`` upgrades any authenticated user to ``creator`` (creating
the profile stub). ``GET/PUT /creator/profile`` are creator-only and reject
``registered`` users with 403 (see ``deps.require_creator``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_creator
from ..models import CreatorProfile, User, UserRole
from ..schemas import CreatorProfileOut, CreatorProfileUpdate

router = APIRouter(prefix="/creator", tags=["creator"])


def _get_or_create_profile(db: Session, user: User) -> CreatorProfile:
    profile = db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    if profile is None:
        profile = CreatorProfile(user_id=user.id, display_name=user.username)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.post("/apply", response_model=CreatorProfileOut)
def apply_creator(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upgrade the current user to creator (self-serve) and return the profile stub."""
    if user.role != UserRole.creator:
        user.role = UserRole.creator
        user.is_creator = True
        db.add(user)
        db.commit()
    return _get_or_create_profile(db, user)


@router.get("/profile", response_model=CreatorProfileOut)
def get_profile(user: User = Depends(require_creator), db: Session = Depends(get_db)):
    """Creator-only: fetch (or lazily create) the creator profile."""
    return _get_or_create_profile(db, user)


@router.put("/profile", response_model=CreatorProfileOut)
def update_profile(
    payload: CreatorProfileUpdate,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: update display name, bio, avatar and payout info."""
    profile = _get_or_create_profile(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile
