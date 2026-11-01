"""能力面（single source of truth）：所有“AI 能看到哪些能力”的地方都从这里取。

背景（2026-11-01 事故）：能力信息分散在 4 处——
1) mcp/capability_catalog.json（能力行 + enabled 开关）
2) capability_configs 表（线上真实开关，由 1 同步而来）
3) mastra_online_capabilities()（online.* 代码内定义，含设备浏览器）
4) 各链路自己的 prompt（work_mode_brief / mastra orchestrator / chat.py 工具清单）
任何一处漏配，模型就会“看不到正确工具”，退而选错（例：抓网页选了图生视频）。

本模块把这些合并成一个视图，并提供**意图 → 允许能力**的确定性过滤：
- web_read：只允许 web.* / online.browser_extract_page，禁止 comfly.*/video.*/image.*
- generate / publish / device / chat：不做反向限制（仅剔除未启用项）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_CATALOG_PATHS = (
    Path(__file__).resolve().parents[3] / "mcp" / "capability_catalog.json",
    Path(__file__).resolve().parents[3] / "mcp" / "capability_catalog.local.json",
)

_WEB_IDS = ("web.page_analyze", "web.site_crawl", "online.browser_extract_page")
_GENERATION_PREFIXES = ("comfly.", "video.", "image.", "suno", "minimax/")

_WEB_HINTS = ("网页", "官网", "网站", "页面", "正文", "联系我们", "联系方式", "抓取", "打开 http", "查看 http", "看看这个网站", "店铺")
_GEN_HINTS = ("生成图片", "生成视频", "画一张", "做个视频", "配音", "数字人")


def load_catalog() -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for path in _CATALOG_PATHS:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            caps = data.get("capabilities") if isinstance(data, dict) and "capabilities" in data else data
            if isinstance(caps, dict):
                for k, v in caps.items():
                    if isinstance(v, dict):
                        merged[str(k)] = dict(v)
        except Exception:
            continue
    return merged


def online_capabilities() -> Dict[str, Dict[str, Any]]:
    try:
        from .mastra_online_capabilities import mastra_online_capabilities

        caps = mastra_online_capabilities() or {}
        return {str(k): v for k, v in caps.items() if isinstance(v, dict)}
    except Exception:
        return {}


def all_capabilities(db=None, *, include_disabled: bool = False) -> Dict[str, Dict[str, Any]]:
    """合并能力行 + online 能力，返回 {id: {name, description, side_effect, source, enabled}}。"""
    surface: Dict[str, Dict[str, Any]] = {}
    for cid, row in load_catalog().items():
        enabled = bool(row.get("enabled", True))
        if not enabled and not include_disabled:
            continue
        surface[cid] = {
            "name": str(row.get("name") or cid),
            "description": str(row.get("description") or "")[:120],
            "side_effect": str(row.get("side_effect") or "read"),
            "source": "capability",
            "enabled": enabled,
        }
    for cid, row in online_capabilities().items():
        surface[cid] = {
            "name": str(row.get("name") or cid),
            "description": str(row.get("description") or "")[:120],
            "side_effect": str(row.get("side_effect") or "external"),
            "source": "online",
            "enabled": True,
        }
    if db is not None:
        try:
            from ..models import CapabilityConfig

            for row in db.query(CapabilityConfig).filter(CapabilityConfig.enabled.is_(True)).all():
                cid = str(getattr(row, "capability_id", "") or "")
                if cid and cid not in surface:
                    surface[cid] = {
                        "name": cid,
                        "description": str(getattr(row, "description", "") or "")[:120],
                        "side_effect": "read",
                        "source": "db",
                        "enabled": True,
                    }
        except Exception:
            pass
    return surface


def intent_of(query: str) -> str:
    text = str(query or "").lower()
    if any(h in text for h in _GEN_HINTS):
        return "generate"
    if any(h in text for h in _WEB_HINTS) or "http://" in text or "https://" in text:
        return "web_read"
    return "chat"


def allowed_ids_for_intent(intent: str, ids: Iterable[str]) -> List[str]:
    ids = [str(i) for i in ids]
    if intent != "web_read":
        return ids
    allowed = [i for i in ids if i in _WEB_IDS]
    return allowed or ids


def is_generation_capability(cid: str) -> bool:
    c = str(cid or "")
    return any(c.startswith(p) for p in _GENERATION_PREFIXES)


def web_read_guard(capability_id: str, payload: Any, user_text: str = "") -> Optional[str]:
    """网页抓取类请求误用生成能力时返回错误说明（None 表示放行）。"""
    if intent_of(user_text) != "web_read":
        return None
    if not is_generation_capability(capability_id):
        return None
    return (
        "这次是读取网页内容，不能用生成类能力（%s）。"
        "请改用 web.page_analyze（单页）/ web.site_crawl（多页）；"
        "页面有验证码或需要点按钮时用 online.browser_extract_page。" % capability_id
    )
