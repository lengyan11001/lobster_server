from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    H5MountedAccountDefault,
    User,
    WechatContactMemory,
    WechatInteractionOutcome,
    WechatLearningCandidate,
    WechatStrategyRule,
)
from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()

_VALID_INTENT_LEVELS = {"high", "medium", "low", "none"}
_VALID_STAGES = {"unknown", "new", "warming", "qualified", "proposal", "won", "lost", "service"}
_VALID_RULE_CATEGORIES = {
    "general",
    "fact",
    "tone",
    "product",
    "price",
    "service",
    "commitment",
    "forbidden",
    "group_rule",
    "followup",
}
_HIGH_RISK_CATEGORIES = {"price", "commitment", "forbidden", "group_rule"}
_PROFILE_FIELDS = {
    "company",
    "role",
    "industry",
    "region",
    "needs",
    "budget",
    "timeline",
    "objections",
    "preferences",
    "interests",
    "products",
    "relationship",
    "notes",
    "tags",
}
_LAST_HISTORY_PRUNE: Dict[int, datetime] = {}


class WechatIntelligenceSettingsIn(BaseModel):
    learning_mode: str = Field(default="confirm", max_length=16)


class WechatContextIn(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=160)
    contact_key: str = Field(..., min_length=1, max_length=240)
    contact_name: str = Field(default="", max_length=240)
    latest_message: str = Field(default="", max_length=4000)


class WechatLearningCandidateIn(BaseModel):
    category: str = Field(default="general", max_length=32)
    title: str = Field(default="", max_length=200)
    content: str = Field(..., min_length=1, max_length=4000)
    evidence: str = Field(default="", max_length=4000)
    confidence: int = Field(default=0, ge=0, le=100)
    risk_level: str = Field(default="medium", max_length=16)


class WechatObservationIn(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=160)
    contact_key: str = Field(..., min_length=1, max_length=240)
    contact_name: str = Field(default="", max_length=240)
    event_type: str = Field(..., min_length=1, max_length=32)
    status: str = Field(default="completed", max_length=24)
    inbound_message_id: str = Field(default="", max_length=255)
    inbound_text: str = Field(default="", max_length=4000)
    reply_text: str = Field(default="", max_length=4000)
    category: str = Field(default="", max_length=32)
    intent_level: str = Field(default="", max_length=16)
    topic: str = Field(default="", max_length=160)
    conversation_summary: str = Field(default="", max_length=2000)
    stage: str = Field(default="", max_length=48)
    next_followup: str = Field(default="", max_length=2000)
    profile_updates: Dict[str, Any] = Field(default_factory=dict)
    learning_candidates: List[WechatLearningCandidateIn] = Field(default_factory=list, max_length=8)
    payload: Dict[str, Any] = Field(default_factory=dict)
    error_message: str = Field(default="", max_length=2000)


class WechatCandidateDecisionIn(BaseModel):
    decision: str = Field(..., max_length=16)
    title: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[str] = Field(default=None, max_length=32)
    priority: int = Field(default=50, ge=0, le=100)
    note: str = Field(default="", max_length=1000)


class WechatRuleUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[str] = Field(default=None, max_length=32)
    priority: Optional[int] = Field(default=None, ge=0, le=100)
    status: Optional[str] = Field(default=None, max_length=24)


def _owner(db: Session, current_user: User) -> User:
    return online_user_for_mobile_user(db, current_user)


def _clean(value: Any, max_chars: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_chars]


def _bounded_payload(value: Dict[str, Any], max_chars: int = 8000) -> Dict[str, Any]:
    payload = dict(value or {})
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return {"summary": _clean(payload, max_chars), "truncated": True}
    if len(encoded) <= max_chars:
        return payload
    return {"summary": encoded[:max_chars], "truncated": True}


