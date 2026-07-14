from __future__ import annotations

import re
import uuid
from html import unescape
from urllib.parse import parse_qs, quote_plus, urlparse
from datetime import datetime
from typing import Any, Optional
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CreativeGenerationJob, GlobalLeadCrmContact, GlobalLeadJob, User
from .auth import get_current_user
from .linkedin_mining import (
    create_linkedin_mining_job_from_payload,
    linkedin_mining_job_payload,
    _schedule_linkedin_mining_autorun,
)
from .social_leads import (
    create_social_leads_job_from_payload,
    social_leads_job_payload,
    _schedule_social_leads_autorun,
)

router = APIRouter()


SOURCE_CATALOG: list[dict[str, str]] = [
    {"id": "google", "name": "Google", "kind": "search", "status": "connected"},
    {"id": "bing", "name": "Bing", "kind": "search", "status": "connected"},
    {"id": "yandex", "name": "Yandex", "kind": "search", "status": "search_link"},
    {"id": "yahoo", "name": "Yahoo", "kind": "search", "status": "search_link"},
    {"id": "linkedin", "name": "LinkedIn", "kind": "people", "status": "connected"},
    {"id": "x", "name": "X", "kind": "social", "status": "connected"},
    {"id": "tiktok", "name": "TikTok", "kind": "social", "status": "connected"},
    {"id": "reddit", "name": "Reddit", "kind": "community", "status": "conditional"},
    {"id": "facebook", "name": "Facebook", "kind": "social", "status": "needs_connector"},
    {"id": "whatsapp", "name": "WhatsApp", "kind": "messaging", "status": "needs_connector"},
    {"id": "crunchbase", "name": "Crunchbase", "kind": "company", "status": "needs_connector"},
    {"id": "zoominfo", "name": "ZoomInfo", "kind": "people", "status": "needs_connector"},
    {"id": "apollo", "name": "Apollo", "kind": "people", "status": "needs_connector"},
    {"id": "tradeatlas", "name": "TradeAtlas", "kind": "trade", "status": "needs_connector"},
    {"id": "10times", "name": "10times", "kind": "event", "status": "needs_connector"},
    {"id": "glassdoor", "name": "Glassdoor", "kind": "company", "status": "needs_connector"},
]
SOURCE_BY_ID = {row["id"]: row for row in SOURCE_CATALOG}
CONNECTED_SOURCES = {"google", "bing", "linkedin", "x", "tiktok"}
WEB_SEARCH_SOURCES = {"google", "bing"}
TERMINAL = {"completed", "failed", "canceled", "stale", "deleted"}
SEARCH_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


class GlobalLeadJobIn(BaseModel):
    title: str = Field("", max_length=180)
    company_name: str = Field("", max_length=255)
    domain: str = Field("", max_length=255)
    region: str = Field("", max_length=120)
    target_profile: str = Field("", max_length=2000)
    keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    reddit_communities: list[str] = Field(default_factory=list)
    max_items: int = Field(80, ge=10, le=300)
    auto_run: bool = True


class GlobalLeadStatusPatch(BaseModel):
    status: str = Field("", max_length=32)
    tags: list[str] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.utcnow()


def _clean_text(value: Any, limit: int = 255) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _clean_long(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\r\n?", "\n", text)
    return text[:limit]


def _clean_list(values: Any, *, limit: int = 20) -> list[str]:
    if isinstance(values, str):
        raw = re.split(r"[\n,，;；]+", values)
    elif isinstance(values, list):
        raw = values
    else:
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_text(item, 160)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _normalize_domain(value: Any) -> str:
    text = _clean_text(value, 255).lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0].strip()
    text = text[4:] if text.startswith("www.") else text
    return text[:255]


