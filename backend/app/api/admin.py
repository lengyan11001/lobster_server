"""管理后台：管理员/代理商登录、用户查询、积分充值、数据统计。

路由挂载在 /admin 前缀。支持两种角色：
- 管理员（admin）：通过 .env 配置的用户名密码登录，拥有全部权限
- 代理商（agent）：通过自己的账号密码登录，仅能查看和管理自己的下级用户
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, FileResponse
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import AgentCommissionLedger, CapabilityCallLog, CreditLedger, H5ChatDevicePresence, RechargeOrder, ScheduledTask, ScheduledTaskRun, SkillUnlock, User, UserSkillVisibility
from ..services.credit_ledger import append_credit_ledger
from ..services.credits_amount import quantize_credits

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
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

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
    description: str = "管理员手动加积分"


@router.post("/admin/api/add-credits")
def admin_add_credits(
    body: AddCreditsBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    if body.amount == 0:
        raise HTTPException(status_code=400, detail="积分数量不能为 0")
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        if body.user_id not in sub_ids:
            raise HTTPException(status_code=403, detail="无权操作此用户")
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    old_credits = quantize_credits(user.credits or 0)
    delta = quantize_credits(body.amount)
    new_credits = old_credits + delta
    if new_credits < 0:
        raise HTTPException(status_code=400, detail=f"积分不足，当前 {old_credits}，操作 {delta}")
    user.credits = new_credits

    append_credit_ledger(
        db,
        user.id,
        delta,
        "recharge",
        new_credits,
        description=body.description[:200],
        meta={"source": "admin_panel"},
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
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    base_q = db.query(User)
    if ctx.role == "agent":
        sub_ids = _agent_visible_user_ids(db, ctx.user_id)
        base_q = base_q.filter(User.id.in_(sub_ids)) if sub_ids else base_q.filter(False)
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
    from .skills import _user_visible_package_ids, _load_registry, _pkg_store_visibility, _skill_store_admin
    visible = _user_visible_package_ids(db, user_id)
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
    return {
        "user_id": user_id,
        "is_admin": _skill_store_admin(user),
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
    _ensure_user_visibility_seeded(db, user_id)
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
