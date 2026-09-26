"""manage（项目管理与 AI 赋能）独立站的 API。

与主站共用 users 与同一套 JWT：登录直接复用 api/auth 的 router，
本文件只提供 manage 自己的业务接口（/api/manage/*）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..manage_models import (
    MAiEmployee,
    MDeliveryLog,
    MAuditLog,
    MCustomer,
    MCustomerLog,
    MDelivery,
    MDispatch,
    MPlanVersion,
    MWorkLog,
    MCheckup,
    MCompany,
    MCondition,
    MFinanceEntry,
    MInventoryItem,
    MMembership,
    MMembershipRole,
    MPlanNode,
    MProduct,
    MProject,
)
from ..services import device_labels, document_scan
from ..models import (
    H5ChatDevicePresence,
    PublishMetricEvent,
    PublishMetricSample,
    User,
    UserDeviceLabel,
    UserInstallation,
)
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/manage", tags=["manage"])

ROLE_LABEL = {
    "boss": "老板", "gm": "总经理", "bd": "商务", "sales": "业务", "delivery": "交付",
    "support": "客服", "finance": "财务", "operation": "运营", "content": "内容",
    "market": "市场", "hr": "人事", "supply": "采购",
}
FULL_ACCESS = {"boss", "gm"}
# 与 api/admin.py 保持一致：管理后台管理员令牌形如 lobster-admin-<password>
ADMIN_TOKEN_PREFIX = "lobster-admin-"


MANAGE_BRAND = "bihuo"


def _user_by_phone(db: Session, phone: str) -> Optional[User]:
    """按手机号找原系统用户（只取 brand=bihuo）。短信注册账号的邮箱形如 188xxxx@sms.lobster.local。"""
    import re as _re

    raw = str(phone or "").strip()
    digits = _re.sub(r"\D", "", raw)
    candidates = []
    if len(digits) == 11 and digits.startswith("1"):
        candidates.append(digits + "@sms.lobster.local")
    if raw:
        candidates.append(raw)
    for email in candidates:
        row = (db.query(User)
               .filter(User.email == email, User.brand_mark == MANAGE_BRAND).first())
        if row:
            return row
    return None


def _roles_for(db: Session, company_id: int, user_id: int) -> List[MMembershipRole]:
    return (
        db.query(MMembershipRole)
        .join(MMembership, MMembership.id == MMembershipRole.membership_id)
        .filter(MMembership.company_id == company_id, MMembership.user_id == user_id)
        .all()
    )


def _require_company(db: Session, company_id: int, user: User, *, write: bool = False) -> MCompany:
    company = db.query(MCompany).filter(MCompany.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="公司不存在")
    roles = {r.role_code for r in _roles_for(db, company_id, user.id)}
    is_owner = company.owner_user_id == user.id
    is_admin = str(user.role or "").lower() == "admin"
    if not roles and not is_owner and not is_admin:
        raise HTTPException(status_code=404, detail="公司不存在")
    return company


def _require_plan_admin(db: Session, company: MCompany, user: User) -> None:
    roles = {r.role_code for r in _roles_for(db, company.id, user.id)}
    if company.owner_user_id == user.id or roles & FULL_ACCESS or str(user.role or "").lower() == "admin":
        return
    raise HTTPException(status_code=403, detail="只有老板 / 总经理可以调整或确认工作安排")


def _audit(db: Session, company_id: Optional[int], user_id: Optional[int], action: str,
           target_type: str = "", target_id: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
    db.add(MAuditLog(company_id=company_id, actor_user_id=user_id, action=action,
                     target_type=target_type, target_id=str(target_id or ""), detail=detail or {}))


class CompanyIn(BaseModel):
    name: str
    short_name: str = ""
    industry: str = ""


class MemberIn(BaseModel):
    company_id: int
    display_name: str = ""
    phone: str = ""
    user_id: Optional[int] = None
    dept: str = ""
    remark: str = ""
    role_code: str = "sales"
    level: str = "p1"
    email: str = ""


class ProductIn(BaseModel):
    company_id: int
    name: str
    price: float = 0
    unit: str = ""
    cycle_days: int = 0
    deliverable: str = ""
    description: str = ""


class ProjectIn(BaseModel):
    company_id: int
    name: str
    goal: str = ""
    success_criteria: str = ""
    start_at: str = ""
    end_at: str = ""
    product_ids: List[int] = Field(default_factory=list)
    membership_ids: List[int] = Field(default_factory=list)


class PlanIn(BaseModel):
    mode: str = "ai"
    extra_requirements: str = ""


class NodeIn(BaseModel):
    owner_name: Optional[str] = None
    owner_membership_id: Optional[int] = None
    requirement: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None


class DraftIn(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


class FinanceIn(BaseModel):
    company_id: int
    entry_type: str = "income"
    category: str = ""
    amount: float = 0
    happened_on: str = ""
    summary: str = ""


class CheckupRunIn(BaseModel):
    company_id: int
    slot: str = "0900"
    use_ai: bool = True


class AcceptIn(BaseModel):
    action_index: int = 0


class BossIn(BaseModel):
    user_id: Optional[int] = None
    phone: str = ""
    company_name: str = ""


class AdminActor:
    """平台管理员（来自原管理后台的 lobster-admin- 令牌），没有 users 行。"""

    id = 0
    email = "platform-admin@local"
    brand_mark = "bihuo"

    def __init__(self, name: str = "admin") -> None:
        self.role = "admin"
        self.display_name = name or "admin"


def _bearer_token(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "")
    return auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""


async def current_actor(request: Request, db: Session = Depends(get_db)):
    """manage 的主体：普通用户（JWT）或平台管理员（管理后台令牌）。

    管理后台的管理员账号不在 users 表里，登录后拿到的是 lobster-admin-<password>，
    这里同时接受它，避免「管理员登不进来」。
    """
    token = _bearer_token(request)
    if token.startswith(ADMIN_TOKEN_PREFIX):
        expected = (settings.lobster_admin_password or "").strip()
        if not expected or token != ADMIN_TOKEN_PREFIX + expected:
            raise HTTPException(status_code=401, detail="管理员令牌无效")
        return AdminActor((getattr(settings, "lobster_admin_username", "") or "admin").strip())
    from .auth import get_current_user

    return await get_current_user(request=request, token=token, db=db)


def _is_admin_actor(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").lower() == "admin" and getattr(user, "id", 0) == 0


def _member_json(db: Session, m: MMembership) -> Dict[str, Any]:
    roles = db.query(MMembershipRole).filter(MMembershipRole.membership_id == m.id).all()
    return {
        "id": m.id, "user_id": m.user_id, "name": m.display_name, "dept": m.dept,
        "email": m.email, "load": m.load_pct, "remark": m.remark, "status": m.status,
        "roles": [{"code": r.role_code, "label": ROLE_LABEL.get(r.role_code, r.role_code),
                   "level": r.level} for r in roles],
    }


def _node_json(n: MPlanNode) -> Dict[str, Any]:
    return {
        "id": n.id, "parent_id": n.parent_id, "node_type": n.node_type, "title": n.title,
        "detail": n.detail, "owner_kind": n.owner_kind, "owner_name": n.owner_name,
        "owner_membership_id": n.owner_membership_id, "requirement": n.requirement,
        "start_at": n.start_at, "end_at": n.end_at, "kpi": n.kpi, "deliverable": n.deliverable,
        "status": n.status, "progress": n.progress, "weight": n.weight, "order_index": n.order_index,
        "complete": bool(n.requirement and n.start_at and n.end_at
                         and (n.owner_name or n.node_type == "phase")),
    }


def _project_json(p: MProject) -> Dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "goal": p.goal, "success_criteria": p.success_criteria,
        "start_at": p.start_at, "end_at": p.end_at, "status": p.status,
        "arrangement_status": p.arrangement_status, "progress": p.progress,
        "plan_source": p.plan_source, "products": p.products or [], "members": p.members or [],
        "owner_membership_id": p.owner_membership_id,
    }


class AdminLoginIn(BaseModel):
    username: str
    password: str


@router.post("/admin/login")
def manage_admin_login(body: AdminLoginIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """用原管理后台的管理员账号登录 manage（同一套 .env 凭据）。"""
    username = body.username.strip()
    password = body.password.strip()
    admin_u = (getattr(settings, "lobster_admin_username", "") or "").strip()
    admin_p = (getattr(settings, "lobster_admin_password", "") or "").strip()
    if not admin_u or not admin_p:
        raise HTTPException(status_code=503, detail="服务器未配置平台管理员账号")
    if username != admin_u or password != admin_p:
        raise HTTPException(status_code=400, detail="管理员账号或密码错误")
    return {"ok": True, "access_token": ADMIN_TOKEN_PREFIX + admin_p, "role": "admin",
            "display_name": "管理员"}


class ManageLoginIn(BaseModel):
    account: str
    password: str


@router.post("/login", summary="\u7edf\u4e00\u767b\u5f55\uff1a\u8d26\u53f7\u81ea\u5df1\u51b3\u5b9a\u8eab\u4efd\uff08\u4e0d\u5206 tab\uff09")
def manage_login(body: ManageLoginIn, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """\u540c\u4e00\u4e2a\u8f93\u5165\u6846\uff1a

    · \u8d26\u53f7\u7b49\u4e8e\u5e73\u53f0\u7ba1\u7406\u5458\u7528\u6237\u540d\uff08.env \u91cc\u7684 lobster_admin_username\uff09\uff1a\u8d70\u7ba1\u7406\u5458\u4ee4\u724c
    · \u5176\u4ed6\u4e00\u5f8b\u5f53\u666e\u901a\u8d26\u53f7\uff1a\u590d\u7528\u4e3b\u7ad9\u624b\u673a\u53f7/\u90ae\u7bb1 + \u5bc6\u7801\u767b\u5f55\uff08\u540c\u4e00\u5957 users \u8868\u4e0e JWT\uff09
    """
    account = (body.account or "").strip()
    password = body.password or ""
    if not account or not password:
        raise HTTPException(status_code=400, detail="\u8bf7\u8f93\u5165\u8d26\u53f7\u4e0e\u5bc6\u7801")

    admin_u = (getattr(settings, "lobster_admin_username", "") or "").strip()
    admin_p = (getattr(settings, "lobster_admin_password", "") or "").strip()
    if admin_u and account.lower() == admin_u.lower():
        if not admin_p:
            raise HTTPException(status_code=503, detail="\u670d\u52a1\u5668\u672a\u914d\u7f6e\u5e73\u53f0\u7ba1\u7406\u5458\u5bc6\u7801")
        if password != admin_p:
            raise HTTPException(status_code=400, detail="\u7ba1\u7406\u5458\u5bc6\u7801\u9519\u8bef")
        return {"ok": True, "kind": "admin", "role": "admin", "display_name": "\u7ba1\u7406\u5458",
                "access_token": ADMIN_TOKEN_PREFIX + admin_p}

    from .auth import PhonePasswordLoginBody, login_phone_password

    token = login_phone_password(PhonePasswordLoginBody(account=account, password=password), request, db)
    access = getattr(token, "access_token", None)
    if not access and isinstance(token, dict):
        access = token.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="\u8d26\u53f7\u6216\u5bc6\u7801\u9519\u8bef")
    return {"ok": True, "kind": "user", "role": "user", "access_token": access}


@router.get("/bootstrap")
def bootstrap(user: Any = Depends(current_actor), db: Session = Depends(get_db)) -> Dict[str, Any]:
    if _is_admin_actor(user):
        companies = [{"id": c.id, "name": c.name, "owner": False, "roles": []}
                     for c in db.query(MCompany).order_by(MCompany.id).all()]
        return {"user": {"id": 0, "email": user.email, "role": "admin", "brand_mark": user.brand_mark},
                "is_platform_admin": True, "companies": companies, "roles": ROLE_LABEL}
    rows = (
        db.query(MMembership, MCompany)
        .join(MCompany, MCompany.id == MMembership.company_id)
        .filter(MMembership.user_id == user.id, MMembership.status == "active")
        .all()
    )
    companies: List[Dict[str, Any]] = []
    for membership, company in rows:
        companies.append({
            "id": company.id, "name": company.name, "owner": company.owner_user_id == user.id,
            "roles": [{"code": r.role_code, "label": ROLE_LABEL.get(r.role_code, r.role_code), "level": r.level}
                      for r in _roles_for(db, company.id, user.id)],
        })
    seen = {c["id"] for c in companies}
    for company in db.query(MCompany).filter(MCompany.owner_user_id == user.id).all():
        if company.id not in seen:
            companies.append({"id": company.id, "name": company.name, "owner": True, "roles": []})
    return {
        "user": {"id": user.id, "email": user.email, "role": user.role, "brand_mark": user.brand_mark},
        "is_platform_admin": str(user.role or "").lower() == "admin",
        "companies": companies,
        "roles": ROLE_LABEL,
    }


@router.get("/directory/lookup")
def directory_lookup(q: str = Query("", max_length=120), user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """按手机号（或邮箱）找原系统用户；只认 brand=bihuo。"""
    key = (q or "").strip()
    if not key:
        return {"found": False}
    row = _user_by_phone(db, key)
    if not row:
        row = (db.query(User)
               .filter(User.email == key, User.brand_mark == MANAGE_BRAND).first())
    if not row:
        return {"found": False}
    return {"found": True,
            "user": {"id": row.id, "email": row.email, "brand_mark": row.brand_mark,
                     "phone": (row.email.split("@")[0] if "@sms.lobster.local" in (row.email or "") else "")}}


@router.post("/companies")
def create_company(body: CompanyIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """新建公司：创建者成为该公司的老板。

    平台管理员（令牌不是用户 JWT，id=0）不能凭空建公司——公司必须有归属账号，
    这类情况走「标记老板」把公司建在具体账号名下。
    """
    if getattr(user, "id", 0) <= 0:
        raise HTTPException(status_code=400, detail="平台管理员请用「标记老板」把公司建在具体账号名下")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="请填公司名称")
    company = MCompany(name=body.name.strip(), short_name=body.short_name.strip(),
                       industry=body.industry.strip(), owner_user_id=user.id)
    db.add(company)
    db.flush()
    membership = MMembership(company_id=company.id, user_id=getattr(user, "id", None),
                             display_name=user.email.split("@")[0], email=user.email,
                             dept="经营管理", remark="创建者")
    db.add(membership)
    db.flush()
    db.add(MMembershipRole(company_id=company.id, membership_id=membership.id, role_code="boss", level="p4"))
    _audit(db, company.id, user.id, "company.create", "company", company.id, {"name": company.name})
    db.commit()
    return {"ok": True, "company_id": company.id, "name": company.name}


def _purge_company(db: Session, company_id: int) -> Dict[str, int]:
    """删除一家公司及其全部业务数据（成员/项目/客户/财务/日检/条件/槽位/审计…）。"""
    removed: Dict[str, int] = {}
    mids = [m.id for m in db.query(MMembership).filter(MMembership.company_id == company_id).all()]
    if mids:
        removed["membership_role"] = (db.query(MMembershipRole)
                                      .filter(MMembershipRole.membership_id.in_(mids))
                                      .delete(synchronize_session=False))
    for name, model in (("plan_node", MPlanNode), ("plan_version", MPlanVersion),
                        ("dispatch", MDispatch), ("customer_log", MCustomerLog),
                        ("customer", MCustomer), ("delivery", MDelivery),
                        ("work_log", MWorkLog), ("checkup", MCheckup),
                        ("condition", MCondition), ("finance_entry", MFinanceEntry),
                        ("product", MProduct), ("project", MProject),
                        ("ai_employee", MAiEmployee), ("audit_log", MAuditLog)):
        removed[name] = (db.query(model).filter(model.company_id == company_id)
                         .delete(synchronize_session=False))
    removed["membership"] = (db.query(MMembership).filter(MMembership.company_id == company_id)
                             .delete(synchronize_session=False))
    removed["company"] = (db.query(MCompany).filter(MCompany.id == company_id)
                          .delete(synchronize_session=False))
    return {k: int(v or 0) for k, v in removed.items()}


@router.delete("/companies/{company_id}")
def delete_company(company_id: int, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = db.query(MCompany).filter(MCompany.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="公司不存在")
    actor_id = getattr(user, "id", 0) or 0
    is_admin = str(getattr(user, "role", "") or "").lower() == "admin" and actor_id == 0
    roles = {r.role_code for r in _roles_for(db, company_id, actor_id)} if actor_id else set()
    if not is_admin and company.owner_user_id != actor_id and not (roles & FULL_ACCESS):
        raise HTTPException(status_code=403, detail="只有该公司老板或平台管理员可以删除公司")
    name = company.name
    detail = _purge_company(db, company_id)
    _audit(db, None, actor_id, "company.delete", "company", company_id, {"name": name, **detail})
    db.commit()
    return {"ok": True, "deleted": company_id, "name": name, "detail": detail}


@router.get("/members")
def list_members(company_id: int, q: str = Query("", max_length=80),
                 include_disabled: bool = Query(False),
                 user: Any = Depends(current_actor),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    query = db.query(MMembership).filter(MMembership.company_id == company_id)
    if not include_disabled:
        query = query.filter(MMembership.status == "active")
    rows = query.order_by(MMembership.id).all()
    key = (q or "").strip().lower()
    if key:
        rows = [m for m in rows
                if key in (m.display_name or "").lower()
                or key in (m.dept or "").lower()
                or key in (m.email or "").lower()
                or key in (m.remark or "").lower()]
    return {"members": [_member_json(db, m) for m in rows]}


@router.post("/members")
def add_member(body: MemberIn, user: Any = Depends(current_actor),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    if not body.user_id and body.phone.strip():
        matched = _user_by_phone(db, body.phone)
        if matched:
            body.user_id = matched.id
    if body.user_id:
        dup = (db.query(MMembership)
               .filter(MMembership.company_id == company.id, MMembership.user_id == body.user_id).first())
        if dup:
            raise HTTPException(status_code=409, detail="该账号已在公司里")
    linked = db.query(User).filter(User.id == body.user_id).first() if body.user_id else None
    name = (body.display_name or (linked.email.split("@")[0] if linked else "")).strip() or "未命名"
    membership = MMembership(company_id=company.id, user_id=body.user_id, display_name=name,
                             dept=body.dept, email=(linked.email if linked else body.email),
                             remark=body.remark)
    db.add(membership)
    db.flush()
    db.add(MMembershipRole(company_id=company.id, membership_id=membership.id,
                           role_code=body.role_code, level=body.level))
    _audit(db, company.id, user.id, "member.add", "membership", membership.id,
           {"role": body.role_code, "level": body.level, "linked_user": body.user_id})
    db.commit()
    return {"ok": True, "member": _member_json(db, membership)}




class MemberPatchIn(BaseModel):
    display_name: Optional[str] = None
    dept: Optional[str] = None
    remark: Optional[str] = None
    load_pct: Optional[int] = None
    status: Optional[str] = None
    roles: Optional[List[Dict[str, Any]]] = None


@router.patch("/members/{membership_id}")
def update_member(membership_id: int, body: MemberPatchIn,
                  user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    membership = db.query(MMembership).filter(MMembership.id == membership_id).first()
    if not membership:
        raise HTTPException(status_code=404, detail="成员不存在")
    company = _require_company(db, membership.company_id, user)
    _require_plan_admin(db, company, user)
    for field in ("display_name", "dept", "remark", "load_pct", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(membership, field, value)
    if body.roles is not None:
        db.query(MMembershipRole).filter(MMembershipRole.membership_id == membership.id).delete()
        seen = set()
        for item in body.roles:
            code = str((item or {}).get("code") or "").strip()
            if not code or code in seen or code not in ROLE_LABEL:
                continue
            seen.add(code)
            db.add(MMembershipRole(company_id=membership.company_id, membership_id=membership.id,
                                   role_code=code, level=str((item or {}).get("level") or "p1")))
    _audit(db, membership.company_id, getattr(user, "id", 0), "member.update", "membership",
           membership.id, {"fields": list(body.dict(exclude_unset=True).keys())})
    db.commit()
    return {"ok": True, "member": _member_json(db, membership)}


@router.delete("/members/{membership_id}")
def delete_member(membership_id: int, user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    membership = db.query(MMembership).filter(MMembership.id == membership_id).first()
    if not membership:
        raise HTTPException(status_code=404, detail="成员不存在")
    company = _require_company(db, membership.company_id, user)
    _require_plan_admin(db, company, user)
    if membership.user_id and company.owner_user_id == membership.user_id:
        raise HTTPException(status_code=400, detail="公司创建者不能被移除")
    db.query(MMembershipRole).filter(MMembershipRole.membership_id == membership.id).delete()
    db.delete(membership)
    _audit(db, company.id, getattr(user, "id", 0), "member.delete", "membership", membership_id,
           {"name": membership.display_name})
    db.commit()
    return {"ok": True, "deleted": membership_id}


@router.get("/products")
def list_products(company_id: int, user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = db.query(MProduct).filter(MProduct.company_id == company_id).order_by(MProduct.id).all()
    return {"products": [{"id": p.id, "name": p.name, "price": float(p.price or 0), "unit": p.unit,
                          "cycle_days": p.cycle_days, "deliverable": p.deliverable,
                          "description": p.description, "status": p.status} for p in rows]}


@router.post("/products")
def create_product(body: ProductIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    row = MProduct(company_id=company.id, name=body.name.strip(), price=Decimal(str(body.price or 0)),
                   unit=body.unit, cycle_days=body.cycle_days, deliverable=body.deliverable,
                   description=body.description)
    db.add(row)
    _audit(db, company.id, user.id, "product.create", "product", "", {"name": row.name})
    db.commit()
    return {"ok": True, "product_id": row.id}




class ProductPatchIn(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    unit: Optional[str] = None
    cycle_days: Optional[int] = None
    deliverable: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


@router.patch("/products/{product_id}")
def update_product(product_id: int, body: ProductPatchIn,
                   user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MProduct).filter(MProduct.id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    for field in ("name", "unit", "cycle_days", "deliverable", "description", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.price is not None:
        row.price = Decimal(str(body.price))
    _audit(db, company.id, getattr(user, "id", 0), "product.update", "product", row.id)
    db.commit()
    return {"ok": True, "product": {"id": row.id, "name": row.name, "price": float(row.price or 0),
                                    "unit": row.unit, "cycle_days": row.cycle_days,
                                    "deliverable": row.deliverable, "description": row.description,
                                    "status": row.status}}


@router.delete("/products/{product_id}")
def delete_product(product_id: int, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MProduct).filter(MProduct.id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    db.delete(row)
    _audit(db, company.id, getattr(user, "id", 0), "product.delete", "product", product_id,
           {"name": row.name})
    db.commit()
    return {"ok": True, "deleted": product_id}


@router.get("/projects")
def list_projects(company_id: int, user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = db.query(MProject).filter(MProject.company_id == company_id).order_by(MProject.id.desc()).all()
    return {"projects": [_project_json(p) for p in rows]}


@router.post("/projects")
def create_project(body: ProjectIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    products = (db.query(MProduct).filter(MProduct.id.in_(body.product_ids)).all()
                if body.product_ids else [])
    members = (db.query(MMembership).filter(MMembership.id.in_(body.membership_ids)).all()
               if body.membership_ids else [])
    project = MProject(
        company_id=company.id, name=body.name.strip(), goal=body.goal,
        success_criteria=body.success_criteria, start_at=body.start_at, end_at=body.end_at,
        created_by=user.id, owner_membership_id=members[0].id if members else None,
        products=[{"id": p.id, "name": p.name, "price": float(p.price or 0), "deliverable": p.deliverable}
                  for p in products],
        members=[{"id": m.id, "name": m.display_name} for m in members],
    )
    db.add(project)
    db.flush()
    _audit(db, company.id, user.id, "project.create", "project", project.id, {"name": project.name})
    db.commit()
    return {"ok": True, "project": _project_json(project)}




class ProjectPatchIn(BaseModel):
    product_ids: Optional[List[int]] = None
    membership_ids: Optional[List[int]] = None
    name: Optional[str] = None
    goal: Optional[str] = None
    success_criteria: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    status: Optional[str] = None


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectPatchIn,
                   user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)
    for field in ("name", "goal", "success_criteria", "start_at", "end_at", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(project, field, value)
    if body.product_ids is not None:
        rows = (db.query(MProduct).filter(MProduct.id.in_(body.product_ids)).all()
                if body.product_ids else [])
        project.products = [{"id": r.id, "name": r.name, "price": float(r.price or 0),
                             "deliverable": r.deliverable} for r in rows]
    if body.membership_ids is not None:
        rows = (db.query(MMembership).filter(MMembership.id.in_(body.membership_ids)).all()
                if body.membership_ids else [])
        project.members = [{"id": r.id, "name": r.display_name} for r in rows]
    _audit(db, company.id, getattr(user, "id", 0), "project.update", "project", project.id,
           {"fields": list(body.dict(exclude_unset=True).keys())})
    db.commit()
    return {"ok": True, "project": _project_json(project)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)
    name = project.name
    db.query(MPlanNode).filter(MPlanNode.project_id == project_id).delete()
    db.query(MCheckup).filter(MCheckup.project_id == project_id).delete()
    db.query(MCondition).filter(MCondition.project_id == project_id).delete()
    db.delete(project)
    _audit(db, company.id, getattr(user, "id", 0), "project.delete", "project", project_id, {"name": name})
    db.commit()
    return {"ok": True, "deleted": project_id}


@router.get("/projects/{project_id}")
def project_detail(project_id: int, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    _require_company(db, project.company_id, user)
    nodes = (db.query(MPlanNode).filter(MPlanNode.project_id == project_id)
             .order_by(MPlanNode.order_index, MPlanNode.id).all())
    return {"project": _project_json(project), "nodes": [_node_json(n) for n in nodes],
            "draft": project.arrangement_draft or {}}


DEFAULT_PLAN_DAYS = 90   # 项目周期空时的兜底长度（天）
PLAN_MIN_GAP_SECONDS = 6   # 同一个项目两次生成的最短间隔，防连点重复扣费

PLAN_SYSTEM = """你是「AI 项目总监」。你的唯一目标：让这个项目真的做成，而不是把任务排得好看。
只输出一个 JSON 对象，不要解释、不要 markdown 代码块。

