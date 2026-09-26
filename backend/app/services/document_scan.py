"""发票 / 账单图片理解：图片 -> 视觉模型 -> 结构化字段，给 manage 财务记账自动填报。

搬自 insurance.bhzn.top 的「图片理解」（那边是 app.py 的 VLM_CHAIN，图片 -> 模型 -> JSON -> 填表），
manage 侧只保留记账需要的字段，并把失败原因原样返回（不吞异常，前端能看见到底缺什么）。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

MAX_EDGE = 1600
MAX_BYTES = 12 * 1024 * 1024
DEFAULT_VISION_MODEL = "qwen3-vl-plus"

DOC_TYPE_LABEL = {"invoice": "发票", "receipt": "收据", "bill": "账单", "other": "票据"}
CATEGORIES = ("设备", "软件服务", "投放推广", "差旅", "办公", "人力", "税费", "其他")

PROMPT = """你是财务票据识别助手。请仔细读这张发票 / 收据 / 账单图片，只输出一个 JSON 对象，不要输出任何解释文字、不要 Markdown 代码块。

JSON 字段：
- doc_type: 票据类型，只能是 invoice(发票) / receipt(收据) / bill(账单) / other 之一
- amount: 价税合计（实际应付或实收金额），纯数字不带货币符号；看不清写 null
- date: 开票日期或账单日期，格式 YYYY-MM-DD；看不清写 null
- party: 开票方 / 商户 / 收款方名称（也就是「对方」）；看不清写 ""
- title: 这张票据是什么，20 字以内的中文摘要（例如「阿里云 9 月服务器费」「滴滴打车」）
- items: 主要项目 / 服务内容简述，40 字以内
- invoice_no: 发票号码 / 单据号；看不清写 ""
- category: 建议记账分类，只能从 设备 / 软件服务 / 投放推广 / 差旅 / 办公 / 人力 / 税费 / 其他 里选一个
- direction: expense(开销) 或 income(收入)，一般票据是 expense
- confidence: 0-1 的数字，表示你整体识别的把握

只输出 JSON。"""


def _provider() -> Tuple[str, str, str]:
    key = (settings.dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    base = (settings.dashscope_base_url or "https://dashscope.aliyuncs.com").rstrip("/")
    model = (os.environ.get("MANAGE_VISION_MODEL") or DEFAULT_VISION_MODEL).strip()
    return key, base + "/compatible-mode/v1", model


def _shrink_image(data: bytes) -> bytes:
    """票据图片先压成 1600 长边 JPEG，少传几 MB，识别率基本不变。"""
    try:
        from PIL import Image
    except Exception:
        logger.warning("[manage-vision] Pillow 不可用，按原图送模型")
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        if max(img.size) > MAX_EDGE:
            img.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        logger.warning("[manage-vision] 图片转码失败，按原图送模型", exc_info=True)
        return data


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def _parse_json(text: str) -> Optional[dict]:
    clean = _content_text(text).strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z]*\s*", "", clean)
        clean = re.sub(r"```\s*$", "", clean).strip()
    i, j = clean.find("{"), clean.rfind("}")
    if i >= 0 and j > i:
        clean = clean[i:j + 1]
    try:
        data = json.loads(clean)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    raw = re.sub(r"[^0-9.\-]", "", str(value))
    if not raw or raw in ("-", "."):
        return None
    try:
        return round(float(raw), 2)
    except Exception:
        return None


def _norm_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    def txt(key: str, limit: int) -> str:
        return str(raw.get(key) or "").strip()[:limit]

    doc_type = txt("doc_type", 12).lower()
    if doc_type not in DOC_TYPE_LABEL:
        doc_type = "other"
    date = txt("date", 10)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = ""
    party = txt("party", 80)
    title = txt("title", 60)
    items = txt("items", 120)
    summary = title or (party + " " + items).strip() or "票据"
    category = txt("category", 16)
    if category not in CATEGORIES:
        category = "其他"
    conf = raw.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except Exception:
        conf = None
    return {
        "doc_type": doc_type,
        "doc_type_label": DOC_TYPE_LABEL[doc_type],
        "amount": _num(raw.get("amount")),
        "date": date,
        "party": party,
        "title": title,
        "items": items,
        "summary": summary[:80],
        "invoice_no": txt("invoice_no", 40),
        "category": category,
        "entry_type": "income" if txt("direction", 12).lower() == "income" else "expense",
        "confidence": conf,
    }


async def scan_finance_document(data: bytes, filename: str = "") -> Dict[str, Any]:
    """识别一张发票 / 账单图片，返回 manage 财务表单可直接用的字段。"""
    if not data:
        return {"ok": False, "error": "文件是空的"}
    if len(data) > MAX_BYTES:
        return {"ok": False, "error": f"图片超过 {MAX_BYTES // (1024 * 1024)}MB，请压缩后再传"}
    key, base, model = _provider()
    if not key:
        return {"ok": False, "error": "服务端没配视觉模型密钥（DASHSCOPE_API_KEY）"}
    b64 = base64.b64encode(_shrink_image(data)).decode()
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]}],
    }
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=False) as client:
            resp = await client.post(
                base + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
    except Exception as exc:
        return {"ok": False, "error": f"调用视觉模型失败：{exc}", "model": model}
    latency = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        return {"ok": False, "error": f"视觉模型返回 HTTP {resp.status_code}：{resp.text[:200]}",
                "model": model, "latency_ms": latency}
    try:
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        return {"ok": False, "error": f"解析视觉模型响应失败：{exc}", "model": model, "latency_ms": latency}
    raw = _parse_json(str(content))
    if raw is None:
        return {"ok": False, "error": "视觉模型没有返回 JSON", "raw_text": _content_text(content)[:400],
                "model": model, "latency_ms": latency}
    return {"ok": True, "model": model, "latency_ms": latency, "fields": _norm_fields(raw),
            "raw_text": _content_text(content)[:2000], "file": filename}
