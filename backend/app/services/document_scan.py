"""发票 / 账单理解：图片 / PDF -> 视觉模型 -> 结构化字段，给 manage 财务记账自动填报。

来自 insurance.bhzn.top 的「图片理解」，manage 侧只保留记账需要的字段。
输入形态（用户实际会传的东西）：
1. 图片：JPG / PNG / WEBP / BMP / HEIC(PIL 能解就解) → 压成 1600 长边 JPEG；
2. 图片型 PDF（扫描件 / 打印成 PDF / 电子发票导出）→ pypdf 取每页内嵌图 → 逐页转 JPEG 一起送模型；
3. 文字型 PDF（有文字层）→ pypdf 抽文本，直接把文字送模型（不需要渲染器）。
失败原因原样返回，前端能看到到底缺什么（不吞异常）。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

MAX_EDGE = 1600
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 5
MAX_PDF_TEXT = 8000
DEFAULT_VISION_MODEL = "qwen3-vl-plus"

DOC_TYPE_LABEL = {"invoice": "发票", "receipt": "收据", "bill": "账单", "other": "票据"}
CATEGORIES = ("设备", "软件服务", "投放推广", "差旅", "办公", "人力", "税费", "其他")

PROMPT = """你是财务票据识别助手。请仔细读这份发票 / 收据 / 账单（图片之一页或多页、也可能是 PDF 抽出的文字），只输出一个 JSON 对象，不要输出任何解释文字、不要 Markdown 代码块。

JSON 字段：
- doc_type: 票据类型，只能是 invoice(发票) / receipt(收据) / bill(账单) / other 之一
- amount: 价税合计（实际应付或实收金额），纯数字不带货币符号；多页时把所有页面的金额加总；看不清写 null
- date: 开票日期或账单日期，格式 YYYY-MM-DD；多页时取最早日期；看不清写 null
- party: 开票方 / 商户 / 收款方名称（也就是「对方」）；看不清写 ""
- title: 这张票据是什么，20 字以内的中文摘要（例如「阿里云 9 月服务器费」「滴滴打车」）
- items: 主要项目 / 服务内容简述，40 字以内
- invoice_no: 发票号码 / 单据号，多张用 / 连接；看不清写 ""
- category: 建议记账分类，只能从 设备 / 软件服务 / 投放推广 / 差旅 / 办公 / 人力 / 税费 / 其他 里选一个
- direction: expense(开销) 或 income(收入)，一般票据是 expense
- confidence: 0-1 的数字，表示你整体识别的把握

