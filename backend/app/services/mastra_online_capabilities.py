from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def _schema(properties: Dict[str, Any], required: list[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


_ONLINE_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "online.image_studio": {
        "name": "图片创作",
        "description": "在 Online 图片创作工作台生成图片，支持提示词、参考图、比例、质量和背景参数。",
        "keywords": ["生成图片", "图片创作", "参考图", "海报", "配图"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "image_studio_generate",
        "arg_schema": _schema(
            {
                "prompt": {"type": "string", "description": "完整图片需求"},
                "reference_image_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "model": {"type": "string", "default": "gpt-image-2"},
                "aspect_ratio": {"type": "string", "default": "9:16"},
                "quality": {"type": "string", "default": "high"},
                "background": {"type": "string", "default": "auto"},
            },
            ["prompt"],
        ),
    },
    "online.digital_human_v2": {
        "name": "数字人 2.0",
        "description": "调用 Online 数字人 2.0 工作台生成口播视频，使用用户已配置的数字人和声音。",
        "keywords": ["数字人", "口播", "形象分身", "声音", "视频"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "shanjian_digital_human_video",
        "arg_schema": _schema(
            {
                "script": {"type": "string", "description": "口播文案"},
                "title": {"type": "string"},
                "profile_id": {"type": "string", "description": "可选数字人配置 ID"},
                "virtualman_id": {"type": "string", "description": "可选数字人 ID"},
                "voice": {"type": "string", "description": "可选声音分身 ID；省略时使用用户默认声音"},
                "aspect_ratio": {"type": "string", "default": "9:16"},
            },
            ["script"],
        ),
    },
    "online.local_bestseller_plan": {
        "name": "同城爆款内容规划",
        "description": "根据用户 IP 人设生成同城爆款内容规划；服务器会补齐已保存的人设资料。",
        "keywords": ["同城爆款", "内容规划", "IP人设", "日更计划"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "local_bestseller_plan",
        "arg_schema": _schema({"days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30}}),
    },
    "online.local_bestseller_scene": {
        "name": "同城爆款分镜",
        "description": "根据用户 IP 人设批量生成同城爆款分镜和画面。",
        "keywords": ["同城爆款", "分镜", "场景图", "IP人设"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "local_bestseller_scene_batch",
        "arg_schema": _schema(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30},
                "model": {"type": "string", "default": "gpt-image-2"},
                "quality": {"type": "string", "default": "high"},
            }
        ),
    },
    "online.local_bestseller_video": {
        "name": "同城爆款视频",
        "description": "按指定天数和第几天方案生成同城爆款场景与视频；服务器会补齐已保存的人设资料。",
        "keywords": ["同城爆款", "爆款视频", "日更视频", "IP人设"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "local_bestseller_daily_video",
        "arg_schema": _schema(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30},
                "day": {"type": "integer", "minimum": 1, "maximum": 30, "default": 1},
                "model": {"type": "string", "default": "gpt-image-2"},
                "quality": {"type": "string", "default": "high"},
                "video_model": {"type": "string"},
            }
        ),
    },
    "online.wechat_takeover": {
        "name": "个人微信接管",
        "description": "接管本机个人微信：首次检查好友申请，然后在上一轮结束 15 秒后继续巡检，默认持续 30 分钟。",
        "keywords": ["个人微信", "微信接管", "自动回复", "好友申请", "私信"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_wechat_poll",
        "arg_schema": _schema(
            {
                "account_id": {"type": "string", "default": "pc-wechat-default"},
                "message_poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 15},
                "takeover_session_minutes": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30},
            }
        ),
    },
    "online.wechat_add_friend": {
        "name": "个人微信加好友",
        "description": "通过本机个人微信按手机号或微信号添加好友。",
        "keywords": ["微信", "加好友", "手机号", "好友申请"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_wechat_add_friend",
        "arg_schema": _schema(
            {
                "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "apply_message": {"type": "string"},
                "remark": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "permission": {"type": "string", "default": "朋友圈"},
            },
            ["targets"],
        ),
    },
    "online.wechat_moments_engage": {
        "name": "朋友圈互动",
        "description": "在本机个人微信朋友圈对指定联系人执行点赞或评论。",
        "keywords": ["朋友圈", "点赞", "评论", "互动"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_wechat_moments_engage",
        "arg_schema": _schema(
            {
                "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "moment_action": {"type": "string", "enum": ["like", "comment", "like_comment", "both"], "default": "like_comment"},
                "max_scrolls": {"type": "integer", "minimum": 1, "maximum": 30, "default": 6},
            },
            ["targets"],
        ),
    },
    "online.moments_generate_images": {
        "name": "朋友圈文案出图",
        "description": "为已生成的朋友圈文案批量生成配图，并将图片写回内容记录。",
        "keywords": ["朋友圈", "文案出图", "朋友圈配图", "内容记录"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "ip_moments_generate_images",
        "arg_schema": _schema(
            {
                "record_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 50},
                "model": {"type": "string", "default": "gpt-image-2"},
                "quality": {"type": "string", "default": "high"},
            },
            ["record_ids"],
        ),
    },
    "online.publish_content": {
        "name": "发布内容",
        "description": "通过 Online 已登录的平台账号发布图片、视频或朋友圈内容。",
        "keywords": ["发布", "朋友圈", "视频号", "抖音", "小红书", "平台账号"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "publish_content",
        "arg_schema": _schema(
            {
                "platform": {"type": "string"},
                "asset_id": {"type": "string"},
                "url": {"type": "string"},
                "account_id": {"type": "string"},
                "account_nickname": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "string"},
                "media_type": {"type": "string"},
            },
            ["platform"],
        ),
    },
}


def mastra_online_capabilities() -> Dict[str, Dict[str, Any]]:
    return _ONLINE_CAPABILITIES


def mastra_online_capability(capability_id: str) -> Dict[str, Any] | None:
    return _ONLINE_CAPABILITIES.get(str(capability_id or "").strip().lower())


class OnlineCapabilityParamsError(ValueError):
    pass


def _param_error(path: str, message: str) -> OnlineCapabilityParamsError:
    return OnlineCapabilityParamsError(f"{path or 'params'} {message}")


def _validate_value(value: Any, schema: Dict[str, Any], path: str, *, depth: int = 0) -> Any:
    if depth > 8:
        raise _param_error(path, "嵌套层级过深")
    expected = str(schema.get("type") or "").strip().lower()
    if expected == "string":
        if not isinstance(value, str):
            raise _param_error(path, "必须是文本")
        minimum = int(schema.get("minLength") or 0)
        maximum = int(schema.get("maxLength") or 20_000)
        if len(value) < minimum:
            raise _param_error(path, f"长度不能少于 {minimum}")
        if len(value) > maximum:
            raise _param_error(path, f"长度不能超过 {maximum}")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _param_error(path, "必须是整数")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise _param_error(path, f"不能小于 {schema['minimum']}")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise _param_error(path, f"不能大于 {schema['maximum']}")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _param_error(path, "必须是数字")
        if "minimum" in schema and value < float(schema["minimum"]):
            raise _param_error(path, f"不能小于 {schema['minimum']}")
        if "maximum" in schema and value > float(schema["maximum"]):
            raise _param_error(path, f"不能大于 {schema['maximum']}")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise _param_error(path, "必须是布尔值")
    elif expected == "array":
        if not isinstance(value, list):
            raise _param_error(path, "必须是列表")
        minimum = int(schema.get("minItems") or 0)
        maximum = int(schema.get("maxItems") or 100)
        if len(value) < minimum:
            raise _param_error(path, f"至少需要 {minimum} 项")
        if len(value) > maximum:
            raise _param_error(path, f"最多支持 {maximum} 项")
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        value = [
            _validate_value(item, item_schema, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    elif expected == "object":
        if not isinstance(value, dict):
            raise _param_error(path, "必须是对象")
        value = _normalize_object(value, schema, path, depth=depth + 1)

    allowed = schema.get("enum") if isinstance(schema.get("enum"), list) else []
    if allowed and value not in allowed:
        raise _param_error(path, f"只支持：{', '.join(str(item) for item in allowed)}")
    return value


def _normalize_object(
    params: Dict[str, Any],
    schema: Dict[str, Any],
    path: str = "params",
    *,
    depth: int = 0,
) -> Dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = {
        str(item).strip()
        for item in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if str(item).strip()
    }
    output: Dict[str, Any] = {}
    for key, definition in properties.items():
        field_schema = definition if isinstance(definition, dict) else {}
        if key in params:
            output[key] = _validate_value(params[key], field_schema, f"{path}.{key}", depth=depth)
        elif "default" in field_schema:
            output[key] = deepcopy(field_schema["default"])
        elif key in required:
            raise _param_error(f"{path}.{key}", "不能为空")

    allow_extra = bool(schema.get("additionalProperties", True))
    for key, value in params.items():
        if key in properties:
            continue
        if not allow_extra:
            raise _param_error(f"{path}.{key}", "不是受支持的参数")
        output[key] = value

    for key in required:
        value = output.get(key)
        if isinstance(value, str) and not value.strip():
            raise _param_error(f"{path}.{key}", "不能为空")
        if isinstance(value, list) and not value:
            raise _param_error(f"{path}.{key}", "不能为空")
    return output


def normalize_mastra_online_params(capability_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    definition = mastra_online_capability(capability_id)
    if definition is None:
        raise OnlineCapabilityParamsError("不支持的 Online 能力")
    if not isinstance(params, dict):
        raise OnlineCapabilityParamsError("params 必须是对象")
    schema = definition.get("arg_schema") if isinstance(definition.get("arg_schema"), dict) else _schema({})
    return _normalize_object(params, schema)