要求：
1. 先假设目标必须达成，反推执行路径：3-5 个阶段，时间连续不重叠且完整覆盖项目周期。
2. 每阶段 2-5 个任务；任务可交付、可验收，禁止"推进一下"这类虚动词。
3. 每个任务必须同时给：title、owner_name、requirement、start_at、end_at、kpi、deliverable。
   - owner_name 只能从给定候选人里选，禁止编造。
   - requirement 必须写清验收标准（数量/频率、质量线、交付物）。
4. 时间用 YYYY-MM-DD，落在项目周期内；同一人并行任务不超过 3 个。
5. 如果判断现有资源不足以达成目标，必须在 resource_gap 里说清楚缺什么、建议怎么补。
6. customers 是公司现有客户线索/商机（含阶段、金额、下一步、以及是否已指定负责人）：
   - owner_name 非空 = **已经人工指派**：与这位客户相关的任务（跟进、报价、签约、交付衔接）
     必须由这位负责人执行，owner_name 必须照抄此人，不得改派给别人。
   - owner_name 为空 = 未指派：把该客户的跟进安排写进 tasks（owner_name 从 candidates 里挑最合适的），
     并且在 assignments_suggestions 里**至少给出建议**：建议谁跟、为什么、优先级。
   - 不要编造 customers 里没有的客户；也不要在建议里改动客户数据。
7. 输出 schema:
{"summary":"","phases":[{"title":"","start_at":"","end_at":"","goal":"","tasks":[
 {"title":"","owner_name":"","requirement":"","start_at":"","end_at":"","kpi":"","deliverable":""}]}],
 "milestones":[{"title":"","at":""}],
 "assignments_suggestions":[{"customer":"","suggested_owner":"","priority":"high|medium|low","reason":""}],
 "resource_gap":[{"category":"people|money|device|material|channel|compliance|time",
   "title":"","need":"","now":"","gap":"","impact":"","severity":"high|medium|low",
   "actions":[{"type":"hire|slot|budget|outsource|scope","what":"","when":""}]}]}"""


def _assigned_customer_in_text(task: Dict[str, Any], customers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """任务文字里是否点了某个「已人工指派」的客户；命中则返回该客户与其指派负责人。"""
    if not customers:
        return None
    text = " ".join(
        str(task.get(key) or "")
        for key in ("title", "detail", "requirement", "kpi", "deliverable")
    )
    if not text.strip():
        return None
    for c in customers:
        if not c.get("assigned") or not c.get("owner_name"):
            continue
        for token in filter(None, (str(c.get("name") or "").strip(),
                                  str(c.get("company_name") or "").strip())):
            if len(token) >= 2 and token in text:
                return c
    return None


def _template_plan(ctx: Dict[str, Any], members: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = [m["name"] for m in members] or ["待指派"]
    start = str(ctx.get("start_at") or date.today().isoformat())
    end = str(ctx.get("end_at") or "")
    try:
        begin = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        begin = date.today()
    try:
        finish = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        finish = begin + timedelta(days=90)
    if finish <= begin:
        finish = begin + timedelta(days=90)
    span = max(1, (finish - begin).days)
    seg = max(1, span // 4)
    titles = ["基建与准备", "放量获取", "转化成交", "交付与结算"]
    goals = ["把基础打牢，具备可持续产出能力", "跑通获客闭环，稳定拿到线索",
             "把线索转成合同", "交付验收并沉淀复购"]
    phases = []
    for i, t in enumerate(titles):
        s = begin + timedelta(days=seg * i)
        e = finish if i == 3 else begin + timedelta(days=min(span, seg * (i + 1)) - 1)
        tasks = []
        for j in range(2):
            tasks.append({
                "title": t + " · 关键动作 " + str(j + 1),
                "owner_name": names[(i + j) % len(names)],
                "requirement": "写明数量/频率与验收物（例如：每周交付 X 份，含统计报表）",
                "start_at": s.isoformat(), "end_at": e.isoformat(),
                "kpi": "可量化指标", "deliverable": "可验收的产出物",
            })
        phases.append({"title": "阶段" + "一二三四"[i] + " · " + t, "start_at": s.isoformat(),
                       "end_at": e.isoformat(), "goal": goals[i],
                       "owner_name": names[i % len(names)], "tasks": tasks})
    return {
        "summary": str(ctx.get("name") or "项目") + "：按 " + str(span) + " 天周期拆成 4 个阶段，先跑通再放量。",
        "phases": phases,
        "milestones": [{"title": "首个小目标达成", "at": (begin + timedelta(days=seg)).isoformat()}],
        "resource_gap": [{
            "category": "people", "title": "执行人力",
            "need": "每个阶段至少 1 名负责人", "now": "当前参与人 " + str(len(names)) + " 位",
            "gap": "如无专职负责人，阶段会串行延期",
            "impact": "整体周期可能延长 20%", "severity": "medium",
            "actions": [
                {"type": "hire", "what": "补充 1 名执行负责人",
                 "when": (begin + timedelta(days=7)).isoformat()},
                {"type": "slot", "what": "或增配 1 个虚拟员工槽位承接重复动作",
                 "when": begin.isoformat()},
            ],
        }],
    }


def _llm_token_for_company(db: Session, company: MCompany) -> str:
    """给 AI 通道用的服务端令牌。

    不能直接转发调用方的令牌：平台管理员登录拿到的是 lobster-admin-<pwd>，
    不是 JWT，主站 /api/sutui-chat/completions 会判为无权限。
    这里用公司老板（或任一已绑定账号的 boss/gm 成员）签发一个短期令牌，计费也落在公司账上。
    """
    from .auth import access_token_claims, create_access_token

    owner = db.query(User).filter(User.id == company.owner_user_id).first()
    if owner:
        return create_access_token(data=access_token_claims(owner))
    rows = (db.query(MMembership, User)
            .join(User, User.id == MMembership.user_id)
            .join(MMembershipRole, MMembershipRole.membership_id == MMembership.id)
            .filter(MMembership.company_id == company.id,
                    MMembership.status == "active",
                    MMembershipRole.role_code.in_(("boss", "gm")))
            .all())
    for _membership, member_user in rows:
        return create_access_token(data=access_token_claims(member_user))
    raise HTTPException(status_code=503, detail="该公司没有可用于调用 AI 的账号，请先绑定老板账号")


async def _llm_json(token: str, system: str, payload: Dict[str, Any], *, timeout: float = 150.0) -> Dict[str, Any]:
    body = {
        "model": "",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "stream": False,
        "temperature": 0.2,
    }
    # 主站建任务要求带当前设备槽位（派给谁就带谁的 installation_id）
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post("http://127.0.0.1:8000/api/sutui-chat/completions", json=body, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="AI 通道返回 " + str(resp.status_code) + ": " + (resp.text or "")[:300])
    data = resp.json()
    try:
        text = str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise HTTPException(status_code=502, detail="AI 返回结构异常")
    if text.startswith("`"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise HTTPException(status_code=502, detail="AI 未返回 JSON，请重试")
        try:
            parsed = json.loads(text[a:b + 1])
        except Exception:
            raise HTTPException(status_code=502, detail="AI 返回的 JSON 无法解析，请重试")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="AI 返回的不是对象")
    return parsed


@router.post("/projects/{project_id}/plan")
async def generate_plan(project_id: int, body: PlanIn, request: Request,
                        user: Any = Depends(current_actor),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """生成规划。

    关键：调 AI 可能耗时数十秒，PostgreSQL 的 idle_in_transaction_session_timeout
    （本机 1min）会在等待期间掐掉处于事务中的连接。因此这里严格分三段：
    短会话读取上下文 -> 无事务等待 AI -> 新会话写库。
    """
    db.close()  # 立刻归还请求级连接，后面不再用它

    ctx: Dict[str, Any] = {}
    period_defaulted = False
    member_payload: List[Dict[str, Any]] = []
    customer_payload: List[Dict[str, Any]] = []
    company_id = 0
    read_db = SessionLocal()
    try:
        project = read_db.query(MProject).filter(MProject.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        company = _require_company(read_db, project.company_id, user)
        _require_plan_admin(read_db, company, user)
        company_id = company.id
        llm_token = _llm_token_for_company(read_db, company)
        ctx = {"name": project.name, "goal": project.goal,
               "success_criteria": project.success_criteria,
               "start_at": project.start_at, "end_at": project.end_at,
               "products": project.products or []}
        # 必要条件先齐：目标/周期缺一个都不生成。
        # （以前是静默兜底，结果模型拿不到周期就自己编日期，编出过 2025 年的排期。）
        _need = _project_gaps(project)
        if _need:
            raise HTTPException(status_code=400, detail={
                "message": "先补齐必要条件再生成规划："
                           + "、".join(g["label"] for g in _need),
                "needs": [g["key"] for g in _need],
                "missing": [g["label"] for g in _need],
                "hint": "周期不知道填多久？可以直接用「今天起 90 天」。"})
        last_ver = (read_db.query(MPlanVersion)
                    .filter(MPlanVersion.project_id == project_id)
                    .order_by(MPlanVersion.id.desc()).first())
        if last_ver is not None and last_ver.created_at is not None:
            _gap = (datetime.utcnow() - last_ver.created_at).total_seconds()
            if _gap < PLAN_MIN_GAP_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail="刚刚已经生成过一次（%.0f 秒前），为避免重复扣费，请等几秒再点" % _gap)
        members = (read_db.query(MMembership)
                   .filter(MMembership.company_id == company_id, MMembership.status == "active").all())
        for m in members:
            roles = read_db.query(MMembershipRole).filter(MMembershipRole.membership_id == m.id).all()
            member_payload.append({"membership_id": m.id, "name": m.display_name,
                                   "dept": m.dept or "",
                                   "roles": [{"code": r.role_code, "level": r.level} for r in roles]})
        # 客户（商机）也带给 AI：已人工指派的必须沿用该负责人；未指派的让 AI 至少给指派建议
        customer_rows = (read_db.query(MCustomer)
                         .filter(MCustomer.company_id == company_id)
                         .order_by(MCustomer.id.desc()).limit(80).all())
        name_by_mid = {m.id: m.display_name for m in members}
        for c in customer_rows:
            owner_name = name_by_mid.get(c.owner_membership_id, "") if c.owner_membership_id else ""
            if c.owner_membership_id and not owner_name:
                owner_name = _member_name(read_db, c.owner_membership_id)
            customer_payload.append({
                "name": c.name,
                "company_name": c.company_name or "",
                "stage": STAGE_LABEL.get(c.stage, c.stage),
                "amount": float(c.amount or 0),
                "next_action": (c.next_action or "")[:200],
                "next_follow_at": c.next_follow_at or "",
                "last_follow_at": c.last_follow_at or "",
                "notes": (c.notes or "")[:300],
                "owner_name": owner_name,          # 非空=人工指派，AI 必须沿用
                "assigned": bool(owner_name),
                "membership_id": int(c.owner_membership_id) if c.owner_membership_id else None,
            })
    finally:
        read_db.close()

    if body.mode == "template":
        plan = _template_plan(ctx, member_payload)
        source = "template"
    else:
        plan = await _llm_json(llm_token, PLAN_SYSTEM, {
            "task": "generate_project_plan",
            "project": {"name": ctx.get("name"), "goal": ctx.get("goal"),
                        "success_criteria": ctx.get("success_criteria"),
                        "start_at": ctx.get("start_at"), "end_at": ctx.get("end_at")},
            "products": ctx.get("products") or [],
            "members": member_payload,
            "customers": customer_payload,
            "period": {"start_at": ctx.get("start_at"), "end_at": ctx.get("end_at"),
                       "source": ("default" if period_defaulted else "project")},
            "assignment_rules": [
                "period.start_at / period.end_at 就是项目周期：所有阶段与任务的 start_at/end_at "
                "必须落在这两个日期之间（含端点），不要自己发明年份。",
                "customers 里 owner_name 非空的客户已经人工指派：相关任务必须由该负责人做，不得改派。",
                "owner_name 为空的客户未指派：把跟进安排写进 tasks（owner_name 从 members 里选），"
                "并在 assignments_suggestions 里至少给出建议负责人与理由。",
                "assignments_suggestions 只是建议，不要修改客户本身的数据。",
            ],
            "extra_requirements": body.extra_requirements,
        })
        source = "ai"

    write_db = SessionLocal()
    try:
        project = write_db.query(MProject).filter(MProject.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        write_db.query(MPlanNode).filter(MPlanNode.project_id == project_id).delete()
        order = 0
        by_name = {m["name"]: m for m in member_payload}
        for phase in plan.get("phases") or []:
            order += 1
            ph = MPlanNode(company_id=company_id, project_id=project_id, node_type="phase",
                           title=str(phase.get("title") or "阶段"), detail=str(phase.get("goal") or ""),
                           start_at=str(phase.get("start_at") or ""), end_at=str(phase.get("end_at") or ""),
                           owner_name=str(phase.get("owner_name") or ""),
                           requirement=str(phase.get("goal") or ""), order_index=order)
            write_db.add(ph)
            write_db.flush()
            for task in phase.get("tasks") or []:
                order += 1
                owner = str(task.get("owner_name") or "").strip()
                mid = by_name.get(owner, {}).get("membership_id")
                # 代码级兜底：任务里点到「已人工指派的客户」时，无论模型写了谁（或没写），
                # 都落到该客户的指派负责人身上——不让模型的疏忽改掉人工指派关系。
                hit = _assigned_customer_in_text(task, customer_payload)
                if hit and owner != hit["owner_name"]:
                    owner = str(hit["owner_name"])
                    mid = hit.get("membership_id") or by_name.get(owner, {}).get("membership_id")
                write_db.add(MPlanNode(
                    company_id=company_id, project_id=project_id, parent_id=ph.id, node_type="task",
                    title=str(task.get("title") or "任务"), detail=str(task.get("detail") or ""),
                    owner_name=owner, owner_membership_id=mid,
                    owner_kind="human" if mid else "unassigned",
                    requirement=str(task.get("requirement") or ""),
                    start_at=str(task.get("start_at") or ""), end_at=str(task.get("end_at") or ""),
                    kpi=str(task.get("kpi") or ""), deliverable=str(task.get("deliverable") or ""),
                    order_index=order,
                ))
        for ms in plan.get("milestones") or []:
            order += 1
            write_db.add(MPlanNode(company_id=company_id, project_id=project_id, node_type="milestone",
                                   title=str(ms.get("title") or "里程碑"),
                                   start_at=str(ms.get("at") or ""), end_at=str(ms.get("at") or ""),
                                   requirement=str(ms.get("criteria") or ""), order_index=order))

        project.plan_source = source
        project.arrangement_status = "draft"
        project.status = "planning"
        if period_defaulted:
            project.start_at = str(ctx.get("start_at") or "")
            project.end_at = str(ctx.get("end_at") or "")

        gaps = plan.get("resource_gap") or []
        write_db.query(MCondition).filter(MCondition.project_id == project_id,
                                          MCondition.source == "plan").delete()
        for g in gaps:
            write_db.add(MCondition(
                company_id=company_id, project_id=project_id,
                category=str(g.get("category") or "people"), title=str(g.get("title") or "条件"),
                need=str(g.get("need") or ""), now_state=str(g.get("now") or ""),
                gap=str(g.get("gap") or ""), impact=str(g.get("impact") or ""),
                severity=str(g.get("severity") or "medium"), actions=g.get("actions") or [],
                source="plan",
            ))
        # AI 的客户指派建议：落成「条件」记录（category=people），
        # 未指派客户至少要有建议，这样老板在「条件」页能看到「建议谁跟、为什么」。
        assignments: List[Dict[str, Any]] = []
        assigned_customer_names = {str(c.get("name") or "") for c in customer_payload if c.get("assigned")}
        for item in (plan.get("assignments_suggestions") or [])[:40]:
            if not isinstance(item, dict):
                continue
            customer = str(item.get("customer") or "").strip()
            suggested = str(item.get("suggested_owner") or "").strip()
            if not customer:
                continue
            priority = str(item.get("priority") or "medium").lower()
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            reason = str(item.get("reason") or "").strip()
            record = {"customer": customer, "suggested_owner": suggested,
                      "priority": priority, "reason": reason,
                      "suggested_membership_id": by_name.get(suggested, {}).get("membership_id")}
            assignments.append(record)
            write_db.add(MCondition(
                company_id=company_id, project_id=project_id, category="people",
                title="客户指派建议：" + customer,
                need=suggested or "待定",
                now_state="已指派" if customer in assigned_customer_names else "未指派",
                gap=reason or "AI 建议由该成员负责这个客户的跟进",
                impact="客户若无人跟进，线索会沉掉",
                severity=priority, actions=[], source="plan",
            ))
        _audit(write_db, company_id, user.id, "plan.generate", "project", project_id,
               {"source": source, "nodes": order, "gaps": len(gaps),
                "assignments": len(assignments)})
        write_db.flush()
        plan_version = _save_version(write_db, project, source=source,
                                     summary=plan.get("summary") or "",
                                     requirement=body.extra_requirements or "",
                                     user_id=user.id)
        write_db.commit()
        nodes = (write_db.query(MPlanNode).filter(MPlanNode.project_id == project_id)
                 .order_by(MPlanNode.order_index).all())
        out_nodes = [_node_json(n) for n in nodes]
    finally:
        write_db.close()
    return {"ok": True, "source": source, "summary": plan.get("summary") or "",
            "version": plan_version, "nodes": out_nodes, "resource_gap": gaps,
            "assignments": assignments,
            "period_defaulted": False,   # 现在周期是必填的，不再静默兜底
            "period": {"start_at": str(ctx.get("start_at") or ""),
                       "end_at": str(ctx.get("end_at") or "")}}


@router.patch("/nodes/{node_id}")
def patch_node(node_id: int, body: NodeIn, user: Any = Depends(current_actor),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    node = db.query(MPlanNode).filter(MPlanNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    company = _require_company(db, node.company_id, user)
    _require_plan_admin(db, company, user)
    data = body.dict(exclude_unset=True)
    for key in ("owner_name", "requirement", "start_at", "end_at", "status", "progress"):
        if data.get(key) is not None:
            setattr(node, key, data[key])
    if data.get("owner_membership_id") is not None:
        node.owner_membership_id = data["owner_membership_id"]
        node.owner_kind = "human"
    project = db.query(MProject).filter(MProject.id == node.project_id).first()
    if project and project.arrangement_status == "confirmed":
        project.arrangement_status = "changed"
    db.commit()
    db.refresh(node)
    return {"ok": True, "node": _node_json(node),
            "arrangement_status": project.arrangement_status if project else "draft"}


@router.post("/projects/{project_id}/draft")
def save_draft(project_id: int, body: DraftIn, user: Any = Depends(current_actor),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)
    project.arrangement_draft = body.payload or {}
    if project.arrangement_status == "confirmed":
        project.arrangement_status = "changed"
    _audit(db, company.id, user.id, "arrangement.draft", "project", project.id)
    db.commit()
    return {"ok": True, "saved_at": datetime.utcnow().isoformat()}


def _project_gaps(project: MProject) -> List[Dict[str, Any]]:
    """项目级必要条件：目标、周期。缺了就不该生成规划（更不该确认）。"""
    gaps: List[Dict[str, Any]] = []
    if not str(project.goal or "").strip():
        gaps.append({"key": "goal", "label": "项目目标", "missing": ["目标"]})
    if not str(project.start_at or "").strip():
        gaps.append({"key": "start_at", "label": "开始日期", "missing": ["开始日期"]})
    if not str(project.end_at or "").strip():
        gaps.append({"key": "end_at", "label": "结束日期", "missing": ["结束日期"]})
    return gaps


def _arrangement_gaps(nodes: List[MPlanNode]) -> List[Dict[str, Any]]:
    """哪些节点还没排齐：逐条给出缺什么，给「排齐检查」和确认失败提示共用。

    阶段（phase）是分组，负责人可选：只要求填「要求 + 时间」；
    任务（task）要求责任人 / 要求 / 时间都填。
    """
    gaps: List[Dict[str, Any]] = []
    for n in nodes:
        miss: List[str] = []
        if not n.owner_name and n.node_type != "phase":
            miss.append("责任人")
        if not n.requirement:
            miss.append("要求")
        if not n.start_at:
            miss.append("开始时间")
        if not n.end_at:
            miss.append("结束时间")
        if miss:
            gaps.append({"id": n.id, "title": n.title, "node_type": n.node_type,
                         "owner_kind": n.owner_kind, "missing": miss,
                         "detail": (n.detail or "")[:100]})
    return gaps


@router.get("/projects/{project_id}/arrangement-check")
def arrangement_check(project_id: int, user: Any = Depends(current_actor),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    """确认前的排齐检查：还差哪些节点、每个缺什么、几个是阶段几个是任务。"""
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    _require_company(db, project.company_id, user)
    nodes = (db.query(MPlanNode)
             .filter(MPlanNode.project_id == project_id,
                     MPlanNode.node_type.in_(("phase", "task")))
             .order_by(MPlanNode.order_index).all())
    gaps = _arrangement_gaps(nodes)
    phase_gaps = [g for g in gaps if g["node_type"] == "phase"]
    task_gaps = [g for g in gaps if g["node_type"] == "task"]
    project_gaps = _project_gaps(project)
    return {"project": project.name, "ok": (not gaps) and (not project_gaps),
            "project_gaps": project_gaps, "total": len(nodes),
            "ready": len(nodes) - len(gaps), "missing": gaps,
            "phases_missing": len(phase_gaps), "tasks_missing": len(task_gaps),
            "phase_owner_optional": True,
            "arrangement_status": project.arrangement_status}


@router.post("/projects/{project_id}/confirm")
def confirm_arrangement(project_id: int, user: Any = Depends(current_actor),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)
    nodes = db.query(MPlanNode).filter(MPlanNode.project_id == project_id,
                                       MPlanNode.node_type.in_(("phase", "task"))).all()
    if not nodes:
        raise HTTPException(status_code=400, detail="还没有可确认的节点，先生成规划")
    gaps = _arrangement_gaps(nodes)
    project_gaps = _project_gaps(project)
    if gaps or project_gaps:
        phase_gaps = [g for g in gaps if g["node_type"] == "phase"]
        task_gaps = [g for g in gaps if g["node_type"] == "task"]
        parts = []
        if project_gaps:
            parts.append("项目缺 " + "/".join(g["label"] for g in project_gaps))
        if phase_gaps:
            parts.append(str(len(phase_gaps)) + " 个阶段缺责任人")
        if task_gaps:
            parts.append(str(len(task_gaps)) + " 个任务没排齐")
        raise HTTPException(status_code=400, detail={
            "message": "还不能确认：" + "、".join(parts) + "（点「排齐检查」逐条补齐）",
            "missing": gaps,
            "project_gaps": project_gaps,
            "needs": [g["key"] for g in project_gaps],
            "phases_missing": len(phase_gaps),
            "tasks_missing": len(task_gaps)})
    project.arrangement_status = "confirmed"
    project.status = "running"
    _audit(db, company.id, user.id, "arrangement.confirm", "project", project.id, {"nodes": len(nodes)})
    db.commit()
    return {"ok": True, "arrangement_status": "confirmed", "nodes": len(nodes),
            "confirmed_at": datetime.utcnow().isoformat()}


def _build_checkup(db: Session, project: MProject, slot: str, today: str) -> MCheckup:
    nodes = db.query(MPlanNode).filter(MPlanNode.project_id == project.id).all()
    tasks = [n for n in nodes if n.node_type == "task"]
    today_d = datetime.strptime(today, "%Y-%m-%d").date()
    overdue, missing_owner = [], 0
    for n in tasks:
        if n.end_at:
            try:
                if datetime.strptime(n.end_at, "%Y-%m-%d").date() < today_d and n.status != "completed":
                    overdue.append(n)
            except ValueError:
                pass
        if not n.owner_name:
            missing_owner += 1
    done = sum(1 for n in tasks if n.status == "completed")
    total = len(tasks) or 1
    progress = int(done / total * 100)
    expected = 0
    if project.start_at and project.end_at:
        try:
            s = datetime.strptime(project.start_at, "%Y-%m-%d").date()
            e = datetime.strptime(project.end_at, "%Y-%m-%d").date()
            if e > s:
                expected = max(0, min(100, int((today_d - s).days / (e - s).days * 100)))
        except ValueError:
            expected = 0
    health = "good"
    if project.arrangement_status != "confirmed":
        health = "watch"
    if overdue or (expected and progress + 15 < expected):
        health = "risk"
    evidence = ([("整体进度 " + str(progress) + "%（时间进度 " + str(expected) + "%）"),
                 "任务 " + str(done) + "/" + str(len(tasks)) + " 已完成",
                 "逾期 " + str(len(overdue)) + " 项"] if tasks else ["尚未生成规划"])
    phase = "早盘" if slot == "0900" else "收口"
    if health == "risk":
        head = phase + "：进度落后（" + str(progress) + "% vs 时间 " + str(expected) + "%），逾期 " + str(len(overdue)) + " 项"
        if missing_owner:
            head += "，" + str(missing_owner) + " 项未指派"
    elif health == "watch":
        head = phase + "：工作安排尚未确认，进度 " + str(progress) + "%"
    else:
        head = phase + "：节奏正常，进度 " + str(progress) + "%（时间 " + str(expected) + "%）"
    suggestions = []
    if project.arrangement_status != "confirmed":
        suggestions.append({"what": "先确认工作安排（人员/要求/时间节点）", "why": "未确认前不进入执行跟踪",
                            "owner_role": "boss", "due": today})
    for n in overdue[:3]:
        suggestions.append({"what": "处理逾期任务「" + n.title + "」", "why": "已超过 " + (n.end_at or ""),
                            "owner_role": "boss", "due": today})
    if missing_owner:
        suggestions.append({"what": "给 " + str(missing_owner) + " 个节点指派责任人",
                            "why": "没有责任人的任务不会被执行", "owner_role": "boss", "due": today})
    if not suggestions:
        suggestions.append({"what": "维持当前节奏，按排期推进",
                            "why": "进度 " + str(progress) + "% 与时间匹配", "owner_role": "boss", "due": today})
    decisions = []
    if len(overdue) >= 2:
        decisions.append({"question": "是否增配人力或虚拟员工槽位补上逾期？",
                          "options": ["增配虚拟员工槽位", "调整排期"], "recommend": "增配虚拟员工槽位",
                          "impact_if_ignored": "逾期会顺延到后续阶段，影响整体交付"})
    return MCheckup(company_id=project.company_id, project_id=project.id, slot=slot,
                    checked_on=today, health=health, headline=head, evidence=evidence,
                    suggestions=suggestions, decisions=decisions, changes={},
                    source="rules", confidence="high")


CHECKUP_SYSTEM = """你是「AI 项目体检官」，每天两次给老板做项目体检。你的标准是"项目能不能按时做成"。
只输出一个 JSON 对象，不要解释、不要 markdown。

