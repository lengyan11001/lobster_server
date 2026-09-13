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

from ..db import get_db
from ..manage_models import (
    MAuditLog,
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
from ..models import User
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/manage", tags=["manage"])

ROLE_LABEL = {
    "boss": "老板", "gm": "总经理", "bd": "商务", "sales": "业务", "delivery": "交付",
    "support": "客服", "finance": "财务", "operation": "运营", "content": "内容",
    "market": "市场", "hr": "人事", "supply": "采购",
}
FULL_ACCESS = {"boss", "gm"}


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


class AcceptIn(BaseModel):
    action_index: int = 0


class BossIn(BaseModel):
    user_id: int
    company_name: str = ""


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


@router.get("/bootstrap")
def bootstrap(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
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
def directory_lookup(q: str = Query("", max_length=120), user: User = Depends(get_current_user),
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
def create_company(body: CompanyIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = MCompany(name=body.name.strip(), short_name=body.short_name.strip(),
                       industry=body.industry.strip(), owner_user_id=user.id)
    db.add(company)
    db.flush()
    membership = MMembership(company_id=company.id, user_id=user.id,
                             display_name=user.email.split("@")[0], dept="经营管理", remark="创建者")
    db.add(membership)
    db.flush()
    db.add(MMembershipRole(company_id=company.id, membership_id=membership.id, role_code="boss", level="p4"))
    _audit(db, company.id, user.id, "company.create", "company", company.id, {"name": company.name})
    db.commit()
    return {"ok": True, "company_id": company.id}


@router.get("/members")
def list_members(company_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MMembership)
            .filter(MMembership.company_id == company_id, MMembership.status == "active")
            .order_by(MMembership.id).all())
    return {"members": [_member_json(db, m) for m in rows]}


@router.post("/members")
def add_member(body: MemberIn, user: User = Depends(get_current_user),
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


@router.get("/products")
def list_products(company_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = db.query(MProduct).filter(MProduct.company_id == company_id).order_by(MProduct.id).all()
    return {"products": [{"id": p.id, "name": p.name, "price": float(p.price or 0), "unit": p.unit,
                          "cycle_days": p.cycle_days, "deliverable": p.deliverable,
                          "description": p.description, "status": p.status} for p in rows]}


@router.post("/products")
def create_product(body: ProductIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    row = MProduct(company_id=company.id, name=body.name.strip(), price=Decimal(str(body.price or 0)),
                   unit=body.unit, cycle_days=body.cycle_days, deliverable=body.deliverable,
                   description=body.description)
    db.add(row)
    _audit(db, company.id, user.id, "product.create", "product", "", {"name": row.name})
    db.commit()
    return {"ok": True, "product_id": row.id}


@router.get("/projects")
def list_projects(company_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = db.query(MProject).filter(MProject.company_id == company_id).order_by(MProject.id.desc()).all()
    return {"projects": [_project_json(p) for p in rows]}


@router.post("/projects")
def create_project(body: ProjectIn, user: User = Depends(get_current_user),
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


@router.get("/projects/{project_id}")
def project_detail(project_id: int, user: User = Depends(get_current_user),
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


def _template_plan(project: MProject, members: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = [m["name"] for m in members] or ["待指派"]
    start = project.start_at or date.today().isoformat()
    end = project.end_at or ""
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
        "summary": project.name + "：按 " + str(span) + " 天周期拆成 4 个阶段，先跑通再放量。",
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
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = _require_company(db, project.company_id, user)
    _require_plan_admin(db, company, user)

    members = (db.query(MMembership)
               .filter(MMembership.company_id == company.id, MMembership.status == "active").all())
    member_payload = []
    for m in members:
        roles = db.query(MMembershipRole).filter(MMembershipRole.membership_id == m.id).all()
        member_payload.append({"membership_id": m.id, "name": m.display_name,
                               "roles": [{"code": r.role_code, "level": r.level} for r in roles]})

    if body.mode == "template":
        plan = _template_plan(project, member_payload)
        source = "template"
    else:
        auth = str(request.headers.get("authorization") or "")
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(status_code=401, detail="缺少登录令牌，无法调用 AI")
        # PostgreSQL 的 idle-in-transaction 超时会在我们等 AI 的几十秒里掐掉连接，
        # 所以先把事务收掉，AI 返回后再开新事务写库。
        db.rollback()
        plan = await _llm_json(token, PLAN_SYSTEM, {
            "task": "generate_project_plan",
            "project": {"name": project.name, "goal": project.goal,
                        "success_criteria": project.success_criteria,
                        "start_at": project.start_at, "end_at": project.end_at},
            "products": project.products or [],
            "members": member_payload,
            "extra_requirements": body.extra_requirements,
        })
        source = "ai"

    # 事务在 AI 调用前已结束，重新取一下对象（含模板分支的一致性）
    project = db.query(MProject).filter(MProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    company = db.query(MCompany).filter(MCompany.id == project.company_id).first()

    db.query(MPlanNode).filter(MPlanNode.project_id == project.id).delete()
    order = 0
    by_name = {m["name"]: m for m in member_payload}
    for phase in plan.get("phases") or []:
        order += 1
        ph = MPlanNode(company_id=company.id, project_id=project.id, node_type="phase",
                       title=str(phase.get("title") or "阶段"), detail=str(phase.get("goal") or ""),
                       start_at=str(phase.get("start_at") or ""), end_at=str(phase.get("end_at") or ""),
                       owner_name=str(phase.get("owner_name") or ""),
                       requirement=str(phase.get("goal") or ""), order_index=order)
        db.add(ph)
        db.flush()
        for task in phase.get("tasks") or []:
            order += 1
            owner = str(task.get("owner_name") or "").strip()
            mid = by_name.get(owner, {}).get("membership_id")
            db.add(MPlanNode(
                company_id=company.id, project_id=project.id, parent_id=ph.id, node_type="task",
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
        db.add(MPlanNode(company_id=company.id, project_id=project.id, node_type="milestone",
                         title=str(ms.get("title") or "里程碑"), start_at=str(ms.get("at") or ""),
                         end_at=str(ms.get("at") or ""), requirement=str(ms.get("criteria") or ""),
                         order_index=order))

    project.plan_source = source
    project.arrangement_status = "draft"
    project.status = "planning"

    gaps = plan.get("resource_gap") or []
    db.query(MCondition).filter(MCondition.project_id == project.id,
                                MCondition.source == "plan").delete()
    for g in gaps:
        db.add(MCondition(
            company_id=company.id, project_id=project.id,
            category=str(g.get("category") or "people"), title=str(g.get("title") or "条件"),
            need=str(g.get("need") or ""), now_state=str(g.get("now") or ""),
            gap=str(g.get("gap") or ""), impact=str(g.get("impact") or ""),
            severity=str(g.get("severity") or "medium"), actions=g.get("actions") or [],
            source="plan",
        ))
    _audit(db, company.id, user.id, "plan.generate", "project", project.id,
           {"source": source, "nodes": order, "gaps": len(gaps)})
    db.commit()
    nodes = (db.query(MPlanNode).filter(MPlanNode.project_id == project.id)
             .order_by(MPlanNode.order_index).all())
    return {"ok": True, "source": source, "summary": plan.get("summary") or "",
            "nodes": [_node_json(n) for n in nodes], "resource_gap": gaps}


@router.patch("/nodes/{node_id}")
def patch_node(node_id: int, body: NodeIn, user: User = Depends(get_current_user),
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
def save_draft(project_id: int, body: DraftIn, user: User = Depends(get_current_user),
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
def confirm_arrangement(project_id: int, user: User = Depends(get_current_user),
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


@router.post("/checkups/run")
def run_checkups(body: CheckupRunIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    company = _require_company(db, body.company_id, user)
    slot = body.slot if body.slot in ("0900", "1700") else "0900"
    today = date.today().isoformat()
    projects = db.query(MProject).filter(MProject.company_id == company.id,
                                         MProject.status != "archived").all()
    created = 0
    for project in projects:
        exists = (db.query(MCheckup)
                  .filter(MCheckup.project_id == project.id, MCheckup.slot == slot,
                          MCheckup.checked_on == today).first())
        if exists:
            continue
        db.add(_build_checkup(db, project, slot, today))
        created += 1
    _audit(db, company.id, user.id, "checkup.run", "company", company.id, {"slot": slot, "created": created})
    db.commit()
    return {"ok": True, "slot": slot, "checked_on": today, "created": created}


@router.get("/checkups")
def list_checkups(company_id: int, slot: str = "1700", user: User = Depends(get_current_user),
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
def list_conditions(company_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    _require_company(db, company_id, user)
    rows = (db.query(MCondition).filter(MCondition.company_id == company_id)
            .order_by(MCondition.id).all())
    return {"conditions": [{"id": c.id, "project_id": c.project_id, "category": c.category,
                            "title": c.title, "need": c.need, "now": c.now_state, "gap": c.gap,
                            "impact": c.impact, "severity": c.severity, "actions": c.actions or [],
                            "decided": c.decided, "source": c.source} for c in rows]}


@router.post("/conditions/{condition_id}/accept")
def accept_condition(condition_id: int, body: AcceptIn, user: User = Depends(get_current_user),
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
def list_finance(company_id: int, user: User = Depends(get_current_user),
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
def create_finance(body: FinanceIn, user: User = Depends(get_current_user),
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
def mark_boss(body: BossIn, user: User = Depends(get_current_user),
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