def _source_ids(values: list[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    raw = values or ["google", "bing", "linkedin", "x", "tiktok", "facebook", "crunchbase", "zoominfo", "tradeatlas", "10times", "glassdoor"]
    for item in raw:
        sid = _clean_text(item, 40).lower().replace("_", "-")
        if sid == "twitter":
            sid = "x"
        if sid in SOURCE_BY_ID and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def _search_terms(payload: dict[str, Any]) -> list[str]:
    terms = []
    for value in [payload.get("company_name"), payload.get("domain"), payload.get("region")]:
        if _clean_text(value):
            terms.append(_clean_text(value))
    terms.extend(_clean_list(payload.get("keywords"), limit=8))
    return _clean_list(terms, limit=12)


def _source_search_url(source_id: str, terms: list[str]) -> str:
    query = "+".join([re.sub(r"\s+", "+", term.strip()) for term in terms if term.strip()])
    if source_id == "google":
        return f"https://www.google.com/search?q={query}"
    if source_id == "bing":
        return f"https://www.bing.com/search?q={query}"
    if source_id == "yandex":
        return f"https://yandex.com/search/?text={query}"
    if source_id == "yahoo":
        return f"https://search.yahoo.com/search?p={query}"
    if source_id == "facebook":
        return f"https://www.facebook.com/search/top?q={query}"
    if source_id == "crunchbase":
        return f"https://www.crunchbase.com/discover/organization.companies/field/organizations/identifier/{query}"
    if source_id == "zoominfo":
        return f"https://www.zoominfo.com/search#q={query}"
    if source_id == "apollo":
        return f"https://app.apollo.io/#/people?finderKeywords={query}"
    if source_id == "tradeatlas":
        return f"https://www.tradeatlas.com/search?q={query}"
    if source_id == "10times":
        return f"https://10times.com/search?q={query}"
    if source_id == "glassdoor":
        return f"https://www.glassdoor.com/Search/results.htm?keyword={query}"
    return ""


def _clean_html(value: Any, limit: int = 1000) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _result_url(value: Any) -> str:
    url = unescape(str(value or "")).strip()
    if not url:
        return ""
    if url.startswith("/url?"):
        query = parse_qs(urlparse(url).query)
        url = (query.get("q") or [""])[0]
    if not re.match(r"^https?://", url, flags=re.I):
        return ""
    host = urlparse(url).netloc.lower()
    if any(skip in host for skip in ("google.", "bing.", "microsoft.", "gstatic.", "youtube.com")):
        return ""
    return url[:2000]


def _domain_from_url(value: Any) -> str:
    try:
        host = urlparse(str(value or "")).netloc.lower()
    except Exception:
        return ""
    host = host[4:] if host.startswith("www.") else host
    return host[:255]


def _parse_bing_results(html: str, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    blocks = re.findall(r"<li[^>]+class=\"[^\"]*b_algo[^\"]*\".*?</li>", html or "", flags=re.I | re.S)
    for block in blocks:
        match = re.search(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, flags=re.I | re.S)
        if not match:
            continue
        url = _result_url(match.group(1))
        if not url:
            continue
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.I | re.S)
        rows.append(
            {
                "title": _clean_html(match.group(2), 255),
                "url": url,
                "snippet": _clean_html(snippet_match.group(1) if snippet_match else "", 800),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _parse_rss_results(xml_text: str, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        root = ElementTree.fromstring(xml_text or "")
    except Exception:
        return rows
    for item in root.findall(".//item"):
        url = _result_url(item.findtext("link") or "")
        if not url:
            continue
        rows.append(
            {
                "title": _clean_html(item.findtext("title") or "", 255),
                "url": url,
                "snippet": _clean_html(item.findtext("description") or "", 800),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _parse_google_results(html: str, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", html or "", flags=re.I | re.S):
        url = _result_url(match.group(1))
        if not url or url in seen:
            continue
        title = _clean_html(match.group(2), 255)
        if not title or title.lower() in {"cached", "similar"}:
            continue
        seen.add(url)
        rows.append({"title": title, "url": url, "snippet": ""})
        if len(rows) >= limit:
            break
    return rows


def _web_search_queries(payload: dict[str, Any]) -> list[str]:
    company = _clean_text(payload.get("company_name"), 255)
    domain = _normalize_domain(payload.get("domain"))
    region = _clean_text(payload.get("region"), 120)
    keywords = _clean_list(payload.get("keywords"), limit=6)
    terms = [x for x in [company, domain, region, *keywords] if x]
    queries: list[str] = []
    if terms:
        queries.append(" ".join(terms))
    if domain:
        queries.append(f"site:{domain} contact OR team OR about")
    elif company:
        queries.append(f"\"{company}\" contact OR team OR company")
    return _clean_list(queries, limit=2)


async def _fetch_web_search_results(source_id: str, query: str, limit: int) -> tuple[list[dict[str, str]], str]:
    encoded = quote_plus(query)
    if source_id == "google":
        url = f"https://www.google.com/search?q={encoded}&num={min(10, max(1, limit))}&hl=en"
    elif source_id == "bing":
        url = f"https://www.bing.com/search?q={encoded}&count={min(10, max(1, limit))}&format=rss"
    else:
        return [], "unsupported search source"
    try:
        async with httpx.AsyncClient(headers=SEARCH_HTTP_HEADERS, follow_redirects=True, timeout=10.0) as client:
            errors: list[str] = []
            if source_id == "google":
                try:
                    resp = await client.get(url, timeout=4.0)
                    if resp.status_code < 400:
                        rows = _parse_google_results(resp.text or "", limit)
                        if rows:
                            return rows, ""
                    else:
                        errors.append(f"google returned HTTP {resp.status_code}")
                except Exception as exc:
                    errors.append(str(exc)[:180])
                fallback = f"https://www.bing.com/search?q={encoded}&count={min(10, max(1, limit))}&format=rss"
                fallback_resp = await client.get(fallback)
                if fallback_resp.status_code < 400:
                    rows = _parse_rss_results(fallback_resp.text or "", limit)
                    return rows, "" if rows else "; ".join(x for x in errors if x)[:500]
                return [], (f"fallback returned HTTP {fallback_resp.status_code}; " + "; ".join(x for x in errors if x))[:500]
            resp = await client.get(url)
            if resp.status_code >= 400:
                return [], f"{source_id} returned HTTP {resp.status_code}"
            html = resp.text or ""
            rows = _parse_rss_results(html, limit)
            if not rows:
                rows = _parse_bing_results(html, limit)
            return rows, ""
    except Exception as exc:
        return [], str(exc)[:500]


def _web_result_contact(row: GlobalLeadJob, source_id: str, result: dict[str, str], query: str) -> dict[str, Any]:
    url = result.get("url") or ""
    title = result.get("title") or _domain_from_url(url) or source_id
    domain = _domain_from_url(url) or row.domain or ""
    snippet = result.get("snippet") or ""
    return {
        "job_id": row.job_id,
        "entity_type": "company",
        "name": title,
        "company": row.company_name or title,
        "domain": domain,
        "region": row.region or "",
        "profile_url": url,
        "source_platform": source_id,
        "source_url": url,
        "score": 45 if row.domain and domain.endswith(row.domain) else 35,
        "tags": [source_id, "web_search"],
        "evidence": [
            {
                "title": title,
                "text": snippet,
                "url": url,
                "source_reason": f"{source_id} search: {query}",
            }
        ],
        "raw": {"query": query, "result": result},
    }


async def _run_web_search_sources(db: Session, row: GlobalLeadJob) -> int:
    payload = row.request_payload or {}
    plan = [item for item in (row.source_plan or []) if isinstance(item, dict)]
    selected = {str(item.get("id") or "") for item in plan}
    web_sources = [sid for sid in ("google", "bing") if sid in selected]
    if not web_sources:
        return 0
    plan_map = _source_plan_by_id(plan)
    queries = _web_search_queries(payload)
    if not queries:
        return 0
    total_imported = 0
    per_query_limit = max(3, min(8, int((payload.get("max_items") or 80) / max(1, len(web_sources) * len(queries)))))
    for source_id in web_sources:
        item = plan_map.get(source_id)
        if item:
            item["status"] = "running"
            item["message"] = "正在搜索公开网页"
            item["updated_at"] = _now().isoformat()
            row.source_plan = [dict(x) for x in plan_map.values()]
            flag_modified(row, "source_plan")
        db.commit()
        imported = 0
        errors: list[str] = []
        seen: set[str] = set()
        for query in queries:
            results, error = await _fetch_web_search_results(source_id, query, per_query_limit)
            if error:
                errors.append(error)
            for result in results:
                url = result.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                _upsert_contact(db, row.user_id, _web_result_contact(row, source_id, result, query))
                imported += 1
        total_imported += imported
        if item:
            item["status"] = "completed" if imported or not errors else "failed"
            item["lead_count"] = int(item.get("lead_count") or 0) + imported
            item["message"] = f"已搜索并入库 {imported} 条公开网页线索" if imported else ("未搜索到结果" if not errors else "搜索失败：" + errors[0])
            item["updated_at"] = _now().isoformat()
        row.source_plan = [dict(x) for x in plan_map.values()]
        flag_modified(row, "source_plan")
        result_payload = dict(row.result_payload or {})
        web_summary = result_payload.get("web_search") if isinstance(result_payload.get("web_search"), dict) else {}
        web_summary[source_id] = {"imported": imported, "errors": errors[:3], "queries": queries}
        result_payload["web_search"] = web_summary
        result_payload["imported_contacts"] = int(result_payload.get("imported_contacts") or 0) + imported
        row.result_payload = result_payload
        flag_modified(row, "result_payload")
        row.updated_at = _now()
        db.commit()
    return total_imported


def _build_source_plan(source_ids: list[str], payload: dict[str, Any]) -> list[dict[str, Any]]:
    terms = _search_terms(payload)
    plan: list[dict[str, Any]] = []
    for sid in source_ids:
        info = SOURCE_BY_ID[sid]
        connected = sid in CONNECTED_SOURCES
        status = "queued" if connected else ("ready" if info["status"] == "search_link" else "needs_connector")
        if sid == "reddit" and _clean_list(payload.get("reddit_communities"), limit=12):
            status = "queued"
        elif sid == "reddit":
            status = "needs_input"
        plan.append(
            {
                "id": sid,
                "name": info["name"],
                "kind": info["kind"],
                "status": status,
                "search_url": _source_search_url(sid, terms),
                "message": "",
                "job_id": "",
                "lead_count": 0,
                "updated_at": _now().isoformat(),
            }
        )
    return plan


def _job_title(body: GlobalLeadJobIn) -> str:
    if _clean_text(body.title):
        return _clean_text(body.title, 180)
    parts = [_clean_text(body.company_name), _normalize_domain(body.domain), _clean_text(body.region)]
    title = " - ".join([x for x in parts if x])
    return title or "全球获客任务"


def _job_payload(row: GlobalLeadJob, *, crm_count: Optional[int] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "user_id": row.user_id,
        "status": row.status,
        "stage": row.stage or "",
        "progress": row.progress or 0,
        "title": row.title or "",
        "company_name": row.company_name or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "target_profile": row.target_profile or "",
        "request_payload": row.request_payload or {},
        "source_plan": row.source_plan or [],
        "child_jobs": row.child_jobs or [],
        "result_payload": row.result_payload or {},
        "crm_count": crm_count,
        "error": row.error or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
    }


def _contact_payload(row: GlobalLeadCrmContact) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id or "",
        "entity_type": row.entity_type,
        "name": row.name or "",
        "company": row.company or "",
        "role": row.role or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "email": row.email or "",
        "phone": row.phone or "",
        "social_handle": row.social_handle or "",
        "profile_url": row.profile_url or "",
        "source_platform": row.source_platform or "",
        "source_url": row.source_url or "",
        "score": row.score or 0,
        "status": row.status,
        "tags": row.tags or [],
        "evidence": row.evidence or [],
        "raw": row.raw or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _dedupe_key(user_id: int, item: dict[str, Any]) -> str:
    for key in ("profile_url", "email", "source_url"):
        value = _clean_text(item.get(key), 255).lower()
        if value:
            return f"{key}:{value}"[:255]
    domain = _normalize_domain(item.get("domain"))
    entity_type = _clean_text(item.get("entity_type"), 32)
    if domain and entity_type == "company":
        return f"company-domain:{domain}"[:255]
    handle = _clean_text(item.get("social_handle") or item.get("handle"), 255).lower()
    platform = _clean_text(item.get("source_platform"), 64).lower()
    if handle:
        return f"{platform}:handle:{handle}"[:255]
    base = "|".join(
        [
            str(user_id),
            platform,
            _clean_text(item.get("name"), 120).lower(),
            _clean_text(item.get("company"), 120).lower(),
            _clean_text(item.get("job_id"), 64).lower(),
        ]
    )
    return f"fallback:{base}"[:255]


def _upsert_contact(db: Session, user_id: int, item: dict[str, Any]) -> GlobalLeadCrmContact:
    key = _dedupe_key(user_id, item)
    row = (
        db.query(GlobalLeadCrmContact)
        .filter(GlobalLeadCrmContact.user_id == user_id, GlobalLeadCrmContact.dedupe_key == key)
        .first()
    )
    if row is None:
        row = GlobalLeadCrmContact(user_id=user_id, dedupe_key=key, created_at=_now())
        db.add(row)
    row.job_id = _clean_text(item.get("job_id"), 64) or row.job_id
    row.entity_type = _clean_text(item.get("entity_type"), 32) or "person"
    row.name = _clean_text(item.get("name"), 255) or row.name or ""
    row.company = _clean_text(item.get("company"), 255) or row.company
    row.role = _clean_text(item.get("role"), 255) or row.role
    row.domain = _normalize_domain(item.get("domain")) or row.domain
    row.region = _clean_text(item.get("region"), 120) or row.region
    row.email = _clean_text(item.get("email"), 255) or row.email
    row.phone = _clean_text(item.get("phone"), 80) or row.phone
    row.social_handle = _clean_text(item.get("social_handle") or item.get("handle"), 255) or row.social_handle
    row.profile_url = _clean_long(item.get("profile_url") or item.get("url"), 2000) or row.profile_url
    row.source_platform = _clean_text(item.get("source_platform"), 64) or row.source_platform or ""
    row.source_url = _clean_long(item.get("source_url") or item.get("profile_url") or item.get("url"), 2000) or row.source_url
    row.score = max(row.score or 0, int(item.get("score") or 0))
    row.tags = item.get("tags") if isinstance(item.get("tags"), list) else (row.tags or [])
    row.evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else (row.evidence or [])
    row.raw = item.get("raw") if isinstance(item.get("raw"), dict) else (row.raw or {})
    row.updated_at = _now()
    return row


def _seed_company_contact(db: Session, row: GlobalLeadJob) -> None:
    if not (row.company_name or row.domain):
        return
    _upsert_contact(
        db,
        row.user_id,
        {
            "job_id": row.job_id,
            "entity_type": "company",
            "name": row.company_name or row.domain or "",
            "company": row.company_name or "",
            "domain": row.domain or "",
            "region": row.region or "",
            "source_platform": "query",
            "source_url": f"https://{row.domain}" if row.domain else "",
            "score": 20,
            "tags": ["企业入口"],
            "evidence": [{"title": "用户提交的企业/域名/区域", "text": row.target_profile or ""}],
            "raw": row.request_payload or {},
        },
    )


def _normalize_child_lead(raw: dict[str, Any], *, row: GlobalLeadJob, platform: str, child_job_id: str) -> dict[str, Any]:
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
    url = raw.get("url") or raw.get("profile_url") or raw.get("source") or ""
    return {
        "job_id": row.job_id,
        "entity_type": "person",
        "name": raw.get("name") or raw.get("display_name") or raw.get("username") or raw.get("handle") or raw.get("company") or "",
        "company": raw.get("company") or raw.get("company_name") or row.company_name or "",
        "role": raw.get("role") or raw.get("title") or raw.get("headline") or "",
        "domain": row.domain or "",
        "region": row.region or "",
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "social_handle": raw.get("handle") or raw.get("username") or raw.get("screen_name") or "",
        "profile_url": url,
        "source_platform": platform,
        "source_url": url,
        "score": int(raw.get("score") or raw.get("relevance_score") or 0),
        "tags": [platform],
        "evidence": evidence,
        "raw": {"child_job_id": child_job_id, "lead": raw},
    }


def _extract_child_leads(payload: dict[str, Any], *, platform: str) -> list[dict[str, Any]]:
    result = payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else {}
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    lead_summary = result.get("lead_summary") if isinstance(result.get("lead_summary"), dict) else {}
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    groups = [
        report.get("priority_leads"),
        lead_summary.get("top_leads"),
        lead_summary.get("candidates"),
        candidates,
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            key = str(item.get("profile_url") or item.get("url") or item.get("handle") or item.get("name") or "")
            if not key:
                continue
            dedupe = f"{platform}:{key.lower()}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(item)
    return out


def _source_plan_by_id(plan: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in plan if isinstance(item, dict)}


def _child_idle_seconds(row: CreativeGenerationJob) -> float:
    updated_at = row.updated_at or row.created_at
    if not updated_at:
        return 0.0
    try:
        return max(0.0, (_now() - updated_at).total_seconds())
    except Exception:
        return 0.0


def _maybe_resume_child_job(child_row: CreativeGenerationJob, platform: str) -> bool:
    if child_row.status in TERMINAL:
        return False
    idle = _child_idle_seconds(child_row)
    if child_row.status == "queued" and idle < 15:
        return False
    if child_row.status == "running" and idle < 300:
        return False
    meta = child_row.meta if isinstance(child_row.meta, dict) else {}
    last_requested = str(meta.get("global_leads_resume_requested_at") or "")
    if last_requested:
        try:
            last_dt = datetime.fromisoformat(last_requested)
            if (_now() - last_dt).total_seconds() < 120:
                return False
        except Exception:
            pass
    meta["global_leads_resume_requested_at"] = _now().isoformat()
    child_row.meta = dict(meta)
    flag_modified(child_row, "meta")
    if platform == "linkedin":
        return _schedule_linkedin_mining_autorun(child_row.job_id)
    if platform in {"x", "tiktok", "reddit"}:
        return _schedule_social_leads_autorun(child_row.job_id)
    return False


def _refresh_global_job(db: Session, row: GlobalLeadJob, *, resume_stale_children: bool = False) -> GlobalLeadJob:
    child_jobs = [item for item in (row.child_jobs or []) if isinstance(item, dict)]
    plan = [item for item in (row.source_plan or []) if isinstance(item, dict)]
    plan_map = _source_plan_by_id(plan)
    synced = set((row.result_payload or {}).get("synced_child_jobs") or [])
    imported = int((row.result_payload or {}).get("imported_contacts") or 0)
    running = 0
    failed = 0
    completed = 0

    for child in child_jobs:
        platform = str(child.get("platform") or "").strip().lower()
        child_job_id = str(child.get("job_id") or "").strip()
        if not child_job_id:
            continue
        child_row = (
            db.query(CreativeGenerationJob)
            .filter(CreativeGenerationJob.user_id == row.user_id, CreativeGenerationJob.job_id == child_job_id)
            .first()
        )
        if child_row is None:
            continue
        if resume_stale_children and _maybe_resume_child_job(child_row, platform):
            db.commit()
            db.refresh(child_row)
        child["status"] = child_row.status
        child["progress"] = child_row.progress or 0
        child["updated_at"] = child_row.updated_at.isoformat() if child_row.updated_at else ""
        item = plan_map.get(platform)
        if item:
            item["status"] = child_row.status
            item["job_id"] = child_job_id
            item["updated_at"] = child["updated_at"]
            item["message"] = child_row.error or ""
        if child_row.status in TERMINAL:
            if child_row.status == "failed":
                failed += 1
            else:
                completed += 1
            if child_job_id not in synced and child_row.status == "completed":
                payload = linkedin_mining_job_payload(child_row) if platform == "linkedin" else social_leads_job_payload(child_row, db=db, include_sources=True)
                leads = _extract_child_leads(payload, platform=platform)
                count = 0
                for lead in leads:
                    _upsert_contact(db, row.user_id, _normalize_child_lead(lead, row=row, platform=platform, child_job_id=child_job_id))
                    count += 1
                if item:
                    item["lead_count"] = int(item.get("lead_count") or 0) + count
                imported += count
                synced.add(child_job_id)
        else:
            running += 1

    row.child_jobs = [dict(item) for item in child_jobs]
    row.source_plan = [dict(item) for item in (list(plan_map.values()) if plan_map else plan)]
    row.result_payload = {
        **(row.result_payload or {}),
        "synced_child_jobs": sorted(synced),
        "imported_contacts": imported,
        "child_job_count": len(child_jobs),
        "completed_child_jobs": completed,
        "failed_child_jobs": failed,
    }
    flag_modified(row, "child_jobs")
    flag_modified(row, "source_plan")
    flag_modified(row, "result_payload")
    if child_jobs:
        if running:
            row.status = "running"
            row.stage = "collecting"
            row.progress = max(row.progress or 0, min(95, int((completed + failed) / max(1, len(child_jobs)) * 90)))
        else:
            row.status = "completed" if failed < len(child_jobs) else "failed"
            row.stage = "completed" if row.status == "completed" else "failed"
            row.progress = 100
            row.completed_at = row.completed_at or _now()
    else:
        row.status = "completed"
        row.stage = "ready_for_connectors"
        row.progress = 100
        row.completed_at = row.completed_at or _now()
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def _create_child_jobs(db: Session, current_user: User, row: GlobalLeadJob, body: GlobalLeadJobIn) -> list[dict[str, Any]]:
    payload = row.request_payload or {}
    terms = _search_terms(payload)
    title = row.title or "全球获客任务"
    children: list[dict[str, Any]] = []
    selected = {str(item.get("id") or "") for item in (row.source_plan or []) if isinstance(item, dict)}
    if "linkedin" in selected:
        child = create_linkedin_mining_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "title": f"{title} - LinkedIn",
                "keywords": terms[:12],
                "target_profile": row.target_profile or "寻找目标企业相关负责人、采购、市场、BD、创始人等潜在线索",
                "max_people": min(body.max_items, 100),
                "max_company_employees": 20,
                "max_interactions_per_post": 20,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": "linkedin", "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    for platform in ("x", "tiktok"):
        if platform not in selected:
            continue
        child = create_social_leads_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "platform": platform,
                "title": f"{title} - {platform.upper()}",
                "keywords": terms[:12],
                "source_keywords": terms[:12],
                "country": row.region or "",
                "max_items": min(body.max_items, 100),
                "include_comments": True,
                "include_account_posts": True,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": platform, "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    if "reddit" in selected and _clean_list(body.reddit_communities, limit=12):
        child = create_social_leads_job_from_payload(
            db=db,
            current_user=current_user,
            payload={
                "platform": "reddit",
                "title": f"{title} - Reddit",
                "keywords": terms[:12],
                "communities": _clean_list(body.reddit_communities, limit=12),
                "country": row.region or "",
                "max_items": min(body.max_items, 100),
                "include_comments": True,
                "include_account_posts": True,
                "auto_run": bool(body.auto_run),
            },
            auto_run=bool(body.auto_run),
        )
        meta = dict(child.meta or {})
        meta["global_leads_job_id"] = row.job_id
        child.meta = meta
        children.append({"platform": "reddit", "job_id": child.job_id, "status": child.status, "progress": child.progress or 0})

    db.commit()
    return children


@router.get("/api/global-leads/source-catalog", summary="Global lead source catalog")
def global_lead_source_catalog(current_user: User = Depends(get_current_user)):
    return {"ok": True, "items": SOURCE_CATALOG}


@router.post("/api/global-leads/jobs", summary="Create global company lead job")
async def create_global_lead_job(
    body: GlobalLeadJobIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company_name = _clean_text(body.company_name, 255)
    domain = _normalize_domain(body.domain)
    region = _clean_text(body.region, 120)
    keywords = _clean_list(body.keywords, limit=12)
    if not (company_name or domain or keywords):
        raise HTTPException(status_code=400, detail="请填写企业名称、域名或获客关键词")
    payload = {
        "company_name": company_name,
        "domain": domain,
        "region": region,
        "target_profile": _clean_long(body.target_profile, 2000),
        "keywords": keywords,
        "sources": _source_ids(body.sources),
        "reddit_communities": _clean_list(body.reddit_communities, limit=12),
        "max_items": int(body.max_items or 80),
    }
    job_id = "gl_" + uuid.uuid4().hex[:24]
    row = GlobalLeadJob(
        job_id=job_id,
        user_id=current_user.id,
        status="queued",
        stage="planning",
        progress=5,
        title=_job_title(body),
        company_name=company_name,
        domain=domain,
        region=region,
        target_profile=payload["target_profile"],
        request_payload=payload,
        source_plan=_build_source_plan(payload["sources"], payload),
        child_jobs=[],
        result_payload={},
        meta={"created_from": "online_global_leads"},
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _seed_company_contact(db, row)
    children = _create_child_jobs(db, current_user, row, body)
    row.child_jobs = children
    row.status = "running" if children else "completed"
    row.stage = "collecting" if children else "ready_for_connectors"
    row.progress = 15 if children else 100
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    imported_web = await _run_web_search_sources(db, row)
    if imported_web and not children:
        row.status = "completed"
        row.stage = "completed"
        row.progress = 100
        row.completed_at = row.completed_at or _now()
        db.commit()
        db.refresh(row)
    row = _refresh_global_job(db, row, resume_stale_children=True)
    crm_count = db.query(GlobalLeadCrmContact).filter(
        GlobalLeadCrmContact.user_id == current_user.id,
        GlobalLeadCrmContact.job_id == row.job_id,
        GlobalLeadCrmContact.deleted_at.is_(None),
    ).count()
    return {"ok": True, "job": _job_payload(row, crm_count=crm_count)}


@router.get("/api/global-leads/jobs", summary="List global lead jobs")
async def list_global_lead_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GlobalLeadJob).filter(
        GlobalLeadJob.user_id == current_user.id,
        GlobalLeadJob.deleted_at.is_(None),
    )
    text = _clean_text(q, 120)
    if text:
        like = f"%{text}%"
        query = query.filter(
            or_(
                GlobalLeadJob.title.ilike(like),
                GlobalLeadJob.company_name.ilike(like),
                GlobalLeadJob.domain.ilike(like),
                GlobalLeadJob.region.ilike(like),
            )
        )
    total = query.count()
    rows = query.order_by(GlobalLeadJob.created_at.desc(), GlobalLeadJob.id.desc()).offset(offset).limit(limit).all()
    payloads = []
    for row in rows:
        row = _refresh_global_job(db, row, resume_stale_children=True)
        crm_count = db.query(GlobalLeadCrmContact).filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.job_id == row.job_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        ).count()
        payloads.append(_job_payload(row, crm_count=crm_count))
    return {"ok": True, "total": total, "items": payloads}


@router.get("/api/global-leads/jobs/{job_id}", summary="Get global lead job")
async def get_global_lead_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(GlobalLeadJob)
        .filter(
            GlobalLeadJob.user_id == current_user.id,
            GlobalLeadJob.job_id == job_id.strip().lower(),
            GlobalLeadJob.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = _refresh_global_job(db, row, resume_stale_children=True)
    contacts = (
        db.query(GlobalLeadCrmContact)
        .filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.job_id == row.job_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        )
        .order_by(GlobalLeadCrmContact.score.desc(), GlobalLeadCrmContact.created_at.desc())
        .limit(50)
        .all()
    )
    return {"ok": True, "job": _job_payload(row, crm_count=len(contacts)), "contacts": [_contact_payload(x) for x in contacts]}


@router.get("/api/global-leads/crm", summary="Global lead CRM contacts")
def list_global_lead_crm(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
    source: str = Query(""),
    job_id: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GlobalLeadCrmContact).filter(
        GlobalLeadCrmContact.user_id == current_user.id,
        GlobalLeadCrmContact.deleted_at.is_(None),
    )
    text = _clean_text(q, 120)
    if text:
        like = f"%{text}%"
        query = query.filter(
            or_(
                GlobalLeadCrmContact.name.ilike(like),
                GlobalLeadCrmContact.company.ilike(like),
                GlobalLeadCrmContact.role.ilike(like),
                GlobalLeadCrmContact.domain.ilike(like),
                GlobalLeadCrmContact.social_handle.ilike(like),
            )
        )
    if _clean_text(source, 64):
        query = query.filter(GlobalLeadCrmContact.source_platform == _clean_text(source, 64).lower())
    if _clean_text(job_id, 64):
        query = query.filter(GlobalLeadCrmContact.job_id == _clean_text(job_id, 64).lower())
    total = query.count()
    rows = query.order_by(GlobalLeadCrmContact.created_at.desc(), GlobalLeadCrmContact.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "total": total, "items": [_contact_payload(row) for row in rows]}


@router.patch("/api/global-leads/crm/{contact_id}", summary="Update CRM lead status")
def update_global_lead_crm_contact(
    contact_id: int,
    body: GlobalLeadStatusPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(GlobalLeadCrmContact)
        .filter(
            GlobalLeadCrmContact.user_id == current_user.id,
            GlobalLeadCrmContact.id == contact_id,
            GlobalLeadCrmContact.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="线索不存在")
    status = _clean_text(body.status, 32)
    if status:
        row.status = status
    if body.tags:
        row.tags = _clean_list(body.tags, limit=20)
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return {"ok": True, "contact": _contact_payload(row)}
