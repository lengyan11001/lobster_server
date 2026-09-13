"""manage（项目管理与 AI 赋能）独立站的 API。

与主站共用 users 与同一套 JWT：登录直接复用 api/auth 的 router，
本文件只提供 manage 自己的业务接口（/api/manage/*）。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal, get_db
from ..manage_models import (
    MAiEmployee,
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
    MMembership,
    MMembershipRole,
    MPlanNode,
    MProduct,
    MProject,
)
from ..models import H5ChatDevicePresence, User, UserInstallation
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
    user_id: int
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
        "complete": bool(n.owner_name and n.requirement and n.start_at and n.end_at),
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
    key = (q or "").strip()
    if not key:
        return {"found": False}
    row = (
        db.query(User)
        .filter((User.email == key) | (User.wecom_userid == key) | (User.wechat_openid == key))
        .first()
    )
    if not row:
        return {"found": False}
    return {"found": True, "user": {"id": row.id, "email": row.email, "brand_mark": row.brand_mark}}


@router.post("/companies")
def create_company(body: CompanyIn, user: Any = Depends(current_actor),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
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
    return {"ok": True, "company_id": company.id}


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
6. 输出 schema:
{"summary":"","phases":[{"title":"","start_at":"","end_at":"","goal":"","tasks":[
 {"title":"","owner_name":"","requirement":"","start_at":"","end_at":"","kpi":"","deliverable":""}]}],
 "milestones":[{"title":"","at":""}],
 "resource_gap":[{"category":"people|money|device|material|channel|compliance|time",
   "title":"","need":"","now":"","gap":"","impact":"","severity":"high|medium|low",
   "actions":[{"type":"hire|slot|budget|outsource|scope","what":"","when":""}]}]}"""


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
    member_payload: List[Dict[str, Any]] = []
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
        members = (read_db.query(MMembership)
                   .filter(MMembership.company_id == company_id, MMembership.status == "active").all())
        for m in members:
            roles = read_db.query(MMembershipRole).filter(MMembershipRole.membership_id == m.id).all()
            member_payload.append({"membership_id": m.id, "name": m.display_name,
                                   "roles": [{"code": r.role_code, "level": r.level} for r in roles]})
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
        _audit(write_db, company_id, user.id, "plan.generate", "project", project_id,
               {"source": source, "nodes": order, "gaps": len(gaps)})
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
            "version": plan_version, "nodes": out_nodes, "resource_gap": gaps}


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
    missing = [n.title for n in nodes
               if not (n.owner_name and n.requirement and n.start_at and n.end_at)]
    if missing:
        raise HTTPException(status_code=400,
                            detail="还有 " + str(len(missing)) + " 个节点没排齐（人员/要求/时间节点都要填）："
                                   + "、".join(missing[:3]))
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


