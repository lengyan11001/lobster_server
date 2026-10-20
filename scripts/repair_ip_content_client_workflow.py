# -*- coding: utf-8 -*-
"""把历史脏数据里的 client_workflow + action=ip_content_* 任务归一成服务端任务。

2026-10-20 背景：
    H5/Online 编辑器早期把「朋友圈图文 / IP口播」节点存成
        task_kind=client_workflow + payload.action=ip_content_moments
    客户端没有这个 action，一直报「暂不支持的客户端工作流」；
    10-18 加过 run 级别的归一，但没回写任务行，导致 task/run 类型打架，
    run 挂在 pending 没人执行（u54 的 15:00 朋友圈图文挂了 100 分钟）。

用法：
    python -m scripts.repair_ip_content_client_workflow            # 只统计，不改
    python -m scripts.repair_ip_content_client_workflow --apply    # 真正写库

只改 task_kind/payload，不动 status/next_run_at，暂停中的工作流不会被悄悄启用。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from backend.app.api.scheduled_tasks import (
    _SERVER_SIDE_TASK_KINDS,
    normalize_ip_content_client_workflow,
)
from backend.app.db import SessionLocal
from backend.app.models import ScheduledTask, ScheduledTaskRun


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    parser.add_argument("--user-id", type=int, default=0, help="只处理某个用户")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(ScheduledTask)
        if args.user_id:
            query = query.filter(ScheduledTask.user_id == args.user_id)
        rows = query.all()

        changed = []
        for task in rows:
            kind, payload, fixed = normalize_ip_content_client_workflow(task.task_kind, task.payload or {})
            if not fixed:
                continue
            changed.append((task, kind, payload))

        print(json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "scanned_tasks": len(rows),
                "dirty_tasks": len(changed),
                "sample": [task.id for task, _, _ in changed[:20]],
            },
            ensure_ascii=False,
        ))

        stranded = (
            db.query(ScheduledTaskRun)
            .filter(
                ScheduledTaskRun.task_kind.in_(list(_SERVER_SIDE_TASK_KINDS)),
                ScheduledTaskRun.status == "pending",
            )
            .count()
        )
        print(json.dumps({"stranded_pending_server_side_runs": stranded}, ensure_ascii=False))

        if args.apply and changed:
            now = datetime.utcnow()
            for task, kind, payload in changed:
                task.task_kind = kind
                task.payload = payload
                task.updated_at = now
            db.commit()
            print(json.dumps({"updated": len(changed)}, ensure_ascii=False))
        elif changed:
            print("dry-run：未写库。确认后加 --apply。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