def _maybe_prune_history(db: Session, user_id: int, now: datetime) -> None:
    last = _LAST_HISTORY_PRUNE.get(user_id)
    if last and (now - last).total_seconds() < 3600:
        return
    _LAST_HISTORY_PRUNE[user_id] = now
    cutoff = now - timedelta(days=90)
    db.query(WechatInteractionOutcome).filter(
        WechatInteractionOutcome.user_id == user_id,
        WechatInteractionOutcome.happened_at < cutoff,
    ).delete(synchronize_session=False)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _settings_row(db: Session, user_id: int, *, create: bool = False) -> Optional[H5MountedAccountDefault]:
    row = (
        db.query(H5MountedAccountDefault)
        .filter(H5MountedAccountDefault.user_id == user_id, H5MountedAccountDefault.scope == "wechat_intelligence")
        .first()
    )
    if row is None and create:
        row = H5MountedAccountDefault(
            user_id=user_id,
            scope="wechat_intelligence",
            account_key="global",
            source="h5",
            payload={"learning_mode": "confirm"},
        )
        db.add(row)
    return row


def _settings_payload(db: Session, user_id: int) -> Dict[str, Any]:
    row = _settings_row(db, user_id)
    payload = dict(row.payload or {}) if row else {}
    mode = str(payload.get("learning_mode") or "confirm").lower()
    return {"learning_mode": "full" if mode == "full" else "confirm"}


