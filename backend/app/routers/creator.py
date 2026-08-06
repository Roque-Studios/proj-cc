"""Creator endpoints: self-serve application, profile and gateway settings.

``POST /creator/apply`` upgrades any authenticated user to ``creator`` (creating
the profile stub). ``GET/PUT /creator/profile`` are creator-only. ``GET/PUT
/creator/gateway-settings`` let a creator configure which payment gateways their
subscribers can pay with — strictly per-creator credentials, so enabling a
gateway requires its required config to be complete (see ``app.gateways``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_creator
from ..gateways import (
    CREATOR_GATEWAY_ORDER,
    GATEWAYS,
    is_config_complete,
    merge_config,
    spec_for,
    validate_config_values,
    validate_enable,
)
from ..models import CreatorGatewayConfig, CreatorProfile, User, UserRole
from ..schemas import (
    CreatorProfileOut,
    CreatorProfileUpdate,
    GatewayFieldOut,
    GatewaySettingsOut,
    GatewaySettingsUpdate,
)

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


# --------------------------------------------------------------------------- #
# Payment gateway settings
# --------------------------------------------------------------------------- #


def _settings_out(gateway: str, row: CreatorGatewayConfig | None) -> GatewaySettingsOut:
    """Public settings shape for one gateway — secret values never leave the DB."""
    spec = spec_for(gateway)
    config = row.config if row is not None else {}
    return GatewaySettingsOut(
        gateway=spec.name,
        label=spec.label,
        description=spec.description,
        enabled=bool(row.enabled) if row is not None else False,
        configured=is_config_complete(gateway, config),
        fields=[
            GatewayFieldOut(
                name=field.name,
                label=field.label,
                required=field.required,
                secret=field.secret,
                placeholder=field.placeholder,
                options=list(field.options),
                configured=bool(str(config.get(field.name, "")).strip()),
            )
            for field in spec.fields
        ],
    )


@router.get("/gateway-settings", response_model=list[GatewaySettingsOut])
def get_gateway_settings(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: the settings for every configurable gateway.

    Returns per-field ``configured`` booleans (and full field metadata for the
    form), never the stored secret values.
    """
    rows = {
        row.gateway: row
        for row in db.scalars(
            select(CreatorGatewayConfig).where(
                CreatorGatewayConfig.creator_id == user.id
            )
        ).all()
    }
    return [_settings_out(gateway, rows.get(gateway)) for gateway in CREATOR_GATEWAY_ORDER]


@router.put("/gateway-settings/{gateway}", response_model=GatewaySettingsOut)
def update_gateway_settings(
    gateway: str,
    payload: GatewaySettingsUpdate,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: update one gateway's settings (enabled flag + config).

    ``config`` merges over the stored values (empty strings keep the existing
    value, so secrets are never wiped by a client that can't read them).
    Enabling requires a complete config for that gateway — otherwise ``400``
    with the missing fields listed.
    """
    if gateway not in GATEWAYS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown gateway: {gateway}",
        )

    row = db.scalar(
        select(CreatorGatewayConfig).where(
            CreatorGatewayConfig.creator_id == user.id,
            CreatorGatewayConfig.gateway == gateway,
        )
    )
    existing = row.config if row is not None else {}
    merged = merge_config(gateway, payload.config or {}, existing)

    # Field values must always be valid (environments, day of month).
    try:
        validate_config_values(gateway, merged)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    enabled = (
        payload.enabled if payload.enabled is not None else bool(row.enabled) if row else False
    )
    if enabled:
        # Enabling a gateway with an incomplete config is rejected up front.
        try:
            validate_enable(gateway, merged)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if row is None:
        row = CreatorGatewayConfig(
            creator_id=user.id,
            gateway=gateway,
            enabled=enabled,
            config=merged,
        )
        db.add(row)
    else:
        row.enabled = enabled
        row.config = merged
    db.commit()
    db.refresh(row)
    return _settings_out(gateway, row)
