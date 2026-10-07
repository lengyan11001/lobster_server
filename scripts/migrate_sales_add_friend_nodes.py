#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补回被折叠逻辑删掉 / 掏空的一级「个微自动加好友」节点。

背景
----
2026-08-21 起，工作流读接口（_template_payload -> _canonical_workflow_nodes ->
_clean_legacy_sales_action_children）会把模板里所有一级 native_wechat_add_friend
节点整条丢掉（服务端已在 ee98e35 修好）。副作用：

1) 系统模板 / 镜像里节点还在，但 params 没有 source_mode，运行时是空转
   （客户端会回「本轮抖音私信未识别到客户发送的手机号，已跳过加好友」）。
2) 用户「复制」出来的副本里，这 3 个节点真的没了。

本脚本以系统模板（owner_user_id=0 + meta.system_template_key=system_sales）为准，幂等：

* 系统模板 + 镜像（meta.source=system_mirror）：给一级加好友节点补
  source_mode / max_targets / targets（只在缺省时写，不动已有值）。
* 副本（meta.copied_from=system_sales）：只处理「节点集合 = 系统模板去掉这 3 个一级
  加好友节点」的行（说明是 bug 丢的，不是用户自己删的），按时间插回去。

用法
----
    python3 scripts/migrate_sales_add_friend_nodes.py                      # dry-run
    python3 scripts/migrate_sales_add_friend_nodes.py --apply
    python3 scripts/migrate_sales_add_friend_nodes.py --apply --backup /tmp/wf_backup.json
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


SYSTEM_TEMPLATE_KEY = "system_sales"
ADD_FRIEND_ACTION = "native_wechat_add_friend"
POOL_SOURCE = "server_reported_pool"
DEFAULT_LIMIT = 50


def _is_level1_add_friend(node) -> bool:
    if not isinstance(node, dict):
        return False
    plan = node.get("plan") if isinstance(node.get("plan"), dict) else {}
    payload = plan.get("payload") if isinstance(plan.get("payload"), dict) else {}
    key = str(node.get("ability_key") or node.get("abilityKey") or "")
    action = str(payload.get("action") or "")
    if key != ADD_FRIEND_ACTION and action != ADD_FRIEND_ACTION:
        return False
    return not str(node.get("parent_node_id") or "").strip()


def _ensure_params(node) -> bool:
    plan = node.setdefault("plan", {})
    payload = plan.setdefault("payload", {})
    params = payload.setdefault("params", {})
    changed = False
    if not str(params.get("source_mode") or "").strip():
        params["source_mode"] = POOL_SOURCE
        changed = True
    if not params.get("max_targets"):
        params["max_targets"] = DEFAULT_LIMIT
        changed = True
    # Online 的节点里不存 targets（一级节点的名单不落这里），
    # 顺手清掉上一版脚本写进去的空 targets，保持两边参数一致；有内容的名单不动。
    if isinstance(params.get("targets"), list) and not params["targets"]:
        params.pop("targets", None)
        changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只打印）")
    ap.add_argument("--backup", default="", help="写库前把被改行的原始 nodes 备份到这个 JSON")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.query(H5WorkflowTemplate).all()
        catalogs = [
            r
            for r in rows
            if int(r.owner_user_id or 0) == 0
            and str((r.meta or {}).get("system_template_key") or "") == SYSTEM_TEMPLATE_KEY
        ]
        if not catalogs:
            print("找不到系统模板 system_sales，退出")
            return 2
        catalog = catalogs[0]
        donors = [n for n in (catalog.nodes or []) if _is_level1_add_friend(n)]
        if not donors:
            print("系统模板里没有一级「个微自动加好友」节点，退出")
            return 2
        donor_ids = [str(n.get("id") or "") for n in donors]
        donor_id_set = set(donor_ids)
        catalog_ids = [str(n.get("id") or "") for n in (catalog.nodes or [])]
        print("系统模板 id=%s 一级加好友节点=%s" % (catalog.id, donor_ids))

        fresh = []
        for n in donors:
            item = copy.deepcopy(n)
            _ensure_params(item)
            fresh.append(item)

        backup = {}
        touched = []

        for r in rows:
            meta = r.meta if isinstance(r.meta, dict) else {}
            key = str(meta.get("system_template_key") or "")
            source = str(meta.get("source") or "")
            is_catalog = int(r.owner_user_id or 0) == 0 and key == SYSTEM_TEMPLATE_KEY
            is_mirror = source == "system_mirror" and key == SYSTEM_TEMPLATE_KEY
            if is_catalog or is_mirror:
                nodes = copy.deepcopy(r.nodes or [])
                changed = False
                for node in nodes:
                    if _is_level1_add_friend(node) and _ensure_params(node):
                        changed = True
                if not changed:
                    continue
                backup[str(r.id)] = r.nodes
                touched.append(("params", r.id, r.owner_user_id))
                if args.apply:
                    r.nodes = nodes
                continue

            if str(meta.get("copied_from") or "") != SYSTEM_TEMPLATE_KEY:
                continue
            nodes = copy.deepcopy(r.nodes or [])
            have = {str(n.get("id") or "") for n in nodes if isinstance(n, dict)}
            missing = [i for i in catalog_ids if i not in have]
            if not missing or not set(missing) <= donor_id_set:
                continue
            nodes.extend(copy.deepcopy(n) for n in fresh if str(n.get("id") or "") not in have)
            nodes.sort(key=lambda n: str(n.get("time") or ""))
            backup[str(r.id)] = r.nodes
            touched.append(("nodes", r.id, r.owner_user_id))
            if args.apply:
                r.nodes = nodes

        print("计划改动 %d 行" % len(touched))
        for kind, tid, owner in touched:
            print("  %-6s id=%-4s owner=%s" % (kind, tid, owner))
        if args.backup and touched:
            Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False, indent=1), encoding="utf-8")
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