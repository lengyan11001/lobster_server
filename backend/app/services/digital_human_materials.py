"""数字人自动选素材的唯一策略：分组内宽松随机，最多 20 条。

覆盖所有「自动选素材」入口：
1. 云端数字人成片（shanjian_digital_human._apply_asset_group_materials）
2. H5 / 小程序录音生成（h5_recorder_extract）
3. 素材混剪（services.material_mix_runner）

规则统一：不按文案关键词做闸门，只按素材分组取候选，随机挑最多 20 条。
需要严格按文案标签筛素材时，走 _material_gate_mode() = strict 的显式开关。
"""
from __future__ import annotations

import json
import random
from typing import Any, Iterable

MATERIAL_PICK_LIMIT = 20


def broad_pick(items: Iterable[Any], limit: int = MATERIAL_PICK_LIMIT) -> list[Any]:
    """分组内随机挑选，最多 limit 条。"""
    ordered = [item for item in (items or [])]
    random.shuffle(ordered)
    return ordered[: max(1, int(limit or MATERIAL_PICK_LIMIT))]


def asset_group_of(meta: Any) -> str:
    """资产归属的素材分组（与 /api/assets 的 creative_candidate_group 同义）。"""
    data = meta
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = None
    if not isinstance(data, dict):
        return ""
    name = str(data.get("creative_candidate_group") or "").strip()
    if name:
        return name
    raw = data.get("creative_candidate_groups")
    if isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if text:
                return text
    return ""


def resolve_group_materials(
    db: Any,
    user_ids: Iterable[Any],
    groups: Iterable[str],
    *,
    limit: int = MATERIAL_PICK_LIMIT,
) -> list[dict[str, str]]:
    """按分组取素材并按宽松策略挑 limit 条（image/video，带可访问 URL）。"""
    from ..models import Asset

    wanted = {str(g).strip() for g in (groups or []) if str(g).strip()}
    if not wanted:
        return []
    ids: list[int] = []
    for value in user_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    rows = db.query(Asset).filter(Asset.user_id.in_(ids)).order_by(Asset.id.desc()).all()
    pool: list[dict[str, str]] = []
    for row in rows:
        kind = str(getattr(row, "media_type", "") or "").strip().lower()
        if kind not in {"image", "video"}:
            continue
        if asset_group_of(getattr(row, "meta", None)) not in wanted:
            continue
        url = str(getattr(row, "source_url", "") or "").strip()
        if not url:
            continue
        pool.append(
            {
                "asset_id": str(getattr(row, "asset_id", "") or ""),
                "type": kind,
                "fileUrl": url,
                "name": str(getattr(row, "filename", "") or ""),
            }
        )
    return broad_pick(pool, limit)
