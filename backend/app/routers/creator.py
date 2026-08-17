"""Creator endpoints: self-serve application, profile, gateway and messaging settings.

``POST /creator/apply`` upgrades any authenticated user to ``creator`` (creating
the profile stub). ``GET/PUT /creator/profile`` are creator-only. ``GET/PUT
/creator/gateway-settings`` let a creator configure which payment gateways their
subscribers can pay with — strictly per-creator credentials, so enabling a
gateway requires its required config to be complete (see ``app.gateways``).
``GET/PUT /creator/messaging-settings`` toggle the DM policy
(``allow_messages_from_all_followers``); the toggle takes effect on the very
next message attempt (the DM service reads the profile per send).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_creator
from ..legal import DEFAULT_PRIVACY, DEFAULT_TOS
from ..media import MediaValidationError, validate_upload
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
from ..payments import PaymentProviderError, ProviderConfigurationError
from ..payments.factory import build_provider_from_config
from ..schemas import (
    CreatorProfileOut,
    CreatorProfileUpdate,
    GatewayFieldOut,
    GatewaySettingsOut,
    GatewaySettingsUpdate,
    MessagingSettingsOut,
    MessagingSettingsUpdate,
)
from ..services.subscriptions import tier_price_cents_for
from ..storage import get_avatar_storage, get_banner_storage

router = APIRouter(prefix="/creator", tags=["creator"])


def _get_or_create_profile(db: Session, user: User) -> CreatorProfile:
    """The creator's profile row, creating it lazily (race-safe).

    The admin settings page loads the profile and the messaging settings
    concurrently, and both call this helper — two requests can pass the
    SELECT while no row exists and both try to INSERT. The unique constraint
    then fires for the loser, which used to 500 the whole settings tab (the
    gateway cards never rendered). On ``IntegrityError`` we roll back and
    return the row the winner committed.
    """
    profile = db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    if profile is not None:
        return profile
    profile = CreatorProfile(user_id=user.id, display_name=user.username)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        # Lost the create race — a concurrent request committed the row between
        # our SELECT and INSERT. Adopt the winner's row instead of failing.
        db.rollback()
        profile = db.scalar(
            select(CreatorProfile).where(CreatorProfile.user_id == user.id)
        )
        if profile is None:
            raise
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


def _profile_out(profile: CreatorProfile) -> CreatorProfileOut:
    """The public profile shape with the **effective** legal documents.

    ``tos_text`` / ``privacy_text`` fall back to the platform defaults in
    ``app.legal`` when the creator hasn't set their own (or cleared them), so
    the admin form and the pre-checkout documents always have content.
    """
    return CreatorProfileOut(
        user_id=profile.user_id,
        display_name=profile.display_name,
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        banner_url=profile.banner_url,
        social_links=profile.social_links,
        payout_info=profile.payout_info,
        tos_text=(profile.tos_text or DEFAULT_TOS).strip() or DEFAULT_TOS,
        privacy_text=(profile.privacy_text or DEFAULT_PRIVACY).strip()
        or DEFAULT_PRIVACY,
        tier_price_cents=profile.tier_price_cents,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/profile", response_model=CreatorProfileOut)
def get_profile(user: User = Depends(require_creator), db: Session = Depends(get_db)):
    """Creator-only: fetch (or lazily create) the creator profile."""
    return _profile_out(_get_or_create_profile(db, user))


@router.put("/profile", response_model=CreatorProfileOut)
def update_profile(
    payload: CreatorProfileUpdate,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: update display name, bio, avatar, payout info and the
    legal documents (``tos_text`` / ``privacy_text``)."""
    profile = _get_or_create_profile(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return _profile_out(profile)


# --------------------------------------------------------------------------- #
# Public profile images (hero banner + avatar)
# --------------------------------------------------------------------------- #

_PROFILE_IMAGE_CHUNK_SIZE = 64 * 1024

# kind -> CreatorProfile attribute holding the public url for that image.
_PROFILE_IMAGE_ATTR = {"banner": "banner_url", "avatar": "avatar_url"}


def _read_profile_image(file: UploadFile) -> bytes:
    """Read a profile-image upload, rejecting it once it exceeds the size limit.

    Reads the underlying sync file object (``UploadFile.read`` is async) —
    same pattern as the posts router.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(_PROFILE_IMAGE_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.MAX_MEDIA_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {settings.MAX_MEDIA_SIZE_BYTES} byte size limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _store_profile_image(
    file: UploadFile,
    user: User,
    db: Session,
    *,
    kind: str,
) -> CreatorProfile:
    """Validate + store a public profile image (``banner`` or ``avatar``).

    The file is validated exactly like post media (extension, content type,
    magic bytes, size) and stored in the matching **public** store;
    ``banner_url`` / ``avatar_url`` then points at ``/media/{kind}/{key}``.
    Replacing the image deletes the previous file so orphaned bytes never
    accumulate.
    """
    data = _read_profile_image(file)
    try:
        validate_upload(file.filename or "", file.content_type or "", data)
    except MediaValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    ext = Path(file.filename or "").suffix.lower()
    key = f"{kind}_{user.id}{ext}"
    storage = get_banner_storage() if kind == "banner" else get_avatar_storage()
    profile = _get_or_create_profile(db, user)
    # Save the new file first, then drop the old one — a failed save never
    # leaves the previous image deleted behind a stale url.
    storage.save(key, data)
    current = getattr(profile, _PROFILE_IMAGE_ATTR[kind])
    if current:
        old_key = current.rsplit("/", 1)[-1]
        if old_key and old_key != key:
            storage.delete(old_key)
    setattr(profile, _PROFILE_IMAGE_ATTR[kind], f"/media/{kind}/{key}")
    db.commit()
    db.refresh(profile)
    return profile


def _clear_profile_image(
    profile: CreatorProfile,
    db: Session,
    *,
    kind: str,
) -> None:
    """Remove a stored profile image (``banner`` or ``avatar``) and clear its url."""
    attr = _PROFILE_IMAGE_ATTR[kind]
    current = getattr(profile, attr)
    if not current:
        return
    old_key = current.rsplit("/", 1)[-1]
    storage = get_banner_storage() if kind == "banner" else get_avatar_storage()
    storage.delete(old_key)
    setattr(profile, attr, None)
    db.commit()
    db.refresh(profile)


@router.post("/banner", response_model=CreatorProfileOut)
def upload_banner(
    file: UploadFile = File(...),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: upload the public hero banner for the landing page."""
    return _profile_out(_store_profile_image(file, user, db, kind="banner"))


@router.delete("/banner", response_model=CreatorProfileOut)
def delete_banner(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: remove the hero banner (falls back to the default gradient)."""
    profile = _get_or_create_profile(db, user)
    _clear_profile_image(profile, db, kind="banner")
    return _profile_out(profile)


@router.post("/avatar", response_model=CreatorProfileOut)
def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: upload the public profile avatar (landing hero)."""
    return _profile_out(_store_profile_image(file, user, db, kind="avatar"))


@router.delete("/avatar", response_model=CreatorProfileOut)
def delete_avatar(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: remove the avatar (falls back to the initial letter)."""
    profile = _get_or_create_profile(db, user)
    _clear_profile_image(profile, db, kind="avatar")
    return _profile_out(profile)


# --------------------------------------------------------------------------- #
# Messaging settings (DM policy toggle)
# --------------------------------------------------------------------------- #


@router.get("/messaging-settings", response_model=MessagingSettingsOut)
def get_messaging_settings(
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: the current DM policy."""
    profile = _get_or_create_profile(db, user)
    return MessagingSettingsOut(
        allow_messages_from_all_followers=profile.allow_messages_from_all_followers
    )


@router.put("/messaging-settings", response_model=MessagingSettingsOut)
def update_messaging_settings(
    payload: MessagingSettingsUpdate,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: toggle whether all followers may start a conversation.

    Takes effect immediately — the DM service reads this flag on every send.
    Existing conversations are unaffected: continuing a thread is always
    allowed, so toggling off never cuts off an in-flight DM.
    """
    profile = _get_or_create_profile(db, user)
    profile.allow_messages_from_all_followers = payload.allow_messages_from_all_followers
    db.commit()
    db.refresh(profile)
    return MessagingSettingsOut(
        allow_messages_from_all_followers=profile.allow_messages_from_all_followers
    )


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
                # Echo the stored value for NON-secret fields only (e.g. the
                # environment select) so the form can pre-fill them — a save
                # must never silently reset them. Secrets are never returned.
                value=(
                    (str(config.get(field.name, "")) or None)
                    if not field.secret
                    else None
                ),
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


@router.post("/gateway-settings/{gateway}/plan", response_model=GatewaySettingsOut)
def create_gateway_billing_plan(
    gateway: str,
    user: User = Depends(require_creator),
    db: Session = Depends(get_db),
):
    """Creator-only: create the gateway's monthly billing plan and save its id.

    PayPal subscriptions require a billing plan that already exists at the
    gateway (``/v1/billing/subscriptions`` rejects an unknown ``plan_id``).
    This creates the monthly plan with the creator's **own stored PayPal
    credentials** at their current tier price and saves the returned ``P-...``
    id into their gateway config — the normalized flow, so a creator never
    hand-enters a plan id or runs the bootstrap script (which needs the
    platform env credentials). Only PayPal supports programmatic plan
    creation; Stripe prices are created in the Stripe dashboard.
    """
    if gateway not in GATEWAYS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown gateway: {gateway}",
        )
    if gateway != "paypal":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Billing plans are only created for PayPal — create the "
                f"{GATEWAYS[gateway].label} plan in the gateway dashboard."
            ),
        )
    row = db.scalar(
        select(CreatorGatewayConfig).where(
            CreatorGatewayConfig.creator_id == user.id,
            CreatorGatewayConfig.gateway == gateway,
        )
    )
    if row is None or not is_config_complete(gateway, row.config):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Save the PayPal client credentials (Client ID, Client secret, "
                "Webhook ID) first, then create the billing plan."
            ),
        )
    price_cents = tier_price_cents_for(user.creator_profile)
    try:
        provider = build_provider_from_config(gateway, row.config)
        plan = provider.create_plan(
            name=(
                f"Content Creator Engine — Monthly Tier "
                f"({user.username or user.id})"
            ),
            price_cents=price_cents,
            currency="usd",
        )
    except (ProviderConfigurationError, PaymentProviderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PayPal could not create the billing plan: {exc}",
        )
    plan_id = plan.get("id")
    if not plan_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal created the billing plan but returned no plan id.",
        )
    row.config = {**row.config, "plan_id": str(plan_id)}
    db.commit()
    db.refresh(row)
    return _settings_out(gateway, row)
