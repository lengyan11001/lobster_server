from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, H5HomePreference, User
from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()


class H5HomeHeroBody(BaseModel):
    asset_id: str = Field(min_length=1, max_length=64)


class H5HomePreferencesBody(BaseModel):
    speech_enabled: bool
    speech_voice_uri: Optional[str] = Field(default=None, max_length=255)


def _hero_asset(db: Session, user_id: int, asset_id: str) -> Optional[Asset]:
    return (
        db.query(Asset)
        .filter(
            Asset.user_id == user_id,
            Asset.asset_id == asset_id,
            Asset.media_type == "image",
        )
        .first()
    )


def _home_payload(db: Session, user_id: int) -> dict:
    preference = db.query(H5HomePreference).filter(H5HomePreference.user_id == user_id).first()
    payload = {
        "hero_asset_id": None,
        "hero_url": None,
        "is_custom": False,
        "speech_enabled": bool(preference.speech_enabled) if preference else True,
        "speech_voice_uri": str(preference.speech_voice_uri or "") if preference else "",
    }
    if not preference or not preference.hero_asset_id:
        return payload

    asset = _hero_asset(db, user_id, preference.hero_asset_id)
    hero_url = str(asset.source_url or "").strip() if asset else ""
    if not hero_url:
        return payload
    payload.update({"hero_asset_id": asset.asset_id, "hero_url": hero_url, "is_custom": True})
    return payload


@router.get("/api/h5/home/preferences", summary="获取 H5 首页个性化设置")
def get_h5_home_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    return _home_payload(db, owner.id)


@router.put("/api/h5/home/preferences", summary="更新 H5 用户偏好")
def update_h5_home_preferences(
    body: H5HomePreferencesBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    preference = db.query(H5HomePreference).filter(H5HomePreference.user_id == owner.id).first()
    if preference is None:
        preference = H5HomePreference(
            user_id=owner.id,
            speech_enabled=body.speech_enabled,
            speech_voice_uri=(body.speech_voice_uri or "").strip() or None,
        )
        db.add(preference)
    else:
        preference.speech_enabled = body.speech_enabled
        if "speech_voice_uri" in body.model_fields_set:
            preference.speech_voice_uri = (body.speech_voice_uri or "").strip() or None
    db.commit()
    return _home_payload(db, owner.id)


@router.put("/api/h5/home/hero", summary="设置 H5 首页 Hero 图片")
def update_h5_home_hero(
    body: H5HomeHeroBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = online_user_for_mobile_user(db, current_user)
    asset_id = body.asset_id.strip()
    asset = _hero_asset(db, owner.id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片素材不存在或不属于当前用户")
    if not str(asset.source_url or "").strip():
        raise HTTPException(status_code=400, detail="图片素材缺少可访问地址")

    preference = db.query(H5HomePreference).filter(H5HomePreference.user_id == owner.id).first()
    if preference:
        preference.hero_asset_id = asset.asset_id
    else:
        preference = H5HomePreference(user_id=owner.id, hero_asset_id=asset.asset_id)
        db.add(preference)
    db.commit()
    return _home_payload(db, owner.id)
