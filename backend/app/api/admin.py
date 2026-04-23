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
from ..models import CapabilityCallLog, CreditLedger, RechargeOrder, User, UserSkillVisibility
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
    password: str


@router.post("/admin/api/login")
def admin_login(body: LoginBody, db: Session = Depends(get_db)):
    """管理后台登录。优先匹配管理员（.env），否则尝试代理商账号密码。"""
    username = body.username.strip()
    password = body.password.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入账号和密码")

    if _admin_enabled():
        admin_u = (settings.lobster_admin_username or "").strip()
        admin_p = (settings.lobster_admin_password or "").strip()
        if username == admin_u and password == admin_p:
            token = ADMIN_TOKEN_PREFIX + admin_p
            return {"ok": True, "token": token, "role": "admin", "display_name": "管理员"}

    from .auth import verify_password, _login_account_key
    account_key = _login_account_key(username)
    if account_key:
        user = db.query(User).filter(User.email == account_key).first()
        if user and user.is_agent and verify_password(password, user.hashed_password):
            agent_jwt = jwt.encode(
                {"sub": str(user.id), "scope": "agent_admin", "exp": datetime.utcnow() + timedelta(days=7)},
                settings.secret_key,
                algorithm=_JWT_ALGORITHM,
            )
            token = AGENT_TOKEN_PREFIX + agent_jwt
            display = user.email.replace("@sms.lobster.local", "")
            return {"ok": True, "token": token, "role": "agent", "user_id": user.id, "display_name": display}

    raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.get("/admin/api/search")
def admin_search_user(
    q: str = "",
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return {"users": []}
    query = db.query(User).filter(
        or_(
            User.email.ilike(f"%{q}%"),
            User.id == int(q) if q.isdigit() else False,
        )
    )
    if ctx.role == "agent":
        sub_ids = _agent_sub_user_ids(db, ctx.user_id)
        query = query.filter(User.id.in_(sub_ids)) if sub_ids else query.filter(False)
    query = query.order_by(User.id).limit(50)
    users = []
    for u in query.all():
        users.append({
            "id": u.id,
            "email": u.email,
            "credits": float(u.credits or 0),
            "role": u.role,
            "is_agent": u.is_agent,
            "parent_user_id": u.parent_user_id,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return {"users": users}


@router.get("/admin/api/user/{user_id}")
def admin_user_detail(
    user_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    if ctx.role == "agent":
        sub_ids = _agent_sub_user_ids(db, ctx.user_id)
        if user_id not in sub_ids:
            raise HTTPException(status_code=403, detail="无权查看此用户")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    ledger = (
        db.query(CreditLedger)
        .filter(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.created_at.desc())
        .limit(50)
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
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        })
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "credits": float(user.credits or 0),
            "role": user.role,
            "is_agent": user.is_agent,
            "parent_user_id": user.parent_user_id,
            "brand_mark": user.brand_mark,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "ledger": ledger_list,
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
        sub_ids = _agent_sub_user_ids(db, ctx.user_id)
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


@router.get("/admin/api/users")
def admin_list_users(
    page: int = 1,
    page_size: int = 20,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    base_q = db.query(User)
    if ctx.role == "agent":
        sub_ids = _agent_sub_user_ids(db, ctx.user_id)
        base_q = base_q.filter(User.id.in_(sub_ids)) if sub_ids else base_q.filter(False)
    total = base_q.with_entities(func.count(User.id)).scalar() or 0
    offset = (max(1, page) - 1) * page_size
    users = base_q.order_by(User.id.desc()).offset(offset).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "credits": float(u.credits or 0),
                "role": u.role,
                "is_agent": u.is_agent,
                "parent_user_id": u.parent_user_id,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }


# ── 技能可见性管理 ──


@router.get("/admin/api/user-skill-visibility/{user_id}")
def admin_get_user_skill_visibility(
    user_id: int,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from .skills import _user_visible_package_ids, _load_registry, _pkg_store_visibility, _skill_store_admin
    visible = _user_visible_package_ids(db, user_id)
    registry = _load_registry()
    packages = registry.get("packages", {})
    all_pkgs = [
        {"id": k, "name": v.get("name", k), "store_visibility": _pkg_store_visibility(v)}
        for k, v in packages.items()
    ]
    return {
        "user_id": user_id,
        "is_admin": _skill_store_admin(user),
        "visible_ids": sorted(visible),
        "all_packages": all_pkgs,
    }


class AdminSkillVisUpdate(BaseModel):
    add: list[str] = []
    remove: list[str] = []


@router.post("/admin/api/user-skill-visibility/{user_id}")
def admin_update_user_skill_visibility(
    user_id: int,
    body: AdminSkillVisUpdate,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from .skills import _ensure_user_visibility_seeded
    _ensure_user_visibility_seeded(db, user_id)
    added, removed = [], []
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
    db.commit()
    return {"ok": True, "added": added, "removed": removed}


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
        agent_sub_ids = _agent_sub_user_ids(db, ctx.user_id)

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
    db.add(user)
    db.commit()
    db.refresh(user)
    action = "设为代理商" if body.is_agent else "取消代理商"
    logger.info("[admin/set-agent] user_id=%s email=%s action=%s", user.id, user.email, action)
    return {"ok": True, "user_id": user.id, "email": user.email, "is_agent": user.is_agent}


# ── 代理商：认领/移除下级 ──


class ClaimSubBody(BaseModel):
    account: str


@router.post("/admin/api/agent/claim-sub")
def agent_claim_sub(
    body: ClaimSubBody,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    """代理商通过账号认领下级用户。仅当该用户未被其他代理商认领时可操作。管理员也可调用。"""
    if ctx.role == "agent":
        agent_user_id = ctx.user_id
    else:
        raise HTTPException(status_code=400, detail="此接口仅限代理商使用，管理员请使用 set-agent + assign-parent")

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
        if target_user.parent_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="该用户不是你的下级")

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
    sub_count = db.query(func.count(User.id)).filter(User.parent_user_id == ctx.user_id).scalar() or 0
    return {
        "role": "agent",
        "user_id": user.id,
        "email": user.email,
        "display_name": user.email.replace("@sms.lobster.local", ""),
        "sub_count": sub_count,
    }
