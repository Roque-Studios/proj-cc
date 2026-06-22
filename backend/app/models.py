import enum
from sqlalchemy import (
    Boolean,
    Column,
    # Date,
    # DateTime,
    # ForeignKey,
    Integer,
    String,
    # UniqueConstraint,
    # func,
    Enum as SQLEnum,
)

from .database import Base

class UserRole(enum.Enum):
    user = "user"
    admin = "admin"
    creator = "creator"


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.user, nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    onboarding_complete = Column(Boolean, default=False, nullable=False)
    activation_token = Column(String, nullable=True)