要求：
1. 结论必须用数字说话，禁止"进展顺利""继续努力"这类空话。
2. 早盘(0900)：只讲"今天要做什么"与"需要老板拍板什么"；收口(1700)：只讲"今天实际干成什么"与"明天怎么改"。
3. 与上一次体检对比，指出变好/变差的具体项（changes）。
4. 建议必须可执行：做什么、谁来做、什么时候之前（due）。
5. 只有当"不决策就会影响目标达成"时才提 decisions，并给推荐选项与忽略后果。
6. 数据不足时把 confidence 标低，不要编造数字。

输出 schema：
{"health":"good|watch|risk","headline":"一句话结论(<=30字,带数字)",
 "evidence":["支撑数字 2-4 条"],
 "suggestions":[{"what":"","why":"","owner_role":"","due":"YYYY-MM-DD"}],
 "decisions":[{"question":"","options":["A","B"],"recommend":"A","impact_if_ignored":""}],
 "changes":{"better":[""],"worse":[""]},"confidence":"high|medium|low"}"""


async def run_checkups_for_company(db: Session, company: MCompany, slot: str, today: str,
                                   *, use_ai: bool = False) -> Dict[str, Any]:
    """生成某公司当日某档位的体检（幂等）。API 与定时任务共用。"""
    projects = (db.query(MProject)
                .filter(MProject.company_id == company.id, MProject.status != "archived").all())
    created = ai_used = ai_failed = 0
    token = ""
    if use_ai:
        try:
            token = _llm_token_for_company(db, company)
        except HTTPException:
            token = ""
    for project in projects:
        exists = (db.query(MCheckup)
                  .filter(MCheckup.project_id == project.id, MCheckup.slot == slot,
                          MCheckup.checked_on == today).first())
        if exists:
            continue
        row = _build_checkup(db, project, slot, today)
        db.add(row)
        db.flush()
        if token:
            other_slot = "1700" if slot == "0900" else "0900"
            prev = (db.query(MCheckup)
                    .filter(MCheckup.project_id == project.id, MCheckup.slot == other_slot)
                    .order_by(MCheckup.id.desc()).first())
            nodes = db.query(MPlanNode).filter(MPlanNode.project_id == project.id).all()
            ctx = {
                "task": "daily_project_checkup", "slot": slot, "checked_on": today,
                "project": {"name": project.name, "goal": project.goal,
                            "period": (project.start_at or "") + " ~ " + (project.end_at or ""),
                            "arrangement_status": project.arrangement_status},
                "rule_baseline": {"health": row.health, "headline": row.headline,
                                  "evidence": row.evidence},
                "nodes": [{"title": n.title, "type": n.node_type, "owner": n.owner_name,
                           "status": n.status, "end_at": n.end_at,
                           "has_requirement": bool(n.requirement)} for n in nodes][:40],
                "last_checkup": ({"slot": prev.slot, "checked_on": prev.checked_on,
                                  "headline": prev.headline, "health": prev.health}
                                 if prev else None),
            }
            try:
                out = await _llm_json(token, CHECKUP_SYSTEM, ctx, timeout=120.0)
                if isinstance(out, dict) and out.get("headline"):
                    row.health = str(out.get("health") or row.health)
                    row.headline = str(out.get("headline"))
                    row.evidence = out.get("evidence") or row.evidence
                    row.suggestions = out.get("suggestions") or row.suggestions
                    row.decisions = out.get("decisions") or row.decisions
                    row.changes = out.get("changes") or {}
                    row.confidence = str(out.get("confidence") or "medium")
                    row.source = "ai"
                    ai_used += 1
            except HTTPException as exc:
                row.source = "rules"
                row.changes = {"ai_error": str(exc.detail)[:200]}
                ai_failed += 1
            except Exception as exc:
                row.source = "rules"
                row.changes = {"ai_error": str(exc)[:200]}
                ai_failed += 1
        created += 1
    return {"created": created, "ai_used": ai_used, "ai_failed": ai_failed,
            "projects": len(projects)}


@router.post("/checkups/run")
async def run_checkups(body: CheckupRunIn, user: Any = Depends(current_actor),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    slot = body.slot if body.slot in ("0900", "1700") else "0900"
    today = date.today().isoformat()
    result = await run_checkups_for_company(db, company, slot, today, use_ai=body.use_ai)
    _audit(db, company.id, getattr(user, "id", 0), "checkup.run", "company", company.id,
           {"slot": slot, "created": result["created"], "ai": body.use_ai})
    db.commit()
    return {"ok": True, "slot": slot, "checked_on": today, **result}


@router.get("/checkups")
def list_checkups(company_id: int, slot: str = "1700", user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    today = date.today().isoformat()
    rows = (db.query(MCheckup, MProject.name)
            .join(MProject, MProject.id == MCheckup.project_id)
            .filter(MCheckup.company_id == company_id, MCheckup.slot == slot,
                    MCheckup.checked_on == today)
            .all())
    items = [{"project_id": c.project_id, "project": name, "slot": c.slot, "checked_on": c.checked_on,
              "health": c.health, "headline": c.headline, "evidence": c.evidence or [],
              "suggestions": c.suggestions or [], "decisions": c.decisions or [],
              "changes": c.changes or {}, "confidence": c.confidence} for c, name in rows]
    if not items:
        items = [{"project_id": p.id, "project": p.name, "slot": slot, "checked_on": today,
                  "health": "watch" if p.arrangement_status != "confirmed" else "good",
                  "headline": "今天还没有这次体检结果，点「立即体检」生成",
                  "evidence": [], "suggestions": [], "decisions": [], "changes": {},
                  "confidence": "low"}
                 for p in db.query(MProject).filter(MProject.company_id == company_id).all()]
    return {"slot": slot, "checked_on": today, "items": items}


@router.get("/conditions")
def list_conditions(company_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MCondition).filter(MCondition.company_id == company_id)
            .order_by(MCondition.id).all())
    return {"conditions": [{"id": c.id, "project_id": c.project_id, "category": c.category,
                            "title": c.title, "need": c.need, "now": c.now_state, "gap": c.gap,
                            "impact": c.impact, "severity": c.severity, "actions": c.actions or [],
                            "decided": c.decided, "source": c.source} for c in rows]}


@router.post("/conditions/{condition_id}/accept")
def accept_condition(condition_id: int, body: AcceptIn, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCondition).filter(MCondition.id == condition_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="条件不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    actions = row.actions or []
    if not actions:
        raise HTTPException(status_code=400, detail="该条件没有可采纳的动作")
    idx = max(0, min(len(actions) - 1, int(body.action_index)))
    what = str(actions[idx].get("what") or "补位动作")
    row.decided = "已采纳：" + what
    _audit(db, company.id, user.id, "condition.accept", "condition", row.id, {"index": idx})
    db.commit()
    return {"ok": True, "decided": row.decided}


@router.get("/finance")
def list_finance(company_id: int, user: Any = Depends(current_actor),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MFinanceEntry).filter(MFinanceEntry.company_id == company_id)
            .order_by(MFinanceEntry.happened_on.desc(), MFinanceEntry.id.desc()).limit(200).all())
    income = sum(float(r.amount or 0) for r in rows if r.entry_type == "income")
    expense = sum(float(r.amount or 0) for r in rows if r.entry_type == "expense")
    return {"summary": {"income": income, "expense": expense, "net": income - expense},
            "entries": [{"id": r.id, "type": r.entry_type, "category": r.category,
                         "amount": float(r.amount or 0), "on": r.happened_on,
                         "summary": r.summary, "status": r.status} for r in rows]}


@router.post("/finance")
def create_finance(body: FinanceIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    row = MFinanceEntry(company_id=company.id,
                        entry_type="expense" if body.entry_type == "expense" else "income",
                        category=body.category, amount=Decimal(str(body.amount or 0)),
                        happened_on=body.happened_on or date.today().isoformat(),
                        summary=body.summary, created_by=user.id)
    db.add(row)
    _audit(db, company.id, user.id, "finance.create", "entry", "", {"type": row.entry_type})
    db.commit()
    return {"ok": True, "id": row.id}


# ---------------- 库存（实体设备） ----------------
# 产品（m_product）是「对外卖的东西」；库存是「公司手里真实存在的设备」，一台一行、可按设备号追踪。
INVENTORY_STATUS = ("in_stock", "in_use", "repair", "scrapped")
INVENTORY_STATUS_LABEL = {"in_stock": "在库", "in_use": "在用", "repair": "维修中", "scrapped": "报废"}


class InventoryIn(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=160)
    product_id: Optional[int] = None
    model_name: str = ""
    sku: str = ""
    quantity: int = 1
    unit: str = "台"
    unit_cost: float = 0
    location: str = ""
    keeper: str = ""
    status: str = "in_stock"
    bought_on: str = ""
    warranty_until: str = ""
    note: str = ""


class InventoryPatchIn(BaseModel):
    company_id: Optional[int] = None
    name: Optional[str] = None
    product_id: Optional[int] = None
    model_name: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    unit_cost: Optional[float] = None
    location: Optional[str] = None
    keeper: Optional[str] = None
    status: Optional[str] = None
    bought_on: Optional[str] = None
    warranty_until: Optional[str] = None
    note: Optional[str] = None


def _inv_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _inv_dict(row: MInventoryItem, product_names: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    names = product_names or {}
    unit_cost = float(row.unit_cost or 0)
    quantity = int(row.quantity or 0)
    return {
        "id": row.id, "name": row.name, "product_id": row.product_id,
        "product_name": names.get(row.product_id or 0, ""),
        "model_name": row.model_name, "sku": row.sku, "quantity": quantity,
        "unit": row.unit, "unit_cost": unit_cost, "amount": round(unit_cost * quantity, 2),
        "location": row.location, "keeper": row.keeper, "status": row.status,
        "status_label": INVENTORY_STATUS_LABEL.get(row.status, row.status),
        "bought_on": row.bought_on, "warranty_until": row.warranty_until, "note": row.note,
    }


@router.get("/inventory")
def list_inventory(company_id: int, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MInventoryItem).filter(MInventoryItem.company_id == company_id)
            .order_by(MInventoryItem.status, MInventoryItem.id.desc()).all())
    names = {p.id: p.name for p in db.query(MProduct).filter(MProduct.company_id == company_id).all()}
    items = [_inv_dict(r, names) for r in rows]
    summary: Dict[str, Any] = {
        "quantity": sum(i["quantity"] for i in items),
        "value": round(sum(i["amount"] for i in items), 2),
        "kinds": len(items),
    }
    for key in INVENTORY_STATUS:
        summary[key] = sum(i["quantity"] for i in items if i["status"] == key)
    return {"items": items, "summary": summary}


@router.post("/inventory")
def create_inventory(body: InventoryIn, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user, write=True)
    status = body.status if body.status in INVENTORY_STATUS else "in_stock"
    row = MInventoryItem(
        company_id=company.id, name=body.name.strip()[:160],
        product_id=body.product_id or None, model_name=_inv_text(body.model_name, 120),
        sku=_inv_text(body.sku, 64), quantity=max(0, int(body.quantity or 0)),
        unit=_inv_text(body.unit, 16) or "台", unit_cost=Decimal(str(body.unit_cost or 0)),
        location=_inv_text(body.location, 120), keeper=_inv_text(body.keeper, 80), status=status,
        bought_on=_inv_text(body.bought_on, 10), warranty_until=_inv_text(body.warranty_until, 10),
        note=_inv_text(body.note, 2000),
    )
    db.add(row)
    _audit(db, company.id, getattr(user, "id", 0), "inventory.create", "inventory", "",
           {"name": row.name, "quantity": row.quantity})
    db.commit()
    return {"ok": True, "item_id": row.id, "item": _inv_dict(row)}


@router.patch("/inventory/{item_id}")
def update_inventory(item_id: int, body: InventoryPatchIn, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MInventoryItem).filter(MInventoryItem.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    company = _require_company(db, body.company_id or row.company_id, user, write=True)
    if company.id != row.company_id:
        raise HTTPException(status_code=404, detail="设备不存在")
    if body.name is not None and body.name.strip():
        row.name = body.name.strip()[:160]
    if body.product_id is not None:
        row.product_id = body.product_id or None
    if body.model_name is not None:
        row.model_name = _inv_text(body.model_name, 120)
    if body.sku is not None:
        row.sku = _inv_text(body.sku, 64)
    if body.quantity is not None:
        row.quantity = max(0, int(body.quantity or 0))
    if body.unit is not None:
        row.unit = _inv_text(body.unit, 16) or "台"
    if body.unit_cost is not None:
        row.unit_cost = Decimal(str(body.unit_cost or 0))
    if body.location is not None:
        row.location = _inv_text(body.location, 120)
    if body.keeper is not None:
        row.keeper = _inv_text(body.keeper, 80)
    if body.status is not None and body.status in INVENTORY_STATUS:
        row.status = body.status
    if body.bought_on is not None:
        row.bought_on = _inv_text(body.bought_on, 10)
    if body.warranty_until is not None:
        row.warranty_until = _inv_text(body.warranty_until, 10)
    if body.note is not None:
        row.note = _inv_text(body.note, 2000)
    _audit(db, company.id, getattr(user, "id", 0), "inventory.update", "inventory", str(row.id))
    db.commit()
    return {"ok": True, "item": _inv_dict(row)}


@router.delete("/inventory/{item_id}")
def delete_inventory(item_id: int, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MInventoryItem).filter(MInventoryItem.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="设备不存在")
    company = _require_company(db, row.company_id, user, write=True)
    db.delete(row)
    _audit(db, company.id, getattr(user, "id", 0), "inventory.delete", "inventory", str(item_id),
           {"name": row.name})
    db.commit()
    return {"ok": True, "deleted": item_id}


@router.post("/finance/scan", summary="上传发票/账单图片，AI 识图后返回可填报的记账字段")
async def scan_finance_bill(company_id: int = Query(..., description="公司 ID"),
                            file: UploadFile = File(...),
                            user: Any = Depends(current_actor),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    data = await file.read()
    result = await document_scan.scan_finance_document(data, file.filename or "")
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "票据识别失败")
    return {"ok": True, "fields": result.get("fields") or {}, "model": result.get("model"),
            "latency_ms": result.get("latency_ms")}


@router.post("/admin/boss")
def mark_boss(body: BossIn, user: Any = Depends(current_actor),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    if str(user.role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="只有平台管理员可以标记老板")
    target = None
    if body.user_id:
        target = db.query(User).filter(User.id == body.user_id).first()
    if target is None and body.phone.strip():
        target = _user_by_phone(db, body.phone)
    if not target:
        raise HTTPException(status_code=404, detail="没找到这个手机号对应的 bihuo 账号（请让对方先用客户端注册/登录）")
    if str(target.brand_mark or "").strip().lower() != MANAGE_BRAND:
        raise HTTPException(status_code=400, detail="该账号不是 bihuo 品牌，不能标记为老板")
    existing = db.query(MCompany).filter(MCompany.owner_user_id == target.id).first()
    if existing:
        return {"ok": True, "company_id": existing.id, "created": False}
    company = MCompany(name=body.company_name.strip() or (target.email.split("@")[0] + " 的公司"),
                       owner_user_id=target.id)
    db.add(company)
    db.flush()
    membership = MMembership(company_id=company.id, user_id=target.id,
                             display_name=target.email.split("@")[0], dept="经营管理",
                             email=target.email, remark="平台管理员标记为老板")
    db.add(membership)
    db.flush()
    db.add(MMembershipRole(company_id=company.id, membership_id=membership.id,
                           role_code="boss", level="p4"))
    _audit(db, company.id, user.id, "admin.boss", "user", target.id)
    db.commit()
    return {"ok": True, "company_id": company.id, "created": True}

# ───────────────────────── 客户（业务岗） ─────────────────────────

STAGE_LABEL = {"lead": "线索", "contacted": "已联系", "proposal": "已报价",
               "negotiating": "洽谈中", "won": "已成交", "lost": "已丢失"}
STAGE_ORDER = ["lead", "contacted", "proposal", "negotiating", "won", "lost"]


class CustomerIn(BaseModel):
    company_id: int
    name: str
    company_name: str = ""
    phone: str = ""
    wechat: str = ""
    source: str = ""
    stage: str = "lead"
    amount: float = 0
    owner_membership_id: Optional[int] = None
    next_action: str = ""
    next_follow_at: str = ""
    notes: str = ""


class CustomerPatchIn(BaseModel):
    name: Optional[str] = None
    company_name: Optional[str] = None
    phone: Optional[str] = None
    wechat: Optional[str] = None
    source: Optional[str] = None
    stage: Optional[str] = None
    amount: Optional[float] = None
    owner_membership_id: Optional[int] = None
    next_action: Optional[str] = None
    next_follow_at: Optional[str] = None
    notes: Optional[str] = None


class CustomerLogIn(BaseModel):
    kind: str = "note"
    content: str = ""
    to_stage: str = ""
    happened_at: str = ""


def _member_name(db: Session, membership_id: Any) -> str:
    try:
        mid = int(membership_id) if membership_id else 0
    except (TypeError, ValueError):
        return ""
    if not mid:
        return ""
    row = db.query(MMembership).filter(MMembership.id == mid).first()
    return str(row.display_name or "") if row else ""


def _check_customer_owner(db: Session, company_id: int, membership_id: Any) -> Optional[int]:
    """客户指派的目标必须是本公司 active 成员，否则 400（防止跨公司/已离职成员）。"""
    if membership_id in (None, "", 0):
        return None
    try:
        mid = int(membership_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="指派对象无效")
    row = (
        db.query(MMembership)
        .filter(MMembership.id == mid,
                MMembership.company_id == company_id,
                MMembership.status == "active")
        .first()
    )
    if not row:
        raise HTTPException(status_code=400, detail="指派对象不是本公司的在册成员")
    return int(row.id)


def _customer_json(db: Session, row: MCustomer, logs: bool = False) -> Dict[str, Any]:
    owner = None
    if row.owner_membership_id:
        m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
        owner = m.display_name if m else None
    data = {"id": row.id, "name": row.name, "company_name": row.company_name,
            "phone": row.phone, "wechat": row.wechat, "source": row.source,
            "stage": row.stage, "stage_label": STAGE_LABEL.get(row.stage, row.stage),
            "amount": float(row.amount or 0), "owner_membership_id": row.owner_membership_id,
            "owner_name": owner or "", "next_action": row.next_action,
            "next_follow_at": row.next_follow_at, "last_follow_at": row.last_follow_at,
            "notes": row.notes}
    if logs:
        rows = (db.query(MCustomerLog).filter(MCustomerLog.customer_id == row.id)
                .order_by(MCustomerLog.id.desc()).limit(50).all())
        data["logs"] = [{"id": x.id, "kind": x.kind, "content": x.content,
                         "from_stage": x.from_stage, "to_stage": x.to_stage,
                         "happened_at": x.happened_at} for x in rows]
    return data


@router.get("/customers")
def list_customers(company_id: int, stage: str = "", q: str = "", mine: bool = False,
                   user: Any = Depends(current_actor), db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MCustomer).filter(MCustomer.company_id == company_id)
            .order_by(MCustomer.id.desc()).all())
    key = (q or "").strip().lower()
    out = []
    for r in rows:
        if stage and r.stage != stage:
            continue
        if key and key not in (r.name or "").lower() and key not in (r.company_name or "").lower()                 and key not in (r.phone or "").lower() and key not in (r.notes or "").lower():
            continue
        out.append(_customer_json(db, r))
    counts = {k: 0 for k in STAGE_ORDER}
    for r in rows:
        counts[r.stage] = counts.get(r.stage, 0) + 1
    return {"customers": out, "counts": counts, "stages": STAGE_LABEL}


@router.post("/customers")
def create_customer(body: CustomerIn, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="请填客户姓名")
    row = MCustomer(company_id=company.id, name=body.name.strip(), company_name=body.company_name,
                    phone=body.phone, wechat=body.wechat, source=body.source,
                    stage=body.stage if body.stage in STAGE_LABEL else "lead",
                    amount=Decimal(str(body.amount or 0)),
                    owner_membership_id=_check_customer_owner(db, company.id, body.owner_membership_id),
                    next_action=body.next_action,
                    next_follow_at=body.next_follow_at, notes=body.notes,
                    created_by=getattr(user, "id", 0))
    db.add(row)
    _audit(db, company.id, getattr(user, "id", 0), "customer.create", "customer", "", {"name": row.name})
    db.commit()
    return {"ok": True, "customer": _customer_json(db, row)}


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: int, body: CustomerPatchIn, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCustomer).filter(MCustomer.id == customer_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    company = _require_company(db, row.company_id, user)
    data = body.dict(exclude_unset=True)
    old_stage = row.stage
    for field in ("name", "company_name", "phone", "wechat", "source", "next_action",
                  "next_follow_at", "notes"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    if data.get("stage"):
        row.stage = data["stage"]
    if data.get("amount") is not None:
        row.amount = Decimal(str(data["amount"]))
    if "owner_membership_id" in data:
        row.owner_membership_id = _check_customer_owner(db, row.company_id, data.get("owner_membership_id"))
        db.add(MCustomerLog(company_id=company.id, customer_id=row.id,
                            actor_user_id=getattr(user, "id", 0), kind="note",
                            content="负责人变更为「" + (_member_name(db, row.owner_membership_id) or "未指派") + "」"))
    if data.get("stage") and data["stage"] != old_stage:
        db.add(MCustomerLog(company_id=company.id, customer_id=row.id,
                            actor_user_id=getattr(user, "id", 0), kind="stage",
                            content="阶段变更为「" + STAGE_LABEL.get(row.stage, row.stage) + "」",
                            from_stage=old_stage, to_stage=row.stage))
        row.last_follow_at = date.today().isoformat()
    _audit(db, company.id, getattr(user, "id", 0), "customer.update", "customer", row.id)
    db.commit()
    return {"ok": True, "customer": _customer_json(db, row)}


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCustomer).filter(MCustomer.id == customer_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    company = _require_company(db, row.company_id, user)
    db.query(MCustomerLog).filter(MCustomerLog.customer_id == row.id).delete()
    db.delete(row)
    _audit(db, company.id, getattr(user, "id", 0), "customer.delete", "customer", customer_id)
    db.commit()
    return {"ok": True, "deleted": customer_id}


@router.get("/customers/{customer_id}")
def customer_detail(customer_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCustomer).filter(MCustomer.id == customer_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    _require_company(db, row.company_id, user)
    deliv = (db.query(MDelivery).filter(MDelivery.customer_id == row.id)
             .order_by(MDelivery.id.desc()).all())
    data = _customer_json(db, row, logs=True)
    data["deliveries"] = [{"id": d.id, "name": d.name, "status": d.status,
                           "promised_at": d.promised_at, "delivered_at": d.delivered_at} for d in deliv]
    return {"customer": data}


@router.post("/customers/{customer_id}/log")
def add_customer_log(customer_id: int, body: CustomerLogIn, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCustomer).filter(MCustomer.id == customer_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    company = _require_company(db, row.company_id, user)
    old_stage = row.stage
    if body.to_stage and body.to_stage in STAGE_LABEL and body.to_stage != old_stage:
        row.stage = body.to_stage
    happened = body.happened_at or date.today().isoformat()
    row.last_follow_at = happened
    db.add(MCustomerLog(company_id=company.id, customer_id=row.id,
                        actor_user_id=getattr(user, "id", 0), kind=body.kind,
                        content=body.content, from_stage=old_stage, to_stage=row.stage,
                        happened_at=happened))
    _audit(db, company.id, getattr(user, "id", 0), "customer.log", "customer", row.id)
    db.commit()
    return {"ok": True, "customer": _customer_json(db, row, logs=True)}


# ───────────────────────── 交付 ─────────────────────────

DELIVERY_LABEL = {"pending": "待开始", "doing": "交付中", "review": "待验收", "accepted": "已验收"}


class DeliveryIn(BaseModel):
    company_id: int
    name: str
    customer_id: Optional[int] = None
    project_id: Optional[int] = None
    owner_membership_id: Optional[int] = None
    promised_at: str = ""
    note: str = ""


class DeliveryPatchIn(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    owner_membership_id: Optional[int] = None
    promised_at: Optional[str] = None
    delivered_at: Optional[str] = None
    accepted_at: Optional[str] = None
    note: Optional[str] = None


def _delivery_json(db: Session, row: MDelivery, logs: bool = False) -> Dict[str, Any]:
    owner = None
    if row.owner_membership_id:
        m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
        owner = m.display_name if m else None
    cust = db.query(MCustomer).filter(MCustomer.id == row.customer_id).first() if row.customer_id else None
    data = {"id": row.id, "name": row.name, "customer_id": row.customer_id,
            "customer_name": cust.name if cust else "", "project_id": row.project_id,
            "status": row.status, "status_label": DELIVERY_LABEL.get(row.status, row.status),
            "owner_membership_id": row.owner_membership_id, "owner_name": owner or "",
            "promised_at": row.promised_at, "delivered_at": row.delivered_at,
            "accepted_at": row.accepted_at, "note": row.note,
            "last_follow_at": (row.last_follow_at or ""),
            "log_count": db.query(MDeliveryLog).filter(MDeliveryLog.delivery_id == row.id).count()}
    if logs:
        rows = (db.query(MDeliveryLog).filter(MDeliveryLog.delivery_id == row.id)
                .order_by(MDeliveryLog.id.desc()).limit(50).all())
        data["logs"] = [{"id": x.id, "kind": x.kind, "content": x.content,
                         "from_status": x.from_status, "to_status": x.to_status,
                         "happened_at": x.happened_at} for x in rows]
    return data


def _delivery_touch_status(row: MDelivery, new_status: str) -> None:
    """推进状态时顺手补齐实际时间（与 PATCH 保持一致）。"""
    row.status = new_status
    today = date.today().isoformat()
    if new_status in ("review", "accepted") and not row.delivered_at:
        row.delivered_at = today
    if new_status == "accepted" and not row.accepted_at:
        row.accepted_at = today


def _delivery_log(db: Session, company: MCompany, row: MDelivery, user: Any, *, kind: str,
                  content: str, from_status: str, happened_at: str = "") -> None:
    db.add(MDeliveryLog(company_id=company.id, delivery_id=row.id, actor_user_id=getattr(user, "id", 0),
                        kind=kind or "note", content=content or "",
                        from_status=from_status or "", to_status=row.status or "",
                        happened_at=happened_at or date.today().isoformat()))


@router.get("/deliveries")
def list_deliveries(company_id: int, status: str = "", user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    q = db.query(MDelivery).filter(MDelivery.company_id == company_id)
    if status:
        q = q.filter(MDelivery.status == status)
    rows = q.order_by(MDelivery.id.desc()).all()
    # 已成交但还没建交付单的客户，提示出来
    won = (db.query(MCustomer)
           .filter(MCustomer.company_id == company_id, MCustomer.stage == "won").all())
    existing = {r.customer_id for r in db.query(MDelivery).filter(
        MDelivery.company_id == company_id, MDelivery.customer_id.isnot(None)).all()}
    pending = [{"id": c.id, "name": c.name, "amount": float(c.amount or 0)}
               for c in won if c.id not in existing]
    counts = {k: 0 for k in DELIVERY_LABEL}
    for r in db.query(MDelivery).filter(MDelivery.company_id == company_id).all():
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"deliveries": [_delivery_json(db, r) for r in rows],
            "counts": counts, "labels": DELIVERY_LABEL, "won_without_delivery": pending}


@router.post("/deliveries")
def create_delivery(body: DeliveryIn, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="请填交付单名称")
    row = MDelivery(company_id=company.id, name=body.name.strip(), customer_id=body.customer_id,
                    project_id=body.project_id, owner_membership_id=body.owner_membership_id,
                    promised_at=body.promised_at, note=body.note)
    db.add(row)
    _audit(db, company.id, getattr(user, "id", 0), "delivery.create", "delivery", "", {"name": row.name})
    db.commit()
    return {"ok": True, "delivery": _delivery_json(db, row)}


@router.patch("/deliveries/{delivery_id}")
def update_delivery(delivery_id: int, body: DeliveryPatchIn, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    company = _require_company(db, row.company_id, user)
    data = body.dict(exclude_unset=True)
    # 先记住原值，再改字段——否则时间线里永远比不出变化
    old_status = row.status
    old_owner = row.owner_membership_id
    for field in ("name", "owner_membership_id", "promised_at", "delivered_at", "accepted_at", "note"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    if data.get("status") and data["status"] in DELIVERY_LABEL:
        if data["status"] != old_status:
            _delivery_touch_status(row, data["status"])
    if row.status != old_status:
        _delivery_log(db, company, row, user, kind="status",
                      content="状态：" + DELIVERY_LABEL.get(old_status, old_status)
                              + " → " + DELIVERY_LABEL.get(row.status, row.status),
                      from_status=old_status)
    if row.owner_membership_id != old_owner:
        who = "未指派"
        if row.owner_membership_id:
            m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
            who = (m.display_name if m else "未指定成员")
        _delivery_log(db, company, row, user, kind="assign",
                      content="指派给 " + who, from_status=row.status)
    _audit(db, company.id, getattr(user, "id", 0), "delivery.update", "delivery", row.id)
    db.commit()
    return {"ok": True, "delivery": _delivery_json(db, row)}


@router.get("/deliveries/{delivery_id}")
def delivery_detail(delivery_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    """交付单详情（含跟进时间线）。"""
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    _require_company(db, row.company_id, user)
    return {"delivery": _delivery_json(db, row, logs=True)}


class DeliveryLogIn(BaseModel):
    kind: str = "note"          # note|call|wechat|visit
    content: str = ""
    to_status: str = ""         # 可选：推进到哪个状态
    happened_at: str = ""


@router.post("/deliveries/{delivery_id}/log")
def add_delivery_log(delivery_id: int, body: DeliveryLogIn, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """交付跟进：写一条描述（可选同时推进状态），跟客户跟进一个路子。"""
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    company = _require_company(db, row.company_id, user)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="写点内容再提交")
    old_status = row.status
    if body.to_status and body.to_status in DELIVERY_LABEL and body.to_status != old_status:
        _delivery_touch_status(row, body.to_status)
    happened = (body.happened_at or "").strip() or date.today().isoformat()
    row.last_follow_at = happened
    db.add(MDeliveryLog(company_id=company.id, delivery_id=row.id, actor_user_id=getattr(user, "id", 0),
                        kind=body.kind or "note", content=content,
                        from_status=old_status, to_status=row.status, happened_at=happened))
    _audit(db, company.id, getattr(user, "id", 0), "delivery.log", "delivery", row.id)
    db.commit()
    return {"ok": True, "delivery": _delivery_json(db, row, logs=True)}


@router.delete("/deliveries/{delivery_id}")
def delete_delivery(delivery_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    company = _require_company(db, row.company_id, user)
    db.query(MDeliveryLog).filter(MDeliveryLog.delivery_id == delivery_id).delete()
    db.delete(row)
    _audit(db, company.id, getattr(user, "id", 0), "delivery.delete", "delivery", delivery_id)
    db.commit()
    return {"ok": True, "deleted": delivery_id}


# ───────────────────────── 工作记录 ─────────────────────────

class WorkLogIn(BaseModel):
    company_id: int
    content: str
    kind: str = "daily"
    minutes: int = 0
    membership_id: Optional[int] = None
    project_id: Optional[int] = None
    node_id: Optional[int] = None
    worked_on: str = ""


@router.get("/worklogs")
def list_worklogs(company_id: int, limit: int = 60, membership_id: Optional[int] = None,
                  user: Any = Depends(current_actor), db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    q = db.query(MWorkLog).filter(MWorkLog.company_id == company_id)
    if membership_id:
        q = q.filter(MWorkLog.membership_id == membership_id)
    rows = q.order_by(MWorkLog.id.desc()).limit(max(1, min(200, limit))).all()
    return {"worklogs": [{"id": r.id, "author": r.author_name, "kind": r.kind, "content": r.content,
                          "minutes": r.minutes, "project_id": r.project_id, "node_id": r.node_id,
                          "worked_on": r.worked_on or r.created_at.date().isoformat()} for r in rows]}


@router.post("/worklogs")
def create_worklog(body: WorkLogIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="请写点内容")
    membership = None
    if body.membership_id:
        membership = db.query(MMembership).filter(MMembership.id == body.membership_id).first()
    if not membership and getattr(user, "id", 0):
        membership = (db.query(MMembership)
                      .filter(MMembership.company_id == company.id, MMembership.user_id == user.id)
                      .first())
    author = membership.display_name if membership else getattr(user, "email", "管理员")
    row = MWorkLog(company_id=company.id, membership_id=membership.id if membership else None,
                   user_id=getattr(user, "id", 0) or None, author_name=author, kind=body.kind,
                   content=content, minutes=max(0, min(1440, int(body.minutes or 0))),
                   project_id=body.project_id, node_id=body.node_id,
                   worked_on=body.worked_on or date.today().isoformat())
    db.add(row)
    if membership:
        membership.load_pct = min(100, (membership.load_pct or 0))
    _audit(db, company.id, getattr(user, "id", 0), "worklog.create", "worklog", "")
    db.commit()
    return {"ok": True, "id": row.id, "author": author}

NODE_DONE = {"done", "completed", "finished", "accepted"}


ROBOT_ACTION_GROUPS = [
    ("video", "\u89c6\u9891\u5236\u4f5c", ["shanjian_digital_human_video", "local_bestseller_daily_video",
                                            "image_studio_generate", "viral_video_remix_start"]),
    ("publish", "\u5185\u5bb9\u53d1\u5e03", ["publish_content"]),
    ("leads", "\u83b7\u5ba2 / \u7ebf\u7d22", ["douyin_leads", "search_collect", "precise_touch"]),
    ("nurture", "\u517b\u53f7 / \u4e92\u52a8", ["account_nurture", "self_comment_monitor",
                                                  "native_wechat_moments_engage"]),
    ("wechat", "\u4e2a\u5fae\u627f\u63a5", ["native_wechat_poll", "native_wechat_add_friend", "stranger_message"]),
    ("content", "\u5185\u5bb9 / \u6587\u6848", ["ip_content_daily"]),
]
ROBOT_DONE = {"completed", "success", "succeeded", "done", "succeed"}
ROBOT_FAIL = {"failed", "error", "timeout", "canceled", "cancelled"}
# \u4e2d\u65ad / \u8df3\u8fc7\uff1a\u4e0d\u662f\u771f\u5931\u8d25\uff08\u5ba2\u6237\u7aef\u91cd\u542f\u3001\u8282\u70b9\u65f6\u95f4\u5230\u3001\u5de5\u4f5c\u6d41\u505c\u7528\u3001\u6b63\u5e38\u6536\u5de5\uff09
ROBOT_INTERRUPTED_HINTS = (
    "\u5df2\u4e2d\u65ad", "\u5df2\u91cd\u542f", "\u5f02\u5e38\u9000\u51fa", "\u957f\u65f6\u95f4\u672a\u4e0a\u62a5\u8fdb\u5ea6",
    "\u5df2\u505c\u7528", "\u5df2\u505c\u6b62", "\u8282\u70b9\u65f6\u95f4\u5df2\u7ed3\u675f", "\u8282\u70b9\u65f6\u95f4\u5df2\u5230",
    "\u5df2\u8fc7\u671f", "\u8d85\u8fc7\u6709\u6548\u6267\u884c\u65f6\u95f4", "\u6b63\u5e38\u6536\u5de5", "\u5df2\u81ea\u52a8\u505c\u6b62",
    "\u672c\u8f6e\u4efb\u52a1\u5df2\u7ed3\u675f", "\u8df3\u8fc7\uff1a", "skipped",
)
# \u5931\u8d25\u539f\u56e0\u5f52\u7c7b\uff08\u987a\u5e8f\u6709\u610f\u4e49\uff1a\u5148\u5339\u914d\u66f4\u5177\u4f53\u7684\uff09
ROBOT_FAIL_RULES = [
    ("unsupported", "\u5ba2\u6237\u7aef\u672a\u5b9e\u73b0\u8be5\u52a8\u4f5c", ("\u6682\u4e0d\u652f\u6301",)),
    ("server_5xx", "\u670d\u52a1\u7aef 5xx / \u7f51\u5173\u9519\u8bef", ("502", "503", "504", "Bad Gateway", "Service Unavailable")),
    ("client_bug", "\u5ba2\u6237\u7aef\u4ee3\u7801\u9519\u8bef\uff08\u65e7\u7248\uff09", ("has no attribute", "AttributeError", "Traceback", "TypeError")),
    ("login", "\u672a\u767b\u5f55 / \u9700\u626b\u7801", ("\u672a\u767b\u5f55", "\u626b\u7801", "\u8bf7\u5148\u767b\u5f55", "login")),
    ("window", "\u5ba2\u6237\u7aef\u4e3b\u7a97\u53e3 / \u5fae\u4fe1\u7a97\u53e3\u672a\u5c31\u7eea", ("\u672a\u627e\u5230\u5df2\u767b\u5f55", "\u8bfb\u53d6\u5fae\u4fe1\u4f1a\u8bdd\u5931\u8d25", "\u4e3b\u7a97\u53e3", "connect_over_cdp", "\u9a71\u52a8\u672a\u6210\u529f")),
    ("balance", "\u4f59\u989d / \u79ef\u5206\u4e0d\u8db3", ("\u4f59\u989d\u4e0d\u8db3", "\u79ef\u5206\u4e0d\u8db3", "\u6b20\u8d39")),
    ("profile", "\u8d44\u6599\u672a\u586b\u9f50", ("\u7f3a\u5c11", "\u8bf7\u5148\u8865\u5168", "\u672a\u586b\u5199", "\u9700\u8981\u5148\u914d\u7f6e")),
    ("publish_flag", "\u53d1\u5e03\u540e\u672a\u8bc6\u522b\u5230\u6210\u529f\u6807\u5fd7", ("\u672a\u68c0\u6d4b\u5230\u6210\u529f\u6807\u5fd7", "\u8bf7\u624b\u52a8\u786e\u8ba4")),
    ("timeout", "\u8d85\u65f6", ("Timeout", "timeout", "\u8d85\u65f6")),
    ("client_workflow_failed", "\u5ba2\u6237\u7aef\u5de5\u4f5c\u6d41\u5931\u8d25\uff08\u65e0\u7ec6\u8282\uff09", ("client workflow failed",)),
]


def _robot_fail_kind(text: str) -> str:
    """\u628a\u62a5\u9519\u5f52\u6210\u4e00\u7c7b\uff0c\u65b9\u4fbf\u9762\u677f\u544a\u8bc9\u4f60\u8be5\u4fee\u4ec0\u4e48\u3002"""
    blob = str(text or "")
    if not blob.strip():
        return "unknown"
    for key, _label, hints in ROBOT_FAIL_RULES:
        for h in hints:
            if h in blob:
                return key
    return "other"


def _robot_fail_label(kind: str) -> str:
    for key, label, _h in ROBOT_FAIL_RULES:
        if key == kind:
            return label
    return {"interrupted": "\u88ab\u4e2d\u65ad / \u8df3\u8fc7\uff08\u4e0d\u7b97\u5931\u8d25\uff09", "unknown": "\u65e0\u62a5\u9519\u4fe1\u606f",
            "other": "\u5176\u4ed6"}.get(kind, kind)


def _robot_is_interrupted(text: str) -> bool:
    blob = str(text or "")
    return any(h in blob for h in ROBOT_INTERRUPTED_HINTS)
ROBOT_ACTION_LABEL = {
    "shanjian_digital_human_video": "\u6570\u5b57\u4eba\u53e3\u64ad\u89c6\u9891",
    "local_bestseller_daily_video": "\u7206\u6b3e\u590d\u523b\u89c6\u9891",
    "image_studio_generate": "\u56fe\u6587\u7d20\u6750",
    "viral_video_remix_start": "\u89c6\u9891\u6df7\u526a",
    "publish_content": "\u53d1\u5e03\u5185\u5bb9",
    "douyin_leads": "\u6296\u97f3\u83b7\u5ba2",
    "search_collect": "\u641c\u7d22\u91c7\u96c6",
    "precise_touch": "\u7cbe\u51c6\u89e6\u8fbe",
    "account_nurture": "\u8d26\u53f7\u517b\u53f7",
    "self_comment_monitor": "\u8bc4\u8bba\u533a\u76d1\u63a7",
    "native_wechat_moments_engage": "\u670b\u53cb\u5708\u4e92\u52a8",
    "native_wechat_poll": "\u4e2a\u5fae\u6d88\u606f\u8f6e\u8be2",
    "native_wechat_add_friend": "\u4e2a\u5fae\u52a0\u597d\u53cb",
    "stranger_message": "\u964c\u751f\u4eba\u79c1\u4fe1",
    "ip_content_daily": "IP \u65e5\u66f4\u5185\u5bb9",
}


def _in_ints(ids: List[int]) -> str:
    vals = [int(x) for x in ids if x is not None]
    return "(" + (",".join(str(v) for v in vals) if vals else "-1") + ")"


def _in_strs(ids: List[str]) -> str:
    vals = [str(x).replace("'", "") for x in ids if x]
    return "(" + (",".join("'" + v + "'" for v in vals) if vals else "''") + ")"


def _robot_scope(db: Session, company: MCompany) -> Dict[str, Any]:
    """\u53e3\u5f84 = \u53ea\u8ba4\u300c\u865a\u62df\u5458\u5de5\u300d\u9875\u624b\u52a8\u6dfb\u52a0\u8fdb\u6765\u7684\u69fd\u4f4d\u3002

    \u4e0d\u518d\u628a\u300c\u5386\u53f2\u6267\u884c\u8bb0\u5f55\u91cc\u51fa\u73b0\u8fc7\u3001\u4f46\u6ca1\u6dfb\u52a0\u8fdb\u6765\u7684\u69fd\u4f4d\u300d\u7b97\u8fdb\u6765\uff08\u5ba2\u6237\u7aef\u91cd\u88c5/\u6362\u69fd\u4f4d\u540e\u7684\u50f5\u5c38\u69fd\u4f4d\uff09\uff0c
    \u8d26\u53f7\u4e5f\u53ea\u53d6\u300c\u8fd9\u4e9b\u5df2\u6dfb\u52a0\u69fd\u4f4d\u201d\u80cc\u540e\u7684\u8d26\u53f7\uff08\u6765\u81ea\u5fc3\u8df3\u91cc\u7684\u5f52\u5c5e\uff09\u3002
    """
    slot_rows = db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id).all()
    slots = {r.installation_id: r.name for r in slot_rows}
    # \u69fd\u4f4d\u80cc\u540e\u7684\u8d26\u53f7\uff1a\u7528\u4e8e\u8bbe\u5907\u7ea7\uff08\u6267\u884c\uff09\u7edf\u8ba1
    slot_uids: List[int] = []
    for inst in slots.keys():
        row = (db.query(H5ChatDevicePresence)
               .filter(H5ChatDevicePresence.installation_id == inst)
               .order_by(H5ChatDevicePresence.last_seen_at.desc()).first())
        if row is not None and row.user_id:
            slot_uids.append(int(row.user_id))
    # \u516c\u53f8\u6210\u5458\u8d26\u53f7\uff1a\u8d26\u53f7\u7ea7\u4ea7\u51fa\uff08\u53d1\u5e03/\u64ad\u653e\u3001\u7ebf\u7d22\u3001\u4e2a\u5fae\u3001\u79c1\u4fe1\u3001\u6210\u7247\uff09
    member_uids: List[int] = []
    for m in db.query(MMembership).filter(MMembership.company_id == company.id).all():
        if m.user_id:
            member_uids.append(int(m.user_id))
    if company.owner_user_id:
        member_uids.append(int(company.owner_user_id))
    return {"uids": sorted(set(slot_uids)), "member_uids": sorted(set(member_uids)), "slots": slots}
    uids: List[int] = []
    for m in db.query(MMembership).filter(MMembership.company_id == company.id).all():
        if m.user_id:
            uids.append(int(m.user_id))
    if company.owner_user_id:
        uids.append(int(company.owner_user_id))
    if int(getattr(company, "id", 0) or 0) == 1 and not uids:
        uids.append(0)
    slot_rows = db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id).all()
    slots = {r.installation_id: r.name for r in slot_rows}
    return {"uids": sorted(set(uids)), "slots": slots}


def _robot_day(dt: Any) -> str:
    if not dt:
        return ""
    try:
        return dt.date().isoformat()
    except Exception:
        return ""


def _robot_slots(db: Session, slot_map: Dict[str, str], by_slot: Dict[str, Dict[str, Any]],
                 slot_plays: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """\u865a\u62df\u5458\u5de5\u5217\u8868\uff1a\u5df2\u6dfb\u52a0\u7684\u69fd\u4f4d\u5168\u90e8\u5217\u51fa\uff08\u6ca1\u5e72\u6d3b\u4e5f\u5728\uff09+ \u5728\u7ebf\u72b6\u6001\u3002"""
    out: List[Dict[str, Any]] = []
    for inst, name in slot_map.items():
        stat = by_slot.get(inst, {"runs": 0, "ok": 0, "fail": 0})
        row, _, online, last_seen = _slot_presence(db, inst)
        # never = \u4ece\u6ca1\u4e0a\u62a5\u8fc7\u5fc3\u8df3\uff08\u5ba2\u6237\u7aef\u6ca1\u8fde\u8fc7 / \u69fd\u4f4d\u53f7\u5df2\u53d8\uff09
        status = "online" if online else ("never" if row is None else "offline")
        out.append({"installation_id": inst, "name": name or inst[:12], "runs": stat["runs"],
                    "ok": stat["ok"], "fail": stat["fail"], "online": online, "status": status,
                    "last_seen": (last_seen or "")[:19], "plays": int(slot_plays.get(inst, 0)),
                    "rate": round(stat["ok"] * 100.0 / stat["runs"], 1) if stat["runs"] else 0.0})
    out.sort(key=lambda x: (0 if x["online"] else 1, -x["runs"]))
    return out[:40]


@router.get("/robot-stats", summary="\u865a\u62df\u5458\u5de5\u6570\u636e\u9762\u677f\uff1a\u89c6\u9891/\u53d1\u5e03/\u7ebf\u7d22/\u4e2a\u5fae\u56de\u590d\u7b49\u6c47\u603b")
def robot_stats(company_id: int, window: str = Query("7d"), user: Any = Depends(current_actor),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """\u628a\u5404\u6761\u6267\u884c\u94fe\u8def\u7684\u4ea7\u51fa\u6c47\u5230\u4e00\u8d77\uff1a\u4e3b\u6570\u636e\u6765\u81ea\u4e3b\u7ad9\u6267\u884c\u8bb0\u5f55\u4e0e\u5404\u57df\u7ed3\u679c\u8868\uff0c
    \u53ea\u505a\u7edf\u8ba1\u4e0d\u6539\u6570\u636e\u3002\u7a97\u53e3\u540c\u300c\u6211\u7684\u4efb\u52a1\u300d\uff1a\u4eca\u65e5 / \u8fd1 7 \u5929 / \u8fd1 30 \u5929 / \u5168\u90e8\u3002"""
    company = _require_company(db, company_id, user)
    label, since = _ai_window(window)
    scope = _robot_scope(db, company)
    uids, slot_map = scope["uids"], scope["slots"]
    member_uids = scope.get("member_uids") or uids
    uin = _in_ints(uids)
    m_uin = _in_ints(member_uids)
    sin = _in_strs(list(slot_map.keys()))
    since_sql = " and created_at >= :since " if since else ""
    since_sql_pub = " and coalesce(published_at, first_seen_at, reported_at) >= :since " if since else ""
    since_sql_hap = " and happened_at >= :since " if since else ""
    params: Dict[str, Any] = {"since": since} if since else {}

    # \u6267\u884c\u8bb0\u5f55\u53ea\u770b\u5df2\u6dfb\u52a0\u7684\u69fd\u4f4d\uff1a\u69fd\u4f4d\u53f7\u53d8\u4e86\u7684\u65e7\u8bb0\u5f55\u4e0d\u518d\u7b97
    runs_scope = "installation_id in " + sin

    def rows(sql: str, **extra) -> List[Any]:
        return db.execute(text(sql), dict(params, **extra)).fetchall()

    # ---- \u6267\u884c\u8bb0\u5f55\uff08\u6309\u80fd\u529b / \u6309\u69fd\u4f4d / \u6309\u5929\uff09----
    by_action: Dict[str, Dict[str, int]] = {}
    reasons: Dict[Any, int] = {}
    reason_sample: Dict[Any, str] = {}
    reason_action_sample: Dict[Any, str] = {}
    for act, status, blob, cnt in rows(
            "select coalesce(result_payload->>'action', payload->>'action', task_kind) act, status, "
            "       coalesce(nullif(error, ''), left(coalesce(result_text, ''), 200), '') blob, count(*) "
            "from scheduled_task_runs where " + runs_scope + since_sql + " group by 1, 2, 3"):
        key = str(act or "other")
        item = by_action.setdefault(key, {"runs": 0, "ok": 0, "fail": 0, "interrupted": 0, "other": 0})
        n = int(cnt or 0)
        item["runs"] += n
        st = str(status or "").lower()
        # 注意：这里别用 text 当变量名——会遮蔽 sqlalchemy.text（rows() 闭包要用）
        err_text = str(blob or "")
        if st in ROBOT_DONE:
            item["ok"] += n
        elif st in ROBOT_FAIL:
            if _robot_is_interrupted(err_text):
                # \u5ba2\u6237\u7aef\u91cd\u542f / \u8282\u70b9\u65f6\u95f4\u5230 / \u5de5\u4f5c\u6d41\u505c\u7528 / \u6b63\u5e38\u6536\u5de5 -> \u4e0d\u7b97\u771f\u5931\u8d25
                item["interrupted"] += n
                kind = "interrupted"
            else:
                item["fail"] += n
                kind = _robot_fail_kind(err_text)
            rk = (key, kind)
            reasons[rk] = reasons.get(rk, 0) + n
            if rk not in reason_sample:
                reason_sample[rk] = err_text.replace("\n", " ")[:160] or "\uff08\u65e0\u62a5\u9519\u4fe1\u606f\uff09"
                reason_action_sample[rk] = key
        else:
            item["other"] += n

    by_slot: Dict[str, Dict[str, Any]] = {}
    for inst, status, cnt in rows(
            "select installation_id, status, count(*) from scheduled_task_runs where " + runs_scope + since_sql
            + " group by 1, 2"):
        key = str(inst or "")
        item = by_slot.setdefault(key, {"installation_id": key, "runs": 0, "ok": 0, "fail": 0,
                                        "name": slot_map.get(key, "")})
        n = int(cnt or 0)
        item["runs"] += n
        st = str(status or "").lower()
        if st in ROBOT_DONE:
            item["ok"] += n
        elif st in ("failed", "error", "timeout", "canceled", "cancelled"):
            item["fail"] += n

    day_runs: Dict[str, int] = {}
    for day, cnt in rows("select date_trunc('day', created_at + interval '8 hours') d, count(*) from scheduled_task_runs where "
                         + runs_scope + since_sql + " group by 1"):
        day_runs[_robot_day(day)] = int(cnt or 0)

    # ---- \u53d1\u5e03 ----
    # \u53d1\u5e03/\u64ad\u653e\u662f\u8d26\u53f7\u7ea7\u4ea7\u51fa\uff1a\u6309\u672c\u516c\u53f8\u8d26\u53f7\u7edf\u8ba1\uff08\u8bbe\u5907\u6ca1\u6dfb\u52a0\u8fdb\u865a\u62df\u5458\u5de5\u4e5f\u8981\u7b97\uff09
    pub_rows = rows("select coalesce(platform, ''), count(*) from publish_metrics where "
                    "(user_id in " + m_uin + " or installation_id in " + sin + ")" + since_sql_pub + " group by 1")
    published_by_platform = {str(k or "unknown"): int(v or 0) for k, v in pub_rows}
    published_total = sum(published_by_platform.values())
    day_pub: Dict[str, int] = {}
    if since_sql_pub:
        for day, cnt in rows("select date_trunc('day', coalesce(published_at, first_seen_at, reported_at) + interval '8 hours') d, count(*) "
                             "from publish_metrics where (user_id in " + m_uin + " or installation_id in " + sin + ")"
                             + since_sql_pub + " group by 1"):
            day_pub[_robot_day(day)] = int(cnt or 0)

    # ---- \u64ad\u653e\u91cf\uff08publish_metrics \u6bcf\u5929\u4e00\u6761\u5feb\u7167\uff0c\u5fc5\u987b\u6bcf\u6761\u89c6\u9891\u53d6\u6700\u65b0\u4e00\u6b21\u518d\u6c42\u548c\uff09----
    plays: Dict[str, Any] = {}
    top_videos: List[Dict[str, Any]] = []
    slot_plays: Dict[str, int] = {}
    try:
        latest = rows(
            "select platform, coalesce(title,''), published_at, installation_id, "
            "       coalesce(views,0), coalesce(likes,0), coalesce(comments,0), coalesce(shares,0), "
            "       coalesce(favorites,0), sampled_day "
            "from (select distinct on (coalesce(item_id, cast(id as varchar))) * "
            "      from publish_metrics where (user_id in " + m_uin + " or installation_id in " + sin + ") "
            "      order by coalesce(item_id, cast(id as varchar)), sampled_day desc nulls last, id desc) t")
        view_list = []
        by_platform: Dict[str, int] = {}
        for platform, title, published_at, inst, views, likes, comments, shares, favorites, sampled_day in latest:
            v = int(views or 0)
            view_list.append({"title": (title or "")[:40], "platform": platform or "",
                              "published_at": (published_at.isoformat(sep=" ")[:10] if published_at else ""),
                              "views": v, "likes": int(likes or 0), "comments": int(comments or 0),
                              "shares": int(shares or 0), "favorites": int(favorites or 0),
                              "sampled_day": str(sampled_day or "")})
            by_platform[str(platform or "unknown")] = by_platform.get(str(platform or "unknown"), 0) + v
            if inst:
                slot_plays[str(inst)] = slot_plays.get(str(inst), 0) + v
        view_list.sort(key=lambda x: -x["views"])
        plays = {
            "items": len(view_list),
            "items_with_views": sum(1 for x in view_list if x["views"] > 0),
            "views": sum(x["views"] for x in view_list),
            "likes": sum(x["likes"] for x in view_list),
            "comments": sum(x["comments"] for x in view_list),
            "shares": sum(x["shares"] for x in view_list),
            "favorites": sum(x["favorites"] for x in view_list),
            "avg_views": (int(sum(x["views"] for x in view_list) / len(view_list)) if view_list else 0),
            "max_views": (view_list[0]["views"] if view_list else 0),
            "sampled_day": (latest[0][9] if latest and latest[0][9] else ""),
            "by_platform": by_platform,
        }
        top_videos = view_list[:10]
    except Exception as exc:
        logger.warning("[MANAGE] robot-stats plays failed: %s", exc)

    # ---- \u7ebf\u7d22 / \u7cbe\u51c6\u5ba2\u6237 ----
    lead_rows = rows("select coalesce(source_platform, ''), count(*) from global_lead_crm_contacts "
                     "where user_id in " + m_uin + since_sql + " group by 1")
    leads_by_platform = {str(k or "unknown"): int(v or 0) for k, v in lead_rows}
    leads_total = sum(leads_by_platform.values())
    day_leads: Dict[str, int] = {}
    if since:
        for day, cnt in rows("select date_trunc('day', created_at + interval '8 hours') d, count(*) from global_lead_crm_contacts "
                             "where user_id in " + m_uin + since_sql + " group by 1"):
            day_leads[_robot_day(day)] = int(cnt or 0)

    # ---- \u4e2a\u5fae\u56de\u590d ----
    wx = {"reply_sent": 0, "failed": 0, "skipped": 0, "groups": 0, "queued": 0}
    day_wx: Dict[str, int] = {}
    for et, st, cnt in rows("select event_type, status, count(*) from wechat_interaction_outcomes where user_id in "
                            + m_uin + since_sql_hap + " group by 1, 2"):
        n = int(cnt or 0)
        et2, st2 = str(et or ""), str(st or "")
        if et2 == "reply_sent":
            wx["reply_sent"] += n
        elif et2 == "reply_skipped":
            wx["skipped"] += n
        elif et2 == "failed" or st2 == "failed":
            wx["failed"] += n
        elif et2.startswith("group_"):
            wx["groups"] += n
    if since:
        for day, cnt in rows("select date_trunc('day', happened_at + interval '8 hours') d, count(*) from wechat_interaction_outcomes "
                             "where user_id in " + m_uin + " and event_type = 'reply_sent'" + since_sql_hap + " group by 1"):
            day_wx[_robot_day(day)] = int(cnt or 0)
    wx_touched = int(rows("select count(*) from wechat_contact_memories where user_id in " + m_uin)[0][0] or 0)
    wx["contacts"] = wx_touched
    wx_handled = wx["reply_sent"] + wx["skipped"] + wx["failed"]
    wx["reply_rate"] = round(wx["reply_sent"] * 100.0 / wx_handled, 1) if wx_handled else 0.0
    recent_outcomes = [
        {"contact": r[0] or "", "inbound": (r[1] or "")[:120], "reply": (r[2] or "")[:160],
         "event": r[3] or "", "status": r[4] or "", "at": (r[5].isoformat(sep=" ")[:16] if r[5] else "")}
        for r in rows("select contact_name, inbound_text, reply_text, event_type, status, happened_at "
                      "from wechat_interaction_outcomes where user_id in " + m_uin + since_sql_hap
                      + " order by id desc limit 20")]

    # ---- H5 \u79c1\u4fe1 ----
    dm_total, dm_replied = 0, 0
    try:
        r0 = rows("select count(*), sum(case when coalesce(reply_text,'') <> '' then 1 else 0 end) "
                  "from h5_chat_messages where user_id in " + m_uin + since_sql)[0]
        dm_total, dm_replied = int(r0[0] or 0), int(r0[1] or 0)
    except Exception:
        pass

    # ---- \u89c6\u9891\u6210\u7247\uff08\u4e09\u4e2a\u6e20\u9053\uff09----
    def _counts(table: str, ok_status: List[str]) -> int:
        try:
            ok = ",".join("'" + s + "'" for s in ok_status)
            sql = ("select count(*) from " + table + " where user_id in " + m_uin + " and status in (" + ok + ")"
                   + (" and created_at >= :since" if since else ""))
            return int(rows(sql)[0][0] or 0)
        except Exception:
            return 0

    videos = {
        "shanjian": _counts("shanjian_digital_human_video_tasks", ["succeed", "success", "completed"]),
        "hifly": _counts("user_hifly_video_assets", ["success", "succeed", "completed"]),
        "wan": _counts("user_wan_role_tasks", ["success", "succeed", "completed"]),
    }
    videos["total"] = sum(videos.values())

    # ---- \u6309\u80fd\u529b\u5206\u7ec4 ----
    groups: List[Dict[str, Any]] = []
    grouped_actions: set = set()
    for key, glabel, actions in ROBOT_ACTION_GROUPS:
        agg = {"key": key, "label": glabel, "runs": 0, "ok": 0, "fail": 0, "interrupted": 0, "actions": []}
        for a in actions:
            grouped_actions.add(a)
            item = by_action.get(a)
            if not item:
                continue
            agg["runs"] += item["runs"]
            agg["ok"] += item["ok"]
            agg["fail"] += item["fail"]
            agg["interrupted"] += item.get("interrupted", 0)
            agg["actions"].append({"action": a, "label": ROBOT_ACTION_LABEL.get(a, a), **item})
        _eff = agg["ok"] + agg["fail"]
        agg["rate"] = round(agg["ok"] * 100.0 / _eff, 1) if _eff else 0.0
        groups.append(agg)
    other_runs = sum(v["runs"] for k, v in by_action.items() if k not in grouped_actions)
    other_ok = sum(v["ok"] for k, v in by_action.items() if k not in grouped_actions)
    other_fail = sum(v["fail"] for k, v in by_action.items() if k not in grouped_actions)
    if other_runs:
        groups.append({"key": "other", "label": "\u5176\u4ed6", "runs": other_runs, "ok": other_ok, "fail": other_fail,
                       "rate": round(other_ok * 100.0 / other_runs, 1), "actions": []})

    online_slots = sum(1 for inst in slot_map.keys() if _slot_presence(db, inst)[2])
    total_runs = sum(v["runs"] for v in by_action.values())
    total_ok = sum(v["ok"] for v in by_action.values())
    total_fail = sum(v["fail"] for v in by_action.values())
    total_interrupted = sum(v.get("interrupted", 0) for v in by_action.values())

    # ---- \u8fd1 14 \u5929\u8d8b\u52bf\uff08\u6267\u884c / \u53d1\u5e03 / \u7ebf\u7d22 / \u4e2a\u5fae\u56de\u590d\uff09----
    trend: List[Dict[str, Any]] = []
    trend_days = _robot_window_days(window)
    for i in range(trend_days - 1, -1, -1):
        d = (_today_beijing() - timedelta(days=i)).isoformat()
        trend.append({"day": d, "runs": day_runs.get(d, 0), "published": day_pub.get(d, 0),
                      "leads": day_leads.get(d, 0), "wechat": day_wx.get(d, 0)})
    trend_total = sum(x["runs"] for x in trend)

    return {
        "ok": True,
        "window": window, "window_label": label, "since": (since.isoformat() if since else ""),
        "company": company.name,
        "scope": {"members": len(member_uids), "slots": len(slot_map), "uids": len(member_uids),
                  "slots_online": online_slots, "slot_uids": len(uids)},
        "summary": {
            "videos": videos, "published": published_total, "published_by_platform": published_by_platform,
            "leads": leads_total, "leads_by_platform": leads_by_platform,
            "plays": plays,
            "wechat": wx, "dm_total": dm_total, "dm_replied": dm_replied,
            "dm_reply_rate": round(dm_replied * 100.0 / dm_total, 1) if dm_total else 0.0,
            "runs": total_runs, "runs_ok": total_ok, "runs_fail": total_fail,
            "runs_interrupted": total_interrupted,
            "success_rate": round(total_ok * 100.0 / (total_ok + total_fail), 1) if (total_ok + total_fail) else 0.0,
            "fail_rate": round(total_fail * 100.0 / (total_ok + total_fail), 1) if (total_ok + total_fail) else 0.0,
            "interrupt_rate": round(total_interrupted * 100.0 / total_runs, 1) if total_runs else 0.0,
        },
        "groups": groups,
        "fail_reasons": [
            {"action": k[0], "action_label": ROBOT_ACTION_LABEL.get(k[0], k[0]), "kind": k[1],
             "label": _robot_fail_label(k[1]), "count": v, "sample": reason_sample.get(k, "")}
            for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:12]
        ],
        "top_videos": top_videos,
        "slot_plays": slot_plays,
        "slots": _robot_slots(db, slot_map, by_slot, slot_plays),
        "trend": trend, "trend_days": trend_days, "trend_total_runs": trend_total,
        "tz_label": "\u5317\u4eac\u65f6\u95f4",
        "recent_outcomes": recent_outcomes,
        "sources": [
            "\u8bbe\u5907\u4ea7\u51fa\uff08\u6267\u884c\uff09\uff1a\u53ea\u7b97\u300c\u865a\u62df\u5458\u5de5\u300d\u91cc\u5df2\u6dfb\u52a0\u7684\u8bbe\u5907",
            "\u8d26\u53f7\u4ea7\u51fa\uff08\u53d1\u5e03 / \u64ad\u653e / \u7ebf\u7d22 / \u4e2a\u5fae\u56de\u590d / \u79c1\u4fe1 / \u6210\u7247\uff09\uff1a\u6309\u672c\u516c\u53f8\u8d26\u53f7\u7edf\u8ba1",
            "\u6267\u884c\uff1ascheduled_task_runs\uff08\u6309\u69fd\u4f4d + \u80fd\u529b action\uff09",
            "\u53d1\u5e03 / \u64ad\u653e\uff1apublish_metrics\uff08\u6bcf\u5929\u4e00\u6761\u5feb\u7167\uff0c\u9762\u677f\u53d6\u6bcf\u6761\u89c6\u9891\u6700\u65b0\u4e00\u6b21\u91c7\u6837\u6c42\u548c\uff09",
            "\u7cbe\u51c6\u5ba2\u6237\uff1aglobal_lead_crm_contacts\uff08created_at\uff09",
            "\u4e2a\u5fae\u56de\u590d\uff1awechat_interaction_outcomes\uff08reply_sent / failed / skipped\uff09 + wechat_contact_memories",
            "\u79c1\u4fe1\uff1ah5_chat_messages\uff08content / reply_text\uff09",
            "\u89c6\u9891\u6210\u7247\uff1ashanjian_digital_human_video_tasks / user_hifly_video_assets / user_wan_role_tasks",
        ],
        "note": "\u53ea\u7edf\u8ba1\u672c\u516c\u53f8\u6210\u5458\u4e0e\u5df2\u6dfb\u52a0\u7684\u865a\u62df\u5458\u5de5\u69fd\u4f4d\u4ea7\u751f\u7684\u6570\u636e\uff1b\u7a97\u53e3\u4e0e\u300c\u6211\u7684\u4efb\u52a1\u300d\u4e00\u81f4\u3002",
    }


@router.get("/team-activity")
def team_activity(company_id: int, window: str = Query("1d"), user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """首页简报用：每个员工本窗口的工作情况 + 虚拟员工本窗口的产出。

    都按时间窗统计（默认今日），不做「从建档开始」的累计；换窗口＝换输入，不改历史。
    """
    company = _require_company(db, company_id, user)
    label, since = _ai_window(window)
    since_date = since.date().isoformat() if since else ""
    member_by_uid: Dict[int, Any] = {}
    members = db.query(MMembership).filter(MMembership.company_id == company.id).order_by(MMembership.id).all()
    for m in members:
        if m.user_id:
            member_by_uid[int(m.user_id)] = m
    people = []
    for m in members:
        q = db.query(MWorkLog).filter(MWorkLog.company_id == company.id, MWorkLog.membership_id == m.id)
        if since_date:
            q = q.filter(MWorkLog.worked_on >= since_date)
        logs = q.order_by(MWorkLog.id.desc()).limit(200).all()
        minutes = sum(int(x.minutes or 0) for x in logs)
        latest = logs[0] if logs else None
        nodes = (db.query(MPlanNode)
                 .filter(MPlanNode.company_id == company.id,
                         MPlanNode.owner_membership_id == m.id).all())
        done = sum(1 for n in nodes if str(n.status or "").strip().lower() in NODE_DONE)
        roles = [r.role_code for r in db.query(MMembershipRole)
                 .filter(MMembershipRole.membership_id == m.id).all()]
        people.append({"membership_id": m.id, "name": m.display_name, "dept": m.dept or "",
                       "roles": roles, "load": m.load_pct or 0,
                       "logs": len(logs), "minutes": minutes,
                       "last_at": (latest.created_at.isoformat() if latest and latest.created_at else ""),
                       "last_on": (latest.worked_on if latest else ""),
                       "last_content": ((latest.content or "")[:140] if latest else ""),
                       "nodes_total": len(nodes), "nodes_done": done, "nodes_open": len(nodes) - done})
    ai_rows = [_ai_row_json(db, company, r, since, member_by_uid)
               for r in db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id)
               .order_by(MAiEmployee.id).all()]
    return {"window": window, "window_label": label, "since": since_date,
            "people": people, "ai": ai_rows,
            "people_logs": sum(p["logs"] for p in people),
            "people_minutes": sum(p["minutes"] for p in people),
            "ai_summary": {"dispatched": sum(a["dispatched"] for a in ai_rows),
                           "succeeded": sum(a["succeeded"] for a in ai_rows),
                           "failed": sum(a["failed"] for a in ai_rows),
                           "running": sum(a["running"] for a in ai_rows)},
            "ai_online": sum(1 for a in ai_rows if a["online"]), "ai_total": len(ai_rows)}


# ───────────────────────── 虚拟员工（原系统槽位） ─────────────────────────

ONLINE_WINDOW = timedelta(minutes=5)
MAIN_BACKEND = "http://127.0.0.1:8000"


def _slot_online(db: Session, user_id: int, installation_id: str) -> tuple:
    row = (db.query(H5ChatDevicePresence)
           .filter(H5ChatDevicePresence.user_id == user_id,
                   H5ChatDevicePresence.installation_id == installation_id).first())
    if not row or not row.last_seen_at:
        return False, ""
    online = (datetime.utcnow() - row.last_seen_at) <= ONLINE_WINDOW
    return online, row.last_seen_at.isoformat()


MANAGE_AI_WINDOWS = {"1d": ("今日", 1), "7d": ("近 7 天", 7), "30d": ("近 30 天", 30), "all": ("全部", 0)}
DISPATCH_DONE = {"completed", "success", "succeeded", "done", "finished", "ok"}
DISPATCH_BAD = {"failed", "error", "timeout", "canceled", "cancelled"}


def _ai_window(key: str) -> tuple:
    """把窗口串转成 (中文标签, 起始时间)；all 返回 None 表示不设下限。

    起点按「北京时间当天 00:00」算，再换算成列里存的 UTC 时间（否则早上 8 点前的记录会算到前一天）。
    """
    label, days = MANAGE_AI_WINDOWS.get(str(key or "1d").strip().lower(), MANAGE_AI_WINDOWS["1d"])
    if not days:
        return label, None
    start_bj = _today_beijing() - timedelta(days=days - 1)
    start_utc = datetime(start_bj.year, start_bj.month, start_bj.day) - timedelta(hours=TIMELINE_TZ_OFFSET_HOURS)
    return label, start_utc


def _robot_window_days(key: str) -> int:
    """趋势图画多少根柱：跟窗口一致；all 最多 30 天。"""
    _label, days = MANAGE_AI_WINDOWS.get(str(key or "1d").strip().lower(), MANAGE_AI_WINDOWS["1d"])
    return 1 if days == 1 else (days if days else 30)


def _slot_presence(db: Session, installation_id: str) -> tuple:
    """按槽位号取最近一次心跳（不限归属人）。返回 (presence 行, remote_support 字典, 在线, 最后心跳)。"""
    if not installation_id:
        return None, {}, False, ""
    row = (db.query(H5ChatDevicePresence)
           .filter(H5ChatDevicePresence.installation_id == installation_id)
           .order_by(H5ChatDevicePresence.last_seen_at.desc()).first())
    if not row:
        return None, {}, False, ""
    payload = row.account_payload if isinstance(row.account_payload, dict) else {}
    remote = payload.get("remote_support") if isinstance(payload.get("remote_support"), dict) else {}
    online = bool(row.last_seen_at and (datetime.utcnow() - row.last_seen_at) <= ONLINE_WINDOW)
    return row, remote, online, (row.last_seen_at.isoformat() if row.last_seen_at else "")


# 中继返回的设备在线判定必须「宽进」：中继在设备在线时返回 deviceView（顶层 online=true，
# 且不带 lastDevice 字段），只有设备离线时才回落到绑定时留下的快照（lastDevice.online=false）。
# 历史实现只读 lastDevice.online，结果把在线设备一律判成离线（远程弹窗永远提示「不在线」）。
RELAY_DEVICE_ONLINE_GRACE_SECONDS = 180


def _relay_device_online(bound: Optional[Dict[str, Any]]) -> bool:
    """设备在中继上是否在线：顶层 online → lastDevice.online → lastSeen 时间窗。"""
    if not isinstance(bound, dict):
        return False
    if bound.get("online") is True:
        return True
    last = bound.get("lastDevice")
    if isinstance(last, dict) and last.get("online") is True:
        return True
    try:
        seen_ms = int(float(bound.get("lastSeen") or 0))
    except (TypeError, ValueError):
        seen_ms = 0
    if seen_ms > 0 and (time.time() * 1000 - seen_ms) <= RELAY_DEVICE_ONLINE_GRACE_SECONDS * 1000:
        return True
    return False


def _relay_last_seen_text(bound: Optional[Dict[str, Any]]) -> str:
    """把中继的 lastSeen(ms) 转成北京时间文案，便于排障。"""
    try:
        seen_ms = int(float((bound or {}).get("lastSeen") or 0))
    except (TypeError, ValueError):
        seen_ms = 0
    if seen_ms <= 0:
        return "从未在线"
    return (datetime.utcfromtimestamp(seen_ms / 1000) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _remote_support_call(method: str, path: str, body: Optional[dict] = None) -> dict:
    """以平台管理员身份调用远程支持中继（BHZN-ToDesk 服务）。"""
    base = str(getattr(settings, "remote_support_service_url", "http://127.0.0.1:38080") or "").rstrip("/")
    key = str(getattr(settings, "remote_support_service_key", "") or "").strip()
    password = str(getattr(settings, "lobster_admin_password", "") or "").strip()
    if not key or not password:
        raise HTTPException(status_code=503, detail="远程支持未配置（缺少服务密钥）")
    headers = {"Authorization": "Bearer " + ADMIN_TOKEN_PREFIX + password,
               "X-Remote-Service-Key": key, "X-Lobster-Brand": "bihuo"}
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.request(method, base + path, headers=headers, json=body)
        data = resp.json() if resp.content else {}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="远程支持服务不可用: " + str(exc)[:160]) from exc
    if resp.status_code >= 400:
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("detail") or "")
        raise HTTPException(status_code=resp.status_code if resp.status_code >= 400 else 502,
                            detail="远程支持： " + (detail or ("HTTP " + str(resp.status_code))))
    return data if isinstance(data, dict) else {}


def _dispatch_agg(db: Session, company_id: int, ai_id: int, since) -> Dict[str, Any]:
    """一台设备在时间窗内的派活汇总（不从头累计）。"""
    q = db.query(MDispatch).filter(MDispatch.company_id == company_id,
                                   MDispatch.ai_employee_id == ai_id)
    if since is not None:
        q = q.filter(MDispatch.created_at >= since)
    rows = q.order_by(MDispatch.id.desc()).limit(200).all()
    ok = fail = other = 0
    for r in rows:
        st = str(r.status or "").strip().lower()
        if st in DISPATCH_DONE:
            ok += 1
        elif st in DISPATCH_BAD:
            fail += 1
        else:
            other += 1
    latest = rows[0] if rows else None
    node = db.query(MPlanNode).filter(MPlanNode.id == latest.node_id).first() if latest else None
    return {"dispatched": len(rows), "succeeded": ok, "failed": fail, "running": other,
            "last_at": (latest.created_at.isoformat() if latest and latest.created_at else ""),
            "last_node": (node.title if node else ""), "last_status": (latest.status if latest else ""),
            "last_error": ((latest.error or "")[:160] if latest else "")}


def _ai_row_json(db: Session, company: MCompany, row: MAiEmployee, since,
                 member_by_uid: Dict[int, Any]) -> Dict[str, Any]:
    presence, remote, online, last_seen = _slot_presence(db, row.installation_id)
    member = None
    if presence is not None and presence.user_id:
        member = member_by_uid.get(int(presence.user_id))
    device_id = (row.device_id or "").strip().upper() or str(remote.get("device_id") or "").strip().upper()
    agg = _dispatch_agg(db, company.id, row.id, since)
    # 设备备注（H5 上给设备起的名字）：按机器身份保存，槽位 ID 变了也还在；
    # 这台机器没备注时给一条「沿用建议」（同账号下最近改过、且那条槽位已离线）。
    presence_user_id = int(presence.user_id) if (presence is not None and presence.user_id) else 0
    device_label, device_label_source = "", "none"
    suggested_label = None
    suggested_labels: list = []
    if presence_user_id and row.installation_id:
        device_label, device_label_source = device_labels.resolve_device_label(
            db, presence_user_id, row.installation_id
        )
        if not device_label:
            # 机器身份也换过（不只是槽位变）时连不上，这时把最近改过的备注列出来让老板直接选
            suggested_labels = device_labels.suggest_device_labels(
                db, user_id=presence_user_id, installation_id=row.installation_id
            )
            suggested_label = suggested_labels[0] if suggested_labels else None
    return {"id": row.id, "name": row.name, "installation_id": row.installation_id,
            "device_id": device_id, "source": (row.source or "slot"), "note": (row.note or ""),
            "online": online, "last_seen": last_seen, "status": row.status,
            "device_label": device_label,
            "device_label_source": device_label_source,
            "suggested_label": suggested_label,
            "suggested_labels": suggested_labels,
            "capabilities": row.capabilities or [],
            "owner_name": (member.display_name if member else ""),
            "owner_label": ("属于 " + member.display_name) if member else "",
            "remote_enabled": bool(remote.get("enabled")),
            "remote_running": bool(remote.get("running")),
            "remote_bound": bool(device_id),
            "can_remote": bool(device_id),
            "dispatched": agg["dispatched"], "succeeded": agg["succeeded"],
            "failed": agg["failed"], "running": agg["running"],
            "last_at": agg["last_at"], "last_node": agg["last_node"],
            "last_status": agg["last_status"], "last_error": agg["last_error"]}


@router.get("/ai-employees")
def list_ai_employees(company_id: int, online_only: bool = Query(False), window: str = Query("1d"),
                      user: Any = Depends(current_actor),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    """虚拟员工＝手动添加进来的设备（不再从组织架构成员自动派生）。

    · 添加过的设备一直显示，离线也在（带在线/离线状态与最后心跳）
    · 槽位号刚好属于某个员工时只打一个「属于 xxx」标签，不再建立归属关系
    · 执行情况按时间窗汇总，不做「从建档开始」的累计
    """
    company = _require_company(db, company_id, user)
    label, since = _ai_window(window)
    member_by_uid: Dict[int, Any] = {}
    for m in db.query(MMembership).filter(MMembership.company_id == company.id).all():
        if m.user_id:
            member_by_uid[int(m.user_id)] = m
    rows = (db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id)
            .order_by(MAiEmployee.id).all())
    out = [_ai_row_json(db, company, r, since, member_by_uid) for r in rows]
    online_count = sum(1 for x in out if x["online"])
    totals = {"dispatched": 0, "succeeded": 0, "failed": 0, "running": 0}
    for x in out:
        for k in totals:
            totals[k] += int(x.get(k) or 0)
    if online_only:
        out = [x for x in out if x["online"]]
    return {"ai_employees": out, "online_count": online_count, "total": len(out),
            "window": window, "window_label": label, "since": (since.isoformat() if since else ""),
            "summary": totals}


class AiEmployeeIn(BaseModel):
    company_id: int
    installation_id: Optional[str] = None
    device_id: Optional[str] = None
    verification_code: Optional[str] = None
    name: Optional[str] = None
    note: Optional[str] = None
    capabilities: Optional[List[str]] = None


@router.post("/ai-employees")
def create_ai_employee(body: AiEmployeeIn, user: Any = Depends(current_actor),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """手动添加虚拟员工。两种标识二选一：

    1) 槽位号 installation_id —— 最省事，适合自己人；
    2) 设备号 + 验证码 —— 远程客户端窗口上当前显示的设备号与验证码，
       只有真机才知道，用来防止槽位号被别人冒领。
    """
    company = _require_company(db, body.company_id, user)
    _require_plan_admin(db, company, user)
    slot = (body.installation_id or "").strip()
    device_id = (body.device_id or "").strip().upper()
    code = (body.verification_code or "").strip()
    source = "slot"
    if device_id:
        hit = None
        for cand in (db.query(H5ChatDevicePresence)
                     .order_by(H5ChatDevicePresence.last_seen_at.desc()).limit(500).all()):
            payload = cand.account_payload if isinstance(cand.account_payload, dict) else {}
            remote = payload.get("remote_support") if isinstance(payload.get("remote_support"), dict) else {}
            if str(remote.get("device_id") or "").strip().upper() == device_id:
                hit = (cand, remote)
                break
        if hit is None:
            raise HTTPException(status_code=404,
                                detail="没找到设备号 " + device_id + "：该设备要先运行远程客户端并上报过心跳")
        cand, remote = hit
        if not code or str(remote.get("verification_code") or "").strip() != code:
            raise HTTPException(status_code=403, detail="验证码不对：请填该设备远程窗口上当前显示的验证码")
        slot = cand.installation_id
        source = "device"
    if not slot:
        raise HTTPException(status_code=400, detail="请填槽位号，或填设备号 + 验证码")
    presence, remote, online, last_seen = _slot_presence(db, slot)
    if presence is None and source == "slot":
        raise HTTPException(status_code=404, detail="槽位号不存在：该设备要先登录客户端上报过心跳")
    if not device_id and presence is not None:
        device_id = str(remote.get("device_id") or "").strip().upper()
    existing = (db.query(MAiEmployee)
                .filter(MAiEmployee.company_id == company.id,
                        MAiEmployee.installation_id == slot).first())
    default_name = "虚拟员工 · " + ((presence.display_name if presence and presence.display_name else slot[:6]))
    if existing is None:
        existing = MAiEmployee(company_id=company.id,
                               name=(body.name or "").strip() or default_name,
                               installation_id=slot, owner_membership_id=None,
                               device_id=device_id, source=source,
                               note=(body.note or "").strip()[:255],
                               capabilities=[str(x).strip() for x in (body.capabilities or []) if str(x).strip()],
                               created_by=getattr(user, "id", 0))
        db.add(existing)
        db.flush()
        action = "ai_employee.create"
    else:
        if (body.name or "").strip():
            existing.name = body.name.strip()
        if device_id:
            existing.device_id = device_id
        existing.source = source
        if body.note is not None:
            existing.note = (body.note or "").strip()[:255]
        if body.capabilities is not None:
            existing.capabilities = [str(x).strip() for x in body.capabilities if str(x).strip()]
        action = "ai_employee.update"
    remote_bound, remote_error = False, ""
    if device_id and source == "device":
        try:
            _remote_support_call("POST", "/api/remote-admin/devices",
                                 {"deviceId": device_id, "verificationCode": code, "label": existing.name})
            remote_bound = True
        except HTTPException as exc:
            remote_error = str(exc.detail)[:200]
    _audit(db, company.id, getattr(user, "id", 0), action, "ai_employee", existing.id,
           {"slot": slot, "device_id": device_id, "source": source, "remote_bound": remote_bound})
    db.commit()
    return {"ok": True, "id": existing.id, "installation_id": slot, "device_id": device_id,
            "source": source, "online": online, "last_seen": last_seen,
            "remote_bound": remote_bound, "remote_error": remote_error}


@router.post("/ai-employees/{ai_id}/remote-session")
def ai_employee_remote_session(ai_id: int, user: Any = Depends(current_actor),
                               db: Session = Depends(get_db)) -> Dict[str, Any]:
    """为这台虚拟员工开一个远程会话：管理端做控制方，弹窗里直接看远程画面。"""
    row = db.query(MAiEmployee).filter(MAiEmployee.id == ai_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="虚拟员工不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    _, remote, online, last_seen = _slot_presence(db, row.installation_id)
    device_id = (row.device_id or "").strip().upper() or str(remote.get("device_id") or "").strip().upper()
    if not device_id:
        raise HTTPException(status_code=400,
                            detail="这台设备没绑定远程设备号：用「设备号 + 验证码」重新添加一次即可")
    devices = _remote_support_call("GET", "/api/remote-admin/devices")
    bound = None
    for item in (devices.get("devices") or []):
        if str(item.get("deviceId") or item.get("id") or "").strip().upper() == device_id:
            bound = item
            break
    if bound is None:
        raise HTTPException(status_code=409, detail=(
            "这台设备的远程客户端还没连上中继（设备号 " + device_id
            + "）：让对方打开远程客户端，或者用「设备号 + 验证码」重新添加一次"))
    if not _relay_device_online(bound):
        raise HTTPException(status_code=409, detail=(
            "远程客户端当前不在线（设备号 " + device_id
            + "，最后一次在线 " + _relay_last_seen_text(bound)
            + "）：让对方打开远程客户端再试；若对方机器上远程开关是关的，先在客户端系统配置里打开"))
    data = _remote_support_call("POST", "/api/remote-admin/controller-session")
    public_url = str(getattr(settings, "remote_support_public_url", "") or "").rstrip("/")
    _audit(db, company.id, getattr(user, "id", 0), "ai_employee.remote", "ai_employee", row.id,
           {"device_id": device_id, "online": online})
    db.commit()
    return {"ok": True, "public_url": public_url, "token": str(data.get("token") or ""),
            "expires_at": str(data.get("expiresAt") or ""), "device_id": device_id,
            "online": online, "name": row.name, "last_seen": last_seen}


class AiEmployeePatchIn(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None
    capabilities: Optional[List[str]] = None
    status: Optional[str] = None


class DeviceLabelIn(BaseModel):
    """设备备注名（H5 设备列表里显示的名字）；空字符串表示清除。"""

    display_name: str = Field(default="", max_length=128)


@router.post("/ai-employees/{ai_id}/device-label", summary="设置/清除这台设备的备注名（按机器身份保存）")
def set_ai_employee_device_label(
    ai_id: int,
    body: DeviceLabelIn,
    user: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """给设备起名（备注）。名字存两份：

    · ``user_device_labels``：按 machine_instance_id 保存 —— 客户端换槽位（换账号/换品牌/
      OTA 后新的签名槽位）后仍自动套用，不会再退回默认名字；
    · ``h5_chat_device_presence.display_name``：当前槽位那行，H5 设备列表直接用。

    没有心跳/槽位记录的设备（例如只按设备号+验证码添加的远程设备）会提示先让设备上线。
    """
    row = db.query(MAiEmployee).filter(MAiEmployee.id == ai_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="虚拟员工不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    presence, _remote, _online, _last_seen = _slot_presence(db, row.installation_id)
    owner_user_id = int(presence.user_id) if (presence is not None and presence.user_id) else 0
    if not owner_user_id:
        raise HTTPException(
            status_code=409,
            detail="这台设备还没有心跳记录，先让客户端上线（打开客户端）再改设备名",
        )
    name = (body.display_name or "").strip()[:128]
    if name:
        device_labels.remember_device_label(
            db, user_id=owner_user_id, installation_id=row.installation_id,
            display_name=name, source="manual",
        )
        presence.display_name = name
        db.add(presence)
        device_labels.apply_label_to_machine_slots(
            db, user_id=owner_user_id, installation_id=row.installation_id, display_name=name
        )
    else:
        presence.display_name = None
        db.add(presence)
        existing = (
            db.query(UserDeviceLabel)
            .filter(
                UserDeviceLabel.user_id == owner_user_id,
                UserDeviceLabel.last_installation_id == row.installation_id,
            )
            .first()
        )
        if existing is not None:
            db.delete(existing)
    _audit(db, company.id, getattr(user, "id", 0), "ai_employee.device_label", "ai_employee",
           row.id, {"installation_id": row.installation_id, "display_name": name})
    db.commit()
    return {
        "ok": True,
        "ai_id": row.id,
        "installation_id": row.installation_id,
        "device_label": name,
        "note": "设备名已按机器身份保存：以后这台机器换槽位（换账号/换品牌/OTA）也会自动沿用",
    }


@router.patch("/ai-employees/{ai_id}")
def update_ai_employee(ai_id: int, body: AiEmployeePatchIn, user: Any = Depends(current_actor),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MAiEmployee).filter(MAiEmployee.id == ai_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="虚拟员工不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    if body.name is not None:
        row.name = body.name.strip() or row.name
    if body.capabilities is not None:
        row.capabilities = [str(x).strip() for x in body.capabilities if str(x).strip()]
    if body.note is not None:
        row.note = (body.note or "").strip()[:255]
    if body.status is not None:
        row.status = body.status
    db.commit()
    return {"ok": True}


@router.delete("/ai-employees/{ai_id}")
def delete_ai_employee(ai_id: int, user: Any = Depends(current_actor),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MAiEmployee).filter(MAiEmployee.id == ai_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="虚拟员工不存在")
    company = _require_company(db, row.company_id, user)
    _require_plan_admin(db, company, user)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": ai_id}


def _first_id(obj: Any) -> str:
    found = {"v": ""}

    def walk(x: Any) -> None:
        if found["v"]:
            return
        if isinstance(x, dict):
            for key in ("task_id", "id", "run_id"):
                v = x.get(key)
                if isinstance(v, (str, int)) and str(v).strip():
                    found["v"] = str(v)
                    return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found["v"]


class DispatchIn(BaseModel):
    ai_employee_id: int


@router.post("/nodes/{node_id}/dispatch")
async def dispatch_node(node_id: int, body: DispatchIn, user: Any = Depends(current_actor),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """把工作安排里的一个节点派给虚拟员工（走主站定时任务链路）。"""
    node = db.query(MPlanNode).filter(MPlanNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    company = _require_company(db, node.company_id, user)
    _require_plan_admin(db, company, user)
    ai = (db.query(MAiEmployee)
          .filter(MAiEmployee.id == body.ai_employee_id, MAiEmployee.company_id == company.id).first())
    if not ai:
        raise HTTPException(status_code=404, detail="虚拟员工不存在（先在公司里绑定该账号并登录过客户端）")
    project = db.query(MProject).filter(MProject.id == node.project_id).first()
    token = _llm_token_for_company(db, company)

    task_payload = {
        "title": "[项目] " + (project.name if project else "") + " · " + node.title,
        "task_kind": "chat_message",
        "content": ("请完成项目节点：\n" + node.title + "\n\n要求：" + (node.requirement or "（未填写）")
                    + "\n时间节点：" + (node.start_at or "?") + " ~ " + (node.end_at or "?")
                    + (("\n项目目标：" + project.goal) if project and project.goal else "")),
        "schedule_type": "once",
        "installation_ids": [ai.installation_id],
        "payload": {"manage": {"company_id": company.id,
                               "project_id": node.project_id, "node_id": node.id}},
    }
    # 主站建任务要求带当前设备槽位（派给谁就带谁的 installation_id）
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
               "X-Installation-Id": ai.installation_id}
    error = ""
    task_id = ""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(MAIN_BACKEND + "/api/scheduled-tasks/tasks",
                                     json=task_payload, headers=headers)
            if resp.status_code >= 400:
                error = "建任务失败 HTTP " + str(resp.status_code) + ": " + (resp.text or "")[:200]
            else:
                data = resp.json() if resp.content else {}
                task_id = _first_id(data)
                if task_id:
                    run_resp = await client.post(
                        MAIN_BACKEND + "/api/scheduled-tasks/tasks/" + task_id + "/run-now",
                        json={}, headers=headers)
                    if run_resp.status_code >= 400:
                        error = "派单失败 HTTP " + str(run_resp.status_code) + ": " + (run_resp.text or "")[:200]
                else:
                    error = "建任务返回里没找到任务 id：" + json.dumps(data, ensure_ascii=False)[:200]
    except Exception as exc:
        error = "调用主站失败：" + str(exc)[:200]

    record = MDispatch(company_id=company.id, project_id=node.project_id, node_id=node.id,
                       ai_employee_id=ai.id, installation_id=ai.installation_id,
                       task_id=task_id, status="requested" if not error else "failed", error=error)
    db.add(record)
    if not error:
        node.owner_kind = "virtual_employee"
        node.owner_ai_employee_id = ai.id
        node.owner_name = ai.name
        if node.status == "not_started":
            node.status = "in_progress"
        if project and project.arrangement_status == "confirmed":
            project.arrangement_status = "changed"
    _audit(db, company.id, getattr(user, "id", 0), "node.dispatch", "plan_node", node.id,
           {"ai": ai.id, "task_id": task_id, "error": error})
    db.commit()
    if error:
        raise HTTPException(status_code=502, detail=error)
    return {"ok": True, "task_id": task_id, "ai_employee": ai.name,
            "node": _node_json(node)}


@router.get("/nodes/{node_id}/execution")
async def node_execution(node_id: int, user: Any = Depends(current_actor),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    """回读该节点派给虚拟员工后的执行情况（含执行明细 targets_detail）。"""
    node = db.query(MPlanNode).filter(MPlanNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    company = _require_company(db, node.company_id, user)
    rec = (db.query(MDispatch).filter(MDispatch.node_id == node_id)
           .order_by(MDispatch.id.desc()).first())
    if not rec:
        return {"dispatched": False}
    token = _llm_token_for_company(db, company)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    runs = []
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(MAIN_BACKEND + "/api/scheduled-tasks/runs",
                                    params={"limit": 50}, headers=headers)
            if resp.status_code < 400:
                data = resp.json() if resp.content else {}
                runs = data.get("runs") or []
    except Exception as exc:
        return {"dispatched": True, "task_id": rec.task_id, "status": rec.status,
                "error": "读取执行记录失败：" + str(exc)[:160]}
    mine = []
    for r in runs:
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        manage = payload.get("manage") if isinstance(payload.get("manage"), dict) else {}
        if str(manage.get("node_id") or "") == str(node_id):
            mine.append(r)
    latest = mine[0] if mine else None
    if latest:
        rec.run_id = str(latest.get("id") or "")
        rec.status = str(latest.get("status") or rec.status)
        db.commit()
    return {"dispatched": True, "task_id": rec.task_id, "installation_id": rec.installation_id,
            "status": (latest or {}).get("status") or rec.status,
            "error": rec.error,
            "run": ({"id": latest.get("id"), "status": latest.get("status"),
                     "progress": latest.get("progress") or {},
                     "result_text": latest.get("result_text") or "",
                     "targets_detail": ((latest.get("result_payload") or {}).get("targets_detail")
                                        if isinstance(latest.get("result_payload"), dict) else None)}
                    if latest else None)}


# ───────────────────────── 规划版本（对比 / 回滚） ─────────────────────────

def _snapshot_project(db: Session, project: MProject) -> Dict[str, Any]:
    nodes = (db.query(MPlanNode).filter(MPlanNode.project_id == project.id)
             .order_by(MPlanNode.order_index).all())
    return {"nodes": [_node_json(n) for n in nodes],
            "progress": project.progress, "arrangement_status": project.arrangement_status}


def _save_version(db: Session, project: MProject, *, source: str, summary: str,
                  requirement: str, user_id: int) -> int:
    last = (db.query(MPlanVersion).filter(MPlanVersion.project_id == project.id)
            .order_by(MPlanVersion.version.desc()).first())
    version = (last.version + 1) if last else 1
    db.add(MPlanVersion(company_id=project.company_id, project_id=project.id, version=version,
                        source=source, summary=summary, requirement=requirement,
                        snapshot=_snapshot_project(db, project), created_by=user_id))
    db.flush()
    return version


@router.get("/projects/{project_id}/versions")
def list_versions(project_id: int, user: Any = Depends(current_actor),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    _require_company(db, project.company_id, user)
    rows = (db.query(MPlanVersion).filter(MPlanVersion.project_id == project_id)
            .order_by(MPlanVersion.version.desc()).all())
    out = []
    for r in rows:
        snap = r.snapshot if isinstance(r.snapshot, dict) else {}
        nodes = snap.get("nodes") or []
        out.append({"id": r.id, "version": r.version, "source": r.source,
                    "summary": r.summary, "requirement": r.requirement,
                    "nodes": len(nodes),
                    "tasks": len([n for n in nodes if n.get("node_type") == "task"]),
                    "complete": len([n for n in nodes if n.get("complete")]),
                    "created_at": r.created_at.isoformat()})
    return {"versions": out}


@router.post("/projects/{project_id}/rollback/{version_id}")
def rollback_version(project_id: int, version_id: int, user: Any = Depends(current_actor),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)
    ver = (db.query(MPlanVersion)
           .filter(MPlanVersion.id == version_id, MPlanVersion.project_id == project_id).first())
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")
    snap = ver.snapshot if isinstance(ver.snapshot, dict) else {}
    nodes = snap.get("nodes") or []
    db.query(MPlanNode).filter(MPlanNode.project_id == project_id).delete()
    id_map = {}
    for item in nodes:
        old_id = item.get("id")
        row = MPlanNode(company_id=company.id, project_id=project_id,
                        node_type=item.get("node_type") or "task",
                        title=item.get("title") or "节点", detail=item.get("detail") or "",
                        owner_kind=item.get("owner_kind") or "unassigned",
                        owner_membership_id=item.get("owner_membership_id"),
                        owner_name=item.get("owner_name") or "",
                        requirement=item.get("requirement") or "",
                        start_at=item.get("start_at") or "", end_at=item.get("end_at") or "",
                        kpi=item.get("kpi") or "", deliverable=item.get("deliverable") or "",
                        status=item.get("status") or "not_started",
                        progress=int(item.get("progress") or 0),
                        weight=int(item.get("weight") or 1),
                        order_index=int(item.get("order_index") or 0))
        db.add(row)
        db.flush()
        id_map[old_id] = row.id
    for item in nodes:
        parent = item.get("parent_id")
        if parent in id_map:
            child = db.query(MPlanNode).filter(MPlanNode.id == id_map[item.get("id")]).first()
            if child:
                child.parent_id = id_map[parent]
    project.arrangement_status = "draft"
    project.plan_source = ver.source
    _audit(db, company.id, getattr(user, "id", 0), "plan.rollback", "project", project_id,
           {"version": ver.version})
    db.commit()
    return {"ok": True, "restored_version": ver.version, "nodes": len(nodes)}


# ── 发布数据（播放量）：online 客户端采集 → 云端汇总 → 管理后台展示 ───────────────
# 展示端就是 manage.bhzn.top，与 H5 / 手机端无关；朋友圈（moments）本轮不采集。


def _require_metrics_admin(actor: Any) -> None:
    """发布数据是跨客户端的运营数据，只给平台管理员看。"""
    if _is_admin_actor(actor) or str(getattr(actor, "role", "") or "").lower() == "admin":
        return
    raise HTTPException(status_code=403, detail="只有平台管理员可以查看发布数据")


def _metrics_window(days: int) -> tuple[str, str, str, str, str]:
    """返回 (本期起, 本期止, 上期起, 上期止, 取数起点)——全部按北京自然日字符串。"""
    today = datetime.utcnow() + timedelta(hours=8)
    end_day = today.strftime("%Y-%m-%d")
    start_day = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    prev_end = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    prev_start = (today - timedelta(days=2 * days - 1)).strftime("%Y-%m-%d")
    fetch_since = (today - timedelta(days=2 * days + 7)).strftime("%Y-%m-%d")
    return start_day, end_day, prev_start, prev_end, fetch_since


def _account_label(email: str) -> str:
    raw = str(email or "").strip()
    if raw.startswith("1") and "@" in raw:
        local = raw.split("@", 1)[0]
        if local.isdigit() and len(local) == 11:
            return local
    return raw or "—"


def _event_gain(rows: List[Any], start_day: str, end_day: str) -> int:
    """窗口内播放量增量 = 末值 − 窗口前最近一次值（没有历史则视为窗口内第一条）。"""
    inside = [r for r in rows if start_day <= r.sampled_day <= end_day]
    if not inside:
        return 0
    before = [r for r in rows if r.sampled_day < start_day]
    base = int((before[-1] if before else inside[0]).views or 0)
    return max(0, int(inside[-1].views or 0) - base)


def _growth_pct(current: int, previous: int) -> Optional[float]:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


@router.get("/publish-metrics/overview", summary="发布数据总览：按平台 / 客户端 / 账号汇总")
def publish_metrics_overview(
    days: int = Query(7, ge=1, le=180),
    platform: str = Query("", description="douyin / wechat_channels；留空=两者"),
    user_id: Optional[int] = Query(None),
    q: str = Query("", description="按手机号 / 邮箱 / 安装 ID 搜索"),
    stale_hours: int = Query(26, ge=1, le=720, description="多久没上报算掉线"),
    actor: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from .publish_metrics import METRIC_PLATFORMS, PLATFORM_LABELS, window_views_gain

    _require_metrics_admin(actor)
    platforms = [platform.strip()] if platform.strip() in METRIC_PLATFORMS else list(METRIC_PLATFORMS)
    start_day, end_day, prev_start, prev_end, fetch_since = _metrics_window(days)

    ev_query = db.query(PublishMetricEvent).filter(
        PublishMetricEvent.platform.in_(platforms),
        PublishMetricEvent.sampled_day >= fetch_since,
    )
    samples_query = db.query(PublishMetricSample).filter(
        PublishMetricSample.platform.in_(platforms),
        PublishMetricSample.sampled_day >= fetch_since,
    )
    if user_id:
        ev_query = ev_query.filter(PublishMetricEvent.user_id == int(user_id))
        samples_query = samples_query.filter(PublishMetricSample.user_id == int(user_id))
    keyword = (q or "").strip().lower()
    if keyword:
        matched = [
            int(uid)
            for (uid,) in db.query(User.id)
            .filter(func.lower(User.email).like(f"%{keyword}%"))
            .all()
        ]
        if matched:
            ev_query = ev_query.filter(PublishMetricEvent.user_id.in_(matched))
            samples_query = samples_query.filter(PublishMetricSample.user_id.in_(matched))
        else:
            ev_query = ev_query.filter(PublishMetricEvent.installation_id.like(f"%{keyword}%"))
            samples_query = samples_query.filter(PublishMetricSample.installation_id.like(f"%{keyword}%"))

    events = ev_query.order_by(PublishMetricEvent.sampled_day.asc(), PublishMetricEvent.id.asc()).all()
    latest_rows = samples_query.all()

    now = datetime.utcnow()
    series: Dict[Any, List[Any]] = {}
    for row in events:
        series.setdefault((row.user_id, row.platform, row.item_id), []).append(row)

    users: Dict[int, Dict[str, Any]] = {}
    machines: Dict[str, Dict[str, Any]] = {}
    platform_buckets: Dict[str, Dict[str, Any]] = {
        p: {
            "platform": p,
            "platform_label": PLATFORM_LABELS.get(p, p),
            "item_count": 0,
            "views": 0,
            "views_gain": 0,
            "previous_views_gain": 0,
            "user_ids": set(),
            "account_count": 0,
        }
        for p in platforms
    }

    for (uid, plat, item_id), rows in series.items():
        inside = [r for r in rows if start_day <= r.sampled_day <= end_day]
        if not inside:
            continue
        last = inside[-1]
        gain = _event_gain(rows, start_day, end_day)
        prev_gain = _event_gain(rows, prev_start, prev_end)
        bucket = platform_buckets.setdefault(
            plat,
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_count": 0,
                "views": 0,
                "views_gain": 0,
                "previous_views_gain": 0,
                "user_ids": set(),
                "account_count": 0,
            },
        )
        bucket["item_count"] += 1
        bucket["views"] += int(last.views or 0)
        bucket["views_gain"] += gain
        bucket["previous_views_gain"] += prev_gain
        bucket["user_ids"].add(uid)

        user = users.setdefault(
            uid,
            {
                "user_id": uid,
                "label": "",
                "email": "",
                "brand": "",
                "item_count": 0,
                "views": 0,
                "views_gain": 0,
                "previous_views_gain": 0,
                "platforms": {},
                "machines": set(),
                "last_reported_at": None,
                "accounts": {},
            },
        )
        user["item_count"] += 1
        user["views"] += int(last.views or 0)
        user["views_gain"] += gain
        user["previous_views_gain"] += prev_gain
        plat_row = user["platforms"].setdefault(
            plat,
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_count": 0,
                "views": 0,
                "views_gain": 0,
            },
        )
        plat_row["item_count"] += 1
        plat_row["views"] += int(last.views or 0)
        plat_row["views_gain"] += gain
        if last.account_nickname:
            acct = user["accounts"].setdefault(
                str(last.account_id or last.account_nickname),
                {
                    "account_id": last.account_id,
                    "nickname": last.account_nickname,
                    "item_count": 0,
                    "views": 0,
                    "views_gain": 0,
                },
            )
            acct["item_count"] += 1
            acct["views"] += int(last.views or 0)
            acct["views_gain"] += gain

    for row in latest_rows:
        user = users.get(row.user_id)
        if user is None:
            continue
        if row.installation_id:
            user["machines"].add(row.installation_id)
            machine = machines.setdefault(
                row.installation_id,
                {
                    "installation_id": row.installation_id,
                    "user_id": row.user_id,
                    "label": "",
                    "brand": "",
                    "last_reported_at": None,
                    "samples": 0,
                    "platforms": set(),
                },
            )
            machine["samples"] += 1
            machine["platforms"].add(row.platform)
            if row.reported_at and (
                machine["last_reported_at"] is None or row.reported_at > machine["last_reported_at"]
            ):
                machine["last_reported_at"] = row.reported_at
        if row.reported_at and (
            user["last_reported_at"] is None or row.reported_at > user["last_reported_at"]
        ):
            user["last_reported_at"] = row.reported_at

    if users:
        user_rows = db.query(User).filter(User.id.in_(list(users.keys()))).all()
        for row in user_rows:
            user = users.get(row.id)
            if user is None:
                continue
            label = _account_label(row.email or "")
            user["label"] = label
            user["email"] = row.email or ""
            user["brand"] = row.brand_mark or ""
            for machine in machines.values():
                if machine["user_id"] == row.id:
                    machine["label"] = label
                    machine["brand"] = row.brand_mark or ""

    def _machine_json(item: Dict[str, Any]) -> Dict[str, Any]:
        last = item["last_reported_at"]
        hours = round((now - last).total_seconds() / 3600, 1) if last else None
        return {
            "installation_id": item["installation_id"],
            "user_id": item["user_id"],
            "label": item["label"],
            "brand": item["brand"],
            "samples": item["samples"],
            "platforms": sorted(item["platforms"]),
            "last_reported_at": (last.isoformat() + "Z") if last else None,
            "hours_since": hours,
            "stale": (hours is None) or hours > stale_hours,
        }

    users_out: List[Dict[str, Any]] = []
    for user in users.values():
        last = user["last_reported_at"]
        hours = round((now - last).total_seconds() / 3600, 1) if last else None
        users_out.append(
            {
                "user_id": user["user_id"],
                "label": user["label"],
                "email": user["email"],
                "brand": user["brand"],
                "item_count": user["item_count"],
                "views": user["views"],
                "views_gain": user["views_gain"],
                "previous_views_gain": user["previous_views_gain"],
                "growth_pct": _growth_pct(user["views_gain"], user["previous_views_gain"]),
                "platforms": sorted(user["platforms"].values(), key=lambda p: -int(p["views"])),
                "accounts": sorted(
                    user["accounts"].values(), key=lambda a: (-int(a["views"]), str(a.get("nickname") or ""))
                ),
                "machines": [_machine_json(m) for m in machines.values() if m["user_id"] == user["user_id"]],
                "last_reported_at": (last.isoformat() + "Z") if last else None,
                "hours_since": hours,
                "stale": (hours is None) or hours > stale_hours,
            }
        )
    users_out.sort(key=lambda u: (-int(u["views_gain"]), -int(u["views"]), str(u["label"])))

    platform_rows: List[Dict[str, Any]] = []
    for plat in platforms:
        bucket = platform_buckets.get(plat)
        if not bucket:
            continue
        platform_rows.append(
            {
                "platform": bucket["platform"],
                "platform_label": bucket["platform_label"],
                "item_count": bucket["item_count"],
                "views": bucket["views"],
                "views_gain": bucket["views_gain"],
                "previous_views_gain": bucket["previous_views_gain"],
                "growth_pct": _growth_pct(bucket["views_gain"], bucket["previous_views_gain"]),
                "user_count": len(bucket["user_ids"]),
            }
        )
    platform_rows.sort(key=lambda p: -int(p["views"]))

    total_gain = sum(int(p["views_gain"]) for p in platform_rows)
    prev_total_gain = sum(int(p["previous_views_gain"]) for p in platform_rows)
    machine_rows = sorted(
        (_machine_json(m) for m in machines.values()),
        key=lambda m: (not m["stale"], -(m["hours_since"] if m["hours_since"] is not None else 0)),
    )
    return {
        "ok": True,
        "days": days,
        "window": {"start_day": start_day, "end_day": end_day,
                   "previous_start_day": prev_start, "previous_end_day": prev_end},
        "totals": {
            "views": sum(int(p["views"]) for p in platform_rows),
            "views_gain": total_gain,
            "previous_views_gain": prev_total_gain,
            "growth_pct": _growth_pct(total_gain, prev_total_gain),
            "item_count": sum(int(p["item_count"]) for p in platform_rows),
            "user_count": len(users_out),
            "machine_count": len(machine_rows),
            "stale_machine_count": len([m for m in machine_rows if m["stale"]]),
        },
        "platforms": platform_rows,
        "users": users_out,
        "machines": machine_rows,
        "schedule": {
            "window": "02:00-06:00",
            "timezone": "Asia/Shanghai",
            "hint": "客户端按 installation_id 在窗口内错峰固定一分钟；未见 02:00-06:00 之外的采集",
        },
        "note": (
            "views_gain = 窗口内新增播放量（末值 − 窗口前基线）；views = 当前累计播放量。"
            "首次上报的作品本期不计增长，下一期起计入。仅抖音与视频号；朋友圈本轮不采集。"
            "stale=true 表示该机器超过 "
            f"{stale_hours} 小时没有上报。"
        ),
    }


@router.get("/publish-metrics/items", summary="发布数据明细：作品曲线（按北京自然日）")
def publish_metrics_items(
    days: int = Query(30, ge=1, le=180),
    platform: str = Query("", description="douyin / wechat_channels；留空=两者"),
    user_id: Optional[int] = Query(None),
    account_id: Optional[int] = Query(None),
    item_id: str = Query(""),
    limit: int = Query(80, ge=1, le=500),
    actor: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from .publish_metrics import METRIC_PLATFORMS, PLATFORM_LABELS

    _require_metrics_admin(actor)
    platforms = [platform.strip()] if platform.strip() in METRIC_PLATFORMS else list(METRIC_PLATFORMS)
    start_day, end_day, _prev_start, _prev_end, fetch_since = _metrics_window(days)

    query = db.query(PublishMetricEvent).filter(
        PublishMetricEvent.platform.in_(platforms),
        PublishMetricEvent.sampled_day >= fetch_since,
    )
    if user_id:
        query = query.filter(PublishMetricEvent.user_id == int(user_id))
    if account_id:
        query = query.filter(PublishMetricEvent.account_id == int(account_id))
    wanted_item = (item_id or "").strip()
    if wanted_item:
        query = query.filter(PublishMetricEvent.item_id == wanted_item)
    rows = query.order_by(PublishMetricEvent.sampled_day.asc(), PublishMetricEvent.id.asc()).all()

    series: Dict[Any, List[Any]] = {}
    for row in rows:
        series.setdefault((row.user_id, row.platform, row.item_id), []).append(row)

    titles = {
        (row.user_id, row.platform, row.item_id): row.title
        for row in db.query(PublishMetricSample)
        .filter(
            PublishMetricSample.platform.in_(platforms),
            PublishMetricSample.sampled_day >= fetch_since,
        )
        .all()
    }
    items: List[Dict[str, Any]] = []
    for (uid, plat, iid), points in series.items():
        gain = _event_gain(points, start_day, end_day)
        last = points[-1]
        items.append(
            {
                "user_id": uid,
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_id": iid,
                "title": titles.get((uid, plat, iid)),
                "account_id": last.account_id,
                "account_nickname": last.account_nickname,
                "views": int(last.views or 0),
                "views_gain": gain,
                "first_day": points[0].sampled_day,
                "last_day": points[-1].sampled_day,
                "points": [
                    {
                        "day": r.sampled_day,
                        "views": int(r.views or 0),
                        "likes": int(r.likes or 0),
                        "comments": int(r.comments or 0),
                        "shares": int(r.shares or 0),
                        "favorites": int(r.favorites or 0),
                    }
                    for r in points
                ],
            }
        )
    items.sort(key=lambda e: (-int(e["views_gain"]), -int(e["views"])))
    return {
        "ok": True,
        "days": days,
        "window": {"start_day": start_day, "end_day": end_day},
        "item_total": len(items),
        "items": items[:limit],
    }


# ── 任务时间线 / 我的任务 / AI 辅助 ──────────────────────────────────────────────
# 时间线：项目列表二级界面用。上面是日期刻度，下面每个任务一根横条，
#         跨多天合成一根（不是一天一个格子），颜色区分状态：
#         绿=完成、黄=预警（3 天内到期）、红=超期、青=进行中、灰=未开始、紫=受阻。

TIMELINE_STATE_LABELS = {
    "done": "完成",
    "overdue": "超期",
    "warn": "预警",
    "active": "进行中",
    "blocked": "受阻",
    "pending": "未开始",
}

TIMELINE_TZ_OFFSET_HOURS = 8
TIMELINE_WARN_DAYS = 3


def _today_beijing() -> date:
    return (datetime.utcnow() + timedelta(hours=TIMELINE_TZ_OFFSET_HOURS)).date()


def _parse_day(value: Any) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except (TypeError, ValueError):
        return None


def _node_day_range(node: MPlanNode) -> tuple[Optional[date], Optional[date]]:
    """任务起止（只认日期部分）；缺一头就用另一头补齐，方便画条。"""
    start = _parse_day(node.start_at)
    end = _parse_day(node.end_at)
    if start and not end:
        end = start
    if end and not start:
        start = end
    if start and end and end < start:
        start, end = end, start
    return start, end


def _timeline_state(node: MPlanNode, today: date) -> str:
    """时间线状态：done / overdue / warn / active / blocked / pending。"""
    status = str(node.status or "").strip().lower()
    if status in ("completed", "done"):
        return "done"
    if status == "blocked":
        return "blocked"
    _start, end = _node_day_range(node)
    if end is not None:
        if end < today:
            return "overdue"
        if (end - today).days <= TIMELINE_WARN_DAYS:
            return "warn"
    if status == "in_progress":
        return "active"
    return "pending"


def _timeline_task_json(
    node: MPlanNode,
    *,
    project_name: str = "",
    company_name: str = "",
    today: date,
    window_start: date,
) -> Optional[Dict[str, Any]]:
    start, end = _node_day_range(node)
    if start is None or end is None:
        return None
    state = _timeline_state(node, today)
    return {
        "id": node.id,
        "project_id": node.project_id,
        "project_name": project_name,
        "company_name": company_name,
        "node_type": node.node_type,
        "title": node.title,
        "detail": node.detail or "",
        "owner_name": node.owner_name or "",
        "owner_membership_id": node.owner_membership_id,
        "requirement": node.requirement or "",
        "kpi": node.kpi or "",
        "deliverable": node.deliverable or "",
        "start_at": node.start_at or "",
        "end_at": node.end_at or "",
        "start_day": start.isoformat(),
        "end_day": end.isoformat(),
        "offset_days": (start - window_start).days,
        "span_days": (end - start).days + 1,
        "days_left": (end - today).days,
        "status": node.status,
        "progress": int(node.progress or 0),
        "state": state,
        "state_label": TIMELINE_STATE_LABELS.get(state, state),
    }


def _company_scope(db: Session, actor: Any, company_id: Optional[int]) -> List[MCompany]:
    """当前主体能看的公司：管理员=全部（或指定），真实员工=自己 active 成员关系所在公司。"""
    if _is_admin_actor(actor):
        if company_id:
            row = db.query(MCompany).filter(MCompany.id == int(company_id)).first()
            return [row] if row else []
        return db.query(MCompany).order_by(MCompany.id.asc()).all()
    rows = (
        db.query(MCompany)
        .join(MMembership, MMembership.company_id == MCompany.id)
        .filter(MMembership.user_id == int(getattr(actor, "id", 0) or 0),
                MMembership.status == "active")
        .order_by(MCompany.id.asc())
        .all()
    )
    owned = db.query(MCompany).filter(MCompany.owner_user_id == int(getattr(actor, "id", 0) or 0)).all()
    seen = {c.id for c in rows}
    for company in owned:
        if company.id not in seen:
            rows.append(company)
            seen.add(company.id)
    if company_id:
        return [c for c in rows if c.id == int(company_id)]
    return rows


def _my_membership_ids(db: Session, actor: Any) -> List[int]:
    return [
        int(row.id)
        for row in db.query(MMembership)
        .filter(MMembership.user_id == int(getattr(actor, "id", 0) or 0),
                MMembership.status == "active")
        .all()
    ]


CONFIRMED_ARRANGEMENT = ("confirmed", "changed")


def _confirmed_project_ids(db: Session, company_ids: List[int]) -> List[int]:
    """已经确认过安排的项目（含有待确认变更的）。草稿项目不算。"""
    rows = (db.query(MProject.id)
            .filter(MProject.company_id.in_(company_ids),
                    MProject.arrangement_status.in_(CONFIRMED_ARRANGEMENT)).all())
    return [int(r[0]) for r in rows]


def _task_query(db: Session, company_ids: List[int], project_id: Optional[int],
                only_confirmed_project: bool = False) -> List[MPlanNode]:
    if not company_ids:
        return []
    query = db.query(MPlanNode).filter(MPlanNode.company_id.in_(company_ids))
    if project_id:
        # 显式指定项目时不过滤：项目里的时间线/详情要能看草稿
        query = query.filter(MPlanNode.project_id == int(project_id))
    elif only_confirmed_project:
        ids = _confirmed_project_ids(db, company_ids)
        if not ids:
            return []
        query = query.filter(MPlanNode.project_id.in_(ids))
    return query.order_by(MPlanNode.start_at.asc(), MPlanNode.order_index.asc(), MPlanNode.id.asc()).all()


@router.get("/tasks/timeline", summary="任务时间线：日期刻度 + 任务横条（跨天合并，颜色=状态）")
def tasks_timeline(
    company_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    from_day: str = Query("", description="窗口开始日 YYYY-MM-DD，默认今天-7"),
    to_day: str = Query("", description="窗口结束日 YYYY-MM-DD，默认今天+30"),
    actor: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    companies = _company_scope(db, actor, company_id)
    if not companies:
        raise HTTPException(status_code=404, detail="没有可查看的公司")
    company_ids = [int(c.id) for c in companies]
    company_names = {int(c.id): c.name for c in companies}

    today = _today_beijing()
    nodes = _task_query(db, company_ids, project_id, only_confirmed_project=True)
    # 未显式给窗口（前端选「全部」）时：有任务就按任务起止自适应，没有就默认今天±
    explicit_from = _parse_day(from_day)
    explicit_to = _parse_day(to_day)
    if explicit_from is None or explicit_to is None:
        spans = [_node_day_range(n) for n in nodes]
        spans = [(s, e) for s, e in spans if s and e]
        if spans:
            data_start = min(s for s, _ in spans)
            data_end = max(e for _, e in spans)
            window_start = explicit_from or (data_start - timedelta(days=3))
            window_end = explicit_to or (data_end + timedelta(days=3))
        else:
            window_start = explicit_from or (today - timedelta(days=7))
            window_end = explicit_to or (today + timedelta(days=30))
    else:
        window_start, window_end = explicit_from, explicit_to
    if window_end < window_start:
        window_start, window_end = window_end, window_start

    project_rows = db.query(MProject).filter(MProject.company_id.in_(company_ids)).all()
    project_names = {int(p.id): p.name for p in project_rows}

    tasks: List[Dict[str, Any]] = []
    for node in nodes:
        item = _timeline_task_json(
            node,
            project_name=project_names.get(int(node.project_id), ""),
            company_name=company_names.get(int(node.company_id), ""),
            today=today,
            window_start=window_start,
        )
        if item is None:
            continue
        if _parse_day(item["end_day"]) < window_start or _parse_day(item["start_day"]) > window_end:
            continue
        tasks.append(item)

    summary = {key: 0 for key in TIMELINE_STATE_LABELS}
    for item in tasks:
        summary[item["state"]] = summary.get(item["state"], 0) + 1
    tasks.sort(key=lambda x: (x["start_day"], x["project_name"], x["title"]))
    return {
        "ok": True,
        "today": today.isoformat(),
        "window": {"from_day": window_start.isoformat(), "to_day": window_end.isoformat(),
                   "days": (window_end - window_start).days + 1,
                   "today_index": (today - window_start).days},
        "state_labels": TIMELINE_STATE_LABELS,
        "summary": summary,
        "project_count": len({t["project_id"] for t in tasks}),
        "task_count": len(tasks),
        "tasks": tasks,
        "projects": [{"id": int(p.id), "name": p.name} for p in project_rows],
        "companies": [{"id": int(c.id), "name": c.name} for c in companies],
        "note": (
            "每条任务一根横条，跨多天合并显示；颜色=状态："
            "绿=完成、黄=预警（3 天内到期）、红=超期、青=进行中、灰=未开始、紫=受阻。"
        ),
    }


@router.get("/tasks/mine", summary="我的任务：真实员工登录后看到派给自己的任务")
def tasks_mine(
    company_id: Optional[int] = Query(None),
    include_done: bool = Query(True),
    actor: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    companies = _company_scope(db, actor, company_id)
    if not companies:
        raise HTTPException(status_code=404, detail="没有可查看的公司")
    company_ids = [int(c.id) for c in companies]
    company_names = {int(c.id): c.name for c in companies}
    today = _today_beijing()

    is_admin = _is_admin_actor(actor) or str(getattr(actor, "role", "") or "").lower() == "admin"
    memberships = _my_membership_ids(db, actor)
    my_names = {
        str(row.display_name or "").strip()
        for row in db.query(MMembership)
        .filter(MMembership.user_id == int(getattr(actor, "id", 0) or 0))
        .all()
        if str(row.display_name or "").strip()
    }

    nodes = _task_query(db, company_ids, None, only_confirmed_project=True)
    project_rows = db.query(MProject).filter(MProject.company_id.in_(company_ids)).all()
    project_names = {int(p.id): p.name for p in project_rows}
    # 草稿项目（还没点「确认安排」）：不派活，但要告诉人有多少件在等确认
    draft_counts: Dict[int, int] = {}
    for n in db.query(MPlanNode).filter(MPlanNode.company_id.in_(company_ids)).all():
        if int(n.project_id) not in draft_counts:
            draft_counts[int(n.project_id)] = 0
        draft_counts[int(n.project_id)] += 1
    draft_projects = [{"id": int(p.id), "name": p.name,
                       "nodes": draft_counts.get(int(p.id), 0),
                       "status": p.arrangement_status}
                      for p in project_rows if p.arrangement_status not in CONFIRMED_ARRANGEMENT]
    draft_projects.sort(key=lambda x: x["id"])

    tasks: List[Dict[str, Any]] = []
    for node in nodes:
        mine = False
        if is_admin:
            mine = True
        elif node.owner_membership_id and int(node.owner_membership_id) in memberships:
            mine = True
        elif my_names and str(node.owner_name or "").strip() in my_names:
            mine = True
        if not mine:
            continue
        start, end = _node_day_range(node)
        state = _timeline_state(node, today)
        if not include_done and state == "done":
            continue
        tasks.append(
            {
                "id": node.id,
                "project_id": node.project_id,
                "project_name": project_names.get(int(node.project_id), ""),
                "company_id": node.company_id,
                "company_name": company_names.get(int(node.company_id), ""),
                "title": node.title,
                "detail": node.detail or "",
                "owner_name": node.owner_name or "",
                "requirement": node.requirement or "",
                "kpi": node.kpi or "",
                "deliverable": node.deliverable or "",
                "start_at": node.start_at or "",
                "end_at": node.end_at or "",
                "start_day": start.isoformat() if start else "",
                "end_day": end.isoformat() if end else "",
                "days_left": (end - today).days if end else None,
                "status": node.status,
                "progress": int(node.progress or 0),
                "state": state,
                "state_label": TIMELINE_STATE_LABELS.get(state, state),
            }
        )

    order = {"overdue": 0, "warn": 1, "active": 2, "blocked": 3, "pending": 4, "done": 5}
    tasks.sort(key=lambda t: (order.get(t["state"], 9), t["end_day"] or "9999", t["title"]))
    summary = {key: 0 for key in TIMELINE_STATE_LABELS}
    for item in tasks:
        summary[item["state"]] = summary.get(item["state"], 0) + 1
    return {
        "ok": True,
        "today": today.isoformat(),
        "scope": "all" if is_admin else "mine",
        "actor": {"id": int(getattr(actor, "id", 0) or 0), "name": str(getattr(actor, "email", "") or "")},
        "state_labels": TIMELINE_STATE_LABELS,
        "summary": summary,
        "task_count": len(tasks),
        "tasks": tasks,
        "draft_projects": draft_projects,
        "note": (
            "真实员工用自己账号登录时，这里只列派给自己的任务；平台管理员/老板看到的是全部任务，"
            "方便替员工检查。每条任务可用「AI 辅助」生成思路与执行方案；"
            "只有「确认安排」过的项目才会出现在这里，草稿不算。"
        ),
    }


class _CoachIn(BaseModel):
    """AI 辅助生成的重点；缺省=思路 + 执行方案都给。"""

    focus: str = ""


def _can_coach(db: Session, actor: Any, node: MPlanNode) -> bool:
    if _is_admin_actor(actor) or str(getattr(actor, "role", "") or "").lower() == "admin":
        return True
    company = db.query(MCompany).filter(MCompany.id == node.company_id).first()
    actor_id = int(getattr(actor, "id", 0) or 0)
    if company and company.owner_user_id == actor_id:
        return True
    roles = {r.role_code for r in _roles_for(db, node.company_id, actor_id)}
    if roles & FULL_ACCESS:
        return True
    if node.owner_membership_id:
        row = (
            db.query(MMembership)
            .filter(MMembership.id == int(node.owner_membership_id))
            .first()
        )
        if row and int(row.user_id or 0) == actor_id:
            return True
    if str(node.owner_name or "").strip():
        mine = (
            db.query(MMembership)
            .filter(MMembership.company_id == node.company_id,
                    MMembership.user_id == actor_id,
                    MMembership.status == "active")
            .all()
        )
        if str(node.owner_name or "").strip() in {str(m.display_name or "").strip() for m in mine}:
            return True
    return False


async def _llm_text(token: str, system: str, user_text: str, *, timeout: float = 150.0) -> str:
    """纯文本 AI 调用（复用主站 /api/sutui-chat/completions，与生成规划同一条通道）。"""
    body = {
        "model": "",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "temperature": 0.4,
    }
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            "http://127.0.0.1:8000/api/sutui-chat/completions", json=body, headers=headers
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="AI 通道返回 " + str(resp.status_code) + ": " + (resp.text or "")[:300],
        )
    try:
        text = str(resp.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        raise HTTPException(status_code=502, detail="AI 返回结构异常")
    if not text:
        raise HTTPException(status_code=502, detail="AI 返回空内容，请重试")
    return text


COACH_SYSTEM_PROMPT = (
    "你是企业项目管理助理。用户是这家公司的一线执行员工，需要把派给自己的任务做成可落地的方案。\n"
    "请用简体中文输出 Markdown，不要寒暄，不要重复任务原文，按下面结构：\n"
    "## 思路（怎么想）\n"
    "3-5 条，讲清目标拆解、优先级、关键假设与风险。\n"
    "## 执行方案（怎么做）\n"
    "按步骤给到「做什么-产出物-预计耗时-依赖/需要谁配合」，可执行到天。\n"
    "## 验收标准\n"
    "对应任务里的 KPI / 交付物给出可检查的标准。\n"
    "## 可能踩的坑\n"
    "2-3 条，附规避动作。\n"
    "要求具体、可执行；涉及数字时给出估算范围；不要编造公司内部数据。"
)


FOLLOWUP_COACH_PROMPT = (
    "你是企业项目管理助理，帮一线同事把「下一次跟进」定下来。\n"
    "只依据给定资料（跟进线 + 客户/交付资料），不要编造公司内部数据。\n"
    "请用简体中文输出 Markdown，不要寒暄，按下面结构：\n"
    "## 现在什么情况\n"
    "2-4 句概括进展、卡点、对方态度，并点出跟进线里缺什么信息。\n"
    "## 下一步动作\n"
    "3-5 条，每条写清：做什么 / 找谁 / 说什么（可给话术要点）/ 期望对方给什么反馈。\n"
    "## 时间与节奏\n"
    "给出下次跟进的具体日期（或天数）与频率，并说明为什么。\n"
    "## 风险与备选\n"
    "2-3 条可能的变数 + 对应备选动作。\n"
    "## 一句话建议\n"
    "一句话给出「下一次先做什么」。\n"
    "要求具体、可执行；不要重复原文。"
)


async def _followup_coach(db: Session, actor: Any, company: MCompany, *, subject: str,
                          profile: Dict[str, Any], timeline: List[Dict[str, Any]],
                          extra: Optional[Dict[str, Any]] = None,
                          focus: str = "", audit_action: str = "", audit_target: str = "",
                          audit_id: int = 0) -> Dict[str, Any]:
    """跟进建议通用部分：组 payload -> 调 AI -> 记审计。
    关键：AI 可能跑几十秒，请求会话不能一直挂着事务（idle_in_transaction 会掉连接）。
    """
    payload: Dict[str, Any] = {"跟进对象": subject}
    for k, v in profile.items():
        if v not in (None, "", [], {}):
            payload[k] = v
    if timeline:
        payload["跟进线（新→旧）"] = timeline
    else:
        payload["跟进线（新→旧）"] = ["（还没有跟进记录）"]
    if extra:
        for k, v in extra.items():
            if v not in (None, "", [], {}):
                payload[k] = v
    focus = (focus or "").strip()[:200]
    if focus:
        payload["本次特别要求"] = focus
    token = _llm_token_for_company(db, company)
    company_id = int(company.id)
    try:
        db.commit()
    except Exception:
        db.rollback()
    text = await _llm_text(token, FOLLOWUP_COACH_PROMPT, json.dumps(payload, ensure_ascii=False))
    try:
        write_db = SessionLocal()
        try:
            _audit(write_db, company_id, int(getattr(actor, "id", 0) or 0), audit_action or "followup.ai_coach",
                   audit_target, audit_id, {"focus": focus})
            write_db.commit()
        finally:
            write_db.close()
    except Exception as exc:
        logger.warning("[MANAGE] followup coach audit failed %s=%s err=%s", audit_target, audit_id, exc)
    return {"ok": True, "generated_at": datetime.utcnow().isoformat() + "Z", "focus": focus,
            "text": text, "note": "AI 建议仅供参考，发出前请自己过一遍口径。"}


@router.post("/customers/{customer_id}/coach", summary="AI 辅助：根据跟进线 + 客户资料给下一步跟进建议")
async def customer_followup_coach(customer_id: int, body: _CoachIn = _CoachIn(),
                                  actor: Any = Depends(current_actor),
                                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MCustomer).filter(MCustomer.id == customer_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    company = _require_company(db, row.company_id, actor)
    logs = (db.query(MCustomerLog).filter(MCustomerLog.customer_id == row.id)
            .order_by(MCustomerLog.id.desc()).limit(20).all())
    timeline = [{"时间": x.happened_at or "", "方式": x.kind or "note",
                 "内容": (x.content or "")[:400],
                 "阶段变化": ((STAGE_LABEL.get(x.from_stage, x.from_stage) + " → "
                                          + STAGE_LABEL.get(x.to_stage, x.to_stage))
                                         if x.from_stage != x.to_stage else "")}
                for x in logs]
    owner = None
    if row.owner_membership_id:
        m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
        owner = m.display_name if m else None
    deliv = (db.query(MDelivery).filter(MDelivery.customer_id == row.id)
             .order_by(MDelivery.id.desc()).limit(5).all())
    profile = {"客户": row.name, "对方公司": row.company_name or "",
               "手机": row.phone or "", "微信": row.wechat or "",
               "来源": row.source or "",
               "当前阶段": STAGE_LABEL.get(row.stage, row.stage),
               "商机金额": ("¥" + str(float(row.amount or 0))),
               "负责人": owner or "未指派",
               "已填的下一步": row.next_action or "",
               "已填的下次跟进": row.next_follow_at or "",
               "最近跟进": row.last_follow_at or "还没跟进过",
               "备注": (row.notes or "")[:800]}
    extra = {}
    if deliv:
        extra["关联交付单"] = [
            {"名称": d.name, "状态": DELIVERY_LABEL.get(d.status, d.status),
             "承诺": d.promised_at or "", "已交付": d.delivered_at or ""} for d in deliv]
    out = await _followup_coach(db, actor, company, subject=row.name, profile=profile,
                                timeline=timeline, extra=extra, focus=body.focus,
                                audit_action="customer.ai_coach", audit_target="customer",
                                audit_id=int(row.id))
    out["customer_id"] = int(row.id)
    out["customer_name"] = row.name
    return out


@router.post("/deliveries/{delivery_id}/coach", summary="AI 辅助：根据跟进线 + 交付资料给下一步跟进建议")
async def delivery_followup_coach(delivery_id: int, body: _CoachIn = _CoachIn(),
                                  actor: Any = Depends(current_actor),
                                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    company = _require_company(db, row.company_id, actor)
    logs = (db.query(MDeliveryLog).filter(MDeliveryLog.delivery_id == row.id)
            .order_by(MDeliveryLog.id.desc()).limit(20).all())
    timeline = [{"时间": x.happened_at or "", "类型": x.kind or "note",
                 "内容": (x.content or "")[:400],
                 "状态变化": ((DELIVERY_LABEL.get(x.from_status, x.from_status) + " → "
                                          + DELIVERY_LABEL.get(x.to_status, x.to_status))
                                         if x.from_status != x.to_status else "")}
                for x in logs]
    owner = None
    if row.owner_membership_id:
        m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
        owner = m.display_name if m else None
    profile = {"交付单": row.name,
               "当前状态": DELIVERY_LABEL.get(row.status, row.status),
               "负责人": owner or "未指派",
               "承诺交付": row.promised_at or "",
               "已交付": row.delivered_at or "",
               "已验收": row.accepted_at or "",
               "最近跟进": row.last_follow_at or "还没跟进过",
               "备注": (row.note or "")[:800]}
    extra = {}
    cust = db.query(MCustomer).filter(MCustomer.id == row.customer_id).first() if row.customer_id else None
    if cust:
        extra["客户资料"] = {"客户": cust.name, "对方公司": cust.company_name or "",
                                            "阶段": STAGE_LABEL.get(cust.stage, cust.stage),
                                            "商机金额": float(cust.amount or 0),
                                            "客户备注": (cust.notes or "")[:400]}
    out = await _followup_coach(db, actor, company, subject=row.name, profile=profile,
                                timeline=timeline, extra=extra, focus=body.focus,
                                audit_action="delivery.ai_coach", audit_target="delivery",
                                audit_id=int(row.id))
    out["delivery_id"] = int(row.id)
    out["delivery_name"] = row.name
    return out


@router.post("/tasks/{node_id}/coach", summary="AI 辅助：为这条任务生成思路与执行方案")
async def task_ai_coach(
    node_id: int,
    body: _CoachIn = _CoachIn(),
    actor: Any = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    node = db.query(MPlanNode).filter(MPlanNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not _can_coach(db, actor, node):
        raise HTTPException(status_code=403, detail="只能给自己负责的任务用 AI 辅助")
    company = db.query(MCompany).filter(MCompany.id == node.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="公司不存在")
    project = db.query(MProject).filter(MProject.id == node.project_id).first()

    focus = (body.focus or "").strip()[:200]
    payload: Dict[str, Any] = {
        "任务": node.title,
        "任务说明": (node.detail or "")[:2000],
        "负责人": node.owner_name or "",
        "要求": (node.requirement or "")[:1500],
        "KPI": (node.kpi or "")[:800],
        "交付物": (node.deliverable or "")[:800],
        "起止": f"{node.start_at or '待定'} ~ {node.end_at or '待定'}",
        "当前进度": f"{int(node.progress or 0)}%",
        "当前状态": str(node.status or ""),
        "项目": (project.name if project else ""),
        "项目目标": ((project.goal or "")[:800] if project else ""),
    }
    if project and project.success_criteria:
        payload["项目成功标准"] = str(project.success_criteria)[:800]
    if focus:
        payload["本次特别要求"] = focus

    token = _llm_token_for_company(db, company)
    node_id = int(node.id)
    company_id = int(node.company_id)
    task_title = node.title
    # 关键：AI 调用可能几十秒，期间不能让请求会话一直挂着事务——
    # PostgreSQL 的 idle_in_transaction_session_timeout 会掐掉连接，导致随后的审计写入 500。
    # 因此读阶段结束后就收尾，AI 返回后用独立会话只写一行审计（写失败也不影响把结果给用户）。
    try:
        db.commit()
    except Exception:
        db.rollback()
    text = await _llm_text(token, COACH_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
    try:
        write_db = SessionLocal()
        try:
            _audit(write_db, company_id, int(getattr(actor, "id", 0) or 0), "task.ai_coach",
                   "plan_node", node_id, {"focus": focus})
            write_db.commit()
        finally:
            write_db.close()
    except Exception as exc:  # 审计失败不能把生成结果丢掉
        logger.warning("[MANAGE] task.ai_coach 审计写入失败 node=%s err=%s", node_id, exc)
    return {
        "ok": True,
        "node_id": node_id,
        "task_title": task_title,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "focus": focus,
        "text": text,
        "note": "AI 生成内容仅供执行参考，落地前请和负责人确认口径。",
    }
