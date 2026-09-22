"""账号级微信联系方式池。

抖音私信接管节点（本机）识别到客户发来的手机号后，如果节点没有勾选「自动提交好友申请」，
就把号码上报到这里；同账号的另一台机器跑「个微自动加好友」节点时可以领取这些号码，
提交本机微信加好友。数据按账号（user_id + 品牌 + 平台）持有，不绑设备。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, WechatContactReport
from ..services.brand_context import request_brand_mark
from .auth import get_current_user
from .installation_slots import INSTALLATION_ID_HEADER

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PLATFORM = "douyin"
SUPPORTED_PLATFORMS = {"douyin", "wechat"}
MAX_REPORT_ITEMS = 500
MAX_CLAIM_LIMIT = 200
# 领走后设备挂了/没回执：超过这个时间允许别的机器重新领取，避免号码卡死。
STALE_CLAIM_MINUTES = 240

_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_WECHAT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{5,19}$")


class ContactItemIn(BaseModel):
    value: str = Field("", max_length=160)
    kind: str = Field("", max_length=24)
    username: str = Field("", max_length=240)
    conversation_id: str = Field("", max_length=240)


class ContactReportIn(BaseModel):
    platform: str = Field(DEFAULT_PLATFORM, max_length=32)
    account_label: str = Field("", max_length=128)
    items: List[ContactItemIn] = Field(default_factory=list)


class ContactClaimIn(BaseModel):
    platform: str = Field(DEFAULT_PLATFORM, max_length=32)
    limit: int = Field(50, ge=1, le=MAX_CLAIM_LIMIT)


class ContactAckIn(BaseModel):
    platform: str = Field(DEFAULT_PLATFORM, max_length=32)
    added: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)
    error: str = Field("", max_length=500)


def _normalize_platform(raw: Any) -> str:
    text = str(raw or "").strip().lower() or DEFAULT_PLATFORM
    if text not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的上报平台：{text}")
    return text


def _installation_id(request: Request) -> str:
    return (
        request.headers.get(INSTALLATION_ID_HEADER)
        or request.headers.get("x-installation-id")
        or ""
    ).strip()


def normalize_contact_value(raw: Any) -> Tuple[str, str]:
    """把号码/微信号归一化，返回 (值, 类型)；认不出来返回 ("", "")。"""
    text = re.sub(r"[\s\-()（）]", "", str(raw or "").strip())
    if not text:
        return "", ""
    if _MOBILE_RE.match(text):
        return text, "mobile"
    if _WECHAT_ID_RE.match(text):
        return text, "wechat_id"
    return "", ""


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _count_status(db: Session, *, user_id: int, brand: str, platform: str, status: str) -> int:
    return int(
        db.query(func.count(WechatContactReport.id))
        .filter(
            WechatContactReport.user_id == user_id,
            WechatContactReport.brand_mark == brand,
            WechatContactReport.platform == platform,
            WechatContactReport.status == status,
        )
        .scalar()
        or 0
    )


@router.post("/api/wechat-contact-pool/report", summary="本机上报抖音私信识别到的微信联系方式")
def report_wechat_contacts(
    body: ContactReportIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    brand = request_brand_mark(request)
    platform = _normalize_platform(body.platform)
    installation_id = _installation_id(request)
    account_label = str(body.account_label or "").strip()[:128]
    now = datetime.utcnow()

    created = 0
    refreshed = 0
    skipped = 0
    seen: set[str] = set()
    for item in list(body.items or [])[:MAX_REPORT_ITEMS]:
        value, kind = normalize_contact_value(item.value)
        if not value or value in seen:
            skipped += 1
            continue
        seen.add(value)
        row = (
            db.query(WechatContactReport)
            .filter(
                WechatContactReport.user_id == current_user.id,
                WechatContactReport.brand_mark == brand,
                WechatContactReport.platform == platform,
                WechatContactReport.value == value,
            )
            .first()
        )
        if row is None:
            db.add(
                WechatContactReport(
                    user_id=current_user.id,
                    brand_mark=brand,
                    platform=platform,
                    value=value,
                    kind=kind,
                    source_username=str(item.username or "").strip()[:240] or None,
                    source_conversation=str(item.conversation_id or "").strip()[:240] or None,
                    source_account=account_label or None,
                    reported_by=installation_id or None,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
            )
            created += 1
            continue
        if row.status == "added":
            # 已经加过的号码不回炉，避免把好友申请重复发一遍。
            skipped += 1
            continue
        row.kind = row.kind or kind
        row.source_username = str(item.username or "").strip()[:240] or row.source_username
        row.source_conversation = str(item.conversation_id or "").strip()[:240] or row.source_conversation
        row.source_account = account_label or row.source_account
        row.reported_by = installation_id or row.reported_by
        if row.status == "failed":
            row.status = "pending"
            row.last_error = None
        row.updated_at = now
        refreshed += 1
    db.commit()
    pending = _count_status(db, user_id=current_user.id, brand=brand, platform=platform, status="pending")
    logger.info(
        "[wechat-contact-pool] report user=%s brand=%s platform=%s created=%s refreshed=%s skipped=%s pending=%s",
        current_user.id,
        brand,
        platform,
        created,
        refreshed,
        skipped,
        pending,
    )
    return {
        "ok": True,
        "brand": brand,
        "platform": platform,
        "created": created,
        "refreshed": refreshed,
        "skipped": skipped,
        "pending": pending,
        "reported_at": _iso(now),
    }


@router.post("/api/wechat-contact-pool/claim", summary="个微加好友领取本账号的待添加号码")
def claim_wechat_contacts(
    body: ContactClaimIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    brand = request_brand_mark(request)
    platform = _normalize_platform(body.platform)
    installation_id = _installation_id(request)
    limit = max(1, min(int(body.limit or 50), MAX_CLAIM_LIMIT))
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=STALE_CLAIM_MINUTES)
    rows = (
        db.query(WechatContactReport)
        .filter(
            WechatContactReport.user_id == current_user.id,
            WechatContactReport.brand_mark == brand,
            WechatContactReport.platform == platform,
            or_(
                WechatContactReport.status == "pending",
                (WechatContactReport.status == "claimed")
                & (WechatContactReport.claimed_at.is_(None) | (WechatContactReport.claimed_at < stale_before)),
            ),
        )
        .order_by(WechatContactReport.id.asc())
        .limit(limit)
        .all()
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        row.status = "claimed"
        row.claimed_by = installation_id or None
        row.claimed_at = now
        row.claim_count = int(row.claim_count or 0) + 1
        row.updated_at = now
        items.append(
            {
                "id": row.id,
                "value": row.value,
                "kind": row.kind or "mobile",
                "username": row.source_username or "",
                "account_label": row.source_account or "",
            }
        )
    db.commit()
    pending = _count_status(db, user_id=current_user.id, brand=brand, platform=platform, status="pending")
    logger.info(
        "[wechat-contact-pool] claim user=%s brand=%s platform=%s installation=%s claimed=%s pending=%s",
        current_user.id,
        brand,
        platform,
        installation_id,
        len(items),
        pending,
    )
    return {
        "ok": True,
        "brand": brand,
        "platform": platform,
        "claimed": len(items),
        "pending": pending,
        "items": items,
    }


@router.post("/api/wechat-contact-pool/ack", summary="个微加好友回执：已提交/失败的号码")
def ack_wechat_contacts(
    body: ContactAckIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    brand = request_brand_mark(request)
    platform = _normalize_platform(body.platform)
    now = datetime.utcnow()
    error_text = str(body.error or "").strip()[:500]
    added_values = {value for value, _kind in (normalize_contact_value(item) for item in (body.added or [])) if value}
    failed_values = {value for value, _kind in (normalize_contact_value(item) for item in (body.failed or [])) if value}
    if not added_values and not failed_values:
        return {"ok": True, "added": 0, "released": 0}
    rows = (
        db.query(WechatContactReport)
        .filter(
            WechatContactReport.user_id == current_user.id,
            WechatContactReport.brand_mark == brand,
            WechatContactReport.platform == platform,
            WechatContactReport.value.in_(sorted(added_values | failed_values)),
        )
        .all()
    )
    added = 0
    released = 0
    for row in rows:
        if row.value in added_values:
            row.status = "added"
            row.added_at = now
            row.last_error = None
            row.claimed_by = None
            row.claimed_at = None
            added += 1
        elif row.value in failed_values and row.status != "added":
            # 提交失败就放回待领取，让本机下一轮或同账号的其它机器再来。
            row.status = "pending"
            row.last_error = error_text or row.last_error
            row.claimed_by = None
            row.claimed_at = None
            released += 1
        row.updated_at = now
    db.commit()
    logger.info(
        "[wechat-contact-pool] ack user=%s brand=%s platform=%s added=%s released=%s",
        current_user.id,
        brand,
        platform,
        added,
        released,
    )
    return {"ok": True, "added": added, "released": released}


@router.get("/api/wechat-contact-pool/stats", summary="查看本账号的池子概览")
def stats_wechat_contacts(
    request: Request,
    platform: str = Query(DEFAULT_PLATFORM, max_length=32),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    brand = request_brand_mark(request)
    normalized = _normalize_platform(platform)
    last_reported = (
        db.query(func.max(WechatContactReport.created_at))
        .filter(
            WechatContactReport.user_id == current_user.id,
            WechatContactReport.brand_mark == brand,
            WechatContactReport.platform == normalized,
        )
        .scalar()
    )
    last_added = (
        db.query(func.max(WechatContactReport.added_at))
        .filter(
            WechatContactReport.user_id == current_user.id,
            WechatContactReport.brand_mark == brand,
            WechatContactReport.platform == normalized,
        )
        .scalar()
    )
    return {
        "ok": True,
        "brand": brand,
        "platform": normalized,
        "pending": _count_status(db, user_id=current_user.id, brand=brand, platform=normalized, status="pending"),
        "claimed": _count_status(db, user_id=current_user.id, brand=brand, platform=normalized, status="claimed"),
        "added": _count_status(db, user_id=current_user.id, brand=brand, platform=normalized, status="added"),
        "failed": int(
            db.query(func.count(WechatContactReport.id))
            .filter(
                WechatContactReport.user_id == current_user.id,
                WechatContactReport.brand_mark == brand,
                WechatContactReport.platform == normalized,
                WechatContactReport.last_error.isnot(None),
            )
            .scalar()
            or 0
        ),
        "last_reported_at": _iso(last_reported),
        "last_added_at": _iso(last_added),
    }
