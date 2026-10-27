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
        "side_effect": "write",
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
        "side_effect": "write",
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
        "side_effect": "write",
        "arg_schema": _schema({"days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30}}),
    },
    "online.local_bestseller_scene": {
        "name": "同城爆款分镜",
        "description": "根据用户 IP 人设批量生成同城爆款分镜和画面。",
        "keywords": ["同城爆款", "分镜", "场景图", "IP人设"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "local_bestseller_scene_batch",
        "side_effect": "write",
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
        "side_effect": "write",
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
        "description": "接管本机个人微信：首次检查好友申请，然后在上一轮结束 15 秒后继续巡检，接管时长按节点结束时间换算，未配置时默认 30 分钟。",
        "keywords": ["个人微信", "微信接管", "自动回复", "好友申请", "私信"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_wechat_poll",
        "side_effect": "external",
        "arg_schema": _schema(
            {
                "account_id": {"type": "string", "default": "pc-wechat-default"},
                "message_poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 15},
                "takeover_session_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 30},
            }
        ),
    },
    "online.whatsapp_takeover": {
        "name": "个人whatapp助手",
        "description": "接管本机 Windows 桌面版 WhatsApp，按轮次读取未读私聊并自动生成回复。",
        "keywords": ["WhatsApp", "whatapp", "私信接管", "自动回复", "桌面客户端"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_whatsapp_poll",
        "side_effect": "external",
        "arg_schema": _schema(
            {
                "account_id": {"type": "string", "default": "desktop-whatsapp-default"},
                "message_poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 15},
                "takeover_session_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 30},
                "max_unread_per_round": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                "reply_instruction": {"type": "string", "maxLength": 4000, "default": ""},
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
        "side_effect": "external",
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
        "side_effect": "external",
        "arg_schema": _schema(
            {
                "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "moment_action": {"type": "string", "enum": ["like", "comment", "like_comment", "both"], "default": "like_comment"},
                "max_scrolls": {"type": "integer", "minimum": 1, "maximum": 30, "default": 6},
            },
            ["targets"],
        ),
    },
    "online.wechat_send_message": {
        "name": "微信发消息",
        "description": (
            "用本机个人微信给指定联系人或群发一条文本消息。收件人可以写微信备注名、昵称或微信号，"
            "多个目标会逐个发送；发送前会核对当前会话是否就是这个收件人，对不上就跳过并说明原因。"
        ),
        "keywords": ["微信", "发消息", "发信息", "私信", "通知", "转告", "发给", "告诉", "发这个"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "native_wechat_send_message",
        "side_effect": "external",
        "arg_schema": _schema(
            {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                    "description": "收件人：微信备注名 / 昵称 / 微信号，可传多个",
                },
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "要发送的文本内容，原样发出，不要带引号或说明",
                },
                "account_id": {"type": "string", "default": "pc-wechat-default"},
            },
            ["targets", "message"],
        ),
    },
    "online.moments_generate_images": {
        "name": "朋友圈文案出图",
        "description": "为已生成的朋友圈文案批量生成配图，并将图片写回内容记录。",
        "keywords": ["朋友圈", "文案出图", "朋友圈配图", "内容记录"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "ip_moments_generate_images",
        "side_effect": "write",
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
        "side_effect": "external",
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
    "online.browser_extract_page": {
        "name": "设备浏览器抓取网页",
        "description": "在用户已登录的 Online 设备上用浏览器打开网址，等待渲染后提取标题、正文、链接和联系方式。用于服务器抓取失败、需要登录或依赖 JS 渲染的页面。",
        "keywords": ["浏览器", "网页", "网址", "登录后抓取", "渲染", "官网"],
        "execution_target": "online",
        "task_kind": "client_workflow",
        "action": "browser_extract_page",
        "side_effect": "read",
        "arg_schema": _schema(
            {
                "url": {"type": "string", "description": "要打开的完整网址"},
                "wait_ms": {"type": "integer", "minimum": 0, "maximum": 30000, "default": 3000},
                "scroll_rounds": {"type": "integer", "minimum": 0, "maximum": 10, "default": 2},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 60000, "default": 30000},
                "click_texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                    "description": "需要自己点开的导航/入口文案，例如 联系我们、产品中心、价格",
                },
                "assist": {
                    "type": "boolean",
                    "default": False,
                    "description": "需要登录或 18+ 确认时传 true：拉起可见浏览器，让用户手动完成后自动继续提取",
                },
                "assist_timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 600, "default": 180},
            },
            ["url"],
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

# ── 2026-10-27 给 AI 的元数据补齐：layer / 边界 / 输出契约 / 降级 / 示例 ──
# 目的：模型不只知道"能做什么"，还要知道"什么时候不该用、失败怎么办、返回什么"。
CATALOG_VERSION = "2026-10-27"

_ENRICHMENT: Dict[str, Dict[str, Any]] = {
    "online.image_studio": {
        "layer": "produce",
        "boundary": {
            "not_for": ["视频", "数字人口播", "批量分镜"],
            "use_instead": [
                {"scene": "口播视频", "capability": "online.digital_human_v2"},
                {"scene": "同城爆款分镜批量出图", "capability": "online.local_bestseller_scene"},
            ],
        },
        "outputs": {"fields": ["task_id", "asset_id", "url"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["模型不支持所选比例时改为同族可用模型并提示", "参考图无法访问时忽略参考图重试一次"],
        "examples": [{"params": {"prompt": "海边日落，一位穿白裙的女生背影", "aspect_ratio": "9:16"},
                      "note": "单张竖版配图"}],
    },
    "online.digital_human_v2": {
        "layer": "produce",
        "boundary": {
            "not_for": ["纯图文", "不需要人像的口播混剪"],
            "use_instead": [{"scene": "不需要数字人形象的素材混剪", "capability": "online.image_studio"}],
        },
        "outputs": {"fields": ["task_id", "video_url", "duration"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["未选模板时只出纯口播原片（无字幕/无混剪）并明确提示", "时长被模板上限裁剪时提示实际出片时长"],
        "examples": [{"params": {"script": "今年做实体最难的是获客成本", "aspect_ratio": "9:16"},
                      "note": "15-30 秒口播"}],
    },
    "online.local_bestseller_plan": {
        "layer": "plan",
        "boundary": {"not_for": ["直接出片", "跨城市投放"],
                     "use_instead": [{"scene": "直接出片", "capability": "online.local_bestseller_video"}]},
        "outputs": {"fields": ["plan_id", "days", "topics"], "status": ["completed", "failed"]},
        "degrade": ["人设资料缺失时按行业通用模板生成并提示补充"],
        "examples": [{"params": {"days": 7}, "note": "一周选题与脚本规划"}],
    },
    "online.local_bestseller_scene": {
        "layer": "produce",
        "boundary": {"not_for": ["成片", "数字人口播"],
                     "use_instead": [{"scene": "直接成片", "capability": "online.local_bestseller_video"}]},
        "outputs": {"fields": ["task_id", "scene_ids", "image_urls"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["生图失败的分镜自动重试一次，仍失败则跳过并在结果里标注"],
        "examples": [{"params": {"days": 3, "quality": "high"}, "note": "3 天分镜批量出图"}],
    },
    "online.local_bestseller_video": {
        "layer": "produce",
        "boundary": {"not_for": ["只出图", "需要真人出镜"],
                     "use_instead": [{"scene": "只出分镜图", "capability": "online.local_bestseller_scene"},
                                     {"scene": "真人口播", "capability": "online.digital_human_v2"}]},
        "outputs": {"fields": ["task_id", "video_url", "duration"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["视频模型不可用时退回图生图方案并提示"],
        "examples": [{"params": {"days": 7, "day": 1}, "note": "第 1 天成片"}],
    },
    "online.wechat_takeover": {
        "layer": "ops",
        "boundary": {"not_for": ["企业微信", "群发营销"],
                     "use_instead": [{"scene": "主动加好友", "capability": "online.wechat_add_friend"}]},
        "outputs": {"fields": ["task_id", "handled_count", "takeover_minutes"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["微信未登录时直接失败并提示先登录 PC 微信"],
        "examples": [{"params": {"takeover_session_minutes": 30}, "note": "接管 30 分钟"}],
    },
    "online.whatsapp_takeover": {
        "layer": "ops",
        "boundary": {"not_for": ["个人微信", "手机端 WhatsApp"],
                     "use_instead": [{"scene": "个人微信", "capability": "online.wechat_takeover"}]},
        "outputs": {"fields": ["task_id", "handled_count"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["桌面客户端未启动时提示先打开 WhatsApp Desktop"],
        "examples": [{"params": {}, "note": "按默认轮次接管"}],
    },
    "online.wechat_add_friend": {
        "layer": "ops",
        "boundary": {"not_for": ["批量群发", "朋友圈互动"],
                     "use_instead": [{"scene": "朋友圈点赞评论", "capability": "online.wechat_moments_engage"}]},
        "outputs": {"fields": ["task_id", "added_count", "failed_targets"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["达到当日上限时停止并返回已加数量"],
        "examples": [{"params": {"max_targets": 10}, "note": "最多加 10 个"}],
    },
    "online.wechat_moments_engage": {
        "layer": "ops",
        "boundary": {"not_for": ["私信回复", "加好友"],
                     "use_instead": [{"scene": "私信接管", "capability": "online.wechat_takeover"}]},
        "outputs": {"fields": ["task_id", "liked", "commented"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["朋友圈不可见时跳过该联系人并记录"],
        "examples": [{"params": {}, "note": "默认点赞+简短评论"}],
    },
    "online.wechat_send_message": {
        "layer": "ops",
        "boundary": {"not_for": ["群发", "接管会话"],
                     "use_instead": [{"scene": "持续接管回复", "capability": "online.wechat_takeover"}]},
        "outputs": {"fields": ["task_id", "sent"], "status": ["completed", "failed"]},
        "degrade": ["联系人不存在时返回失败原因，不重试"],
        "examples": [{"params": {"target": "张三", "text": "您好，资料已发您"}, "note": "单条发送"}],
    },
    "online.moments_generate_images": {
        "layer": "produce",
        "boundary": {"not_for": ["视频", "长图文"],
                     "use_instead": [{"scene": "通用配图", "capability": "online.image_studio"}]},
        "outputs": {"fields": ["task_id", "image_urls"], "status": ["queued", "running", "completed", "failed"]},
        "degrade": ["生成失败时返回可用的部分图片"],
        "examples": [{"params": {"prompt": "秋天落叶，朋友圈配图"}, "note": "朋友圈九宫格之一"}],
    },
    "online.publish_content": {
        "layer": "publish",
        "boundary": {"not_for": ["创作", "素材生成"],
                     "use_instead": [{"scene": "生成内容", "capability": "online.image_studio"}]},
        "outputs": {"fields": ["task_id", "platform", "publish_url"], "status": ["queued", "running", "published", "failed"]},
        "degrade": ["平台未登录时返回未登录原因并保留草稿", "自动发布失败时提示改用人工审核发布"],
        "examples": [{"params": {"platform": "wechat_moments", "source_mode": "parent_latest_run"},
                      "note": "发布上游节点产物"}],
    },
    "online.browser_extract_page": {
        "layer": "discover",
        "boundary": {"not_for": ["需要登录后私密数据", "批量爬站"],
                     "use_instead": [{"scene": "整站抓取", "capability": "online.browser_extract_page"}]},
        "outputs": {"fields": ["title", "text", "links", "images", "method"], "status": ["completed", "failed"]},
        "degrade": ["直连失败改用 Jina Reader", "X/GitHub 自动走专用适配器", "仍失败退回无头浏览器"],
        "examples": [{"params": {"url": "https://x.com/xxx/status/123", "max_chars": 5000},
                      "note": "抓取推文正文"}],
    },
}

for _cid, _extra in _ENRICHMENT.items():
    if _cid in _ONLINE_CAPABILITIES:
        _ONLINE_CAPABILITIES[_cid].update(deepcopy(_extra))


def validate_capability_catalog() -> list[str]:
    """检查每个能力是否具备 AI 路由所需的完整元数据。"""
    problems: list[str] = []
    required = ("name", "description", "keywords", "arg_schema", "layer", "boundary", "outputs", "degrade", "examples")
    for cid, cfg in _ONLINE_CAPABILITIES.items():
        for key in required:
            if not cfg.get(key):
                problems.append(f"{cid}: missing {key}")
        boundary = cfg.get("boundary") or {}
        if not boundary.get("not_for"):
            problems.append(f"{cid}: boundary.not_for empty")
        for item in boundary.get("use_instead") or []:
            target = str((item or {}).get("capability") or "")
            if target and target not in _ONLINE_CAPABILITIES:
                problems.append(f"{cid}: unknown use_instead {target}")
        schema = cfg.get("arg_schema") or {}
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in props:
                problems.append(f"{cid}: required '{req}' missing from properties")
    return problems
