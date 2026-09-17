"""客户指派 + AI 规划沿用指派关系 的回归测试。

需求（2026-09-17）：
  「客户里面怎么指派到某个人的？编辑的时候增加个指派的功能。如果人工指派了，让 ai 生成的时候
    考虑这个指派关系。如果没有，把客户情况也带给 ai 让来安排，但是至少安排建议。」

覆盖：
1. 客户创建/编辑支持 owner_membership_id，且必须是自己公司的在册成员（跨公司/离职 → 400）；
2. 指派变更会写一条跟进记录（可追溯）；
3. AI 生成规划时：客户清单（含是否已指派+负责人）与指派规则一起进 payload；
4. 已人工指派的客户，任务负责人被代码强制修正为该负责人（即使模型没写/写错）；
5. 未指派客户的 AI 建议（assignments_suggestions）落成「条件」记录并随接口返回；
6. 页面包含「指派」入口（编辑下拉 + 卡片快捷指派 + 生成后的建议抽屉）。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.manage_models import (
    MCompany,
    MCondition,
    MCustomer,
    MCustomerLog,
    MMembership,
    MMembershipRole,
    MPlanNode,
    MProject,
)


class _Admin:
    id = 0
    email = "platform-admin@local"
    brand_mark = "bihuo"
    role = "admin"


@pytest.fixture
def crm(db_session, test_user):
    """一家公司 + 两个成员（林珂 / 周研）+ 两个客户（一个已指派、一个未指派）。"""
    company = MCompany(name="必火科技（测试）", short_name="必火", owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()

    def member(name: str, dept: str, role: str) -> MMembership:
        row = MMembership(company_id=company.id, display_name=name, dept=dept,
                          email=f"{name}@test.local", load_pct=50, status="active")
        db_session.add(row)
        db_session.flush()
        db_session.add(MMembershipRole(company_id=company.id, membership_id=row.id,
                                       role_code=role, level="p3"))
        return row

    lin = member("林珂", "业务中心", "sales")
    zhou = member("周研", "内容中心", "content")

    other_company = MCompany(name="别的公司", owner_user_id=test_user.id)
    db_session.add(other_company)
    db_session.flush()
    outsider = MMembership(company_id=other_company.id, display_name="外人", dept="X",
                           email="outsider@test.local", status="active")
    db_session.add(outsider)
    db_session.flush()

    assigned = MCustomer(company_id=company.id, name="华创科技", company_name="华创",
                         stage="negotiating", amount=39800, owner_membership_id=lin.id,
                         next_action="周四报价", created_by=test_user.id)
    unassigned = MCustomer(company_id=company.id, name="深视传媒", company_name="深视",
                           stage="lead", amount=0, next_action="", created_by=test_user.id)
    db_session.add_all([assigned, unassigned])
    db_session.commit()
    return {"company": company, "lin": lin, "zhou": zhou, "outsider": outsider,
            "assigned": assigned, "unassigned": unassigned, "other_company": other_company,
            # 纯整数副本：generate_plan 会关掉请求会话，之后不能再访问 ORM 属性
            "lin_id": int(lin.id), "zhou_id": int(zhou.id), "company_id": int(company.id),
            "assigned_id": int(assigned.id), "unassigned_id": int(unassigned.id)}


# ── 1/2：指派字段与校验 ────────────────────────────────────────────────────


def test_customer_json_exposes_owner(db_session, crm):
    payload = manage_api._customer_json(db_session, crm["assigned"])
    assert payload["owner_membership_id"] == crm["lin"].id
    assert payload["owner_name"] == "林珂"
    assert manage_api._customer_json(db_session, crm["unassigned"])["owner_name"] == ""


def test_assign_customer_to_member(db_session, test_user, crm):
    row = manage_api.update_customer(
        crm["unassigned"].id,
        manage_api.CustomerPatchIn(owner_membership_id=crm["zhou"].id),
        user=test_user, db=db_session,
    )
    assert row["customer"]["owner_name"] == "周研"
    logs = db_session.query(MCustomerLog).filter(
        MCustomerLog.customer_id == crm["unassigned"].id).all()
    assert any("周研" in (x.content or "") for x in logs), "指派变更要留一条跟进记录"

    # 取消指派
    cleared = manage_api.update_customer(
        crm["unassigned"].id,
        manage_api.CustomerPatchIn(owner_membership_id=None),
        user=test_user, db=db_session,
    )
    assert cleared["customer"]["owner_name"] == ""


def test_assign_rejects_foreign_or_inactive_member(db_session, test_user, crm):
    with pytest.raises(HTTPException) as err:
        manage_api.update_customer(
            crm["unassigned"].id,
            manage_api.CustomerPatchIn(owner_membership_id=crm["outsider"].id),
            user=test_user, db=db_session,
        )
    assert err.value.status_code == 400
    assert "在册成员" in str(err.value.detail)

    crm["zhou"].status = "inactive"
    db_session.commit()
    with pytest.raises(HTTPException) as err2:
        manage_api.create_customer(
            manage_api.CustomerIn(company_id=crm["company"].id, name="新客户",
                                  owner_membership_id=crm["zhou"].id),
            user=test_user, db=db_session,
        )
    assert err2.value.status_code == 400


# ── 3：AI 规划带上客户与指派规则 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_payload_carries_customers_and_rules(
    db_session, db_session_factory, test_user, crm, monkeypatch
):
    project = MProject(company_id=crm["company"].id, name="华创 0→30 万", created_by=test_user.id,
                       goal="三个月做到 30 万", start_at="2026-09-20", end_at="2026-12-20",
                       status="planning", arrangement_status="draft")
    db_session.add(project)
    db_session.commit()

    captured: dict = {}

    async def _fake_llm(token, system, payload, *, timeout=150.0):
        captured["payload"] = payload
        captured["system"] = system
        return {
            "summary": "测试规划",
            "phases": [{"title": "阶段一", "start_at": "2026-09-20", "end_at": "2026-10-20",
                        "goal": "打基础",
                        "tasks": [
                            # 已指派客户：模型写错人（周研）→ 代码必须改回林珂
                            {"title": "华创科技报价与谈判", "owner_name": "周研",
                             "requirement": "出报价单", "start_at": "2026-09-21",
                             "end_at": "2026-09-25", "kpi": "报价 1 份", "deliverable": "报价单"},
                            # 未指派客户：模型留空 → 允许（但会给建议）
                            {"title": "深视传媒首轮触达", "owner_name": "",
                             "requirement": "触达 3 次", "start_at": "2026-09-26",
                             "end_at": "2026-09-30", "kpi": "3 次", "deliverable": "记录"},
                        ]}],
            "milestones": [],
            "assignments_suggestions": [
                {"customer": "深视传媒", "suggested_owner": "林珂", "priority": "high",
                 "reason": "业务中心负责获客，且其负载适中"}
            ],
            "resource_gap": [],
        }

    monkeypatch.setattr(manage_api, "_llm_json", _fake_llm)
    monkeypatch.setattr(manage_api, "_llm_token_for_company", lambda db, company: "svc-token")
    monkeypatch.setattr(manage_api, "_save_version", lambda db, project, **kw: 1)
    # generate_plan 内部用 SessionLocal() 打开短会话（真机上就是同一套库）；
    # 测试里换成绑定到临时 SQLite 的 sessionmaker，否则查不到 m_project。
    monkeypatch.setattr(manage_api, "SessionLocal", db_session_factory)

    # generate_plan 一开始就 db.close()（真机上故意如此，避免 AI 等待期间挂着事务），
    # 所以这里用一个轻量身份对象，避免访问已脱离会话的 ORM User。
    actor = type("_Actor", (), {"id": test_user.id, "role": "user", "brand_mark": "bihuo",
                                "email": "owner@test.local"})()
    result = await manage_api.generate_plan(
        project.id, manage_api.PlanIn(mode="ai", extra_requirements="优先华创"), user=actor,
        request=None, db=db_session,
    )

    payload = captured["payload"]
    assert payload["task"] == "generate_project_plan"
    customers = {c["name"]: c for c in payload["customers"]}
    assert customers["华创科技"]["assigned"] is True
    assert customers["华创科技"]["owner_name"] == "林珂"
    assert customers["深视传媒"]["assigned"] is False
    assert any("人工指派" in rule for rule in payload["assignment_rules"])
    assert "assignments_suggestions" in captured["system"]
    assert "华创科技" in json.dumps(payload, ensure_ascii=False)

    # 任务负责人被修正：已指派客户 → 林珂（而不是模型写的周研）
    check_db = db_session_factory()
    try:
        nodes = {n.title: n for n in check_db.query(MPlanNode)
                 .filter(MPlanNode.project_id == project.id).all()}
        conds = check_db.query(MCondition).filter(
            MCondition.project_id == project.id, MCondition.source == "plan",
            MCondition.category == "people").all()
    finally:
        check_db.close()
    hc = nodes["华创科技报价与谈判"]
    assert hc.owner_name == "林珂", ascii(hc.owner_name)
    assert hc.owner_membership_id == crm["lin_id"]
    # 未指派客户的任务保持 AI 的安排（这里留空=待指派），不会被乱改
    assert nodes["深视传媒首轮触达"].owner_name == ""

    # 建议落成「条件」并随接口返回
    assert result["assignments"][0]["customer"] == "深视传媒"
    assert result["assignments"][0]["suggested_owner"] == "林珂"
    assert result["assignments"][0]["suggested_membership_id"] == crm["lin_id"]
    assert any("深视传媒" in (c.title or "") for c in conds)
    assert any(c.severity == "high" and c.need == "林珂" for c in conds)


def test_assigned_customer_in_text_matches_name_or_company():
    customers = [{"name": "华创科技", "company_name": "华创", "assigned": True,
                  "owner_name": "林珂", "membership_id": 11},
                 {"name": "深视传媒", "company_name": "深视", "assigned": False,
                  "owner_name": "", "membership_id": None}]
    hit = manage_api._assigned_customer_in_text({"title": "去华创科技签合同"}, customers)
    assert hit and hit["owner_name"] == "林珂"
    hit2 = manage_api._assigned_customer_in_text({"requirement": "对接华创的采购"}, customers)
    assert hit2 and hit2["membership_id"] == 11
    assert manage_api._assigned_customer_in_text({"title": "深视传媒首轮触达"}, customers) is None
    assert manage_api._assigned_customer_in_text({"title": "写周报"}, customers) is None


# ── 6：页面入口 ───────────────────────────────────────────────────────────


def test_manage_page_exposes_customer_assignment():
    page = pathlib.Path(__file__).resolve().parents[2] / "manage_static" / "index.html"
    html = page.read_text(encoding="utf-8", errors="surrogateescape")
    assert 'id="cuOwner"' in html and "指派负责人" in html
    assert "openAssign" in html and 'data-cassign' in html
    assert "owner_membership_id" in html
    assert "assignments" in html          # 生成后展示 AI 指派建议
    assert "客户指派建议" in html
