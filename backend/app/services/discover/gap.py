"""内容缺口：热搜 + 搜索联想词 → 候选选题 → 加权评分（免费数据源）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import hotlist, scoring, suggestions


def _demand_from_rank(rank: int, total: int) -> float:
    if total <= 1:
        return 8.0
    ratio = 1 - (rank / max(1, total))
    return round(3 + ratio * 6, 1)


async def build(track: str, *, platforms: Optional[List[str]] = None, limit: int = 20,
                use_cache: bool = True) -> Dict[str, Any]:
    track = str(track or "").strip()
    if not track:
        return {"track": track, "items": [], "errors": [{"error": "missing track"}]}

    hot = await hotlist.fetch(platforms or ["weibo", "douyin", "toutiao"], limit=limit, use_cache=use_cache)
    sug = await suggestions.fetch(track, use_cache=use_cache)

    items: List[Dict[str, Any]] = []
    for platform, rows in (hot.get("items") or {}).items():
        total = len(rows)
        for idx, row in enumerate(rows):
            title = row.get("title") or ""
            matched = track in title
            items.append({
                "topic": title,
                "source": f"hot:{platform}",
                "demand": _demand_from_rank(idx, total) + (1.0 if matched else 0),
                "competition": 5.0,          # 免费数据拿不到供给量，给中性分，需人工/评论佐证
                "match": 9.0 if matched else 4.0,
                "cost": 8.0,
                "timeliness": 9.0 if matched else 6.0,
                "note": "热度来源，竞争度需补充评论/搜索结果验证",
            })
    for phrase in (sug.get("items") or [])[:15]:
        items.append({
            "topic": phrase,
            "source": f"suggest:{sug.get('source', '')}",
            "demand": 7.0,
            "competition": 6.0,
            "match": 8.0,
            "cost": 9.0,
            "timeliness": 5.0,
            "note": "搜索联想词，说明有稳定检索需求",
        })

    ranked = scoring.rank(items)[:40]
    return {"track": track, "items": ranked,
            "errors": list(hot.get("errors") or []) + ([{"error": sug.get("error")}] if sug.get("error") else []),
            "sources": {"hotlist": hot.get("source") or {}, "suggest": sug.get("source") or ""}}


def format_markdown(data: Dict[str, Any]) -> str:
    lines = [f"# 蓝海选题（{data.get('track') or ''}）", "",
             "> 竞争度按中性分处理：免费数据拿不到供给量，需用评论区/搜索结果二次验证。", ""]
    labels = {"high": "🔵 高优先（≥7）", "mid": "🟢 中优先（5–7）", "watch": "⚪ 观察池（<5）"}
    for key in ("high", "mid", "watch"):
        rows = [x for x in data.get("items") or [] if x["bucket"] == key]
        if not rows:
            continue
        lines.append(f"## {labels[key]}")
        lines.append("| 选题 | 分 | 需求 | 匹配 | 来源 | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for it in rows[:10]:
            lines.append(f"| {it['topic'][:40]} | {it['score']} | {it['demand']} | {it['match']} | {it['source']} | {it['note']} |")
        lines.append("")
    if not data.get("items"):
        lines.append("没有拿到候选选题，可能热搜与联想词都为空。")
    for err in data.get("errors") or []:
        if err.get("error"):
            lines.append(f"> 数据源失败：{err}")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    if scoring.selftest():
        problems.extend(scoring.selftest())
    if hotlist.selftest():
        problems.extend(hotlist.selftest())
    if suggestions.selftest():
        problems.extend(suggestions.selftest())
    return problems
