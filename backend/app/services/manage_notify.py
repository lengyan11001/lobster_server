"""manage（项目 / 任务）→ 飞书播报：把任务情况和进度推给员工。

多租户：**每个公司用老板自己配的飞书自建应用**（m_integration，App Secret 加密存储）；
没配就回退到服务端 .env 的凭证（FEISHU_APP_ID / FEISHU_APP_SECRET / MANAGE_FEISHU_WEBHOOK）。

身份：员工用手机号登录，所以成员 → users.phone →（mobiles→open_id）发私聊；
带邮箱的成员用邮箱兜底。

播报四类：
  1) 每日 09:00 今日任务（按项目分组、逾期高亮）；配了应用再给每个员工私聊自己那份；
  2) 每日 17:00 逾期提醒（没有逾期不发）；
  3) 项目体检后收口卡片；
  4) 派活 / 换负责人的新任务卡片。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..manage_models import MCompany, MIntegration, MMembership, MPlanNode, MProject
from ..models import User
from .brand_context import phone_from_account_email
from . import feishu_bot, integration_secrets

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))
MANAGE_HOME = "https://manage.bhzn.top"
FEISHU_KIND = "feishu"
DONE_STATUSES = {"completed", "done", "cancelled"}
STATUS_TEXT = {
    "not_started": "未开始", "in_progress": "进行中", "blocked": "受阻",
    "completed": "已完成", "done": "已完成", "cancelled": "已取消",
    "risk": "风险",
}
_MAX_LINES = 40


def today_str() -> str:
    return datetime.now(BEIJING).date().isoformat()


# ── 公司级飞书配置 ──

def get_integration(db: Session, company_id: int) -> Optional[MIntegration]:
    return (db.query(MIntegration)
            .filter(MIntegration.company_id == int(company_id), MIntegration.kind == FEISHU_KIND)
            .first())


def company_feishu_config(db: Session, company, *, ignore_enabled: bool = False) -> Dict[str, Any]:
    """公司自己的飞书配置；没配/没开就回退服务端 env。company 可传对象或 company_id。

    ignore_enabled=True 用于「自检」：即使没勾「开启推送」，也要能验证已保存的凭证。
    """
    company_id = int(company.id) if hasattr(company, "id") else int(company)
    row = get_integration(db, company_id)
    if row is not None and (bool(row.enabled) or ignore_enabled):
        secret = integration_secrets.decrypt(str(row.app_secret_enc or ""))
        if str(row.app_id or "").strip() or str(row.webhook or "").strip():
            return {"source": "company", "enabled": bool(row.enabled), "disabled_saved": not bool(row.enabled),
                    "app_id": str(row.app_id or "").strip(), "app_secret": secret,
                    "webhook": str(row.webhook or "").strip()}
    env_app = feishu_bot._env("FEISHU_APP_ID")
    env_secret = feishu_bot._env("FEISHU_APP_SECRET")
    env_hook = feishu_bot.webhook_url()
    if env_app or env_hook:
        return {"source": "env", "enabled": True, "app_id": env_app, "app_secret": env_secret,
                "webhook": env_hook}
    return {"source": "none", "enabled": False, "app_id": "", "app_secret": "", "webhook": ""}


def _can_send(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("enabled")) and bool(cfg.get("webhook") or cfg.get("app_id"))


def _can_private(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("app_id") and cfg.get("app_secret"))


def member_identifier(db: Session, member: MMembership) -> tuple:
    """员工身份：优先绑定的 open_id > 登录账号手机号 > 邮箱。返回 (值, 类型)。"""
    bound = str(getattr(member, "feishu_open_id", "") or "").strip()
    if bound:
        return bound, "open_id"
    phone = ""
    if getattr(member, "user_id", None):
        user = db.query(User).filter(User.id == int(member.user_id)).first()
        # 手机号登录：账号邮箱形如 13800000000@sms.lobster.local（见 brand_context.phone_from_account_email）
        phone = phone_from_account_email(str(getattr(user, "email", "") or "")) if user is not None else ""
    if phone:
        return phone, "mobile"
    email = str(getattr(member, "email", "") or "").strip()
    if email:
        return email, "email"
    return "", ""


async def member_open_id(db: Session, member: MMembership, cfg: Dict[str, Any], *, client=None) -> str:
    """把员工换成 open_id（已经是 open_id 的直接返回）。"""
    ident, kind = member_identifier(db, member)
    if not ident:
        return ""
    if kind == "open_id":
        return ident
    try:
        return await feishu_bot.resolve_open_id(ident, kind=kind, app_id=cfg.get("app_id", ""),
                                                app_secret=cfg.get("app_secret", ""), client=client)
    except Exception as exc:
        logger.warning("[manage-notify] 身份转换失败 member=%s kind=%s: %s", member.id, kind, exc)
        return ""

# ── 任务口径 ──

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
    end, start = _parse_day(node.end_at), _parse_day(today)
    return bool(end and start and end < start)


def overdue_days(node: MPlanNode, today: str) -> int:
    end, start = _parse_day(node.end_at), _parse_day(today)
    if not (end and start):
        return 0
    return max(0, (start - end).days)


def task_line(node: MPlanNode, *, today: str) -> str:
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
    ids = [int(c) for c in company_ids if c is not None]
    if not ids:
        return []
    rows = (db.query(MPlanNode)
            .filter(MPlanNode.company_id.in_(ids))
            .filter(~MPlanNode.status.in_(list(DONE_STATUSES)))
            .order_by(MPlanNode.end_at.asc(), MPlanNode.project_id.asc(), MPlanNode.order_index.asc())
            .all())
    return list(rows)[: max(1, int(limit))]


def digest_lines(db: Session, company: MCompany, *, today: str = "", limit: int = _MAX_LINES) -> Dict[str, Any]:
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
    day = today or today_str()
    rows = (db.query(MPlanNode)
            .filter(MPlanNode.company_id == int(company.id))
            .filter(MPlanNode.owner_kind == "member")
            .filter(MPlanNode.owner_membership_id == int(membership.id))
            .filter(~MPlanNode.status.in_(list(DONE_STATUSES)))
            .order_by(MPlanNode.end_at.asc())
            .limit(max(1, int(limit))).all())
    if not rows:
        return []
    return ["你手上未完成 %d 条：" % len(rows)] + ["- " + task_line(n, today=day) for n in rows]


# ── 发送 ──

async def _send_group(cfg, title, lines, *, template="blue", note="", link=MANAGE_HOME,
                      link_text="打开管理台", client=None):
    if not cfg.get("webhook"):
        return {"ok": False, "skipped": "公司/服务端未配置群 webhook"}
    try:
        return await feishu_bot.send_card(title, lines, template=template, note=note, link=link,
                                          link_text=link_text, webhook=cfg["webhook"], client=client)
    except Exception as exc:
        logger.warning("[manage-notify] 群卡片失败: %s", exc)
        return {"ok": False, "error": str(exc)[:200], "hint": feishu_bot.hint_for(exc)}


async def _send_private(cfg, open_id, title, lines, *, template="blue", client=None):
    if not _can_private(cfg) or not open_id:
        return False
    try:
        card = feishu_bot.build_card(title, lines, template=template,
                                     note="来自 manage.bhzn.top 项目看板", link=MANAGE_HOME)
        await feishu_bot.send_card_to_user(open_id, card, app_id=cfg.get("app_id", ""),
                                           app_secret=cfg.get("app_secret", ""), client=client)
        return True
    except Exception as exc:
        logger.warning("[manage-notify] 私聊失败 open_id=%s: %s", open_id, exc)
        return False


async def send_daily_digest(db: Session, company: MCompany, *, today: str = "",
                            client=None) -> Dict[str, Any]:
    """群里发今日任务卡片；配了（公司的）自建应用再给每个有任务的成员私聊他自己那份。"""
    day = today or today_str()
    cfg = company_feishu_config(db, company)
    payload = digest_lines(db, company, today=day)
    result: Dict[str, Any] = {"company_id": int(company.id), "source": cfg["source"],
                              "group": None, "private": 0, "unreachable": 0}
    result["group"] = await _send_group(cfg, payload["title"], payload["lines"],
                                        template=payload["template"], client=client)
    if not _can_private(cfg):
        return result
    members = (db.query(MMembership)
               .filter(MMembership.company_id == int(company.id))
               .filter(MMembership.status == "active").all())
    for member in members:
        lines = member_lines(db, company, member, today=day)
        if not lines:
            continue
        oid = await member_open_id(db, member, cfg, client=client)
        if not oid:
            result["unreachable"] += 1
            continue
        if await _send_private(cfg, oid, "你的任务 · %s · %s" % (str(company.name or "公司"), day),
                               lines, client=client):
            result["private"] += 1
    return result


async def notify_overdue_only(db: Session, company: MCompany, *, today: str = "",
                              client=None) -> Dict[str, Any]:
    """只发逾期清单；没有逾期就跳过（17:00 档用，避免刷屏）。"""
    day = today or today_str()
    cfg = company_feishu_config(db, company)
    payload = digest_lines(db, company, today=day)
    if not payload["counts"]["overdue"]:
        return {"ok": False, "skipped": "no overdue", "company_id": int(company.id)}
    lines = ["逾期 %d 条，请尽快跟进：" % payload["counts"]["overdue"]] + payload["lines"]
    return await _send_group(cfg, "逾期提醒 · %s · %s" % (str(company.name or "公司"), day), lines,
                             template="red", client=client)


async def send_checkup_summary(db: Session, company: MCompany, created: Sequence[Any] = (),
                               *, today: str = "", client=None) -> Dict[str, Any]:
    """项目体检后发一条收口提醒（逾期 + 停滞）。"""
    day = today or today_str()
    cfg = company_feishu_config(db, company)
    payload = digest_lines(db, company, today=day, limit=20)
    lines = ["本轮体检生成 %d 条记录。" % len(list(created or ()))] + payload["lines"]
    return await _send_group(cfg, "项目收口 · %s · %s" % (str(company.name or "公司"), day), lines,
                             template=payload["template"], note="来自 manage.bhzn.top 项目体检",
                             client=client)


async def notify_task_assigned(db: Session, node: MPlanNode, *, project: Optional[MProject] = None,
                               actor_name: str = "", client=None) -> Dict[str, Any]:
    """派活 / 换负责人：群播报 +（配了应用）给负责人私聊。"""
    project = project or db.query(MProject).filter(MProject.id == int(node.project_id)).first()
    cfg = company_feishu_config(db, node.company_id)
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
    out = await _send_group(cfg, title, lines, template="green", client=client)
    if _can_private(cfg) and getattr(node, "owner_membership_id", None):
        member = (db.query(MMembership)
                  .filter(MMembership.id == int(node.owner_membership_id)).first())
        if member is not None:
            oid = await member_open_id(db, member, cfg, client=client)
            if await _send_private(cfg, oid, "你有新任务 · %s" % (str(node.title or "")), lines,
                                   template="green", client=client):
                out["private"] = True
    return out


async def notify_all_daily(db: Session, *, today: str = "", client=None) -> Dict[str, Any]:
    """遍历所有在用公司发今日任务（cron 用）。每个公司用自己的飞书凭证。"""
    day = today or today_str()
    companies = db.query(MCompany).filter(MCompany.status == "active").all()
    out = {"date": day, "companies": len(companies), "sent": 0, "skipped": 0, "results": []}
    for company in companies:
        cfg = company_feishu_config(db, company)
        if not _can_send(cfg):
            out["skipped"] += 1
            continue
        out["sent"] += 1
        out["results"].append(await send_daily_digest(db, company, today=day, client=client))
    logger.info("[manage-notify] daily digest date=%s companies=%s sent=%s skipped=%s",
                day, len(companies), out["sent"], out["skipped"])
    return out