"""manage（项目 / 任务）→ 飞书播报：把任务情况和进度推给员工。

数据来源就是 manage 自己的表：
  m_project（项目：name/status/progress）+ m_plan_node（节点＝任务：status/progress/owner/start_at/end_at）
  + m_membership（员工：display_name/dept/email）。

播报三类：
  1) 每日 09:00 今日任务（按项目分组，逾期高亮）；配了自建应用就给每个员工单独发他自己的那份；
  2) 每日 17:00 / 项目体检后：收口提醒（逾期 + 停滞的节点）；
  3) 派活瞬间：负责人收到「你有新任务」（阶段2 需要 open_id）。

没配 MANAGE_FEISHU_WEBHOOK / FEISHU_APP_* 时全部静默跳过，不影响业务。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..manage_models import MCompany, MMembership, MPlanNode, MProject
from . import feishu_bot

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))
MANAGE_HOME = "https://manage.bhzn.top"
DONE_STATUSES = {"completed", "done", "cancelled"}
STATUS_TEXT = {
    "not_started": "未开始", "in_progress": "进行中", "blocked": "受阻",
    "completed": "已完成", "done": "已完成", "cancelled": "已取消",
    "risk": "风险",
}
_MAX_LINES = 40


def today_str() -> str:
    return datetime.now(BEIJING).date().isoformat()


def _status_text(node: MPlanNode) -> str:
    raw = str(node.status or "").strip().lower()
    return STATUS_TEXT.get(raw, raw or "未开始")


def is_done(node: MPlanNode) -> bool:
    return str(node.status or "").strip().lower() in DONE_STATUSES


def _parse_day(value: Any) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_overdue(node: MPlanNode, today: str) -> bool:
    if is_done(node):
        return False
    end = _parse_day(node.end_at)
    start = _parse_day(today)
    return bool(end and start and end < start)


def overdue_days(node: MPlanNode, today: str) -> int:
    end = _parse_day(node.end_at)
    start = _parse_day(today)
    if not (end and start):
        return 0
    return max(0, (start - end).days)


def task_line(node: MPlanNode, *, today: str) -> str:
    """一行任务：⚠️ **标题**（负责人）· 进行中 40% · 截止 10-09（逾期 2 天）"""
    pieces = ["**%s**" % str(node.title or "").strip()]
    owner = str(node.owner_name or "").strip()
    if owner:
        pieces.append("（%s）" % owner)
    pieces.append("· %s %d%%" % (_status_text(node), int(node.progress or 0)))
    end = str(node.end_at or "").strip()[:10]
    if end:
        days = overdue_days(node, today)
        if is_overdue(node, today) and days:
            pieces.append("· 截止 %s（⚠️ 逾期 %d 天）" % (end, days))
        elif end == today:
            pieces.append("· 截止 %s（今天到期）" % end)
        else:
            pieces.append("· 截止 %s" % end)
    line = " ".join(pieces)
    return ("⚠️ " + line) if is_overdue(node, today) else line


def open_nodes(db: Session, company_ids: Sequence[int], *, today: str = "",
               limit: int = _MAX_LINES) -> List[MPlanNode]:
    """未完成的任务，逾期 / 快到期排前面。"""
    ids = [int(c) for c in company_ids if c is not None]
    if not ids:
        return []
    rows = (
        db.query(MPlanNode)
        .filter(MPlanNode.company_id.in_(ids))
        .filter(~MPlanNode.status.in_(list(DONE_STATUSES)))
        .order_by(MPlanNode.end_at.asc(), MPlanNode.project_id.asc(), MPlanNode.order_index.asc())
        .all()
    )
    return list(rows)[: max(1, int(limit))]


def digest_lines(db: Session, company: MCompany, *, today: str = "", limit: int = _MAX_LINES) -> Dict[str, Any]:
    """一张「今日任务」卡片的内容：按项目分组 + 合计。"""
    day = today or today_str()
    nodes = open_nodes(db, [int(company.id)], today=day, limit=limit)
    projects = {int(p.id): p for p in db.query(MProject).filter(MProject.company_id == int(company.id)).all()}
    groups: Dict[int, List[MPlanNode]] = {}
    for node in nodes:
        groups.setdefault(int(node.project_id), []).append(node)

    lines: List[str] = []
    overdue = 0
    for pid in sorted(groups.keys()):
        project = projects.get(pid)
        head = "**%s**" % (str(project.name) if project else "项目 #%d" % pid)
        if project is not None:
            head += " · %s %d%%" % (str(project.status or ""), int(project.progress or 0))
        lines.append(head)
        for node in groups[pid]:
            if is_overdue(node, day):
                overdue += 1
            lines.append("  " + task_line(node, today=day))
        lines.append("")
    counts = {
        "tasks": len(nodes),
        "overdue": overdue,
        "in_progress": sum(1 for n in nodes if str(n.status or "").lower() == "in_progress"),
        "not_started": sum(1 for n in nodes if str(n.status or "").lower() == "not_started"),
    }
    if not nodes:
        lines.append("今天没有未完成的任务 🎉")
    lines.append("合计：未完成 %d · 进行中 %d · 未开始 %d · 逾期 %d" % (
        counts["tasks"], counts["in_progress"], counts["not_started"], counts["overdue"]))
    return {"title": "今日任务 · %s · %s" % (str(company.name or "公司"), day),
            "lines": [ln for ln in lines if ln is not None],
            "template": "orange" if overdue else "blue",
            "counts": counts}


def member_lines(db: Session, company: MCompany, membership: MMembership, *, today: str = "",
                 limit: int = 20) -> List[str]:
    """某个员工自己的任务（对应 /api/manage/tasks/mine 的口径）。"""
    day = today or today_str()
    rows = (
        db.query(MPlanNode)
        .filter(MPlanNode.company_id == int(company.id))
        .filter(MPlanNode.owner_kind == "member")
        .filter(MPlanNode.owner_membership_id == int(membership.id))
        .filter(~MPlanNode.status.in_(list(DONE_STATUSES)))
        .order_by(MPlanNode.end_at.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    if not rows:
        return []
    lines = ["你手上未完成 %d 条：" % len(rows)]
    lines += ["- " + task_line(n, today=day) for n in rows]
    return lines


async def send_daily_digest(db: Session, company: MCompany, *, today: str = "",
                            client=None) -> Dict[str, Any]:
    """群里发一张今日任务卡片；如配了自建应用，再给每个有飞书身份的成员发他自己那份。"""
    day = today or today_str()
    payload = digest_lines(db, company, today=day)
    result: Dict[str, Any] = {"company_id": int(company.id), "group": None, "private": 0}
    try:
        result["group"] = await feishu_bot.send_card(
            payload["title"], payload["lines"], template=payload["template"],
            note="来自 manage.bhzn.top 项目看板", link=MANAGE_HOME, link_text="打开管理台", client=client)
    except Exception as exc:  # 推送失败不能影响主流程
        logger.warning("[manage-notify] group card failed: %s", exc)
        result["group"] = {"ok": False, "error": str(exc)[:200]}

    if not feishu_bot.app_enabled():
        return result
    members = (db.query(MMembership)
               .filter(MMembership.company_id == int(company.id))
               .filter(MMembership.status == "active").all())
    sent = 0
    for member in members:
        lines = member_lines(db, company, member, today=day)
        if not lines:
            continue
        try:
            receive_id, id_type = await _member_receive_id(member, client=client)
            if not receive_id:
                continue
            card = feishu_bot.build_card(
                "你的任务 · %s · %s" % (str(company.name or "公司"), day), lines,
                template="blue", note="来自 manage.bhzn.top 项目看板", link=MANAGE_HOME)
            await feishu_bot.send_card_to_user(receive_id, card, receive_id_type=id_type, client=client)
            sent += 1
        except Exception as exc:
            logger.warning("[manage-notify] private card failed member=%s: %s", member.id, exc)
    result["private"] = sent
    return result


async def _member_receive_id(member: MMembership, *, client=None):
    """员工飞书身份：优先用绑定的 open_id 列，其次用邮箱换。"""
    oid = str(getattr(member, "feishu_open_id", "") or "").strip()
    if oid:
        return oid, "open_id"
    email = str(getattr(member, "email", "") or "").strip()
    if email:
        oid = await feishu_bot.resolve_open_id(email, client=client)
        if oid:
            return oid, "open_id"
    return "", "open_id"


async def send_checkup_summary(db: Session, company: MCompany, created: Sequence[Any] = (),
                               *, today: str = "", client=None) -> Dict[str, Any]:
    """项目体检后发一条收口提醒（逾期 + 停滞）。"""
    day = today or today_str()
    payload = digest_lines(db, company, today=day, limit=20)
    title = "项目收口 · %s · %s" % (str(company.name or "公司"), day)
    lines = ["本轮体检生成 %d 条记录。" % len(list(created or ()))] + payload["lines"]
    try:
        return await feishu_bot.send_card(title, lines, template=payload["template"],
                                          note="来自 manage.bhzn.top 项目体检", link=MANAGE_HOME,
                                          client=client)
    except Exception as exc:
        logger.warning("[manage-notify] checkup card failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


async def notify_task_assigned(db: Session, node: MPlanNode, *, project: Optional[MProject] = None,
                               actor_name: str = "", client=None) -> Dict[str, Any]:
    """派活 / 换负责人：群播报 + （阶段2）给负责人私聊。"""
    project = project or db.query(MProject).filter(MProject.id == int(node.project_id)).first()
    lines = []
    if project is not None:
        lines.append("**项目**：%s" % str(project.name or ""))
    lines.append("**任务**：%s" % str(node.title or ""))
    if str(node.requirement or "").strip():
        lines.append("**要求**：%s" % str(node.requirement).strip())
    if str(node.start_at or "").strip() or str(node.end_at or "").strip():
        lines.append("**时间**：%s ~ %s" % (str(node.start_at or "?"), str(node.end_at or "?")))
    if str(node.kpi or "").strip():
        lines.append("**KPI**：%s" % str(node.kpi).strip())
    owner = str(node.owner_name or "").strip()
    lines.append("**负责人**：%s" % (owner or "（未分配）"))
    if actor_name:
        lines.append("**派单人**：%s" % actor_name)
    title = "新任务派发 · %s" % (str(project.name) if project is not None else "项目")
    try:
        return await feishu_bot.send_card(title, lines, template="green",
                                          note="来自 manage.bhzn.top 项目看板", link=MANAGE_HOME,
                                          client=client)
    except Exception as exc:
        logger.warning("[manage-notify] assign card failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


async def notify_all_daily(db: Session, *, today: str = "", client=None) -> Dict[str, Any]:
    """遍历所有在用公司发今日任务（cron 用）。"""
    day = today or today_str()
    companies = db.query(MCompany).filter(MCompany.status == "active").all()
    out = {"date": day, "companies": len(companies), "sent": 0, "skipped": 0, "results": []}
    for company in companies:
        if not feishu_bot.webhook_enabled() and not feishu_bot.app_enabled():
            out["skipped"] += 1
            continue
        res = await send_daily_digest(db, company, today=day, client=client)
        out["sent"] += 1
        out["results"].append(res)
    logger.info("[manage-notify] daily digest date=%s companies=%s sent=%s skipped=%s",
                day, len(companies), out["sent"], out["skipped"])
    return out


async def notify_overdue_only(db: Session, company: MCompany, *, today: str = "",
                              client=None) -> Dict[str, Any]:
    """只发逾期清单；没有逾期就跳过（17:00 档用，避免刷屏）。"""
    day = today or today_str()
    payload = digest_lines(db, company, today=day)
    if not payload["counts"]["overdue"]:
        return {"ok": False, "skipped": "no overdue", "company_id": int(company.id)}
    lines = ["逾期 %d 条，请尽快跟进：" % payload["counts"]["overdue"]] + payload["lines"]
    try:
        return await feishu_bot.send_card("逾期提醒 · %s · %s" % (str(company.name or "公司"), day),
                                          lines, template="red",
                                          note="来自 manage.bhzn.top 项目看板", link=MANAGE_HOME,
                                          client=client)
    except Exception as exc:
        logger.warning("[manage-notify] overdue card failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
