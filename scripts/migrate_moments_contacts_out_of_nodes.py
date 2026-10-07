#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把历史模板里「朋友圈点赞评论」节点里存的联系人清掉（改由微信协议助手-通讯录确认）。

节点只下发任务：执行时读机器上「微信协议助手 → 通讯录 → 确认为朋友圈互动联系人」那份。
只动 native_wechat_moments_engage 节点自己的 contact_wx_nos / targets，其它节点/参数不碰。

用法：
    python3 scripts/migrate_moments_contacts_out_of_nodes.py            # dry-run
    python3 scripts/migrate_moments_contacts_out_of_nodes.py --apply --backup /tmp/x.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.models import H5WorkflowTemplate  # noqa: E402


MOMENTS_ACTION = "native_wechat_moments_engage"


def _is_moments_node(node) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    key = str(node.get("ability_key") or node.get("abilityKey") or node.get("type") or node.get("action_type") or "")
    action = str(payload.get("action") or "")
    return key == MOMENTS_ACTION or action == MOMENTS_ACTION


def _clean(node) -> bool:
    if not _is_moments_node(node):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    changed = False
    for key in ("contact_wx_nos", "targets"):
        if key in params:
            params.pop(key, None)
            changed = True
    if changed:
        payload["params"] = params
        plan["payload"] = payload
        node["plan"] = plan
    return changed


def _walk(nodes, counter) -> bool:
    changed = False
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        if _clean(node):
            changed = True
            counter[0] += 1
        for child_key in ("children", "actions"):
            if _walk(node.get(child_key), counter):
                changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup", default="")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.query(H5WorkflowTemplate).all()
        backup = {}
        touched = []
        for row in rows:
            nodes = copy.deepcopy(row.nodes or [])
            counter = [0]
            if not _walk(nodes, counter):
                continue
            backup[str(row.id)] = row.nodes
            touched.append((row.id, row.owner_user_id, counter[0]))
            if args.apply:
                row.nodes = nodes
        print("计划清理 %d 行" % len(touched))
        for tid, owner, n in touched:
            print("  id=%-4s owner=%-5s nodes_cleaned=%s" % (tid, owner, n))
        if args.backup and touched:
            Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
            print("备份写入 %s（%d 行）" % (args.backup, len(backup)))
        if args.apply:
            db.commit()
            print("已提交" if touched else "无需改动")
        else:
            print("dry-run：没有写库（加 --apply 才写）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())