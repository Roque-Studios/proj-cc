"""Create (or promote) the creator/admin user from the CLI.

This platform treats the **creator role as the admin role** (see
``deps.require_admin``), so the operator seeds a single creator account and
logs into the gateway-settings UI with it.

Usage::

    python -m app.seed_creator                          # uses env defaults
    python -m app.seed_creator --email boss@cc.io --password 'S3cret!'

Defaults to ``DEFAULT_ADMIN_EMAIL`` / ``DEFAULT_ADMIN_PASSWORD`` from config
when the flags are omitted. Idempotent: an existing user with that email is
promoted to creator in place (their password is left untouched).

Prints the created/promoted user's id and email; exits non-zero on a weak
password (fewer than 8 characters) since that would lock the operator out of
real checkout security.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import CreatorProfile, User, UserRole
from .security import hash_password


def seed_creator(email: str, password: str) -> tuple[User, str]:
    """Create a creator (admin) user, or promote the existing one in place."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError(f"Invalid email: {email!r}")
    if not password:
        raise ValueError("A password is required")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                username=email.split("@")[0],
                hashed_password=hash_password(password),
                role=UserRole.creator,
                is_creator=True,
                is_active=True,
                onboarding_complete=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            action = "created"
        else:
            if user.role != UserRole.creator or not user.is_creator:
                user.role = UserRole.creator
                user.is_creator = True
            user.is_active = True
            db.commit()
            db.refresh(user)
            action = "promoted"

        # The profile row is created up front so the admin settings tab's first
        # load (profile + messaging settings fetched concurrently) never races
        # on lazy profile creation — both calls would try to INSERT the row and
        # one would 500, hiding the gateway cards.
        profile = db.scalar(
            select(CreatorProfile).where(CreatorProfile.user_id == user.id)
        )
        if profile is None:
            db.add(CreatorProfile(user_id=user.id, display_name=user.username))
            db.commit()
        return user, action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the creator (admin) user.")
    parser.add_argument("--email", default=settings.DEFAULT_ADMIN_EMAIL)
    parser.add_argument("--password", default=settings.DEFAULT_ADMIN_PASSWORD)
    args = parser.parse_args(argv)

    if len(args.password) < 8:
        print(
            "error: password is too short (min 8 chars) — "
            "pass --password with a strong one",
            file=sys.stderr,
        )
        return 1
    try:
        user, action = seed_creator(args.email, args.password)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"creator {action}: id={user.id} email={user.email} "
        f"role={user.role.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
