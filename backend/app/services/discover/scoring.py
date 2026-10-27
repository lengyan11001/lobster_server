"""蓝海选题加权评分（社媒口径，对齐 Easel scoring-model）。"""
from __future__ import annotations

from typing import Any, Dict, List

WEIGHTS = {"demand": 0.30, "competition": 0.25, "match": 0.20, "cost": 0.15, "timeliness": 0.10}


def score(demand: float, competition: float, match: float, cost: float, timeliness: float) -> float:
    values = {
        "demand": max(1.0, min(10.0, demand)),
        "competition": max(1.0, min(10.0, competition)),
        "match": max(1.0, min(10.0, match)),
        "cost": max(1.0, min(10.0, cost)),
        "timeliness": max(1.0, min(10.0, timeliness)),
    }
    return round(sum(values[k] * w for k, w in WEIGHTS.items()), 1)


def bucket(value: float) -> str:
    if value >= 7:
        return "high"
    if value >= 5:
        return "mid"
    return "watch"


def rank(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in items:
        item["score"] = score(item.get("demand", 5), item.get("competition", 5),
                              item.get("match", 5), item.get("cost", 5), item.get("timeliness", 5))
        item["bucket"] = bucket(item["score"])
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def selftest() -> List[str]:
    problems: List[str] = []
    if score(10, 10, 10, 10, 10) != 10.0:
        problems.append("max score should be 10")
    if bucket(7.5) != "high" or bucket(5.5) != "mid" or bucket(3) != "watch":
        problems.append("bucket mapping wrong")
    ranked = rank([{"name": "a", "demand": 9, "competition": 8, "match": 8, "cost": 8, "timeliness": 9}])
    if ranked[0]["bucket"] != "high":
        problems.append("ranking bucket wrong")
    return problems
