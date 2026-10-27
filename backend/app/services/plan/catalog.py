"""把策划层技能注册表转成 AI 能力目录条目。"""
from __future__ import annotations

from typing import Any, Dict

from .skills import SKILLS

_USE_INSTEAD = [{"scene": "生成图片/视频", "capability": "online.image_studio"},
                {"scene": "实时热点", "capability": "online.discover_hotlist"}]


def _schema(props: Dict[str, Any], required: list[str] | None = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": True}


def _example(spec: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for key in spec.get("required") or list(spec.get("args") or {})[:1]:
        prop = (spec.get("args") or {}).get(key) or {}
        if prop.get("type") == "array":
            params[key] = ["示例"]
        elif prop.get("type") == "integer":
            params[key] = 30
        else:
            params[key] = f"{spec['name']}示例"
    return {"params": params, "note": spec["name"]}


PLAN_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    key: {
        "name": spec["name"],
        "description": spec["description"],
        "keywords": spec["keywords"],
        "execution_target": "server",
        "task_kind": "server_action",
        "action": key,
        "side_effect": "read",
        "arg_schema": _schema(spec.get("args") or {}, spec.get("required") or []),
        "layer": "plan",
        "boundary": {"not_for": ["抓取实时数据", "直接产出图片/视频"], "use_instead": _USE_INSTEAD},
        "outputs": {"fields": ["text"], "status": ["completed", "failed"]},
        "degrade": ["模型不可用时返回执行框架与输出模板，提示稍后重试"],
        "examples": [_example(spec)],
    }
    for key, spec in SKILLS.items()
}
