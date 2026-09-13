"""manage（项目管理与 AI 赋能）独立站：与主站同仓、共用认证与数据库，独立端口运行。"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import manage_models  # noqa: F401  注册 m_ 前缀表
from .api.auth import router as auth_router
from .api.manage import router as manage_router
from .db import Base, SessionLocal, engine

logger = logging.getLogger("backend.manage_main")


def _create_manage_tables() -> None:
    tables = [t for name, t in Base.metadata.tables.items() if name.startswith("m_")]
    Base.metadata.create_all(bind=engine, tables=tables)
    logger.info("[MANAGE] tables ready: %s", ", ".join(sorted(t.name for t in tables)))


def _seed_if_needed() -> None:
    """首次部署给一个可用的样例公司（只在指定老板邮箱且其名下没有公司时执行）。"""
    email = (os.environ.get("MANAGE_SEED_OWNER_EMAIL") or "").strip().lower()
    if not email:
        return
    db = SessionLocal()
    try:
        from .manage_models import (MCondition, MCompany, MFinanceEntry, MMembership,
                                    MMembershipRole, MProduct, MProject)
        from .models import User

        owner = db.query(User).filter(User.email == email).first()
        if not owner:
            logger.warning("[MANAGE] seed skipped: owner %s not found", email)
            return
        if db.query(MCompany).filter(MCompany.owner_user_id == owner.id).first():
            return

        company = MCompany(name="必火科技（深圳）", short_name="必火", industry="企业服务",
                           owner_user_id=owner.id)
        db.add(company)
        db.flush()

        def add_member(name: str, dept: str, role: str, level: str, load: int, remark: str) -> MMembership:
            m = MMembership(company_id=company.id, display_name=name, dept=dept, load_pct=load, remark=remark)
            db.add(m)
            db.flush()
            db.add(MMembershipRole(company_id=company.id, membership_id=m.id, role_code=role, level=level))
            return m

        add_member(owner.email.split("@")[0], "经营管理", "boss", "p4", 58, "可管理多家公司，登录后切换")
        lin = add_member("林珂", "业务中心", "sales", "p3", 62, "华南大区 · 成交主力")
        add_member("小柯", "业务中心", "sales", "p1", 30, "新人 · 首月即出 2 单")
        chen = add_member("陈默", "交付中心", "delivery", "p4", 55, "交付负责人 · 验收流程 owner")
        zhou = add_member("周研", "内容中心", "content", "p2", 88, "内容产能瓶颈，建议转派虚拟员工")
        add_member("秦岚", "职能", "finance", "p3", 34, "开票 · 回款 · 对账")

        p1 = MProduct(company_id=company.id, name="抖音获客代运营", price=Decimal("39800"),
                      unit="季", cycle_days=90, deliverable="30 条短视频 + 4 场直播陪跑",
                      description="内容 + 投流 + 私信承接")
        p2 = MProduct(company_id=company.id, name="私域复购陪跑", price=Decimal("28000"),
                      unit="季", cycle_days=90, deliverable="社群 SOP + 朋友圈 + 月度复盘")
        p3 = MProduct(company_id=company.id, name="短视频批量剪辑", price=Decimal("4800"),
                      unit="100 条", cycle_days=7, deliverable="素材二创 + 字幕 + 封面")
        db.add_all([p1, p2, p3])
        db.flush()

        today = date.today()
        project = MProject(
            company_id=company.id, name="抖音渠道 0→30 万", created_by=owner.id,
            goal="3 个月把抖音渠道从 0 做到月成交 30 万",
            success_criteria="单月成交额 ≥30 万，且回款率 ≥80%",
            start_at=today.isoformat(), end_at=(today + timedelta(days=90)).isoformat(),
            status="planning", owner_membership_id=lin.id,
            products=[{"id": p1.id, "name": p1.name, "price": float(p1.price), "deliverable": p1.deliverable},
                      {"id": p3.id, "name": p3.name, "price": float(p3.price), "deliverable": p3.deliverable}],
            members=[{"id": m.id, "name": m.display_name} for m in (lin, chen, zhou)],
        )
        db.add(project)
        db.flush()

        for on, typ, cat, amt, summary in [
            ((today - timedelta(days=2)).isoformat(), "income", "代运营", 39800, "必火-华创 抖音代运营回款"),
            ((today - timedelta(days=3)).isoformat(), "expense", "投放", 18000, "巨量引擎 投放充值"),
            ((today - timedelta(days=4)).isoformat(), "expense", "云服务", 6240, "阿里云账单"),
            ((today - timedelta(days=6)).isoformat(), "income", "陪跑", 28000, "深视传媒 私域陪跑"),
            ((today - timedelta(days=7)).isoformat(), "expense", "人力", 88000, "团队工资"),
        ]:
            db.add(MFinanceEntry(company_id=company.id, entry_type=typ, category=cat,
                                 amount=Decimal(str(amt)), happened_on=on, summary=summary,
                                 created_by=owner.id))

        db.add(MCondition(company_id=company.id, project_id=project.id, category="people",
                          title="内容产能", need="内容剪辑 ≥15 条/天", now_state="内容组 1 人（p2，负载 88%）",
                          gap="缺 1 人", impact="阶段二延期约 11 天 → 成交目标后移", severity="high",
                          source="seed",
                          actions=[{"type": "hire", "what": "招聘 1 名内容剪辑 p2",
                                    "when": (today + timedelta(days=14)).isoformat()},
                                   {"type": "slot", "what": "增配 1 个虚拟员工槽位（批量剪辑）",
                                    "when": today.isoformat()}]))
        db.add(MCondition(company_id=company.id, project_id=project.id, category="device",
                          title="设备 / 虚拟员工", need="≥2 个稳定在线槽位（发布 + 承接）",
                          now_state="2 在线 / 1 离线", gap="夜间在线不稳",
                          impact="私信首响超 5 分钟会掉线索", severity="high", source="seed",
                          actions=[{"type": "slot", "what": "增配 1 个夜间槽位 + 错峰排班",
                                    "when": today.isoformat()}]))
        db.commit()
        logger.info("[MANAGE] demo seed created company=%s owner=%s", company.id, owner.id)
    except Exception:
        db.rollback()
        logger.exception("[MANAGE] seed failed")
    finally:
        db.close()


app = FastAPI(title="Lobster Manage", version="0.1.0", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup() -> None:
    try:
        _create_manage_tables()
    except Exception:
        logger.exception("[MANAGE] create tables failed")
    try:
        _seed_if_needed()
    except Exception:
        logger.exception("[MANAGE] seed step failed")


@app.get("/api/manage/health")
def manage_health() -> dict:
    return {"status": "ok", "service": "manage"}


app.include_router(auth_router, prefix="/auth")  # 与主站/h5 一致：登录 /auth/login-phone-password
app.include_router(manage_router)

_static_dir = Path(__file__).resolve().parent.parent.parent / "manage_static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="manage_static")
else:
    logger.warning("[MANAGE] static dir missing: %s", _static_dir)