def _serialize_contact(row: WechatContactMemory, *, include_profile: bool = True) -> Dict[str, Any]:
    data = {
        "id": row.id,
        "account_id": row.account_id,
        "contact_key": row.contact_key,
        "contact_name": row.contact_name or row.contact_key,
        "rolling_summary": row.rolling_summary or "",
        "stage": row.stage,
        "intent_level": row.intent_level,
        "intent_score": row.intent_score,
        "topic": row.topic or "",
        "next_followup": row.next_followup or "",
        "message_count": row.message_count,
        "inbound_count": row.inbound_count,
        "outbound_count": row.outbound_count,
        "last_inbound_at": _iso(row.last_inbound_at),
        "last_outbound_at": _iso(row.last_outbound_at),
        "last_activity_at": _iso(row.last_activity_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_profile:
        data["profile"] = dict(row.profile or {})
    return data


def _serialize_candidate(row: WechatLearningCandidate) -> Dict[str, Any]:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_ref": row.source_ref or "",
        "account_id": row.account_id or "",
        "contact_key": row.contact_key or "",
        "contact_name": row.contact_name or "",
        "scope": row.scope,
        "category": row.category,
        "title": row.title,
        "content": row.content,
        "evidence": row.evidence or "",
        "confidence": row.confidence,
        "risk_level": row.risk_level,
        "status": row.status,
        "decision_note": row.decision_note or "",
        "rule_id": row.rule_id or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "decided_at": _iso(row.decided_at),
    }


def _serialize_rule(row: WechatStrategyRule) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id or "",
        "contact_key": row.contact_key or "",
        "scope": row.scope,
        "category": row.category,
        "title": row.title,
        "content": row.content,
        "priority": row.priority,
        "risk_level": row.risk_level,
        "status": row.status,
        "source_type": row.source_type,
        "source_ref": row.source_ref or "",
        "version": row.version,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def create_strategy_rule(
    db: Session,
    *,
    user_id: int,
    title: str,
    content: str,
    category: str = "general",
    account_id: str = "",
    contact_key: str = "",
    scope: str = "account",
    priority: int = 50,
    risk_level: str = "medium",
    source_type: str = "manual",
    source_ref: str = "",
) -> WechatStrategyRule:
    clean_content = _clean(content, 4000)
    if not clean_content:
        raise HTTPException(status_code=400, detail="规则内容不能为空")
    clean_category = str(category or "general").strip().lower()
    if clean_category not in _VALID_RULE_CATEGORIES:
        clean_category = "general"
    clean_scope = "contact" if scope == "contact" and contact_key else "account"
    duplicate = (
        db.query(WechatStrategyRule)
        .filter(
            WechatStrategyRule.user_id == user_id,
            WechatStrategyRule.account_id == (account_id or None),
            WechatStrategyRule.contact_key == (contact_key or None),
            WechatStrategyRule.category == clean_category,
            WechatStrategyRule.content == clean_content,
            WechatStrategyRule.status == "active",
        )
        .first()
    )
    if duplicate:
        return duplicate
    rule_count = db.query(func.count(WechatStrategyRule.id)).filter(WechatStrategyRule.user_id == user_id).scalar() or 0
    if int(rule_count) >= 500:
        raise HTTPException(status_code=409, detail="长期规则已达到 500 条，请先停用或删除不再使用的规则")
    row = WechatStrategyRule(
        id=uuid.uuid4().hex,
        user_id=user_id,
        account_id=account_id or None,
        contact_key=contact_key or None,
        scope=clean_scope,
        category=clean_category,
        title=_clean(title, 200) or "微信接管规则",
        content=clean_content,
        priority=max(0, min(100, int(priority))),
        risk_level=str(risk_level or "medium").lower() if str(risk_level or "").lower() in {"low", "medium", "high"} else "medium",
        status="active",
        source_type=_clean(source_type, 32) or "manual",
        source_ref=_clean(source_ref, 255) or None,
        meta={},
    )
    db.add(row)
    return row


def _merge_profile(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key, value in (updates or {}).items():
        if key not in _PROFILE_FIELDS:
            continue
        if isinstance(value, list):
            cleaned = list(dict.fromkeys(_clean(item, 160) for item in value if _clean(item, 160)))[:20]
            if cleaned:
                previous = merged.get(key) if isinstance(merged.get(key), list) else []
                merged[key] = list(dict.fromkeys([*previous, *cleaned]))[:30]
        elif isinstance(value, (str, int, float, bool)):
            cleaned = _clean(value, 1000)
            if cleaned:
                merged[key] = cleaned
    return merged


def _rule_relevance(rule: WechatStrategyRule, latest_message: str) -> int:
    score = int(rule.priority or 0) * 10
    text = f"{rule.title} {rule.content}".lower()
    query = _clean(latest_message, 1000).lower()
    tokens = set(re.findall(r"[a-z0-9]{2,}", query))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        tokens.add(chunk)
        tokens.update(chunk[index : index + 2] for index in range(max(0, len(chunk) - 1)))
    score += sum(50 for token in list(tokens)[:20] if token in text)
    if rule.scope == "contact":
        score += 500
    return score


@router.get("/api/wechat-intelligence/settings", summary="读取个微接管学习设置")
def get_wechat_intelligence_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    return {"ok": True, "settings": _settings_payload(db, owner.id)}


@router.patch("/api/wechat-intelligence/settings", summary="修改个微接管学习设置")
def update_wechat_intelligence_settings(
    body: WechatIntelligenceSettingsIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    mode = str(body.learning_mode or "confirm").strip().lower()
    if mode not in {"confirm", "full"}:
        raise HTTPException(status_code=400, detail="learning_mode 必须是 confirm 或 full")
    row = _settings_row(db, owner.id, create=True)
    assert row is not None
    row.payload = {**dict(row.payload or {}), "learning_mode": mode}
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "settings": {"learning_mode": mode}}


@router.post("/api/wechat-intelligence/context", summary="读取个微接管的轻量上下文")
def get_wechat_intelligence_context(
    body: WechatContextIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    contact = (
        db.query(WechatContactMemory)
        .filter(
            WechatContactMemory.user_id == owner.id,
            WechatContactMemory.account_id == body.account_id,
            WechatContactMemory.contact_key == body.contact_key,
        )
        .first()
    )
    rules = (
        db.query(WechatStrategyRule)
        .filter(
            WechatStrategyRule.user_id == owner.id,
            WechatStrategyRule.status == "active",
            or_(WechatStrategyRule.account_id.is_(None), WechatStrategyRule.account_id == body.account_id),
            or_(WechatStrategyRule.contact_key.is_(None), WechatStrategyRule.contact_key == body.contact_key),
        )
        .order_by(WechatStrategyRule.priority.desc(), WechatStrategyRule.updated_at.desc())
        .limit(100)
        .all()
    )
    ranked = sorted(rules, key=lambda row: _rule_relevance(row, body.latest_message), reverse=True)
    selected: List[Dict[str, Any]] = []
    used_chars = 0
    for row in ranked:
        content = _clean(row.content, 1600)
        if not content or used_chars + len(content) > 7000:
            continue
        selected.append(
            {
                "id": row.id,
                "scope": row.scope,
                "category": row.category,
                "title": row.title,
                "content": content,
                "priority": row.priority,
            }
        )
        used_chars += len(content)
        if len(selected) >= 12:
            break
    return {
        "ok": True,
        "contact": _serialize_contact(contact) if contact else None,
        "rules": selected,
        "limits": {"rule_count": len(selected), "rule_chars": used_chars},
    }


@router.post("/api/wechat-intelligence/observe", summary="回写个微接管结果与学习信号")
def observe_wechat_interaction(
    body: WechatObservationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    now = datetime.utcnow()
    _maybe_prune_history(db, owner.id, now)
    contact = (
        db.query(WechatContactMemory)
        .filter(
            WechatContactMemory.user_id == owner.id,
            WechatContactMemory.account_id == body.account_id,
            WechatContactMemory.contact_key == body.contact_key,
        )
        .with_for_update()
        .first()
    )
    if contact is None:
        contact = WechatContactMemory(
            user_id=owner.id,
            account_id=body.account_id,
            contact_key=body.contact_key,
            contact_name=_clean(body.contact_name, 240) or body.contact_key,
            profile={},
        )
        db.add(contact)
    elif body.contact_name:
        contact.contact_name = _clean(body.contact_name, 240)
    contact.profile = _merge_profile(dict(contact.profile or {}), body.profile_updates)
    if body.conversation_summary:
        contact.rolling_summary = _clean(body.conversation_summary, 2000)
    if body.topic:
        contact.topic = _clean(body.topic, 160)
    if body.next_followup:
        contact.next_followup = _clean(body.next_followup, 2000)
    stage = str(body.stage or "").strip().lower()
    if stage in _VALID_STAGES:
        contact.stage = stage
    requested_intent = str(body.intent_level or "").strip().lower()
    if requested_intent in _VALID_INTENT_LEVELS:
        contact.intent_level = requested_intent
        contact.intent_score = {"none": 0, "low": 25, "medium": 60, "high": 90}[requested_intent]
    intent = contact.intent_level or "none"
    contact.last_activity_at = now
    contact.updated_at = now

    dedup_source = body.inbound_message_id or uuid.uuid4().hex
    dedup_key = _clean(f"{body.account_id}:{body.contact_key}:{dedup_source}:{body.event_type}", 255)
    outcome = WechatInteractionOutcome(
        id=uuid.uuid4().hex,
        user_id=owner.id,
        dedup_key=dedup_key,
        account_id=body.account_id,
        contact_key=body.contact_key,
        contact_name=_clean(body.contact_name, 240) or None,
        event_type=_clean(body.event_type, 32),
        status=_clean(body.status, 24) or "completed",
        category=_clean(body.category, 32) or None,
        intent_level=intent,
        inbound_message_id=_clean(body.inbound_message_id, 255) or None,
        inbound_text=_clean(body.inbound_text, 4000) or None,
        reply_text=_clean(body.reply_text, 4000) or None,
        payload=_bounded_payload(body.payload),
        error_message=_clean(body.error_message, 2000) or None,
        happened_at=now,
    )
    db.add(outcome)
    try:
        db.flush()
        if body.inbound_text or body.reply_text:
            contact.message_count += 1
        if body.inbound_text:
            contact.inbound_count += 1
            contact.last_inbound_at = now
        if body.reply_text:
            contact.outbound_count += 1
            contact.last_outbound_at = now
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(WechatContactMemory)
            .filter(
                WechatContactMemory.user_id == owner.id,
                WechatContactMemory.account_id == body.account_id,
                WechatContactMemory.contact_key == body.contact_key,
            )
            .first()
        )
        return {"ok": True, "deduplicated": True, "contact": _serialize_contact(existing) if existing else None, "candidates": []}

    settings = _settings_payload(db, owner.id)
    created_candidates: List[WechatLearningCandidate] = []
    created_rules: List[WechatStrategyRule] = []
    pending_count = (
        db.query(func.count(WechatLearningCandidate.id))
        .filter(WechatLearningCandidate.user_id == owner.id, WechatLearningCandidate.status == "pending")
        .scalar()
        or 0
    )
    for suggestion in body.learning_candidates:
        if int(pending_count) + len([row for row in created_candidates if row.status == "pending"]) >= 200:
            break
        content = _clean(suggestion.content, 4000)
        if not content:
            continue
        category = str(suggestion.category or "general").strip().lower()
        if category not in _VALID_RULE_CATEGORIES:
            category = "general"
        risk = str(suggestion.risk_level or "medium").strip().lower()
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        if category in _HIGH_RISK_CATEGORIES:
            risk = "high"
        duplicate = (
            db.query(WechatLearningCandidate)
            .filter(
                WechatLearningCandidate.user_id == owner.id,
                WechatLearningCandidate.category == category,
                WechatLearningCandidate.content == content,
                WechatLearningCandidate.status.in_(("pending", "approved", "applied")),
            )
            .first()
        )
        if duplicate:
            continue
        auto_apply = settings["learning_mode"] == "full" and risk == "low" and int(suggestion.confidence or 0) >= 85
        candidate = WechatLearningCandidate(
            id=uuid.uuid4().hex,
            user_id=owner.id,
            source_type="customer_chat",
            source_ref=body.inbound_message_id or None,
            account_id=body.account_id,
            contact_key=body.contact_key,
            contact_name=_clean(body.contact_name, 240) or None,
            scope="account",
            category=category,
            title=_clean(suggestion.title, 200) or "聊天中发现的新规则",
            content=content,
            evidence=_clean(suggestion.evidence, 4000) or _clean(body.inbound_text, 1000) or None,
            confidence=int(suggestion.confidence or 0),
            risk_level=risk,
            status="applied" if auto_apply else "pending",
            decided_at=now if auto_apply else None,
        )
        db.add(candidate)
        created_candidates.append(candidate)
        if auto_apply:
            rule = create_strategy_rule(
                db,
                user_id=owner.id,
                account_id=body.account_id,
                title=candidate.title,
                content=candidate.content,
                category=candidate.category,
                priority=50,
                risk_level=candidate.risk_level,
                source_type="customer_chat_auto",
                source_ref=candidate.id,
            )
            candidate.rule_id = rule.id
            created_rules.append(rule)
    db.commit()
    db.refresh(contact)
    return {
        "ok": True,
        "deduplicated": False,
        "contact": _serialize_contact(contact),
        "candidates": [_serialize_candidate(row) for row in created_candidates],
        "auto_applied_rules": [_serialize_rule(row) for row in created_rules],
    }


@router.get("/api/wechat-intelligence/dashboard", summary="个微接管中枢概览")
def get_wechat_intelligence_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    day_ago = datetime.utcnow() - timedelta(days=1)
    week_ago = datetime.utcnow() - timedelta(days=7)
    contact_count = db.query(func.count(WechatContactMemory.id)).filter(WechatContactMemory.user_id == owner.id).scalar() or 0
    active_contacts = (
        db.query(func.count(WechatContactMemory.id))
        .filter(WechatContactMemory.user_id == owner.id, WechatContactMemory.last_activity_at >= week_ago)
        .scalar()
        or 0
    )
    pending_count = (
        db.query(func.count(WechatLearningCandidate.id))
        .filter(WechatLearningCandidate.user_id == owner.id, WechatLearningCandidate.status == "pending")
        .scalar()
        or 0
    )
    rules_count = (
        db.query(func.count(WechatStrategyRule.id))
        .filter(WechatStrategyRule.user_id == owner.id, WechatStrategyRule.status == "active")
        .scalar()
        or 0
    )
    day_rows = (
        db.query(WechatInteractionOutcome.event_type, WechatInteractionOutcome.status, func.count(WechatInteractionOutcome.id))
        .filter(WechatInteractionOutcome.user_id == owner.id, WechatInteractionOutcome.happened_at >= day_ago)
        .group_by(WechatInteractionOutcome.event_type, WechatInteractionOutcome.status)
        .all()
    )
    stats = {"replied": 0, "group_invites": 0, "failed": 0, "observations": 0}
    for event_type, status, count in day_rows:
        stats["observations"] += int(count or 0)
        if event_type == "reply_sent":
            stats["replied"] += int(count or 0)
        if event_type in {"group_queued", "group_created"} and status != "failed":
            stats["group_invites"] += int(count or 0)
        if status == "failed" or event_type == "failed":
            stats["failed"] += int(count or 0)
    recent = (
        db.query(WechatInteractionOutcome)
        .filter(WechatInteractionOutcome.user_id == owner.id)
        .order_by(WechatInteractionOutcome.happened_at.desc())
        .limit(8)
        .all()
    )
    return {
        "ok": True,
        "settings": _settings_payload(db, owner.id),
        "counts": {
            "contacts": int(contact_count),
            "active_contacts_7d": int(active_contacts),
            "pending_suggestions": int(pending_count),
            "active_rules": int(rules_count),
            **stats,
        },
        "recent": [_serialize_outcome(row) for row in recent],
    }


@router.get("/api/wechat-intelligence/contacts", summary="分页读取个微客户画像")
def list_wechat_intelligence_contacts(
    q: str = Query(default="", max_length=120),
    intent_level: str = Query(default="", max_length=16),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    query = db.query(WechatContactMemory).filter(WechatContactMemory.user_id == owner.id)
    keyword = _clean(q, 120)
    if keyword:
        query = query.filter(
            or_(
                WechatContactMemory.contact_name.ilike(f"%{keyword}%"),
                WechatContactMemory.contact_key.ilike(f"%{keyword}%"),
                WechatContactMemory.rolling_summary.ilike(f"%{keyword}%"),
            )
        )
    if intent_level in _VALID_INTENT_LEVELS:
        query = query.filter(WechatContactMemory.intent_level == intent_level)
    total = query.count()
    rows = query.order_by(WechatContactMemory.last_activity_at.desc(), WechatContactMemory.updated_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "items": [_serialize_contact(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/api/wechat-intelligence/contacts/{contact_id}", summary="读取个微客户画像详情")
def get_wechat_intelligence_contact(
    contact_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    row = db.query(WechatContactMemory).filter(WechatContactMemory.id == contact_id, WechatContactMemory.user_id == owner.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="客户画像不存在")
    outcomes = (
        db.query(WechatInteractionOutcome)
        .filter(
            WechatInteractionOutcome.user_id == owner.id,
            WechatInteractionOutcome.account_id == row.account_id,
            WechatInteractionOutcome.contact_key == row.contact_key,
        )
        .order_by(WechatInteractionOutcome.happened_at.desc())
        .limit(30)
        .all()
    )
    return {"ok": True, "contact": _serialize_contact(row), "outcomes": [_serialize_outcome(item) for item in outcomes]}


@router.get("/api/wechat-intelligence/candidates", summary="分页读取个微学习建议")
def list_wechat_learning_candidates(
    status: str = Query(default="pending", max_length=24),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    query = db.query(WechatLearningCandidate).filter(WechatLearningCandidate.user_id == owner.id)
    if status and status != "all":
        query = query.filter(WechatLearningCandidate.status == status)
    total = query.count()
    rows = query.order_by(WechatLearningCandidate.created_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "items": [_serialize_candidate(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/api/wechat-intelligence/candidates/{candidate_id}/decision", summary="审核个微学习建议")
def decide_wechat_learning_candidate(
    candidate_id: str,
    body: WechatCandidateDecisionIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    row = (
        db.query(WechatLearningCandidate)
        .filter(WechatLearningCandidate.id == candidate_id, WechatLearningCandidate.user_id == owner.id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="学习建议不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="该建议已经处理")
    decision = str(body.decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="decision 必须是 approve 或 reject")
    if body.title is not None:
        row.title = _clean(body.title, 200) or row.title
    if body.content is not None:
        row.content = _clean(body.content, 4000) or row.content
    if body.category is not None:
        category = str(body.category or "").strip().lower()
        if category not in _VALID_RULE_CATEGORIES:
            raise HTTPException(status_code=400, detail="不支持的规则分类")
        row.category = category
    row.decision_note = _clean(body.note, 1000) or None
    row.decided_at = datetime.utcnow()
    if decision == "reject":
        row.status = "rejected"
        db.commit()
        return {"ok": True, "candidate": _serialize_candidate(row), "rule": None}
    rule = create_strategy_rule(
        db,
        user_id=owner.id,
        account_id=row.account_id or "",
        title=row.title,
        content=row.content,
        category=row.category,
        priority=body.priority,
        risk_level=row.risk_level,
        source_type="reviewed_candidate",
        source_ref=row.id,
    )
    row.status = "approved"
    row.rule_id = rule.id
    db.commit()
    return {"ok": True, "candidate": _serialize_candidate(row), "rule": _serialize_rule(rule)}


@router.get("/api/wechat-intelligence/rules", summary="分页读取个微长期规则")
def list_wechat_strategy_rules(
    status: str = Query(default="active", max_length=24),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    query = db.query(WechatStrategyRule).filter(WechatStrategyRule.user_id == owner.id)
    if status and status != "all":
        query = query.filter(WechatStrategyRule.status == status)
    total = query.count()
    rows = query.order_by(WechatStrategyRule.priority.desc(), WechatStrategyRule.updated_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "items": [_serialize_rule(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.patch("/api/wechat-intelligence/rules/{rule_id}", summary="修改个微长期规则")
def update_wechat_strategy_rule(
    rule_id: str,
    body: WechatRuleUpdateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    row = db.query(WechatStrategyRule).filter(WechatStrategyRule.id == rule_id, WechatStrategyRule.user_id == owner.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if body.title is not None:
        row.title = _clean(body.title, 200) or row.title
    if body.content is not None:
        row.content = _clean(body.content, 4000) or row.content
    if body.category is not None:
        category = str(body.category or "").strip().lower()
        if category not in _VALID_RULE_CATEGORIES:
            raise HTTPException(status_code=400, detail="不支持的规则分类")
        row.category = category
    if body.priority is not None:
        row.priority = int(body.priority)
    if body.status is not None:
        status = str(body.status or "").strip().lower()
        if status not in {"active", "disabled", "deleted"}:
            raise HTTPException(status_code=400, detail="不支持的规则状态")
        row.status = status
    row.version += 1
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "rule": _serialize_rule(row)}


def _serialize_outcome(row: WechatInteractionOutcome) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "contact_key": row.contact_key,
        "contact_name": row.contact_name or row.contact_key,
        "event_type": row.event_type,
        "status": row.status,
        "category": row.category or "",
        "intent_level": row.intent_level or "none",
        "inbound_text": row.inbound_text or "",
        "reply_text": row.reply_text or "",
        "payload": dict(row.payload or {}),
        "error_message": row.error_message or "",
        "happened_at": _iso(row.happened_at),
    }


@router.get("/api/wechat-intelligence/outcomes", summary="分页读取个微接管记录")
def list_wechat_interaction_outcomes(
    event_type: str = Query(default="", max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner = _owner(db, current_user)
    query = db.query(WechatInteractionOutcome).filter(WechatInteractionOutcome.user_id == owner.id)
    if event_type:
        query = query.filter(WechatInteractionOutcome.event_type == event_type)
    total = query.count()
    rows = query.order_by(WechatInteractionOutcome.happened_at.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "items": [_serialize_outcome(row) for row in rows], "total": total, "limit": limit, "offset": offset}
