"""内容节点日历：解析 events_china.md + 农历换算，输出未来 N 天节点。

数据来源：ZJU-REAL/Easel（Apache-2.0）的 events-china.md，农历换算见 lunar.py。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import lunar

DATA = Path(__file__).with_name("data") / "events_china.md"

_LUNAR_TO_FESTIVAL = {
    "除夕": "除夕", "正月初一": "春节", "正月十五": "元宵",
    "五月初五": "端午", "七月初七": "七夕", "七月十五": "中元",
    "八月十五": "中秋", "九月初九": "重阳", "腊月初八": "腊八",
    "腊月廿三": "小年", "腊月廿四": "小年",
}
_CN_DAY = {"廿三": 23, "廿四": 24, "初八": 8}

_events_cache: Optional[List[Dict[str, Any]]] = None


def _parse_row(line: str) -> Optional[Dict[str, Any]]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 6:
        return None
    raw_date, name, kind, value, tracks, note = cells[:6]
    if raw_date in ("日期", "") or set(raw_date) <= {"-", " "}:
        return None
    entry: Dict[str, Any] = {"name": name, "type": kind, "value": value, "tracks": tracks, "note": note}
    if raw_date.startswith("农历"):
        token = raw_date[2:]
        fest = _LUNAR_TO_FESTIVAL.get(token)
        if not fest:
            return None
        entry["kind"] = "lunar"
        entry["festival"] = fest
        if token == "腊月廿四":
            entry["offset_days"] = 1
        return entry
    m = re.search(r"(\d{1,2})\.(\d{1,2})", raw_date)
    if not m:
        return None
    entry["kind"] = "fixed"
    entry["month"] = int(m.group(1))
    entry["day"] = int(m.group(2))
    return entry


def load_events() -> List[Dict[str, Any]]:
    global _events_cache
    if _events_cache is not None:
        return _events_cache
    items: List[Dict[str, Any]] = []
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        parsed = _parse_row(line)
        if parsed:
            items.append(parsed)
    _events_cache = items
    return items


def _resolve(entry: Dict[str, Any], today: date) -> Optional[date]:
    if entry["kind"] == "fixed":
        for year in (today.year, today.year + 1):
            try:
                d = date(year, entry["month"], entry["day"])
            except ValueError:
                continue
            if d >= today:
                return d
        return None
    fest = entry.get("festival")
    for solar_year in (today.year, today.year + 1):
        try:
            d = lunar.festival(fest, solar_year)
        except Exception:  # noqa: BLE001
            continue
        d = d + timedelta(days=int(entry.get("offset_days") or 0))
        if d >= today:
            return d
    return None


def upcoming(days: int = 30, *, track: str = "", kinds: Optional[List[str]] = None,
             today: Optional[date] = None) -> List[Dict[str, Any]]:
    today = today or date.today()
    horizon = today + timedelta(days=max(1, days))
    out: List[Dict[str, Any]] = []
    for entry in load_events():
        if kinds and entry["type"] not in kinds and not any(k in entry["type"] for k in kinds):
            continue
        if track and track not in entry["tracks"] and "全赛道" not in entry["tracks"]:
            continue
        d = _resolve(entry, today)
        if not d or d > horizon:
            continue
        out.append({
            "date": d.isoformat(),
            "days_left": (d - today).days,
            "name": entry["name"],
            "type": entry["type"],
            "value": entry["value"],
            "tracks": entry["tracks"],
            "note": entry["note"],
        })
    out.sort(key=lambda x: (x["days_left"], x["name"]))
    return out


def format_markdown(items: List[Dict[str, Any]], *, days: int, track: str = "") -> str:
    lines = [f"# 内容节点日历（未来 {days} 天）", f"赛道：{track or '全部'}", ""]
    if not items:
        lines.append("这段时间没有匹配的节点，可放宽天数或换赛道。")
        return "\n".join(lines)
    lines.append("| 日期 | 剩余 | 节点 | 类型 | 价值 | 建议赛道 |")
    lines.append("|---|---|---|---|---|---|")
    for it in items:
        lines.append(f"| {it['date']} | {it['days_left']}天 | {it['name']} | {it['type']} | {it['value']} | {it['tracks']} |")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    events = load_events()
    if len(events) < 20:
        problems.append(f"events parsed too few: {len(events)}")
    sample = upcoming(3650, today=date(2026, 1, 1))
    names = {x["name"] for x in sample}
    for must in ("春节", "中秋节", "双11"):
        if not any(must in n for n in names):
            problems.append(f"missing event {must}")
    return problems