@router.post("/admin/boss")
def mark_boss(body: BossIn, user: Any = Depends(current_actor),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    if str(user.role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="只有平台管理员可以标记老板")
    target = db.query(User).filter(User.id == body.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="账号不存在")
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
                    owner_membership_id=body.owner_membership_id, next_action=body.next_action,
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
    if data.get("owner_membership_id") is not None:
        row.owner_membership_id = data["owner_membership_id"]
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


def _delivery_json(db: Session, row: MDelivery) -> Dict[str, Any]:
    owner = None
    if row.owner_membership_id:
        m = db.query(MMembership).filter(MMembership.id == row.owner_membership_id).first()
        owner = m.display_name if m else None
    cust = db.query(MCustomer).filter(MCustomer.id == row.customer_id).first() if row.customer_id else None
    return {"id": row.id, "name": row.name, "customer_id": row.customer_id,
            "customer_name": cust.name if cust else "", "project_id": row.project_id,
            "status": row.status, "status_label": DELIVERY_LABEL.get(row.status, row.status),
            "owner_membership_id": row.owner_membership_id, "owner_name": owner or "",
            "promised_at": row.promised_at, "delivered_at": row.delivered_at,
            "accepted_at": row.accepted_at, "note": row.note}


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
    for field in ("name", "owner_membership_id", "promised_at", "delivered_at", "accepted_at", "note"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    if data.get("status") and data["status"] in DELIVERY_LABEL:
        row.status = data["status"]
        today = date.today().isoformat()
        if row.status == "doing" and not row.delivered_at:
            row.delivered_at = ""
        if row.status in ("review", "accepted") and not row.delivered_at:
            row.delivered_at = today
        if row.status == "accepted" and not row.accepted_at:
            row.accepted_at = today
    _audit(db, company.id, getattr(user, "id", 0), "delivery.update", "delivery", row.id)
    db.commit()
    return {"ok": True, "delivery": _delivery_json(db, row)}


@router.delete("/deliveries/{delivery_id}")
def delete_delivery(delivery_id: int, user: Any = Depends(current_actor),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.query(MDelivery).filter(MDelivery.id == delivery_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="交付单不存在")
    company = _require_company(db, row.company_id, user)
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


@router.get("/ai-employees")
def list_ai_employees(company_id: int, user: Any = Depends(current_actor),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, company_id, user)
    members = (db.query(MMembership)
               .filter(MMembership.company_id == company.id)).all()
    # 自愈：老板的成员记录若没绑定 user_id（历史数据/早期 seed），补上，
    # 否则查不到他名下的设备槽位，虚拟员工页会一直空着。
    if company.owner_user_id:
        owner_m = next((m for m in members if m.user_id == company.owner_user_id), None)
        if owner_m is None:
            orphan = next((m for m in members if not m.user_id and m.dept == "经营管理"), None)
            if orphan is not None:
                orphan.user_id = company.owner_user_id
                subject = db.query(User).filter(User.id == company.owner_user_id).first()
                if subject is not None and not orphan.email:
                    orphan.email = subject.email
                db.flush()
                owner_m = orphan
    by_user = {m.user_id: m for m in members if m.user_id}
    if company.owner_user_id and company.owner_user_id not in by_user:
        owner_m = (db.query(MMembership)
                   .filter(MMembership.company_id == company.id,
                           MMembership.user_id == company.owner_user_id).first())
        if owner_m:
            by_user[company.owner_user_id] = owner_m
    discovered = 0
    if by_user:
        slots = (db.query(UserInstallation)
                 .filter(UserInstallation.user_id.in_(list(by_user.keys()))).all())
        known = {r.installation_id for r in
                 db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id).all()}
        for slot in slots:
            if slot.installation_id in known:
                continue
            m = by_user.get(slot.user_id)
            if not m:
                subject = db.query(User).filter(User.id == slot.user_id).first()
                fallback = (subject.email.split("@")[0] if subject and subject.email else "")
            else:
                fallback = m.display_name
            db.add(MAiEmployee(company_id=company.id,
                               name="虚拟员工 · " + (fallback or "设备") + " · " + slot.installation_id[:4],
                               installation_id=slot.installation_id,
                               owner_membership_id=m.id if m else None, capabilities=[],
                               created_by=getattr(user, "id", 0)))
            discovered += 1
        if discovered:
            db.commit()

    rows = (db.query(MAiEmployee).filter(MAiEmployee.company_id == company.id)
            .order_by(MAiEmployee.id).all())
    out = []
    for r in rows:
        m = db.query(MMembership).filter(MMembership.id == r.owner_membership_id).first() if r.owner_membership_id else None
        owner_uid = getattr(m, "user_id", None) if m else company.owner_user_id
        online, seen = _slot_online(db, owner_uid or 0, r.installation_id)
        dispatched = (db.query(MDispatch).filter(MDispatch.node_id.isnot(None),
                                                 MDispatch.ai_employee_id == r.id)
                      .order_by(MDispatch.id.desc()).limit(20).all())
        if r.name.count("·") < 2:
            r.name = r.name + " · " + r.installation_id[:4]
            db.flush()
        out.append({"id": r.id, "name": r.name, "installation_id": r.installation_id,
                    "owner_name": m.display_name if m else "", "online": online,
                    "last_seen": seen, "capabilities": r.capabilities or [],
                    "status": r.status, "dispatched": len(dispatched)})
    return {"ai_employees": out, "discovered": discovered,
            "online_count": sum(1 for x in out if x["online"])}


class AiEmployeePatchIn(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[List[str]] = None
    status: Optional[str] = None


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
