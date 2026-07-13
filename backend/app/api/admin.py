"""管理后台：管理员/代理商登录、用户查询、积分充值、数据统计。

路由挂载在 /admin 前缀。支持两种角色：
- 管理员（admin）：通过 .env 配置的用户名密码登录，拥有全部权限
- 代理商（agent）：通过自己的账号密码登录，仅能查看和管理自己的下级用户
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, FileResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import AgentCommissionLedger, CapabilityCallLog, ContentCompetitorAccount, CreditLedger, H5AgentTemplateGrant, H5ChatDevicePresence, IPContentKeyword, IPContentScheduleTemplate, JuheWechatCallLog, JuheWechatConfig, JuheWechatFriendAddBatch, JuheWechatFriendAddItem, OpenClawMemoryDocument, RechargeOrder, ScheduledTask, ScheduledTaskRun, SkillUnlock, User, UserSkillVisibility
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import quantize_credits, quantize_credits_signed
from ..services.user_feature_flags import FEATURE_FLAG_PACKAGES
from ..services.juhe_wechat import extract_friend_add_target, guid_request, mask_secret, safe_request_snapshot

router = APIRouter()
logger = logging.getLogger(__name__)

ADMIN_TOKEN_PREFIX = "lobster-admin-"
AGENT_TOKEN_PREFIX = "lobster-agent-"
_JWT_ALGORITHM = "HS256"


@dataclass
class AdminContext:
    role: str  # "admin" | "agent"
    user_id: Optional[int] = None


def _admin_enabled() -> bool:
    return bool((settings.lobster_admin_username or "").strip() and (settings.lobster_admin_password or "").strip())


def _verify_admin_token(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    db: Session = Depends(get_db),
) -> AdminContext:
    """解析管理后台 token，返回角色上下文。支持管理员 token 和代理商 JWT token。"""
    if not x_admin_token or not x_admin_token.strip():
        raise HTTPException(status_code=401, detail="缺少管理凭证")
    token = x_admin_token.strip()

    if token.startswith(ADMIN_TOKEN_PREFIX):
        if not _admin_enabled():
            raise HTTPException(status_code=503, detail="管理后台未配置")
        expected = ADMIN_TOKEN_PREFIX + (settings.lobster_admin_password or "").strip()
        if token != expected:
            raise HTTPException(status_code=401, detail="管理员凭证无效")
        return AdminContext(role="admin")

    if token.startswith(AGENT_TOKEN_PREFIX):
        jwt_token = token[len(AGENT_TOKEN_PREFIX):]
        try:
            payload = jwt.decode(jwt_token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
            user_id = int(payload.get("sub", 0))
            if payload.get("scope") != "agent_admin":
                raise HTTPException(status_code=401, detail="凭证无效")
        except (JWTError, ValueError, TypeError):
            raise HTTPException(status_code=401, detail="代理商凭证无效或已过期")
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_agent:
            raise HTTPException(status_code=401, detail="代理商账号无效或已被取消代理资格")
        return AdminContext(role="agent", user_id=user_id)

    raise HTTPException(status_code=401, detail="凭证格式无效")


def _require_admin(ctx: AdminContext = Depends(_verify_admin_token)) -> AdminContext:
    """仅允许管理员角色。"""
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return ctx


def _agent_sub_user_ids(db: Session, agent_user_id: int) -> list[int]:
    """返回代理商直属下级的 user_id 列表。"""
    return [uid for (uid,) in db.query(User.id).filter(User.parent_user_id == agent_user_id).all()]


def _agent_level(user: Optional[User]) -> int:
    if not user or not getattr(user, "is_agent", False):
        return 0
    try:
        level = int(getattr(user, "agent_level", 0) or 0)
    except Exception:
        level = 0
    return level if level in (1, 2) else 1


def _agent_visible_user_ids(db: Session, agent_user_id: int) -> list[int]:
    """代理商可见用户：自己的直属下级，以及直属二级代理名下的下级。"""
    direct_ids = _agent_sub_user_ids(db, agent_user_id)
    if not direct_ids:
        return []
    agent = db.query(User).filter(User.id == agent_user_id).first()
    if _agent_level(agent) == 2:
        return direct_ids
    second_agent_ids = [
        uid
        for (uid,) in (
            db.query(User.id)
            .filter(User.id.in_(direct_ids), User.is_agent == True, User.agent_level == 2)  # noqa: E712
            .all()
        )
    ]
    if not second_agent_ids:
        return direct_ids
    second_sub_ids = [
        uid
        for (uid,) in db.query(User.id).filter(User.parent_user_id.in_(second_agent_ids)).all()
    ]
    return list(dict.fromkeys(direct_ids + second_sub_ids))


def _agent_second_agent_ids(db: Session, agent_user_id: int) -> list[int]:
    return [
        uid
        for (uid,) in (
            db.query(User.id)
            .filter(User.parent_user_id == agent_user_id, User.is_agent == True, User.agent_level == 2)  # noqa: E712
            .all()
        )
    ]


def _require_level1_agent(db: Session, ctx: AdminContext) -> User:
    if ctx.role != "agent" or not ctx.user_id:
        raise HTTPException(status_code=403, detail="仅代理商可执行此操作")
    agent = db.query(User).filter(User.id == ctx.user_id).first()
    if not agent or not agent.is_agent:
        raise HTTPException(status_code=401, detail="代理商账号无效")
    if _agent_level(agent) != 1:
        raise HTTPException(status_code=403, detail="二级代理不能继续设置下级")
    return agent


def _user_public_payload(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "credits": float(u.credits or 0),
        "role": u.role,
        "is_agent": u.is_agent,
        "agent_level": _agent_level(u),
        "agent_openclaw_memory_enabled": bool(getattr(u, "agent_openclaw_memory_enabled", False)),
        "agent_task_dispatch_enabled": bool(getattr(u, "agent_task_dispatch_enabled", False)),
        "parent_user_id": u.parent_user_id,
        "brand_mark": getattr(u, "brand_mark", None),
        "is_overseas_user": bool(getattr(u, "is_overseas_user", False)),
        "llm_model_override": (getattr(u, "llm_model_override", None) or ""),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _assert_can_manage_user(db: Session, ctx: AdminContext, user_id: int, *, allow_agent_self: bool = False) -> None:
    if ctx.role == "admin":
        return
    if ctx.role != "agent" or ctx.user_id is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if allow_agent_self and int(user_id) == int(ctx.user_id):
        return
    if int(user_id) not in _agent_visible_user_ids(db, int(ctx.user_id)):
        raise HTTPException(status_code=403, detail="no permission for this user")


# ── 页面 ──

@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
def admin_page():
    html_path = Path(__file__).resolve().parent.parent / "static" / "admin.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="管理后台页面未找到")
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )

@router.get("/admin/static/{filename}", include_in_schema=False)
def admin_static(filename: str):
    static_dir = Path(__file__).resolve().parent.parent / "static"
    fp = static_dir / filename
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(fp)


# ── API ──

class LoginBody(BaseModel):
    username: str
    password: str = ""
    captcha_id: str = ""
    captcha_answer: str = ""
    sms_code: str = ""


@router.post("/admin/api/login")
def admin_login(body: LoginBody, db: Session = Depends(get_db)):
    """管理后台登录。优先匹配管理员（.env），否则尝试代理商账号密码。"""
    username = body.username.strip()
    password = body.password.strip()
    sms_code = (body.sms_code or "").strip()
    if not username or (not password and not sms_code):
        raise HTTPException(status_code=400, detail="请输入账号和密码或短信验证码")

    if _admin_enabled():
        admin_u = (settings.lobster_admin_username or "").strip()
        admin_p = (settings.lobster_admin_password or "").strip()
        if username == admin_u and password == admin_p:
            token = ADMIN_TOKEN_PREFIX + admin_p
            return {"ok": True, "token": token, "role": "admin", "display_name": "管理员"}

    from .auth import _normalize_cn_mobile, _login_account_key, _verify_sms_challenge, verify_password
    account_key = _login_account_key(username)
    if account_key:
        user = db.query(User).filter(User.email == account_key).first()
        sms_ok = False
        if user and user.is_agent and sms_code:
            mobile = _normalize_cn_mobile(username)
            sms_ok = _verify_sms_challenge(db, mobile, sms_code)
            if not sms_ok:
                raise HTTPException(status_code=400, detail="短信验证码错误或已过期，请重新获取")
        password_ok = bool(user and user.is_agent and password and verify_password(password, user.hashed_password))
        if user and user.is_agent and (password_ok or sms_ok):
            agent_jwt = jwt.encode(
                {"sub": str(user.id), "scope": "agent_admin", "exp": datetime.utcnow() + timedelta(days=7)},
                settings.secret_key,
                algorithm=_JWT_ALGORITHM,
            )
            token = AGENT_TOKEN_PREFIX + agent_jwt
            display = user.email.replace("@sms.lobster.local", "")
            return {
                "ok": True,
                "token": token,
                "role": "agent",
                "user_id": user.id,
                "display_name": display,
                "agent_level": _agent_level(user),
                "agent_openclaw_memory_enabled": bool(getattr(user, "agent_openclaw_memory_enabled", False)),
            }

    raise HTTPException(status_code=401, detail="用户名或密码/验证码错误")


@router.get("/admin/api/search")
def admin_search_user(
    q: str = "",
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return {"users": []}
    filters = [User.email.ilike(f"%{q}%")]
    if q.isdigit():
        id_value = int(q)
        if 0 < id_value <= 2_147_483_647:
            filters.append(User.id == id_value)
    query = db.query(User).filter(
        or_(*filters)
    )
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        query = query.filter(User.id.in_(sub_ids)) if sub_ids else query.filter(False)
    query = query.order_by(User.id).limit(50)
    users = []
    for u in query.all():
        users.append(_user_public_payload(u))
    return {"users": users}


@router.get("/admin/api/user/{user_id}")
def admin_user_detail(
    user_id: int,
    ledger_limit: int = 50,
    ledger_offset: int = 0,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        if user_id != ctx.user_id and user_id not in sub_ids:
            raise HTTPException(status_code=403, detail="无权查看此用户")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    now = datetime.utcnow()
    devices = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == user_id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(20)
        .all()
    )
    task_count = db.query(func.count(ScheduledTask.id)).filter(ScheduledTask.user_id == user_id).scalar() or 0
    run_count = db.query(func.count(ScheduledTaskRun.id)).filter(ScheduledTaskRun.user_id == user_id).scalar() or 0
    ledger_limit = min(max(int(ledger_limit or 50), 1), 200)
    ledger_offset = max(int(ledger_offset or 0), 0)
    ledger_query = db.query(CreditLedger).filter(CreditLedger.user_id == user_id)
    ledger_total = ledger_query.with_entities(func.count(CreditLedger.id)).scalar() or 0
    ledger = (
        ledger_query
        .order_by(CreditLedger.created_at.desc())
        .offset(ledger_offset)
        .limit(ledger_limit)
        .all()
    )
    ledger_list = []
    for entry in ledger:
        ledger_list.append({
            "id": entry.id,
            "delta": float(entry.delta),
            "balance_after": float(entry.balance_after),
            "entry_type": entry.entry_type,
            "description": entry.description,
            "ref_type": entry.ref_type,
            "ref_id": entry.ref_id,
            "meta": entry.meta,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        })
    return {
        "user": _user_public_payload(user),
        "devices": [
            {
                "installation_id": r.installation_id,
                "display_name": r.display_name,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                "online": ((now - r.last_seen_at).total_seconds() <= 20) if r.last_seen_at else False,
            }
            for r in devices
        ],
        "task_summary": {"tasks": int(task_count), "runs": int(run_count)},
        "ledger": ledger_list,
        "ledger_pagination": {
            "total": int(ledger_total),
            "limit": int(ledger_limit),
            "offset": int(ledger_offset),
            "has_prev": ledger_offset > 0,
            "has_next": ledger_offset + ledger_limit < int(ledger_total),
        },
    }


class AddCreditsBody(BaseModel):
    user_id: int
    amount: float
    description: str = "管理员手动调整积分"


@router.post("/admin/api/add-credits")
def admin_add_credits(
    body: AddCreditsBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        if body.user_id not in sub_ids:
            raise HTTPException(status_code=403, detail="无权操作此用户")
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    old_credits = quantize_credits(user.credits or 0)
    delta = quantize_credits_signed(body.amount)
    if delta == 0:
        raise HTTPException(status_code=400, detail="积分数量不能为 0")
    new_credits = old_credits + delta
    if new_credits < 0:
        raise HTTPException(status_code=400, detail=f"积分不足，当前 {old_credits}，操作 {delta}")
    user.credits = quantize_credits(new_credits)
    entry_type = "recharge" if delta > 0 else "admin_deduct"
    description = (body.description or "").strip() or ("管理员手动加积分" if delta > 0 else "管理员手动扣减积分")

    append_credit_ledger(
        db,
        user.id,
        delta,
        entry_type,
        user.credits,
        description=description[:200],
        meta={"source": "admin_panel", "admin_action": "add" if delta > 0 else "deduct"},
    )
    db.commit()
    db.refresh(user)

    return {
        "ok": True,
        "user_id": user.id,
        "email": user.email,
        "old_credits": float(old_credits),
        "new_credits": float(quantize_credits(user.credits)),
        "delta": float(delta),
    }


class ResetPasswordBody(BaseModel):
    user_id: int
    new_password: str


class SetUserLlmModelBody(BaseModel):
    user_id: int
    model: str = ""


@router.post("/admin/api/reset-password")
def admin_reset_password(
    body: ResetPasswordBody,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """管理员重置指定用户的登录密码。密码规则与注册一致（6~128 位）。"""
    pwd = (body.new_password or "").strip()
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if len(pwd) > 128:
        raise HTTPException(status_code=400, detail="密码长度不能超过 128 位")
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from .auth import get_password_hash

    user.hashed_password = get_password_hash(pwd)
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("[admin/reset-password] user_id=%s email=%s ok", user.id, user.email)
    return {"ok": True, "user_id": user.id, "email": user.email}


@router.post("/admin/api/user-llm-model")
def admin_set_user_llm_model(
    body: SetUserLlmModelBody,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    model = (body.model or "").strip()
    if len(model) > 128:
        raise HTTPException(status_code=400, detail="模型ID长度不能超过 128")
    if model and any(ch.isspace() for ch in model):
        raise HTTPException(status_code=400, detail="模型ID不能包含空格或换行")
    if model and model != "openai/gpt-5.5":
        raise HTTPException(status_code=400, detail="当前只允许设置 openai/gpt-5.5；留空恢复默认")
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.llm_model_override = model or None
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "[admin/user-llm-model] user_id=%s email=%s model_override=%s",
        user.id,
        user.email,
        model or "-",
    )
    return {"ok": True, "user": _user_public_payload(user)}


@router.get("/admin/api/users")
def admin_list_users(
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    base_q = db.query(User)
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        base_q = base_q.filter(User.id.in_(sub_ids)) if sub_ids else base_q.filter(False)
    term = (q or "").strip()
    if term:
        filters = [User.email.ilike(f"%{term}%")]
        if term.isdigit():
            id_value = int(term)
            if 0 < id_value <= 2_147_483_647:
                filters.append(User.id == id_value)
        base_q = base_q.filter(or_(*filters))
    total = base_q.with_entities(func.count(User.id)).scalar() or 0
    offset = (max(1, page) - 1) * page_size
    users = base_q.order_by(User.id.desc()).offset(offset).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [
            _user_public_payload(u)
            for u in users
        ],
    }


# ── 模板配置：IP 日更模板与下发 ──


class AdminTemplateBody(BaseModel):
    owner_user_id: Optional[int] = None
    name: str = ""
    keyword_ids: list[int] = Field(default_factory=list)
    competitor_ids: list[int] = Field(default_factory=list)
    memory_doc_ids: list[str] = Field(default_factory=list)
    requirements: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)


class AdminTemplateGrantBody(BaseModel):
    target_user_ids: list[int] = Field(default_factory=list)


class AdminTemplateKeywordBody(BaseModel):
    owner_user_id: Optional[int] = None
    keyword: str = ""
    display_name: str = ""


class AdminTemplateCompetitorBody(BaseModel):
    owner_user_id: Optional[int] = None
    platform: str = "douyin"
    account_key: str = ""
    display_name: str = ""
    homepage_url: str = ""
    industry_tags: str = ""


def _admin_clean_text(value: object, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _admin_clean_int_ids(values: list[int], limit: int = 100) -> list[int]:
    out: list[int] = []
    for raw in values or []:
        try:
            val = int(raw)
        except Exception:
            continue
        if val > 0 and val not in out:
            out.append(val)
        if len(out) >= limit:
            break
    return out


def _admin_clean_doc_ids(values: list[str], limit: int = 100) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        val = "".join(ch for ch in str(raw or "").strip() if ch.isalnum() or ch in "_-")[:64]
        if val and val not in out:
            out.append(val)
        if len(out) >= limit:
            break
    return out


def _admin_template_owner_id(db: Session, ctx: AdminContext, requested_owner_user_id: Optional[int]) -> int:
    if ctx.role == "agent":
        if not ctx.user_id:
            raise HTTPException(status_code=403, detail="代理商账号无效")
        return int(ctx.user_id)
    owner_id = int(requested_owner_user_id or 0)
    if owner_id <= 0:
        return 0
    owner = db.query(User).filter(User.id == owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="模板归属用户不存在")
    return owner_id


def _admin_validate_template_refs(
    db: Session,
    owner_user_id: int,
    keyword_ids: list[int],
    competitor_ids: list[int],
    memory_doc_ids: list[str],
) -> tuple[list[int], list[int], list[str]]:
    clean_keywords = _admin_clean_int_ids(keyword_ids, 50)
    clean_competitors = _admin_clean_int_ids(competitor_ids, 50)
    clean_memory_docs = _admin_clean_doc_ids(memory_doc_ids, 50)
    if clean_keywords:
        found = {
            int(x)
            for (x,) in db.query(IPContentKeyword.id)
            .filter(IPContentKeyword.user_id == owner_user_id, IPContentKeyword.id.in_(clean_keywords))
            .all()
        }
        missing = [x for x in clean_keywords if x not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"关键词不存在或不属于归属用户：{missing[:5]}")
    if clean_competitors:
        found = {
            int(x)
            for (x,) in db.query(ContentCompetitorAccount.id)
            .filter(ContentCompetitorAccount.user_id == owner_user_id, ContentCompetitorAccount.id.in_(clean_competitors))
            .all()
        }
        missing = [x for x in clean_competitors if x not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"同行账号不存在或不属于归属用户：{missing[:5]}")
    if clean_memory_docs:
        found = {
            str(x)
            for (x,) in db.query(OpenClawMemoryDocument.doc_id)
            .filter(
                OpenClawMemoryDocument.target_user_id == owner_user_id,
                OpenClawMemoryDocument.status == "active",
                OpenClawMemoryDocument.doc_id.in_(clean_memory_docs),
            )
            .all()
        }
        missing = [x for x in clean_memory_docs if x not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"记忆文档不存在或不属于归属用户：{missing[:5]}")
    return clean_keywords, clean_competitors, clean_memory_docs


def _admin_template_payload(row: IPContentScheduleTemplate, owner: Optional[User] = None, grants: Optional[list[int]] = None) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "owner_user_id": row.user_id,
        "owner_name": (owner.email if owner else "") or "",
        "name": row.name,
        "keyword_ids": _admin_clean_int_ids(row.keyword_ids or [], 50),
        "competitor_ids": _admin_clean_int_ids(row.competitor_ids or [], 50),
        "memory_doc_ids": _admin_clean_doc_ids(row.memory_doc_ids or [], 50),
        "requirements": row.requirements or {},
        "status": row.status,
        "granted_user_ids": grants or [],
        "meta": row.meta or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/admin/api/ip-content/template-options")
def admin_template_options(
    owner_user_id: Optional[int] = None,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    owner_id = _admin_template_owner_id(db, ctx, owner_user_id)
    owner = db.query(User).filter(User.id == owner_id).first()
    keywords = (
        db.query(IPContentKeyword)
        .filter(IPContentKeyword.user_id == owner_id, IPContentKeyword.status == "active")
        .order_by(IPContentKeyword.created_at.desc(), IPContentKeyword.id.desc())
        .limit(300)
        .all()
    )
    competitors = (
        db.query(ContentCompetitorAccount)
        .filter(ContentCompetitorAccount.user_id == owner_id, ContentCompetitorAccount.status == "active")
        .order_by(ContentCompetitorAccount.created_at.desc(), ContentCompetitorAccount.id.desc())
        .limit(300)
        .all()
    )
    memory_docs = (
        db.query(OpenClawMemoryDocument)
        .filter(OpenClawMemoryDocument.target_user_id == owner_id, OpenClawMemoryDocument.status == "active")
        .order_by(OpenClawMemoryDocument.updated_at.desc(), OpenClawMemoryDocument.id.desc())
        .limit(300)
        .all()
    )
    return {
        "ok": True,
        "owner": _user_public_payload(owner) if owner else None,
        "keywords": [
            {"id": row.id, "keyword": row.keyword, "display_name": row.display_name or row.keyword}
            for row in keywords
        ],
        "competitors": [
            {
                "id": row.id,
                "platform": row.platform,
                "account_key": row.account_key,
                "display_name": row.display_name or row.account_key,
                "homepage_url": row.homepage_url,
            }
            for row in competitors
        ],
        "memory_docs": [
            {"doc_id": row.doc_id, "title": row.title, "filename": row.filename}
            for row in memory_docs
        ],
    }


@router.post("/admin/api/ip-content/keywords")
def admin_create_ip_keyword(
    body: AdminTemplateKeywordBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    owner_id = _admin_template_owner_id(db, ctx, body.owner_user_id)
    keyword = _admin_clean_text(body.keyword, 191)
    if not keyword:
        raise HTTPException(status_code=400, detail="请填写关键词")
    display_name = _admin_clean_text(body.display_name, 255)
    row = (
        db.query(IPContentKeyword)
        .filter(IPContentKeyword.user_id == owner_id, IPContentKeyword.keyword == keyword)
        .first()
    )
    if row:
        row.display_name = display_name or keyword
        row.status = "active"
        row.updated_at = datetime.utcnow()
    else:
        row = IPContentKeyword(
            user_id=owner_id,
            keyword=keyword,
            display_name=display_name or keyword,
            status="active",
            meta={"source": "admin_template_config"},
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": {"id": row.id, "keyword": row.keyword, "display_name": row.display_name}}


@router.delete("/admin/api/ip-content/keywords/{keyword_id}")
def admin_delete_ip_keyword(
    keyword_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentKeyword).filter(IPContentKeyword.id == keyword_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="关键词不存在")
    if int(row.user_id or 0) > 0:
        _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    elif ctx.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除该关键词")
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权删除该关键词")
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/admin/api/ip-content/competitors")
def admin_create_ip_competitor(
    body: AdminTemplateCompetitorBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    owner_id = _admin_template_owner_id(db, ctx, body.owner_user_id)
    platform = _admin_clean_text(body.platform, 32) or "douyin"
    account_key = _admin_clean_text(body.account_key, 191)
    if not account_key:
        raise HTTPException(status_code=400, detail="请填写同行账号")
    display_name = _admin_clean_text(body.display_name, 255)
    row = (
        db.query(ContentCompetitorAccount)
        .filter(
            ContentCompetitorAccount.user_id == owner_id,
            ContentCompetitorAccount.platform == platform,
            ContentCompetitorAccount.account_key == account_key,
        )
        .first()
    )
    if row:
        row.display_name = display_name or account_key
        row.homepage_url = _admin_clean_text(body.homepage_url, 1000) or row.homepage_url
        row.industry_tags = _admin_clean_text(body.industry_tags, 1000) or row.industry_tags
        row.status = "active"
        row.updated_at = datetime.utcnow()
    else:
        row = ContentCompetitorAccount(
            user_id=owner_id,
            platform=platform,
            account_key=account_key,
            display_name=display_name or account_key,
            homepage_url=_admin_clean_text(body.homepage_url, 1000) or None,
            industry_tags=_admin_clean_text(body.industry_tags, 1000) or None,
            status="active",
            meta={"source": "admin_template_config"},
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "item": {
            "id": row.id,
            "platform": row.platform,
            "account_key": row.account_key,
            "display_name": row.display_name,
            "homepage_url": row.homepage_url,
        },
    }


@router.delete("/admin/api/ip-content/competitors/{competitor_id}")
def admin_delete_ip_competitor(
    competitor_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(ContentCompetitorAccount).filter(ContentCompetitorAccount.id == competitor_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="同行账号不存在")
    if int(row.user_id or 0) > 0:
        _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    elif ctx.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除该同行账号")
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权删除该同行账号")
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/admin/api/ip-content/templates")
def admin_list_ip_templates(
    owner_user_id: Optional[int] = None,
    q: str = "",
    page: int = 1,
    page_size: int = 20,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    page = max(1, int(page or 1))
    page_size = min(max(int(page_size or 20), 1), 100)
    query = db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.status == "active")
    if ctx.role == "agent":
        query = query.filter(IPContentScheduleTemplate.user_id == int(ctx.user_id or 0))
    elif owner_user_id:
        query = query.filter(IPContentScheduleTemplate.user_id == int(owner_user_id))
    term = (q or "").strip()
    if term:
        query = query.filter(IPContentScheduleTemplate.name.ilike(f"%{term}%"))
    total = int(query.with_entities(func.count(IPContentScheduleTemplate.id)).scalar() or 0)
    rows = (
        query.order_by(IPContentScheduleTemplate.updated_at.desc(), IPContentScheduleTemplate.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    owner_ids = sorted({int(row.user_id) for row in rows})
    owners = {row.id: row for row in db.query(User).filter(User.id.in_(owner_ids)).all()} if owner_ids else {}
    template_ids = [int(row.id) for row in rows]
    grant_map: dict[int, list[int]] = {}
    if template_ids:
        grants = (
            db.query(H5AgentTemplateGrant)
            .filter(H5AgentTemplateGrant.template_id.in_(template_ids), H5AgentTemplateGrant.status == "active")
            .all()
        )
        for grant in grants:
            grant_map.setdefault(int(grant.template_id), []).append(int(grant.target_user_id))
    return {
        "items": [_admin_template_payload(row, owners.get(row.user_id), grant_map.get(int(row.id), [])) for row in rows],
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": page * page_size < total,
        },
    }


@router.get("/admin/api/ip-content/templates/{template_id}/grant-users")
def admin_ip_template_grant_users(
    template_id: int,
    q: str = "",
    page: int = 1,
    page_size: int = 20,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.id == template_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    if ctx.role != "admin" or int(row.user_id or 0) > 0:
        _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权管理该模板")

    authorized_user_ids = [
        int(target_user_id)
        for (target_user_id,) in (
            db.query(H5AgentTemplateGrant.target_user_id)
            .filter(
                H5AgentTemplateGrant.template_id == template_id,
                H5AgentTemplateGrant.owner_user_id == int(row.user_id or 0),
                H5AgentTemplateGrant.status == "active",
            )
            .all()
        )
    ]
    authorized_set = set(authorized_user_ids)

    page = max(1, int(page or 1))
    page_size = min(max(int(page_size or 20), 1), 100)
    query = db.query(User)
    if ctx.role == "agent":
        visible_ids = _agent_visible_user_ids(db, int(ctx.user_id or 0))
        query = query.filter(User.id.in_(visible_ids)) if visible_ids else query.filter(False)
    if int(row.user_id or 0) > 0:
        query = query.filter(User.id != int(row.user_id))

    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        conds = [User.email.ilike(like)]
        if term.isdigit():
            try:
                conds.append(User.id == int(term))
            except Exception:
                pass
        query = query.filter(or_(*conds))

    total = int(query.with_entities(func.count(User.id)).scalar() or 0)
    users = query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "ok": True,
        "template_id": template_id,
        "authorized_user_ids": authorized_user_ids,
        "items": [
            {**_user_public_payload(user), "authorized": int(user.id) in authorized_set}
            for user in users
        ],
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": page * page_size < total,
        },
    }


@router.post("/admin/api/ip-content/templates")
def admin_save_ip_template(
    body: AdminTemplateBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    owner_id = _admin_template_owner_id(db, ctx, body.owner_user_id)
    name = _admin_clean_text(body.name, 160)
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    keyword_ids, competitor_ids, memory_doc_ids = _admin_validate_template_refs(
        db, owner_id, body.keyword_ids, body.competitor_ids, body.memory_doc_ids
    )
    row = IPContentScheduleTemplate(
        user_id=owner_id,
        name=name,
        keyword_ids=keyword_ids,
        competitor_ids=competitor_ids,
        memory_doc_ids=memory_doc_ids,
        memory_docs=[],
        requirements=body.requirements or {},
        meta={**(body.meta or {}), "source": "admin_template_config"},
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _admin_template_payload(row)}


@router.patch("/admin/api/ip-content/templates/{template_id}")
def admin_update_ip_template(
    template_id: int,
    body: AdminTemplateBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.id == template_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权修改该模板")
    name = _admin_clean_text(body.name, 160)
    if not name:
        raise HTTPException(status_code=400, detail="请填写模板名称")
    keyword_ids, competitor_ids, memory_doc_ids = _admin_validate_template_refs(
        db, int(row.user_id), body.keyword_ids, body.competitor_ids, body.memory_doc_ids
    )
    row.name = name
    row.keyword_ids = keyword_ids
    row.competitor_ids = competitor_ids
    row.memory_doc_ids = memory_doc_ids
    row.memory_docs = []
    row.requirements = body.requirements or {}
    row.meta = {**(body.meta or {}), "source": "admin_template_config"}
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _admin_template_payload(row)}


@router.delete("/admin/api/ip-content/templates/{template_id}")
def admin_delete_ip_template(
    template_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.id == template_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权删除该模板")
    row.status = "deleted"
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/admin/api/ip-content/templates/{template_id}/grants")
def admin_save_ip_template_grants(
    template_id: int,
    body: AdminTemplateGrantBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.id == template_id).first()
    if not row or row.status != "active":
        raise HTTPException(status_code=404, detail="模板不存在")
    _assert_can_manage_user(db, ctx, int(row.user_id), allow_agent_self=True)
    if ctx.role == "agent" and int(row.user_id) != int(ctx.user_id or 0):
        raise HTTPException(status_code=403, detail="无权下发该模板")
    requested = _admin_clean_int_ids(body.target_user_ids, 500)
    for target_user_id in requested:
        _assert_can_manage_user(db, ctx, target_user_id)
        if target_user_id == int(row.user_id):
            raise HTTPException(status_code=400, detail="不能把模板下发给归属用户自己")
    now = datetime.utcnow()
    existing = (
        db.query(H5AgentTemplateGrant)
        .filter(H5AgentTemplateGrant.template_id == template_id, H5AgentTemplateGrant.owner_user_id == int(row.user_id))
        .all()
    )
    remaining = set(requested)
    requested_set = set(requested)
    for grant in existing:
        grant.status = "active" if int(grant.target_user_id) in requested_set else "revoked"
        grant.updated_at = now
        remaining.discard(int(grant.target_user_id))
    for target_user_id in remaining:
        db.add(
            H5AgentTemplateGrant(
                template_id=template_id,
                owner_user_id=int(row.user_id),
                target_user_id=target_user_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    return {"ok": True, "template_id": template_id, "target_user_ids": requested}


# ── 技能可见性管理 ──


@router.get("/admin/api/user-skill-visibility/{user_id}")
def admin_get_user_skill_visibility(
    user_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _assert_can_manage_user(db, ctx, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from .skills import (
        _user_has_custom_visibility,
        _user_visible_package_ids,
        _load_registry,
        _pkg_store_visibility,
        _skill_store_admin,
    )
    visible = _user_visible_package_ids(
        db,
        user,
        is_overseas_client=False,
    )
    unlocked = {
        row[0]
        for row in db.query(SkillUnlock.package_id).filter(SkillUnlock.user_id == user_id).all()
    }
    registry = _load_registry()
    packages = registry.get("packages", {})
    all_pkgs = [
        {
            "id": k,
            "name": v.get("name", k),
            "store_visibility": _pkg_store_visibility(v),
            "unlock_price_yuan": v.get("unlock_price_yuan"),
            "unlock_price_credits": v.get("unlock_price_credits"),
            "capabilities_count": len((v.get("capabilities") or {})),
        }
        for k, v in packages.items()
    ]
    feature_pkg_by_id = {str(pkg.get("id") or ""): dict(pkg) for pkg in FEATURE_FLAG_PACKAGES}
    all_pkgs = [dict(pkg) for pkg in FEATURE_FLAG_PACKAGES] + [
        pkg for pkg in all_pkgs if pkg["id"] not in feature_pkg_by_id
    ]
    return {
        "user_id": user_id,
        "is_admin": _skill_store_admin(user),
        "registration_is_overseas_user": bool(getattr(user, "is_overseas_user", False)),
        "has_custom_visibility": _user_has_custom_visibility(db, user_id),
        "visible_ids": sorted(visible),
        "unlocked_ids": sorted(unlocked),
        "all_packages": all_pkgs,
    }


class AdminSkillVisUpdate(BaseModel):
    add: list[str] = []
    remove: list[str] = []
    unlock_add: list[str] = []
    unlock_remove: list[str] = []


@router.post("/admin/api/user-skill-visibility/{user_id}")
def admin_update_user_skill_visibility(
    user_id: int,
    body: AdminSkillVisUpdate,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _assert_can_manage_user(db, ctx, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from .skills import _ensure_user_visibility_seeded
    _ensure_user_visibility_seeded(db, user)
    added, removed, unlocked_added, unlocked_removed = [], [], [], []
    for pkg_id in body.add:
        pkg_id = pkg_id.strip()
        if not pkg_id:
            continue
        exists = db.query(UserSkillVisibility).filter(
            UserSkillVisibility.user_id == user_id,
            UserSkillVisibility.package_id == pkg_id,
        ).first()
        if not exists:
            db.add(UserSkillVisibility(user_id=user_id, package_id=pkg_id))
            added.append(pkg_id)
    for pkg_id in body.remove:
        pkg_id = pkg_id.strip()
        if not pkg_id:
            continue
        row = db.query(UserSkillVisibility).filter(
            UserSkillVisibility.user_id == user_id,
            UserSkillVisibility.package_id == pkg_id,
        ).first()
        if row:
            db.delete(row)
            removed.append(pkg_id)
    for pkg_id in body.unlock_add:
        pkg_id = pkg_id.strip()
        if not pkg_id:
            continue
        exists = db.query(SkillUnlock).filter(
            SkillUnlock.user_id == user_id,
            SkillUnlock.package_id == pkg_id,
        ).first()
        if not exists:
            db.add(SkillUnlock(user_id=user_id, package_id=pkg_id))
            unlocked_added.append(pkg_id)
    for pkg_id in body.unlock_remove:
        pkg_id = pkg_id.strip()
        if not pkg_id:
            continue
        rows = db.query(SkillUnlock).filter(
            SkillUnlock.user_id == user_id,
            SkillUnlock.package_id == pkg_id,
        ).all()
        for row in rows:
            db.delete(row)
            unlocked_removed.append(pkg_id)
    db.commit()
    return {
        "ok": True,
        "added": added,
        "removed": removed,
        "unlocked_added": unlocked_added,
        "unlocked_removed": unlocked_removed,
    }


# ── 数据统计 ──


@router.get("/admin/api/stats")
def admin_stats(
    days: int = 30,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today_start - timedelta(days=max(1, min(days, 90)))

    # 代理商只看自己下级的数据
    agent_sub_ids: Optional[list[int]] = None
    if ctx.role == "agent":
        agent_sub_ids = _agent_visible_user_ids(db, ctx.user_id)

    def _user_filter(q):
        if agent_sub_ids is not None:
            return q.filter(User.id.in_(agent_sub_ids)) if agent_sub_ids else q.filter(False)
        return q

    def _order_filter(q):
        if agent_sub_ids is not None:
            return q.filter(RechargeOrder.user_id.in_(agent_sub_ids)) if agent_sub_ids else q.filter(False)
        return q

    def _ledger_filter(q):
        if agent_sub_ids is not None:
            return q.filter(CreditLedger.user_id.in_(agent_sub_ids)) if agent_sub_ids else q.filter(False)
        return q

    def _cap_filter(q):
        if agent_sub_ids is not None:
            return q.filter(CapabilityCallLog.user_id.in_(agent_sub_ids)) if agent_sub_ids else q.filter(False)
        return q

    total_users = _user_filter(db.query(func.count(User.id))).scalar() or 0
    today_new_users = (
        _user_filter(db.query(func.count(User.id)).filter(User.created_at >= today_start)).scalar() or 0
    )
    total_credits = float(
        _user_filter(db.query(func.coalesce(func.sum(User.credits), 0))).scalar() or 0
    )

    today_recharge_paid = float(
        _order_filter(
            db.query(func.coalesce(func.sum(RechargeOrder.credits), 0))
            .filter(RechargeOrder.status == "paid", RechargeOrder.paid_at >= today_start)
        ).scalar() or 0
    )

    today_recharge_admin = float(
        _ledger_filter(
            db.query(func.coalesce(func.sum(CreditLedger.delta), 0))
            .filter(
                CreditLedger.entry_type == "recharge",
                CreditLedger.description.like("%管理员%"),
                CreditLedger.created_at >= today_start,
            )
        ).scalar() or 0
    )

    today_consume = float(
        _ledger_filter(
            db.query(func.coalesce(func.sum(CreditLedger.delta), 0))
            .filter(
                CreditLedger.entry_type.in_(["sutui_chat", "pre_deduct", "settle", "unit_deduct"]),
                CreditLedger.delta < 0,
                CreditLedger.created_at >= today_start,
            )
        ).scalar() or 0
    )

    paid_orders_today = (
        _order_filter(
            db.query(func.count(RechargeOrder.id))
            .filter(RechargeOrder.status == "paid", RechargeOrder.paid_at >= today_start)
        ).scalar() or 0
    )

    total_paid_revenue_fen = (
        _order_filter(
            db.query(func.coalesce(func.sum(RechargeOrder.callback_amount_fen), 0))
            .filter(RechargeOrder.status == "paid")
        ).scalar() or 0
    )

    date_col = func.date(User.created_at)
    daily_users_raw = (
        _user_filter(
            db.query(date_col.label("d"), func.count(User.id).label("cnt"))
            .filter(User.created_at >= range_start)
        )
        .group_by(date_col)
        .order_by(date_col)
        .all()
    )
    daily_users = [{"date": str(r.d), "count": r.cnt} for r in daily_users_raw]

    order_date = func.date(RechargeOrder.paid_at)
    daily_recharge_raw = (
        _order_filter(
            db.query(order_date.label("d"), func.sum(RechargeOrder.credits).label("total"))
            .filter(RechargeOrder.status == "paid", RechargeOrder.paid_at >= range_start)
        )
        .group_by(order_date)
        .order_by(order_date)
        .all()
    )
    daily_recharge = [{"date": str(r.d), "amount": float(r.total)} for r in daily_recharge_raw]

    ledger_date = func.date(CreditLedger.created_at)

    daily_consume_raw = (
        _ledger_filter(
            db.query(ledger_date.label("d"), func.sum(CreditLedger.delta).label("total"))
            .filter(
                CreditLedger.entry_type.in_(["sutui_chat", "pre_deduct", "settle", "unit_deduct"]),
                CreditLedger.delta < 0,
                CreditLedger.created_at >= range_start,
            )
        )
        .group_by(ledger_date)
        .order_by(ledger_date)
        .all()
    )
    daily_consume = [{"date": str(r.d), "amount": abs(float(r.total))} for r in daily_consume_raw]

    cap_ranking_raw = (
        _cap_filter(
            db.query(
                CapabilityCallLog.capability_id,
                func.count(CapabilityCallLog.id).label("calls"),
                func.sum(CapabilityCallLog.credits_charged).label("credits"),
            )
            .filter(CapabilityCallLog.created_at >= range_start)
        )
        .group_by(CapabilityCallLog.capability_id)
        .order_by(func.count(CapabilityCallLog.id).desc())
        .limit(10)
        .all()
    )
    capability_ranking = [
        {"capability_id": r.capability_id, "calls": r.calls, "credits": float(r.credits or 0)}
        for r in cap_ranking_raw
    ]

    top_consumers_raw = (
        _ledger_filter(
            db.query(
                CreditLedger.user_id,
                func.sum(CreditLedger.delta).label("total_consumed"),
            )
            .filter(CreditLedger.delta < 0, CreditLedger.created_at >= range_start)
        )
        .group_by(CreditLedger.user_id)
        .order_by(func.sum(CreditLedger.delta))
        .limit(10)
        .all()
    )
    top_consumer_ids = [r.user_id for r in top_consumers_raw]
    user_map = {}
    if top_consumer_ids:
        for u in db.query(User).filter(User.id.in_(top_consumer_ids)).all():
            user_map[u.id] = u.email
    top_consumers = [
        {
            "user_id": r.user_id,
            "email": user_map.get(r.user_id, "?"),
            "consumed": abs(float(r.total_consumed)),
        }
        for r in top_consumers_raw
    ]

    return {
        "overview": {
            "total_users": total_users,
            "today_new_users": today_new_users,
            "total_credits_pool": round(total_credits, 2),
            "today_recharge_paid": round(today_recharge_paid, 2),
            "today_recharge_admin": round(today_recharge_admin, 2),
            "today_consume": round(abs(today_consume), 2),
            "paid_orders_today": paid_orders_today,
            "total_revenue_yuan": round(int(total_paid_revenue_fen) / 100, 2),
        },
        "daily_users": daily_users,
        "daily_recharge": daily_recharge,
        "daily_consume": daily_consume,
        "capability_ranking": capability_ranking,
        "top_consumers": top_consumers,
    }


# ── 管理员专属：代理商管理 ──


@router.get("/admin/api/agents")
def admin_list_agents(
    q: str = "",
    page: int = 1,
    page_size: int = 20,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    page = max(1, int(page or 1))
    page_size = min(max(int(page_size or 20), 1), 100)
    term = (q or "").strip()

    def _apply_term(query):
        if not term:
            return query
        filters = [User.email.ilike(f"%{term}%")]
        if term.isdigit():
            id_value = int(term)
            if 0 < id_value <= 2_147_483_647:
                filters.append(User.id == id_value)
        return query.filter(or_(*filters))

    if ctx.role == "admin":
        base_q = db.query(User).filter(User.is_agent == True)  # noqa: E712
        base_q = _apply_term(base_q)
        total = int(base_q.with_entities(func.count(User.id)).scalar() or 0)
        rows = (
            base_q.order_by(User.agent_level.asc(), User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        agent_ids = [row.id for row in rows]
        sub_counts = {}
        if agent_ids:
            sub_counts = {
                int(parent_id): int(count)
                for parent_id, count in (
                    db.query(User.parent_user_id, func.count(User.id))
                    .filter(User.parent_user_id.in_(agent_ids))
                    .group_by(User.parent_user_id)
                    .all()
                )
            }
        summary = {
            "total_agents": int(db.query(func.count(User.id)).filter(User.is_agent == True).scalar() or 0),  # noqa: E712
            "level1_agents": int(
                db.query(func.count(User.id))
                .filter(User.is_agent == True, or_(User.agent_level == 1, User.agent_level == 0))  # noqa: E712
                .scalar()
                or 0
            ),
            "level2_agents": int(
                db.query(func.count(User.id))
                .filter(User.is_agent == True, User.agent_level == 2)  # noqa: E712
                .scalar()
                or 0
            ),
            "sub_users": int(db.query(func.count(User.id)).filter(User.parent_user_id.isnot(None)).scalar() or 0),
            "memory_enabled": int(
                db.query(func.count(User.id))
                .filter(User.is_agent == True, User.agent_openclaw_memory_enabled == True)  # noqa: E712
                .scalar()
                or 0
            ),
            "task_dispatch_enabled": int(
                db.query(func.count(User.id))
                .filter(User.is_agent == True, User.agent_task_dispatch_enabled == True)  # noqa: E712
                .scalar()
                or 0
            ),
        }
        items = []
        for row in rows:
            payload = _user_public_payload(row)
            payload["sub_count"] = sub_counts.get(int(row.id), 0)
            items.append(payload)
        return {
            "role": "admin",
            "summary": summary,
            "items": items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_prev": page > 1,
                "has_next": page * page_size < total,
            },
        }

    if ctx.role != "agent" or not ctx.user_id:
        raise HTTPException(status_code=403, detail="无权访问")

    agent = db.query(User).filter(User.id == ctx.user_id).first()
    visible_ids = _agent_visible_user_ids(db, int(ctx.user_id))
    base_q = db.query(User).filter(User.id.in_(visible_ids)) if visible_ids else db.query(User).filter(False)
    base_q = _apply_term(base_q)
    total = int(base_q.with_entities(func.count(User.id)).scalar() or 0)
    rows = (
        base_q.order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    second_agent_count = (
        int(
            db.query(func.count(User.id))
            .filter(User.id.in_(visible_ids), User.is_agent == True, User.agent_level == 2)  # noqa: E712
            .scalar()
            or 0
        )
        if visible_ids
        else 0
    )
    summary = {
        "total_agents": 1 if agent and getattr(agent, "is_agent", False) else 0,
        "level1_agents": 1 if agent and _agent_level(agent) == 1 else 0,
        "level2_agents": second_agent_count,
        "sub_users": len(visible_ids),
        "memory_enabled": 1 if agent and getattr(agent, "agent_openclaw_memory_enabled", False) else 0,
        "task_dispatch_enabled": 1 if agent and getattr(agent, "agent_task_dispatch_enabled", False) else 0,
    }
    return {
        "role": "agent",
        "agent": _user_public_payload(agent) if agent else None,
        "summary": summary,
        "items": [_user_public_payload(row) for row in rows],
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": page * page_size < total,
        },
    }


class SetAgentBody(BaseModel):
    user_id: int
    is_agent: bool


@router.post("/admin/api/set-agent")
def admin_set_agent(
    body: SetAgentBody,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """管理员将某用户设为/取消代理商。"""
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_agent = body.is_agent
    user.agent_level = 1 if body.is_agent else 0
    if not body.is_agent:
        user.agent_openclaw_memory_enabled = False
        user.agent_task_dispatch_enabled = False
    db.add(user)
    db.commit()
    db.refresh(user)
    action = "设为代理商" if body.is_agent else "取消代理商"
    logger.info("[admin/set-agent] user_id=%s email=%s action=%s", user.id, user.email, action)
    return {
        "ok": True,
        "user_id": user.id,
        "email": user.email,
        "is_agent": user.is_agent,
        "agent_level": _agent_level(user),
        "agent_openclaw_memory_enabled": bool(getattr(user, "agent_openclaw_memory_enabled", False)),
        "agent_task_dispatch_enabled": bool(getattr(user, "agent_task_dispatch_enabled", False)),
    }


# ── 代理商：认领/移除下级 ──


class ClaimSubBody(BaseModel):
    account: str


class SetSecondAgentBody(BaseModel):
    user_id: int
    enabled: bool


class AssignSubParentBody(BaseModel):
    user_id: int
    second_agent_user_id: Optional[int] = None


@router.post("/admin/api/agent/claim-sub")
def agent_claim_sub(
    body: ClaimSubBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """代理商通过账号认领下级用户。仅当该用户未被其他代理商认领时可操作。管理员也可调用。"""
    if ctx.role != "agent":
        raise HTTPException(status_code=400, detail="此接口仅限代理商使用，管理员请使用 set-agent + assign-parent")
    agent = _require_level1_agent(db, ctx)
    agent_user_id = agent.id

    from .auth import _login_account_key
    account_key = _login_account_key(body.account.strip())
    if not account_key:
        raise HTTPException(status_code=400, detail="账号格式无效")

    target_user = db.query(User).filter(User.email == account_key).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="未找到该用户")
    if target_user.id == agent_user_id:
        raise HTTPException(status_code=400, detail="不能添加自己为下级")
    if target_user.parent_user_id is not None:
        if target_user.parent_user_id == agent_user_id:
            raise HTTPException(status_code=400, detail="该用户已经是你的下级")
        current_parent = db.query(User).filter(User.id == target_user.parent_user_id).first()
        if current_parent and current_parent.is_agent:
            raise HTTPException(status_code=409, detail="该用户已被其他代理商认领，无法添加")

    target_user.parent_user_id = agent_user_id
    db.add(target_user)
    db.commit()
    db.refresh(target_user)
    logger.info("[agent/claim-sub] agent_id=%s claimed user_id=%s email=%s", agent_user_id, target_user.id, target_user.email)
    return {
        "ok": True,
        "user_id": target_user.id,
        "email": target_user.email,
    }


@router.post("/admin/api/agent/set-second-agent")
def agent_set_second_agent(
    body: SetSecondAgentBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """一级代理将自己的某个直属下级设置/取消为二级代理。"""
    agent = _require_level1_agent(db, ctx)
    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target_user.parent_user_id != agent.id:
        raise HTTPException(status_code=403, detail="只能设置自己的直属下级")
    if target_user.id == agent.id:
        raise HTTPException(status_code=400, detail="不能设置自己")

    if body.enabled:
        target_user.is_agent = True
        target_user.agent_level = 2
    else:
        if _agent_level(target_user) != 2:
            raise HTTPException(status_code=400, detail="该用户不是二级代理")
        child_count = db.query(func.count(User.id)).filter(User.parent_user_id == target_user.id).scalar() or 0
        if child_count:
            raise HTTPException(status_code=400, detail="请先将该二级代理名下下级移回或改派后再取消")
        target_user.is_agent = False
        target_user.agent_level = 0
        target_user.agent_openclaw_memory_enabled = False
        target_user.agent_task_dispatch_enabled = False
    db.add(target_user)
    db.commit()
    db.refresh(target_user)
    logger.info(
        "[agent/set-second-agent] agent_id=%s target_id=%s enabled=%s",
        agent.id,
        target_user.id,
        body.enabled,
    )
    return {"ok": True, "user": _user_public_payload(target_user)}


@router.post("/admin/api/agent/assign-sub-parent")
def agent_assign_sub_parent(
    body: AssignSubParentBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """一级代理把自己体系内的普通下级分配给某个二级代理，或移回直属。"""
    agent = _require_level1_agent(db, ctx)
    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target_user.id == agent.id:
        raise HTTPException(status_code=400, detail="不能操作自己")
    if _agent_level(target_user) == 2:
        raise HTTPException(status_code=400, detail="二级代理本人不能被分配到其他二级代理名下")

    second_agent_ids = _agent_second_agent_ids(db, agent.id)
    allowed_parent_ids = set([agent.id] + second_agent_ids)
    if target_user.parent_user_id not in allowed_parent_ids:
        raise HTTPException(status_code=403, detail="只能调整自己体系内的下级")

    if body.second_agent_user_id:
        second_agent = db.query(User).filter(User.id == body.second_agent_user_id).first()
        if not second_agent or second_agent.id not in second_agent_ids or _agent_level(second_agent) != 2:
            raise HTTPException(status_code=400, detail="请选择自己的直属二级代理")
        target_user.parent_user_id = second_agent.id
    else:
        target_user.parent_user_id = agent.id
    db.add(target_user)
    db.commit()
    db.refresh(target_user)
    logger.info(
        "[agent/assign-sub-parent] agent_id=%s target_id=%s new_parent=%s",
        agent.id,
        target_user.id,
        target_user.parent_user_id,
    )
    return {"ok": True, "user": _user_public_payload(target_user)}


class RemoveSubBody(BaseModel):
    user_id: int


@router.post("/admin/api/agent/remove-sub")
def agent_remove_sub(
    body: RemoveSubBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """代理商移除自己的下级。管理员也可调用以解除任何上下级关系。"""
    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if ctx.role == "agent":
        _require_level1_agent(db, ctx)
        if target_user.parent_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="该用户不是你的下级")
        if _agent_level(target_user) == 2:
            raise HTTPException(status_code=400, detail="请先取消二级代理后再移除此下级")

    target_user.parent_user_id = None
    db.add(target_user)
    db.commit()
    logger.info("[agent/remove-sub] operator=%s/%s removed parent of user_id=%s", ctx.role, ctx.user_id, target_user.id)
    return {"ok": True, "user_id": target_user.id}


@router.get("/admin/api/agent/my-info")
def agent_my_info(
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """返回当前登录角色信息。"""
    if ctx.role == "admin":
        return {"role": "admin", "display_name": "管理员"}
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    direct_sub_count = db.query(func.count(User.id)).filter(User.parent_user_id == ctx.user_id).scalar() or 0
    visible_sub_count = len(_agent_visible_user_ids(db, int(ctx.user_id)))
    return {
        "role": "agent",
        "user_id": user.id,
        "email": user.email,
        "display_name": user.email.replace("@sms.lobster.local", ""),
        "sub_count": direct_sub_count,
        "visible_sub_count": visible_sub_count,
        "agent_level": _agent_level(user),
        "agent_openclaw_memory_enabled": bool(getattr(user, "agent_openclaw_memory_enabled", False)),
    }


def _commission_relation_label(relation: str) -> str:
    return {
        "direct_sub": "直属下级充值",
        "second_agent_self": "二级代理本人充值",
        "second_level_sub_direct": "直属下级充值",
        "second_level_sub_grand": "二级下级充值",
    }.get(relation or "", relation or "-")


@router.get("/admin/api/agent/commissions")
def agent_commissions(
    limit: int = 100,
    offset: int = 0,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """代理商查看自己的分润金额和成功充值分润流水。"""
    if ctx.role != "agent" or not ctx.user_id:
        raise HTTPException(status_code=403, detail="仅代理商可查看")
    limit = min(max(int(limit or 100), 1), 200)
    offset = max(int(offset or 0), 0)
    base_q = db.query(AgentCommissionLedger).filter(AgentCommissionLedger.agent_user_id == ctx.user_id)
    total = base_q.with_entities(func.count(AgentCommissionLedger.id)).scalar() or 0
    total_commission_fen = (
        base_q.with_entities(func.coalesce(func.sum(AgentCommissionLedger.commission_fen), 0)).scalar() or 0
    )
    rows = (
        base_q.order_by(AgentCommissionLedger.created_at.desc(), AgentCommissionLedger.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    source_ids = [r.source_user_id for r in rows]
    order_ids = [r.recharge_order_id for r in rows]
    users = {u.id: u for u in db.query(User).filter(User.id.in_(source_ids)).all()} if source_ids else {}
    orders = {o.id: o for o in db.query(RechargeOrder).filter(RechargeOrder.id.in_(order_ids)).all()} if order_ids else {}

    def _yuan(fen: int) -> float:
        return round(int(fen or 0) / 100, 2)

    items = []
    for r in rows:
        u = users.get(r.source_user_id)
        order = orders.get(r.recharge_order_id)
        items.append({
            "id": r.id,
            "source_user_id": r.source_user_id,
            "source_account": (u.email.replace("@sms.lobster.local", "") if u else str(r.source_user_id)),
            "recharge_order_id": r.recharge_order_id,
            "out_trade_no": r.out_trade_no,
            "relation": r.relation,
            "relation_label": _commission_relation_label(r.relation),
            "relation_level": int(r.relation_level or 0),
            "base_amount_fen": int(r.base_amount_fen or 0),
            "base_amount_yuan": _yuan(r.base_amount_fen),
            "rate_percent": round(int(r.rate_bps or 0) / 100, 2),
            "commission_fen": int(r.commission_fen or 0),
            "commission_yuan": _yuan(r.commission_fen),
            "paid_at": order.paid_at.isoformat() if order and order.paid_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {
        "summary": {
            "total": int(total),
            "total_commission_fen": int(total_commission_fen or 0),
            "total_commission_yuan": _yuan(total_commission_fen),
        },
        "items": items,
        "pagination": {
            "total": int(total),
            "limit": int(limit),
            "offset": int(offset),
            "has_prev": offset > 0,
            "has_next": offset + limit < int(total),
        },
    }


def _juhe_owner_filter(query, ctx: AdminContext):
    if ctx.role == "admin":
        return query
    return query.filter(JuheWechatConfig.owner_role == "agent", JuheWechatConfig.owner_user_id == ctx.user_id)


def _juhe_can_bind_user(db: Session, ctx: AdminContext, user_id: int) -> None:
    _assert_can_manage_user(db, ctx, user_id, allow_agent_self=True)


def _juhe_config_payload(row: JuheWechatConfig, db: Session | None = None) -> dict:
    user_label = ""
    if db is not None:
        u = db.query(User).filter(User.id == row.user_id).first()
        if u:
            user_label = u.email.replace("@sms.lobster.local", "")
    return {
        "id": row.id,
        "user_id": row.user_id,
        "user_label": user_label,
        "label": row.label,
        "guid": row.guid,
        "masked_app_key": mask_secret(row.app_key or ""),
        "has_app_secret": bool((row.app_secret or "").strip()),
        "owner_role": getattr(row, "owner_role", None) or "user",
        "owner_user_id": getattr(row, "owner_user_id", None),
        "status": row.status,
        "last_status": row.last_status,
        "last_status_at": row.last_status_at.isoformat() if row.last_status_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class AdminJuheConfigBody(BaseModel):
    id: Optional[int] = None
    user_id: int
    label: str = ""
    guid: str
    app_key: str = ""
    app_secret: str = ""


@router.get("/admin/api/juhe-wechat/configs")
def admin_juhe_list_configs(
    user_id: Optional[int] = None,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    q = db.query(JuheWechatConfig).filter(JuheWechatConfig.status != "deleted")
    q = _juhe_owner_filter(q, ctx)
    if user_id:
        _juhe_can_bind_user(db, ctx, int(user_id))
        q = q.filter(JuheWechatConfig.user_id == int(user_id))
    rows = q.order_by(JuheWechatConfig.created_at.desc()).limit(300).all()
    return {"configs": [_juhe_config_payload(r, db) for r in rows]}


@router.post("/admin/api/juhe-wechat/configs")
def admin_juhe_save_config(
    body: AdminJuheConfigBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _juhe_can_bind_user(db, ctx, int(body.user_id))
    guid = (body.guid or "").strip()
    if not guid:
        raise HTTPException(status_code=400, detail="GUID不能为空")
    if len(guid) > 96:
        raise HTTPException(status_code=400, detail="GUID过长")
    label = (body.label or "").strip() or ("微信实例 " + guid[-6:])
    app_key = (body.app_key or "").strip()
    app_secret = (body.app_secret or "").strip()

    if body.id:
        row = db.query(JuheWechatConfig).filter(JuheWechatConfig.id == int(body.id), JuheWechatConfig.status != "deleted").first()
        if not row:
            raise HTTPException(status_code=404, detail="实例不存在")
        if ctx.role != "admin" and (getattr(row, "owner_role", None) != "agent" or getattr(row, "owner_user_id", None) != ctx.user_id):
            raise HTTPException(status_code=403, detail="无权修改该实例")
    else:
        row = (
            db.query(JuheWechatConfig)
            .filter(JuheWechatConfig.user_id == int(body.user_id), JuheWechatConfig.guid == guid)
            .first()
        )
        if row and ctx.role != "admin" and (getattr(row, "owner_role", None) != "agent" or getattr(row, "owner_user_id", None) != ctx.user_id):
            raise HTTPException(status_code=403, detail="该用户下已存在同 GUID 实例，代理无权接管")
        if row and row.status == "deleted":
            row.status = "active"
        if row is None:
            row = JuheWechatConfig(user_id=int(body.user_id), guid=guid, label=label)
            db.add(row)
        if not app_key or not app_secret:
            raise HTTPException(status_code=400, detail="新增实例必须填写 App Key 和 App Secret")

    row.user_id = int(body.user_id)
    row.label = label[:120]
    row.guid = guid
    if app_key:
        row.app_key = app_key
    if app_secret:
        row.app_secret = app_secret
    if not (row.app_key or "").strip() or not (row.app_secret or "").strip():
        raise HTTPException(status_code=400, detail="实例缺少 App Key 或 App Secret")
    row.status = "active"
    row.owner_role = ctx.role
    row.owner_user_id = ctx.user_id if ctx.role == "agent" else None
    db.commit()
    db.refresh(row)
    return {"ok": True, "config": _juhe_config_payload(row, db)}


@router.delete("/admin/api/juhe-wechat/configs/{config_id}")
def admin_juhe_delete_config(
    config_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(JuheWechatConfig).filter(JuheWechatConfig.id == config_id, JuheWechatConfig.status != "deleted").first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    if ctx.role != "admin" and (getattr(row, "owner_role", None) != "agent" or getattr(row, "owner_user_id", None) != ctx.user_id):
        raise HTTPException(status_code=403, detail="无权删除该实例")
    row.status = "deleted"
    db.commit()
    return {"ok": True}


@router.post("/admin/api/juhe-wechat/configs/{config_id}/status")
async def admin_juhe_check_status(
    config_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    row = db.query(JuheWechatConfig).filter(JuheWechatConfig.id == config_id, JuheWechatConfig.status != "deleted").first()
    if not row:
        raise HTTPException(status_code=404, detail="实例不存在")
    if ctx.role != "admin" and (getattr(row, "owner_role", None) != "agent" or getattr(row, "owner_user_id", None) != ctx.user_id):
        raise HTTPException(status_code=403, detail="无权检测该实例")
    payload = {"guid": row.guid}
    try:
        data, http_status, latency_ms = await guid_request(path="/client/get_client_status", data=payload, config=row)
        success = http_status == 200 and int(data.get("errcode") or 0) == 0
        status_value = None
        if success and isinstance(data.get("data"), dict):
            try:
                status_value = int(data["data"].get("status"))
            except Exception:
                status_value = None
        row.last_status = status_value
        row.last_status_at = datetime.utcnow()
        db.add(JuheWechatCallLog(
            user_id=row.user_id,
            config_id=row.id,
            action="admin_status",
            upstream_path="/client/get_client_status",
            success=success,
            http_status=http_status,
            latency_ms=latency_ms,
            request_payload=safe_request_snapshot(payload),
            response_payload=data,
            error_message="" if success else str(data)[:1000],
        ))
        db.commit()
        return {"ok": success, "status": status_value, "upstream": data, "latency_ms": latency_ms}
    except httpx.HTTPError as exc:
        db.add(JuheWechatCallLog(
            user_id=row.user_id,
            config_id=row.id,
            action="admin_status",
            upstream_path="/client/get_client_status",
            success=False,
            request_payload=safe_request_snapshot(payload),
            error_message=str(exc),
        ))
        db.commit()
        raise HTTPException(status_code=502, detail=f"检测失败: {exc}") from exc


class JuheFriendAddTarget(BaseModel):
    contact: str
    nickname: str = ""
    remark: str = ""


class JuheFriendAddBatchBody(BaseModel):
    config_id: int
    title: str = ""
    verify_content: str = ""
    interval_seconds: int = 30
    contacts: list[JuheFriendAddTarget]


def _juhe_batch_payload(row: JuheWechatFriendAddBatch) -> dict:
    return {
        "id": row.id,
        "owner_role": row.owner_role,
        "owner_user_id": row.owner_user_id,
        "target_user_id": row.target_user_id,
        "config_id": row.config_id,
        "title": row.title,
        "verify_content": row.verify_content,
        "interval_seconds": row.interval_seconds,
        "status": row.status,
        "total_count": row.total_count,
        "success_count": row.success_count,
        "failed_count": row.failed_count,
        "skipped_count": row.skipped_count,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _juhe_item_payload(row: JuheWechatFriendAddItem) -> dict:
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "raw_contact": row.raw_contact,
        "nickname": row.nickname,
        "remark": row.remark,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "resolved_username": row.resolved_username,
        "resolved_scene": row.resolved_scene,
        "error_message": row.error_message,
        "response_payload": row.response_payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
    }


def _juhe_recount_batch(db: Session, batch: JuheWechatFriendAddBatch) -> None:
    rows = db.query(JuheWechatFriendAddItem.status, func.count(JuheWechatFriendAddItem.id)).filter(
        JuheWechatFriendAddItem.batch_id == batch.id
    ).group_by(JuheWechatFriendAddItem.status).all()
    counts = {str(k): int(v) for k, v in rows}
    batch.total_count = sum(counts.values())
    batch.success_count = counts.get("success", 0)
    batch.failed_count = counts.get("failed", 0)
    batch.skipped_count = counts.get("skipped", 0)


async def _juhe_process_friend_batch_async(batch_id: int) -> None:
    from ..db import SessionLocal

    while True:
        db = SessionLocal()
        try:
            batch = db.query(JuheWechatFriendAddBatch).filter(JuheWechatFriendAddBatch.id == batch_id).first()
            if not batch or batch.status in {"finished", "deleted"}:
                return
            config = db.query(JuheWechatConfig).filter(
                JuheWechatConfig.id == batch.config_id,
                JuheWechatConfig.status != "deleted",
            ).first()
            if not config:
                batch.status = "failed"
                batch.error_message = "实例不存在或已删除"
                batch.finished_at = datetime.utcnow()
                db.commit()
                return
            item = (
                db.query(JuheWechatFriendAddItem)
                .filter(
                    JuheWechatFriendAddItem.batch_id == batch_id,
                    JuheWechatFriendAddItem.status.in_(["pending", "retry"]),
                )
                .order_by(JuheWechatFriendAddItem.id.asc())
                .first()
            )
            if not item:
                _juhe_recount_batch(db, batch)
                batch.status = "finished" if batch.failed_count == 0 else "finished_with_errors"
                batch.finished_at = datetime.utcnow()
                db.commit()
                return
            if not batch.started_at:
                batch.started_at = datetime.utcnow()
            batch.status = "running"
            item.status = "running"
            item.attempt_count = int(item.attempt_count or 0) + 1
            db.commit()

            raw_contact = (item.raw_contact or "").strip()
            search_payload = {"guid": config.guid, "username": raw_contact, "from_scene": 0, "search_scene": 1}
            add_payload = None
            response_payload = None
            error_message = ""
            success = False
            try:
                search_data, search_http, search_latency = await guid_request(
                    path="/contact/search_contact",
                    data=search_payload,
                    config=config,
                    timeout_seconds=45,
                )
                response_payload = {"search": search_data}
                db.add(JuheWechatCallLog(
                    user_id=batch.target_user_id,
                    config_id=config.id,
                    action="friend_search",
                    upstream_path="/contact/search_contact",
                    success=search_http == 200 and int(search_data.get("errcode") or 0) == 0,
                    http_status=search_http,
                    latency_ms=search_latency,
                    request_payload=safe_request_snapshot(search_payload),
                    response_payload=search_data,
                    error_message="" if search_http == 200 else str(search_data)[:1000],
                ))
                target = extract_friend_add_target(search_data)
                if not target.get("username"):
                    raise RuntimeError("未从搜索结果中解析到可添加的 username")
                verify_text = (item.remark or "").strip() or (batch.verify_content or "").strip()
                add_payload = {
                    "guid": config.guid,
                    "username": target["username"],
                    "verify_content": verify_text,
                    "scene": int(target.get("scene") or 3),
                    "ticket": target.get("ticket") or "",
                }
                add_data, add_http, add_latency = await guid_request(
                    path="/contact/add_friend",
                    data=add_payload,
                    config=config,
                    timeout_seconds=45,
                )
                response_payload["add"] = add_data
                success = add_http == 200 and int(add_data.get("errcode") or 0) == 0
                if not success:
                    error_message = str(add_data.get("errmsg") or add_data.get("message") or add_data)[:1000]
                db.add(JuheWechatCallLog(
                    user_id=batch.target_user_id,
                    config_id=config.id,
                    action="friend_add",
                    upstream_path="/contact/add_friend",
                    success=success,
                    http_status=add_http,
                    latency_ms=add_latency,
                    request_payload=safe_request_snapshot(add_payload),
                    response_payload=add_data,
                    error_message="" if success else error_message,
                ))
                item.resolved_username = target["username"]
                item.resolved_ticket = target.get("ticket") or ""
                item.resolved_scene = int(target.get("scene") or 3)
            except Exception as exc:
                error_message = str(exc)[:1000]

            item.search_payload = search_payload
            item.add_payload = add_payload
            item.response_payload = response_payload
            item.error_message = error_message or None
            item.status = "success" if success else "failed"
            item.processed_at = datetime.utcnow()
            _juhe_recount_batch(db, batch)
            remaining = db.query(func.count(JuheWechatFriendAddItem.id)).filter(
                JuheWechatFriendAddItem.batch_id == batch_id,
                JuheWechatFriendAddItem.status.in_(["pending", "retry"]),
            ).scalar() or 0
            if not remaining:
                batch.status = "finished" if batch.failed_count == 0 else "finished_with_errors"
                batch.finished_at = datetime.utcnow()
                db.commit()
                return
            db.commit()
            delay = max(5, min(3600, int(batch.interval_seconds or 30)))
        finally:
            db.close()
        await asyncio.sleep(delay)


def _juhe_process_friend_batch(batch_id: int) -> None:
    try:
        asyncio.run(_juhe_process_friend_batch_async(batch_id))
    except Exception:
        logger.exception("[juhe friend batch] background task crashed batch_id=%s", batch_id)


@router.post("/admin/api/juhe-wechat/friend-add/batches")
def admin_juhe_create_friend_batch(
    body: JuheFriendAddBatchBody,
    background_tasks: BackgroundTasks,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    config = db.query(JuheWechatConfig).filter(JuheWechatConfig.id == body.config_id, JuheWechatConfig.status != "deleted").first()
    if not config:
        raise HTTPException(status_code=404, detail="实例不存在")
    if ctx.role != "admin" and (getattr(config, "owner_role", None) != "agent" or getattr(config, "owner_user_id", None) != ctx.user_id):
        raise HTTPException(status_code=403, detail="无权使用该实例")
    _juhe_can_bind_user(db, ctx, config.user_id)
    contacts: list[JuheFriendAddTarget] = []
    seen: set[str] = set()
    for item in body.contacts or []:
        contact = (item.contact or "").strip()
        if not contact or contact in seen:
            continue
        seen.add(contact)
        contacts.append(item)
    if not contacts:
        raise HTTPException(status_code=400, detail="没有有效联系人")
    if len(contacts) > 1000:
        raise HTTPException(status_code=400, detail="单次最多导入 1000 条")
    interval = max(5, min(3600, int(body.interval_seconds or 30)))
    batch = JuheWechatFriendAddBatch(
        owner_role=ctx.role,
        owner_user_id=ctx.user_id,
        target_user_id=config.user_id,
        config_id=config.id,
        title=(body.title or "").strip()[:160] or ("批量加人 " + datetime.utcnow().strftime("%Y-%m-%d %H:%M")),
        verify_content=(body.verify_content or "").strip()[:200],
        interval_seconds=interval,
        status="pending",
        total_count=len(contacts),
        meta={"source": "admin_panel", "doc_rate_limit": "100 requests/minute/device; default interval is conservative"},
    )
    db.add(batch)
    db.flush()
    for item in contacts:
        db.add(JuheWechatFriendAddItem(
            batch_id=batch.id,
            raw_contact=(item.contact or "").strip()[:256],
            nickname=(item.nickname or "").strip()[:160] or None,
            remark=(item.remark or "").strip()[:200] or None,
            status="pending",
        ))
    db.commit()
    db.refresh(batch)
    background_tasks.add_task(_juhe_process_friend_batch, batch.id)
    return {"ok": True, "batch": _juhe_batch_payload(batch)}


@router.get("/admin/api/juhe-wechat/friend-add/batches")
def admin_juhe_list_friend_batches(
    config_id: Optional[int] = None,
    limit: int = 50,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    limit = max(1, min(100, int(limit or 50)))
    q = db.query(JuheWechatFriendAddBatch)
    if ctx.role != "admin":
        q = q.filter(JuheWechatFriendAddBatch.owner_role == "agent", JuheWechatFriendAddBatch.owner_user_id == ctx.user_id)
    if config_id:
        q = q.filter(JuheWechatFriendAddBatch.config_id == int(config_id))
    rows = q.order_by(JuheWechatFriendAddBatch.created_at.desc()).limit(limit).all()
    return {"items": [_juhe_batch_payload(r) for r in rows]}


@router.get("/admin/api/juhe-wechat/friend-add/batches/{batch_id}")
def admin_juhe_get_friend_batch(
    batch_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    batch = db.query(JuheWechatFriendAddBatch).filter(JuheWechatFriendAddBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if ctx.role != "admin" and (batch.owner_role != "agent" or batch.owner_user_id != ctx.user_id):
        raise HTTPException(status_code=403, detail="无权查看该批次")
    items = (
        db.query(JuheWechatFriendAddItem)
        .filter(JuheWechatFriendAddItem.batch_id == batch.id)
        .order_by(JuheWechatFriendAddItem.id.asc())
        .all()
    )
    return {"batch": _juhe_batch_payload(batch), "items": [_juhe_item_payload(i) for i in items]}


@router.post("/admin/api/juhe-wechat/friend-add/batches/{batch_id}/retry")
def admin_juhe_retry_friend_batch(
    batch_id: int,
    background_tasks: BackgroundTasks,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    batch = db.query(JuheWechatFriendAddBatch).filter(JuheWechatFriendAddBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if ctx.role != "admin" and (batch.owner_role != "agent" or batch.owner_user_id != ctx.user_id):
        raise HTTPException(status_code=403, detail="无权操作该批次")
    db.query(JuheWechatFriendAddItem).filter(
        JuheWechatFriendAddItem.batch_id == batch.id,
        JuheWechatFriendAddItem.status.in_(["failed", "pending", "retry", "running"]),
    ).update({"status": "retry", "error_message": None}, synchronize_session=False)
    batch.status = "pending"
    batch.error_message = None
    batch.finished_at = None
    _juhe_recount_batch(db, batch)
    db.commit()
    background_tasks.add_task(_juhe_process_friend_batch, batch.id)
    return {"ok": True, "batch": _juhe_batch_payload(batch)}
