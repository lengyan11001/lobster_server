"""策划层七维评分（确定性计算，规则见 Easel shared/scoring-dimensions）。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

DIMENSIONS: List[Tuple[str, float, bool]] = [
    ("流量潜力", 0.25, False),
    ("账号匹配", 0.20, False),
    ("竞争差异化", 0.15, False),
    ("变现潜力", 0.15, False),
    ("时效价值", 0.10, False),
    ("制作成本", 0.08, True),
    ("合规风险", 0.07, True),
]


def _clamp(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 5.0
    return max(1.0, min(10.0, num))


def total(scores: Dict[str, Any]) -> float:
    """综合分（满分 100）。"""
    value = 0.0
    for name, weight, _reverse in DIMENSIONS:
        value += _clamp(scores.get(name)) / 10 * weight * 100
    return round(value, 1)


def verdict(value: float) -> str:
    if value >= 70:
        return "做"
    if value >= 50:
        return "改方向"
    return "不做"


def table(scores: Dict[str, Any]) -> str:
    lines = ["| 维度 | 分数 | 权重 |", "|---|---|---|"]
    for name, weight, reverse in DIMENSIONS:
        score = _clamp(scores.get(name))
        flag = "（反向）" if reverse else ""
        lines.append(f"| {name}{flag} | {score:g} | {int(weight * 100)}% |")
    value = total(scores)
    lines.append("")
    lines.append(f"**综合分 {value} / 100 → {verdict(value)}**")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    best = {name: 10 for name, _w, _r in DIMENSIONS}
    if total(best) != 100.0:
        problems.append(f"max total should be 100, got {total(best)}")
    worst = {name: 1 for name, _w, _r in DIMENSIONS}
    if total(worst) != 10.0:
        problems.append(f"min total should be 10, got {total(worst)}")
    if verdict(70) != "做" or verdict(50) != "改方向" or verdict(49.9) != "不做":
        problems.append("verdict thresholds wrong")
    return problems
