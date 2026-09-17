"""manage 任务时间线 / 我的任务 / AI 辅助 的回归测试。

需求（2026-09-17）：
  1) 项目列表加「时间线」二级界面：上面日期刻度，下面每个任务一根横条，跨多天合并，
     颜色区分状态（绿=完成、黄=预警、红=超期…）；
  2) 真实员工用自己的账号登录时能看到派给自己的任务，并且每条任务有「AI 辅助」按钮，
     AI 帮着写思路与执行方案。

覆盖：
- 时间线状态判定：完成/超期/预警/进行中/未开始/受阻；
- 跨多天任务合成一根条（offset_days / span_days 正确，而不是一天一个格子）；
- 时间窗裁剪（窗口外的任务不出现）；
- 我的任务：真实员工只看到 owner 是自己的；管理员看到全部；
- AI 辅助权限：负责人本人可用；无关同事 403；管理员可用；
- AI 辅助成功/失败路径（LLM 通道 mock）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.manage_models import MCompany, MMembership, MMembershipRole, MPlanNode, MProject


def _day(offset: int) -> str:
    return (datetime.utcnow() + timedelta(hours=8) + timedelta(days=offset)).date().isoformat()


class _Admin:
    id = 0
    email = "platform-admin@local"
    brand_mark = "bihuo"
    role = "admin"


@pytest.fixture
def scoped_company(db_session, test_user):
    """一家公司：test_user 是老板；再建一个「真实员工」成员，把任务派给他。"""
    company = MCompany(name="必火科技（测试）", short_name="必火", industry="企业服务",
                       owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()

    worker = MMembership(company_id=company.id, display_name="林珂", dept="业务中心",
                         email="worker@test.local", load_pct=60, status="active")
    db_session.add(worker)
    db_session.flush()
    db_session.add(MMembershipRole(company_id=company.id, membership_id=worker.id,
                                   role_code="sales", level="p3"))

    outsider = MMembership(company_id=company.id, display_name="周研", dept="内容中心",
                           email="outsider@test.local", load_pct=50, status="active")
    db_session.add(outsider)
    db_session.flush()
    db_session.add(MMembershipRole(company_id=company.id, membership_id=outsider.id,
                                   role_code="content", level="p2"))

    project = MProject(company_id=company.id, name="抖音渠道 0→30 万", created_by=test_user.id,
                       goal="3 个月把抖音渠道从 0 做到月成交 30 万", status="running",
                       arrangement_status="confirmed", start_at=_day(-10), end_at=_day(60))
    db_session.add(project)
    db_session.flush()

    def node(title: str, *, start: str, end: str, status: str, owner=None, progress: int = 0,
             node_type: str = "task") -> MPlanNode:
        row = MPlanNode(company_id=company.id, project_id=project.id, node_type=node_type,
                        title=title, detail="", owner_kind="member" if owner else "unassigned",
                        owner_membership_id=(owner.id if owner else None),
                        owner_name=(owner.display_name if owner else ""),
                        requirement="每周产出 8 条短视频", start_at=start, end_at=end,
                        kpi="周均播放 5 万", deliverable="8 条成片", status=status,
                        progress=progress, weight=1, order_index=0)
        db_session.add(row)
        db_session.flush()
        return row

    done = node("选题库搭建", start=_day(-8), end=_day(-4), status="completed",
                owner=worker, progress=100)
    overdue = node("首条爆款脚本", start=_day(-6), end=_day(-2), status="in_progress",
                   owner=worker, progress=40)
    warn = node("本周成片交付", start=_day(-1), end=_day(2), status="in_progress",
                owner=worker, progress=10)
    active = node("投放测试", start=_day(1), end=_day(15), status="in_progress", owner=worker)
    pending = node("复盘 SOP", start=_day(20), end=_day(30), status="not_started", owner=outsider)
    blocked = node("直播排期", start=_day(-3), end=_day(1), status="blocked", owner=outsider)
    db_session.commit()

    worker_user = type("_U", (), {"id": 1001, "email": "worker@test.local", "role": "user",
                                  "brand_mark": "bihuo"})()
    worker_membership_id = worker.id
    return {
        "company": company,
        "project": project,
        "worker": worker,
        "worker_user": worker_user,
        "worker_membership_id": worker_membership_id,
        "nodes": {"done": done, "overdue": overdue, "warn": warn, "active": active,
                  "pending": pending, "blocked": blocked},
    }


def test_timeline_state_mapping(scoped_company):
    today = manage_api._today_beijing()
    nodes = scoped_company["nodes"]
    assert manage_api._timeline_state(nodes["done"], today) == "done"
    assert manage_api._timeline_state(nodes["overdue"], today) == "overdue"
    assert manage_api._timeline_state(nodes["warn"], today) == "warn"
    assert manage_api._timeline_state(nodes["active"], today) == "active"
    assert manage_api._timeline_state(nodes["pending"], today) == "pending"
    assert manage_api._timeline_state(nodes["blocked"], today) == "blocked"
    assert manage_api.TIMELINE_STATE_LABELS["done"] == "完成"
    assert manage_api.TIMELINE_STATE_LABELS["overdue"] == "超期"


def test_timeline_merges_multi_day_task_into_one_bar(db_session, test_user, scoped_company):
    payload = manage_api.tasks_timeline(
        company_id=scoped_company["company"].id, project_id=None,
        from_day=_day(-10), to_day=_day(35), actor=test_user, db=db_session,
    )
    assert payload["ok"] is True
    by_title = {t["title"]: t for t in payload["tasks"]}
    # 「投放测试」跨 15 天 → 一根 15 天的条，而不是 15 个格子
    active = by_title["投放测试"]
    assert active["span_days"] == 15
    assert active["start_day"] == _day(1) and active["end_day"] == _day(15)
    # 相对窗口起点（窗口从 _day(-10) 开始，任务从 _day(1) 开始 → 偏移 11 天）
    assert active["offset_days"] == 11
    assert active["state"] == "active"

    # 同一窗口里六种状态都有
    assert payload["summary"]["done"] >= 1
    assert payload["summary"]["overdue"] >= 1
    assert payload["summary"]["warn"] >= 1
    assert payload["summary"]["active"] >= 1
    assert payload["summary"]["pending"] >= 1
    assert payload["summary"]["blocked"] >= 1
    assert payload["task_count"] == 6
    assert payload["state_labels"]["warn"] == "预警"


def test_timeline_window_filters_out_far_tasks(db_session, test_user, scoped_company):
    payload = manage_api.tasks_timeline(
        company_id=scoped_company["company"].id, project_id=None,
        from_day=_day(-2), to_day=_day(3), actor=test_user, db=db_session,
    )
    titles = {t["title"] for t in payload["tasks"]}
    assert "复盘 SOP" not in titles          # 20 天后才开始，窗口外
    assert "选题库搭建" not in titles         # 4 天前就结束了，窗口外
    assert "本周成片交付" in titles           # 窗口内
    assert "首条爆款脚本" in titles           # 超期条也保留（方便在时间线上看到红色）
    # 与窗口有交集的任务会保留并按窗口裁剪偏移：投放测试 _day(1) 起 → 偏移 3 天
    assert titles == {"首条爆款脚本", "本周成片交付", "投放测试", "直播排期"}
    active = [t for t in payload["tasks"] if t["title"] == "投放测试"][0]
    assert active["offset_days"] == 3


def test_my_tasks_returns_only_own_tasks(db_session, scoped_company):
    worker_user = scoped_company["worker_user"]
    # 让 worker 这个「真实员工账号」在库里存在一条 active 成员关系
    db_session.add(MMembership(company_id=scoped_company["company"].id, user_id=worker_user.id,
                               display_name="林珂", dept="业务中心", email="worker@test.local",
                               status="active"))
    db_session.commit()

    payload = manage_api.tasks_mine(company_id=scoped_company["company"].id, include_done=True,
                                    actor=worker_user, db=db_session)
    titles = {t["title"] for t in payload["tasks"]}
    assert payload["scope"] == "mine"
    assert "选题库搭建" in titles and "首条爆款脚本" in titles
    assert "复盘 SOP" not in titles and "直播排期" not in titles  # 别人的任务不可见
    assert payload["summary"]["overdue"] >= 1
    # 排序：超期/预警在前，完成垫底
    assert payload["tasks"][0]["state"] in ("overdue", "warn")
    assert payload["tasks"][-1]["state"] == "done"


def test_my_tasks_admin_sees_everything(db_session, scoped_company):
    payload = manage_api.tasks_mine(company_id=scoped_company["company"].id, include_done=True,
                                    actor=_Admin(), db=db_session)
    assert payload["scope"] == "all"
    assert payload["task_count"] == 6


def test_coach_permission(db_session, test_user, scoped_company):
    worker_user = scoped_company["worker_user"]
    db_session.add(MMembership(company_id=scoped_company["company"].id, user_id=worker_user.id,
                               display_name="林珂", dept="业务中心", email="worker@test.local",
                               status="active"))
    db_session.commit()
    node = scoped_company["nodes"]["overdue"]
    outsider = type("_U", (), {"id": 2002, "email": "outsider@test.local", "role": "user",
                              "brand_mark": "bihuo"})()
    assert manage_api._can_coach(db_session, worker_user, node) is True
    assert manage_api._can_coach(db_session, test_user, node) is True   # 老板
    assert manage_api._can_coach(db_session, _Admin(), node) is True    # 平台管理员
    assert manage_api._can_coach(db_session, outsider, node) is False   # 无关同事


@pytest.mark.asyncio
async def test_coach_endpoint_returned_text(db_session, scoped_company, monkeypatch):
    worker_user = scoped_company["worker_user"]
    db_session.add(MMembership(company_id=scoped_company["company"].id, user_id=worker_user.id,
                               display_name="林珂", dept="业务中心", email="worker@test.local",
                               status="active"))
    db_session.commit()
    node = scoped_company["nodes"]["overdue"]

    captured: dict = {}

    async def _fake_llm(token, system, user_text, *, timeout=150.0):
        captured["token"] = token
        captured["system"] = system
        captured["user"] = user_text
        return "## 思路（怎么想）\n- 先定选题\n\n## 执行方案（怎么做）\n1. 今天出 3 个选题"

    monkeypatch.setattr(manage_api, "_llm_text", _fake_llm)
    monkeypatch.setattr(manage_api, "_llm_token_for_company", lambda db, company: "svc-token")
    monkeypatch.setattr(manage_api, "_audit", lambda *a, **k: None)

    result = await manage_api.task_ai_coach(
        node.id, manage_api._CoachIn(focus="只做第一周"), actor=worker_user, db=db_session
    )
    assert result["ok"] is True
    assert result["node_id"] == node.id
    assert "执行方案" in result["text"]
    assert captured["token"] == "svc-token"
    assert "只做第一周" in captured["user"]
    assert "首条爆款脚本" in captured["user"]
    assert "思路" in captured["system"]


@pytest.mark.asyncio
async def test_coach_endpoint_forbidden_for_outsider(db_session, scoped_company):
    node = scoped_company["nodes"]["overdue"]
    outsider = type("_U", (), {"id": 2002, "email": "outsider@test.local", "role": "user",
                              "brand_mark": "bihuo"})()
    with pytest.raises(HTTPException) as err:
        await manage_api.task_ai_coach(node.id, manage_api._CoachIn(), actor=outsider, db=db_session)
    assert err.value.status_code == 403
    assert "自己负责" in str(err.value.detail)


@pytest.mark.asyncio
async def test_coach_survives_long_llm_and_broken_audit(db_session, scoped_company, monkeypatch):
    """AI 调用耗时很长、审计写入又失败时，也必须把生成结果返回（线上曾因
    idle_in_transaction_session_timeout 在这里 500，把已生成的内容丢掉）。"""
    worker_user = scoped_company["worker_user"]
    db_session.add(MMembership(company_id=scoped_company["company"].id, user_id=worker_user.id,
                               display_name="林珂", dept="业务中心", email="worker@test.local",
                               status="active"))
    db_session.commit()
    node = scoped_company["nodes"]["warn"]

    class _BrokenSession:
        def __init__(self):
            raise RuntimeError("connection terminated due to idle-in-transaction timeout")

    async def _fake_llm(token, system, user_text, *, timeout=150.0):
        return "## 思路（怎么想）\n- 先拆解\n\n## 执行方案（怎么做）\n1. 今天先出 3 条"

    monkeypatch.setattr(manage_api, "_llm_text", _fake_llm)
    monkeypatch.setattr(manage_api, "_llm_token_for_company", lambda db, company: "svc-token")
    monkeypatch.setattr(manage_api, "SessionLocal", _BrokenSession)

    result = await manage_api.task_ai_coach(
        node.id, manage_api._CoachIn(), actor=worker_user, db=db_session
    )
    assert result["ok"] is True
    assert "执行方案" in result["text"]


def test_manage_page_exposes_timeline_and_my_tasks():
    import pathlib

    page = pathlib.Path(__file__).resolve().parents[2] / "manage_static" / "index.html"
    html = page.read_text(encoding="utf-8", errors="surrogateescape")
    assert 'data-go="s-my"' in html and 'id="s-my"' in html
    assert 'id="openTimeline"' in html and 'id="tlModal"' in html
    assert "/api/manage/tasks/timeline" in html
    assert "/api/manage/tasks/mine" in html
    assert "/coach" in html
    assert "tl-bar" in html and "TASK_STATE" in html


def test_timeline_auto_window_covers_task_dates(db_session, test_user):
    """不传窗口（前端「全部」）时按任务起止自适应，否则一年前的项目会看不到。"""
    company = MCompany(name="历史项目公司", owner_user_id=test_user.id)
    db_session.add(company)
    db_session.flush()
    project = MProject(company_id=company.id, name="去年的项目", created_by=test_user.id,
                       status="planning", arrangement_status="draft",
                       start_at="2025-04-01", end_at="2025-06-07")
    db_session.add(project)
    db_session.flush()
    db_session.add(MPlanNode(company_id=company.id, project_id=project.id, node_type="task",
                             title="历史任务", detail="", owner_kind="member",
                             owner_name="余功勋", requirement="r", start_at="2025-04-01",
                             end_at="2025-04-12", kpi="k", deliverable="d", status="not_started",
                             progress=0, weight=1, order_index=0))
    db_session.commit()

    payload = manage_api.tasks_timeline(
        company_id=company.id, project_id=None, from_day="", to_day="",
        actor=test_user, db=db_session,
    )
    assert payload["task_count"] == 1
    assert payload["window"]["from_day"] <= "2025-04-01"
    assert payload["window"]["to_day"] >= "2025-04-12"
    task = payload["tasks"][0]
    assert task["state"] == "overdue"          # 2025 年的任务今天看就是超期（红）
    assert task["span_days"] == 12
