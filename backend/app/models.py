import enum
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship

from .database import Base


class UserRole(enum.Enum):
    registered = "registered"
    creator = "creator"


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(
        SQLEnum(UserRole),
        default=UserRole.registered,
        server_default=UserRole.registered.value,
        nullable=False,
    )
    is_creator = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    onboarding_complete = Column(Boolean, default=False, nullable=False)
    activation_token = Column(String, nullable=True)

    creator_profile = relationship(
        "CreatorProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class CreatorProfile(Base):
    """Creator profile extension (one-to-one with User).

    Holds the creator-facing fields: display name, bio, avatar and a payout
    info placeholder that will be filled in by the payments integration.
    """

    __tablename__ = "creator_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    display_name = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    payout_info = Column(JSON, nullable=True)  # placeholder, e.g. {"method": "...", "email": "..."}
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="creator_profile")
