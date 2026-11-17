"""清理因 installation_id 漂移而永远离线的虚拟员工槽位。

用法（默认 dry-run，只打印计划）：
    .venv/bin/python scripts/cleanup_stale_ai_employees.py
    .venv/bin/python scripts/cleanup_stale_ai_employees.py --apply

规则（2026-11-17，配合「固化槽位=唯一事实」）：
1. 槽位的 installation_id 超过 7 天没有心跳：
   · 该账号当前只有 1 个活跃 installation_id  → 回正（把槽位改成它，保留名字/备注/公司）
   · 该账号当前有多个活跃 id                    → 不猜，只报告
2. 槽位的 installation_id 从没上报过，且账号也没有活跃设备 → 归档（status=archived，不删数据）
3. 多个槽位指向同一个 installation_id → 保留最近心跳那条，其余归档
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "/opt/lobster-server" if os.path.isdir("/opt/lobster-server") else ".")

from sqlalchemy import text  # noqa: E402

from backend.app.db import SessionLocal  # noqa: E402

STALE_DAYS = 7
LIVE_MINUTES = 10


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库；不加只打印计划")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        stale_cut = now - timedelta(days=STALE_DAYS)
        live_cut = now - timedelta(minutes=LIVE_MINUTES)

        slots = db.execute(text(
            "SELECT id, company_id, name, installation_id, coalesce(status,'') AS status "
            "FROM m_ai_employee ORDER BY company_id, id"
        )).fetchall()
        pres = db.execute(text(
            "SELECT installation_id, max(last_seen_at) AS seen FROM h5_chat_device_presence GROUP BY 1"
        )).fetchall()
        seen = {str(r[0]): r[1] for r in pres if r[0]}
        live_by_user = defaultdict(list)
        for iid, ts in seen.items():
            if ts and ts > live_cut and iid.startswith("u"):
                live_by_user[iid.split("-", 1)[0]].append(iid)

        dup_counter = defaultdict(list)
        for r in slots:
            dup_counter[str(r[3] or "")].append(r)

        plan = []
        for r in slots:
            sid, cid, name, iid = int(r[0]), int(r[1]), str(r[2] or ""), str(r[3] or "")
            if str(r[4] or "") == "archived":
                continue
            last = seen.get(iid)
            pref = iid.split("-", 1)[0] if iid.startswith("u") else ""
            live_ids = live_by_user.get(pref, [])
            if iid and len(dup_counter[iid]) > 1:
                # 同一设备被登记到多个公司/多条槽位：只报告，不自动归档（可能是人工有意为之）
                plan.append((sid, cid, name, iid, "report", "重复槽位（同一 id 被多条占用）"))
                continue
            if last and last > stale_cut:
                continue
            if len(live_ids) == 1 and live_ids[0] != iid:
                plan.append((sid, cid, name, iid, "rebind:" + live_ids[0], "7 天无心跳，账号当前只有这台在线"))
            elif not live_ids:
                plan.append((sid, cid, name, iid, "archive", "账号无在线设备且槽位长期无心跳"))
            else:
                plan.append((sid, cid, name, iid, "report", "账号有多台在线设备，需人工确认"))

        if not plan:
            print("没有需要处理的槽位")
            return 0
        for sid, cid, name, iid, action, why in plan:
            print(f"[{action}] slot={sid} company={cid} name={name} iid={iid[-12:]}  # {why}")
        print(f"共 {len(plan)} 条")

        if not args.apply:
            print("dry-run：未写库。加 --apply 执行")
            return 0
        done = 0
        for sid, cid, name, iid, action, why in plan:
            if action.startswith("rebind:"):
                target = action.split(":", 1)[1]
                db.execute(text("UPDATE m_ai_employee SET installation_id = :t WHERE id = :i"),
                           {"t": target, "i": sid})
                done += 1
            elif action == "archive":
                db.execute(text("UPDATE m_ai_employee SET status = 'archived' WHERE id = :i"), {"i": sid})
                done += 1
        db.commit()
        print(f"已处理 {done} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
