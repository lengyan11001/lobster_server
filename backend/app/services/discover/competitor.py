"""竞品公开页拆解：免费抓取主页，能拿什么给什么，拿不到的明确标注。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..web_extract import extract_page_server

_COUNT_RE = re.compile(r"(粉丝|获赞|点赞|收藏|作品|笔记)\s*[:：]?\s*([\d.]+\s*[万亿wW]?)")


def extract_counts(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for label, value in _COUNT_RE.findall(text or ""):
        out.setdefault(label, value.strip())
    return out


async def analyze(urls: List[str], *, platform: str = "") -> Dict[str, Any]:
    accounts: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for url in urls[:5]:
        result = await extract_page_server({"url": url, "max_chars": 4000})
        if not result.get("ok"):
            errors.append({"url": url, "error": str(result.get("error") or "fetch failed")})
            continue
        text = result.get("text") or ""
        accounts.append({
            "url": url,
            "title": result.get("title") or "",
            "method": result.get("method") or "",
            "counts": extract_counts(text),
            "excerpt": text[:600],
            "limits": ["播放量/完播率/粉丝增量在创作者中心，外部拿不到，未做估算"],
        })
    return {"platform": platform, "accounts": accounts, "errors": errors}


def format_markdown(data: Dict[str, Any]) -> str:
    lines = ["# 竞品公开信息", ""]
    if not data.get("accounts"):
        lines.append("没抓到账号页，可能被反爬；建议提供账号名改用公开资料。")
    for acc in data.get("accounts") or []:
        lines.append(f"## {acc['title'] or acc['url']}")
        lines.append(f"- 链接：{acc['url']}")
        lines.append(f"- 抓取方式：{acc['method']}")
        if acc["counts"]:
            lines.append("- 可见计数：" + "，".join(f"{k} {v}" for k, v in acc["counts"].items()))
        lines.append(f"- 摘要：{acc['excerpt'][:200]}")
        for limit in acc.get("limits") or []:
            lines.append(f"- 数据边界：{limit}")
        lines.append("")
    for err in data.get("errors") or []:
        lines.append(f"> {err['url']}：{err['error']}")
    return "\n".join(lines)


def selftest() -> List[str]:
    problems: List[str] = []
    counts = extract_counts("粉丝 12.3万 获赞 88.1万 作品 132")
    if counts.get("粉丝") != "12.3万" or counts.get("作品") != "132":
        problems.append(f"count regex failed: {counts}")
    return problems