只输出 JSON。"""


def _provider() -> Tuple[str, str, str]:
    key = (settings.dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    base = (settings.dashscope_base_url or "https://dashscope.aliyuncs.com").rstrip("/")
    model = (os.environ.get("MANAGE_VISION_MODEL") or DEFAULT_VISION_MODEL).strip()
    return key, base + "/compatible-mode/v1", model


def _to_jpeg(data: bytes, max_edge: int = MAX_EDGE, quality: int = 85) -> Optional[bytes]:
    """转成视觉接口通用的 JPEG；PIL 打不开就返回 None（不再把坏字节丢给模型）。"""
    try:
        from PIL import Image
    except Exception:
        logger.warning("[manage-vision] Pillow 不可用，图片不做转码")
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            converted = image.convert("RGB")
            width, height = converted.size
            longest = max(width, height) or 1
            if longest > max_edge:
                scale = float(max_edge) / float(longest)
                converted = converted.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                                             Image.LANCZOS)
            buffer = io.BytesIO()
            converted.save(buffer, format="JPEG", quality=quality, optimize=True)
            return buffer.getvalue()
    except Exception:
        logger.warning("[manage-vision] 图片转码失败（可能是 HEIC / 非图片文件）", exc_info=True)
        return None


def _pdf_images(data: bytes) -> List[bytes]:
    """图片型 PDF：取每页内嵌图（pypdf，不需要额外渲染器），统一转 JPEG。"""
    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        logger.warning("[manage-vision] pypdf 不可用: %s", exc)
        return []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[manage-vision] 打开 PDF 失败: %s", exc)
        return []
    out: List[bytes] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            raw = None
            for image in page.images:
                try:
                    raw = image.data
                except Exception:  # noqa: BLE001
                    raw = None
                if raw:
                    break
            if not raw:
                continue
            jpeg = _to_jpeg(raw)
            if jpeg:
                out.append(jpeg)
        except Exception:  # noqa: BLE001
            logger.warning("[manage-vision] PDF 某页取图失败，跳过该页", exc_info=True)
    return out


def _pdf_text(data: bytes) -> str:
    """文字型 PDF：抽文字层（电子发票导出常见）。"""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    except Exception:  # noqa: BLE001
        logger.warning("[manage-vision] PDF 文字提取失败", exc_info=True)
        return ""
    return "\n".join(p.strip() for p in parts if p and p.strip())[:MAX_PDF_TEXT]


def _looks_like_pdf(data: bytes, filename: str) -> bool:
    if data[:5] == b"%PDF-":
        return True
    return str(filename or "").lower().endswith(".pdf")


def _prepare(data: bytes, filename: str) -> Tuple[Dict[str, Any], str]:
    """把上传文件整理成送模型的内容；返回 (payload, 错误原因)。"""
    if _looks_like_pdf(data, filename):
        images = _pdf_images(data)
        if images:
            return {"kind": "pdf-image", "images": images, "text": "", "pages": len(images)}, ""
        text = _pdf_text(data)
        if text:
            return {"kind": "pdf-text", "images": [], "text": text, "pages": 0}, ""
        return {}, ("这份 PDF 既没有可提取的文字，也没有能识别的页面图片（可能是矢量打印件或加密件），"
                    "请截图后再传，或换一份 PDF")
    jpeg = _to_jpeg(data)
    if not jpeg:
        return {}, "这个文件不是能识别的图片（HEIC / 加密 / 损坏等），请换 JPG / PNG 截图，或直接传 PDF"
    return {"kind": "image", "images": [jpeg], "text": "", "pages": 1}, ""


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
        parsed = json.loads(clean)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


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
    """识别一张（或多页）发票 / 账单，返回 manage 财务表单可直接用的字段。"""
    if not data:
        return {"ok": False, "error": "文件是空的"}
    is_pdf = _looks_like_pdf(data, filename)
    limit = MAX_PDF_BYTES if is_pdf else MAX_IMAGE_BYTES
    if len(data) > limit:
        return {"ok": False, "error": f"文件超过 {limit // (1024 * 1024)}MB，请压缩或只传需要的页"}
    key, base, model = _provider()
    if not key:
        return {"ok": False, "error": "服务端没配视觉模型密钥（DASHSCOPE_API_KEY）"}
    payload, err = _prepare(data, filename)
    if err:
        return {"ok": False, "error": err, "model": model}
    content: List[Dict[str, Any]] = [{"type": "text", "text": PROMPT}]
    if payload["kind"] == "pdf-text":
        content[0]["text"] = PROMPT + "\n\n【PDF 提取出的文字】\n" + payload["text"]
    for image in payload["images"]:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}})
    body = {"model": model, "temperature": 0, "max_tokens": 1200,
            "messages": [{"role": "user", "content": content}]}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=15.0), trust_env=False) as client:
            resp = await client.post(
                base + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
    except Exception as exc:
        return {"ok": False, "error": f"调用视觉模型失败：{exc}", "model": model}
    latency = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        hint = "（该文件模型打不开，请换 JPG / PNG 截图或可识别的 PDF）" if resp.status_code == 400 else ""
        return {"ok": False, "error": f"视觉模型返回 HTTP {resp.status_code}{hint}：{resp.text[:200]}",
                "model": model, "latency_ms": latency}
    try:
        raw_content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        return {"ok": False, "error": f"解析视觉模型响应失败：{exc}", "model": model, "latency_ms": latency}
    raw = _parse_json(str(raw_content))
    if raw is None:
        return {"ok": False, "error": "视觉模型没有返回 JSON", "raw_text": _content_text(raw_content)[:400],
                "model": model, "latency_ms": latency}
    return {"ok": True, "model": model, "latency_ms": latency, "fields": _norm_fields(raw),
            "kind": payload["kind"], "pages": payload["pages"],
            "raw_text": _content_text(raw_content)[:2000], "file": filename}
