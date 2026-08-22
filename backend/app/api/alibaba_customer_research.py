from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from html import unescape as html_unescape
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import settings
from ..models import User
from .auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class AlibabaPublicSignalsBody(BaseModel):
    company_name: str = Field("", max_length=255)
    buyer_name: str = Field("", max_length=255)
    country: str = Field("", max_length=120)
    product_keywords: list[str] = Field(default_factory=list)
    market_scope: str = Field("", max_length=32)
    max_results: int = Field(default=6, ge=1, le=12)


class AlibabaEvidenceBody(AlibabaPublicSignalsBody):
    domain: str = Field("", max_length=255)
    email: str = Field("", max_length=255)
    phone: str = Field("", max_length=120)
    messages_text: str = Field("", max_length=4000)


_UPSTREAM_TASKS: dict[str, dict[str, Any]] = {
    "professional_network_company": {
        "path": "/api/v1/linkedin/web_v2/get_company_profile",
        "method": "GET",
        "allowed": {"url"},
        "title": "职业社媒公司资料",
        "category": "社媒公开资料",
    },
    "short_video_account_search": {
        "path": "/api/v1/tiktok/web/fetch_search_user",
        "method": "GET",
        "allowed": {"keyword", "cursor", "search_id", "cookie"},
        "title": "短视频账号公开资料",
        "category": "社媒公开资料",
    },
    "short_video_content_search": {
        "path": "/api/v1/tiktok/web/fetch_search_video",
        "method": "GET",
        "allowed": {"keyword", "count", "offset", "search_id", "cookie"},
        "title": "短视频内容公开资料",
        "category": "社媒公开资料",
    },
    "commerce_product_search": {
        "path": "/api/v1/tiktok/shop/web/fetch_search_products_list",
        "method": "GET",
        "allowed": {"search_word", "offset", "page_token", "region"},
        "title": "电商商品公开资料",
        "category": "电商公开资料",
    },
    "local_video_account_search": {
        "path": "/api/v1/wechat_channels/fetch_user_search",
        "method": "GET",
        "allowed": {"keywords", "page"},
        "title": "视频号账号公开资料",
        "category": "社媒公开资料",
    },
    "local_video_content_search": {
        "path": "/api/v1/wechat_channels/fetch_search_ordinary",
        "method": "GET",
        "allowed": {"keywords"},
        "title": "视频号内容公开资料",
        "category": "社媒公开资料",
    },
    "visual_social_search": {
        "path": "/api/v1/instagram/v1/fetch_search",
        "method": "GET",
        "allowed": {"query", "select"},
        "title": "图片社媒公开资料",
        "category": "社媒公开资料",
    },
    "public_discussion_search": {
        "path": "/api/v1/twitter/web/fetch_search_timeline",
        "method": "GET",
        "allowed": {"keyword", "search_type", "cursor"},
        "title": "海外公开讨论资料",
        "category": "社媒公开资料",
    },
}


def _upstream_base() -> str:
    base = (getattr(settings, "tikhub_api_base", "") or os.environ.get("TIKHUB_API_BASE") or "").strip()
    if base == "https://api.tikhub.dev":
        base = "https://api.tikhub.io"
    return (base or "https://api.tikhub.io").rstrip("/")


def _upstream_key() -> str:
    key = (getattr(settings, "tikhub_api_key", None) or os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="公开资料调研服务暂未配置")
    return key


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _clean_list(values: Any, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        values = str(values or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _clean_text(item, 80)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _dedupe_strings(values: list[str], limit: int = 20) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _lookup(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _collect_items(payload: Any) -> list[Any]:
    items: list[Any] = []

    def walk(value: Any, depth: int = 0) -> None:
        if len(items) >= 12 or depth > 4:
            return
        if isinstance(value, list):
            for row in value[:12]:
                if isinstance(row, dict):
                    items.append(row)
                else:
                    walk(row, depth + 1)
                if len(items) >= 12:
                    return
            return
        if not isinstance(value, dict):
            return
        for key in ("data", "items", "list", "results", "users", "videos", "products", "aweme_list", "user_list", "companies", "company"):
            if key in value:
                child = value.get(key)
                if isinstance(child, dict) and key == "company":
                    items.append(child)
                else:
                    walk(child, depth + 1)
                if len(items) >= 12:
                    return

    walk(payload)
    if not items and isinstance(payload, dict):
        items.append(payload)
    return items[:12]


def _result_count(payload: Any) -> int:
    items = _collect_items(payload)
    if items:
        return len(items)
    if isinstance(payload, dict):
        for key in ("total", "count", "total_count"):
            try:
                return int(payload.get(key) or 0)
            except Exception:
                pass
    return 0


def _item_summary(item: Any) -> str:
    if not isinstance(item, dict):
        return _clean_text(item, 220)
    title = _clean_text(
        _lookup(item, "title")
        or _lookup(item, "name")
        or _lookup(item, "nickname")
        or _lookup(item, "unique_id")
        or _lookup(item, "username")
        or _lookup(item, "companyName")
        or _lookup(item, "company_name")
        or _lookup(item, "author.nickname")
        or _lookup(item, "user.nickname"),
        100,
    )
    desc = _clean_text(
        _lookup(item, "description")
        or _lookup(item, "desc")
        or _lookup(item, "signature")
        or _lookup(item, "summary")
        or _lookup(item, "content")
        or _lookup(item, "author.signature")
        or _lookup(item, "user.signature"),
        220,
    )
    if title and desc and desc != title:
        return f"{title}：{desc}"
    return title or desc or _clean_text(json.dumps(item, ensure_ascii=False, default=str), 260)


def _payload_snippet(payload: Any) -> str:
    rows = [_item_summary(item) for item in _collect_items(payload)]
    rows = [x for x in rows if x]
    if rows:
        return "\n".join(rows[:8])
    return _clean_text(json.dumps(payload, ensure_ascii=False, default=str), 800)


def _safe_payload_preview(payload: Any) -> Any:
    text = _clean_text(json.dumps(payload, ensure_ascii=False, default=str), 3000)
    try:
        return json.loads(text)
    except Exception:
        return {"preview": text}


_GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "qq.com",
    "163.com",
    "126.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}

_NON_OFFICIAL_DOMAINS = {
    "alibaba.com",
    "made-in-china.com",
    "globalsources.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "google.com",
    "bing.com",
    "yahoo.com",
    "opencorporates.com",
    "dnb.com",
    "emis.com",
    "zoominfo.com",
    "crunchbase.com",
    "apollo.io",
    "signalhire.com",
    "rocketreach.co",
    "craft.co",
    "tracxn.com",
    "cbinsights.com",
    "importgenius.com",
    "panjiva.com",
    "volza.com",
    "seair.co.in",
    "exportgenius.in",
    "listofcompaniesin.com",
    "companylist.org",
    "businesslistings.net",
}

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7",
}


def _setting(name: str, *env_names: str) -> str:
    value = (getattr(settings, name, None) or "").strip() if hasattr(settings, name) else ""
    if value:
        return value
    for env_name in env_names:
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def _domain_from_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "@" in raw and not re.match(r"^https?://", raw, re.IGNORECASE):
        return _domain_from_email(raw)
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_from_email(value: Any) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return ""
    domain = text.rsplit("@", 1)[-1].lower().strip(" .;,\t\r\n")
    if domain.startswith("www."):
        domain = domain[4:]
    if domain in _GENERIC_EMAIL_DOMAINS:
        return ""
    return domain


def _strip_html(value: str, limit: int = 2200) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_unescape(text.replace("&nbsp;", " "))
    text = re.sub(r"@[a-z-]+\s+[^{]+\{[^{}]{0,1600}\}", " ", text, flags=re.IGNORECASE)
    return _clean_text(text, limit)


def _extract_title(html: str, fallback: str = "") -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html or "", flags=re.IGNORECASE)
    if match:
        return _strip_html(match.group(1), 180)
    return _clean_text(fallback, 180)


def _website_page_kind(url: str, text: str = "") -> str:
    value = f"{url} {text}".lower()
    if re.search(r"about|company|profile|who-we-are|qui-sommes|a-propos|propos|about-us|about_us", value):
        return "about"
    if re.search(r"contact|support|enquiry|inquiry|contacts|contact-us|contact_us", value):
        return "contact"
    if re.search(r"product|service|solution|catalog|shop|services|produit|products|solutions", value):
        return "products"
    return "home"


def _extract_official_page_links(base_url: str, html: str, limit: int = 4) -> list[str]:
    links: list[str] = []
    base_host = _domain_from_url(base_url)
    for href, label in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", html or "", flags=re.IGNORECASE):
        text = _strip_html(label, 160)
        kind = _website_page_kind(href, text)
        if kind == "home":
            continue
        url = urljoin(base_url, href).split("#", 1)[0]
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        if _domain_from_url(url) != base_host:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|mp4|avi|zip|rar|7z|css|js)(?:\?|$)", url, re.IGNORECASE):
            continue
        links.append(url)
    return _dedupe_strings(links, limit)


async def _fetch_web_page(client: httpx.AsyncClient, url: str, *, source_type: str, field: str, confidence: str, query: str = "") -> Optional[dict[str, Any]]:
    try:
        resp = await client.get(url)
    except Exception as exc:
        logger.debug("[ALIBABA-RESEARCH] page fetch failed url=%s err=%s", url, exc)
        return None
    if resp.status_code >= 400:
        return None
    content_type = (resp.headers.get("content-type") or "").lower()
    if content_type and not any(x in content_type for x in ("text/html", "application/xhtml", "text/plain", "application/json")):
        return None
    html = resp.text or ""
    snippet = _strip_html(html[:90000], 2200)
    if len(snippet) < 60:
        return None
    final_url = str(resp.url)
    title = _extract_title(html, _domain_from_url(final_url))
    raw = {
        "field": field,
        "status_code": resp.status_code,
        "final_url": final_url,
        "query": query,
        "body_fetched": True,
        "page_kind": _website_page_kind(final_url, title),
    }
    return {
        "source_type": source_type,
        "title": title or final_url,
        "url": final_url,
        "snippet": snippet,
        "confidence": confidence,
        "raw": raw,
    }


async def _fetch_domain_pages(domain: str, *, field: str = "domain", max_pages: int = 4) -> list[dict[str, Any]]:
    host = _domain_from_url(domain)
    if not host:
        return []
    out: list[dict[str, Any]] = []
    host_candidates = _dedupe_strings([host, f"www.{host}" if not host.startswith("www.") else host[4:]], 2)
    async with httpx.AsyncClient(timeout=16.0, follow_redirects=True, trust_env=True, headers=_HTTP_HEADERS) as client:
        home_html = ""
        home_url = ""
        for candidate in host_candidates:
            if out:
                break
            for scheme in ("https", "http"):
                page = await _fetch_web_page(
                    client,
                    f"{scheme}://{candidate}",
                    source_type="official_website",
                    field=field,
                    confidence="A" if field in {"domain", "email"} else "B",
                )
                if not page:
                    continue
                out.append(page)
                home_url = str(page.get("url") or "")
                try:
                    home_resp = await client.get(home_url)
                    home_html = home_resp.text or ""
                except Exception:
                    home_html = ""
                break
        if home_html and home_url:
            for link in _extract_official_page_links(home_url, home_html, max(0, max_pages - len(out))):
                if len(out) >= max_pages:
                    break
                page = await _fetch_web_page(
                    client,
                    link,
                    source_type="official_website",
                    field=field,
                    confidence="A" if field in {"domain", "email"} else "B",
                )
                if page:
                    out.append(page)
        if len(out) < max_pages:
            fallback_bases = [home_url] if home_url else [f"https://{x}" for x in host_candidates] + [f"http://{x}" for x in host_candidates]
            for base_url in fallback_bases:
                if len(out) >= max_pages:
                    break
                if not base_url:
                    continue
                for path in ("/about", "/about-us", "/company", "/contact", "/contact-us", "/products", "/services"):
                    if len(out) >= max_pages:
                        break
                    link = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
                    if any(_domain_from_url(str(x.get("url") or "")) == _domain_from_url(link) and str(x.get("url") or "").rstrip("/") == link.rstrip("/") for x in out):
                        continue
                    page = await _fetch_web_page(
                        client,
                        link,
                        source_type="official_website",
                        field=field,
                        confidence="A" if field in {"domain", "email"} else "B",
                    )
                    if page:
                        out.append(page)
                        if not home_url:
                            home_url = str(page.get("url") or "")
    return out


def _company_tokens(value: Any) -> list[str]:
    text = _clean_text(value, 255)
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5]+", " ", text).lower()
    stop = {"ltd", "llc", "inc", "co", "corp", "company", "limited", "sa", "sarl", "the", "and", "group", "trading", "import", "export"}
    return [x for x in text.split() if len(x) >= 3 and x not in stop][:8]


def _country_aliases(value: Any) -> list[str]:
    country = _clean_text(value, 80).lower()
    aliases = {
        "brazil": ["brazil", "brasil", ".br"],
        "brasil": ["brazil", "brasil", ".br"],
        "巴西": ["brazil", "brasil", ".br"],
        "congo": ["congo", "pointe-noire", "brazzaville", ".cg", ".cd"],
        "刚果": ["congo", "pointe-noire", "brazzaville", ".cg", ".cd"],
        "united states": ["united states", "usa", "u.s.", ".us"],
        "美国": ["united states", "usa", "u.s.", ".us"],
        "india": ["india", ".in"],
        "印度": ["india", ".in"],
        "new zealand": ["new zealand", ".nz"],
        "canada": ["canada", ".ca"],
        "nigeria": ["nigeria", ".ng"],
        "mexico": ["mexico", ".mx"],
        "egypt": ["egypt", ".eg"],
        "ghana": ["ghana", ".gh"],
        "kenya": ["kenya", ".ke"],
    }
    out: list[str] = []
    for key, values in aliases.items():
        if key in country:
            out.extend(values)
    if country:
        out.append(country)
    return _dedupe_strings(out, 8)


def _country_code(value: Any) -> str:
    country = _clean_text(value, 80).lower()
    mapping = {
        "brazil": "br",
        "brasil": "br",
        "巴西": "br",
        "united states": "us",
        "usa": "us",
        "美国": "us",
        "india": "in",
        "印度": "in",
        "new zealand": "nz",
        "canada": "ca",
        "nigeria": "ng",
        "mexico": "mx",
        "egypt": "eg",
        "ghana": "gh",
        "kenya": "ke",
        "united kingdom": "gb",
        "uk": "gb",
        "france": "fr",
        "germany": "de",
        "italy": "it",
        "spain": "es",
        "turkey": "tr",
    }
    for key, code in mapping.items():
        if key in country:
            return code
    return ""


def _token_match_count(tokens: list[str], hay: str) -> int:
    value = hay.lower()
    return sum(1 for token in tokens if token and token in value)


def _is_non_official_domain(host: str) -> bool:
    value = (host or "").lower().lstrip("www.")
    return any(value == item or value.endswith("." + item) for item in _NON_OFFICIAL_DOMAINS)


def _official_candidate_score(body: AlibabaEvidenceBody, row: dict[str, Any]) -> int:
    url = str(row.get("url") or "")
    host = _domain_from_url(url)
    if not host or _is_non_official_domain(host):
        return -100
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or "")
    hay = f"{host} {title} {snippet}".lower()
    tokens = _company_tokens(body.company_name)
    country_hits = _country_aliases(body.country)
    has_country_hit = bool(country_hits and any(alias and alias in hay for alias in country_hits))
    score = 0
    for token in tokens:
        if token in hay:
            score += 18
        if token in host:
            score += 22
    if re.search(r"\b(official|home|about|contact|products|services|solutions)\b", hay, re.IGNORECASE):
        score += 12
    if has_country_hit:
        score += 12
    if re.search(r"/(about|company|contact|products|services|solutions)", url, re.IGNORECASE):
        score += 8
    if re.search(r"directory|company-profile|profile|reviews|supplier|manufacturer|yellow|business-listing|companies/", url, re.IGNORECASE):
        score -= 35
    if host and tokens and not any(token in host for token in tokens):
        score -= 10
    if len(tokens) <= 1 and body.country and not has_country_hit:
        score -= 45
    return score


def _public_search_row_relevant(body: AlibabaEvidenceBody, row: dict[str, Any], field: str) -> bool:
    url = str(row.get("url") or "")
    if re.search(r"/aclick|/ads?|doubleclick|googleadservices", url, re.IGNORECASE):
        return False
    hay = f"{_domain_from_url(url)} {row.get('title') or ''} {row.get('snippet') or ''}".lower()
    company_tokens = _company_tokens(body.company_name)
    buyer_tokens = _company_tokens(body.buyer_name)
    country_hits = _country_aliases(body.country)
    has_country_hit = bool(country_hits and any(alias and alias in hay for alias in country_hits))
    company_need = 2 if len(company_tokens) >= 2 else 1
    company_hits = _token_match_count(company_tokens, hay)
    if field == "company_name":
        if len(company_tokens) <= 1 and body.country and not has_country_hit:
            return False
        return not company_tokens or company_hits >= company_need
    if field == "buyer_name":
        if not buyer_tokens:
            return False
        buyer_phrase = _clean_text(body.buyer_name, 160).lower()
        if buyer_phrase and buyer_phrase in hay:
            return True
        return len(buyer_tokens) == 1 and _token_match_count(buyer_tokens, hay) >= 1
    if field in {"product_keywords", "email"}:
        if len(company_tokens) <= 1 and body.country and not has_country_hit:
            return False
        return bool(company_tokens and company_hits >= company_need)
    return True


def _archive_public_search_queries(body: AlibabaEvidenceBody) -> list[dict[str, str]]:
    company = _clean_text(body.company_name, 160)
    buyer = _clean_text(body.buyer_name, 160)
    country = _clean_text(body.country, 80)
    domain = _domain_from_url(body.domain) or _domain_from_email(body.email)
    products = _clean_list(body.product_keywords, 4)
    rows: list[dict[str, str]] = []

    def add(field: str, query: str, purpose: str) -> None:
        query = _clean_text(query, 260)
        if query:
            rows.append({"field": field, "query": query, "purpose": purpose})

    if company:
        add("company_name", f"{company} official website contact", "official website discovery")
        add("company_name", f"{company} company profile {country}".strip(), "company entity validation")
        add("company_name", f"{company} importer distributor supplier", "trade role validation")
        add("company_name", f"{company} LinkedIn company", "professional company profile discovery")
    if domain:
        add("domain", f"site:{domain} about contact products", "official website body extraction")
        add("domain", f"\"{domain}\" company contact", "domain ownership validation")
    if body.email:
        mail_domain = _domain_from_email(body.email)
        add("email", f"\"{body.email}\"", "email public occurrence")
        if mail_domain:
            add("email", f"\"{mail_domain}\" company contact", "email domain validation")
    if buyer:
        add("buyer_name", f"{buyer} {company or country}".strip(), "buyer identity signal")
    for keyword in products[:2]:
        if company:
            add("product_keywords", f"{company} {keyword}", "product-company relation validation")
    return rows[:10]


async def _search_serper(query: str, max_results: int) -> list[dict[str, Any]]:
    key = _setting("serper_api_key", "SERPER_API_KEY", "GOOGLE_SERPER_API_KEY")
    if not key:
        return []
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        resp = await client.post("https://google.serper.dev/search", json={"q": query, "num": max_results}, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"stable search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    rows: list[dict[str, Any]] = []
    for item in (data.get("organic") or [])[:max_results]:
        if isinstance(item, dict):
            rows.append(
                {
                    "source_type": "web_search",
                    "title": item.get("title") or "",
                    "url": item.get("link") or "",
                    "snippet": item.get("snippet") or "",
                    "confidence": "B",
                    "raw": {"query": query, "provider": "stable_search"},
                }
            )
    return rows


async def _search_google_cse(query: str, max_results: int) -> list[dict[str, Any]]:
    key = _setting("google_cse_api_key", "GOOGLE_CSE_API_KEY", "GOOGLE_API_KEY")
    cx = _setting("google_cse_cx", "GOOGLE_CSE_CX", "GOOGLE_CUSTOM_SEARCH_CX")
    if not key or not cx:
        return []
    params = {"key": key, "cx": cx, "q": query, "num": max(1, min(10, max_results))}
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        resp = await client.get("https://www.googleapis.com/customsearch/v1", params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"stable search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    rows: list[dict[str, Any]] = []
    for item in (data.get("items") or [])[:max_results]:
        if isinstance(item, dict):
            rows.append(
                {
                    "source_type": "web_search",
                    "title": item.get("title") or "",
                    "url": item.get("link") or "",
                    "snippet": item.get("snippet") or "",
                    "confidence": "B",
                    "raw": {"query": query, "provider": "stable_search"},
                }
            )
    return rows


async def _search_tavily(query: str, max_results: int) -> list[dict[str, Any]]:
    key = _setting("tavily_api_key", "TAVILY_API_KEY")
    if not key:
        return []
    payload = {"api_key": key, "query": query, "max_results": max_results, "include_answer": False}
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"stable search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    data = resp.json() if resp.content else {}
    rows: list[dict[str, Any]] = []
    for item in (data.get("results") or [])[:max_results]:
        if isinstance(item, dict):
            rows.append(
                {
                    "source_type": "web_search",
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("content") or "",
                    "confidence": "B",
                    "raw": {"query": query, "provider": "stable_search"},
                }
            )
    return rows


def _duckduckgo_result_url(value: str) -> str:
    raw = str(value or "").replace("&amp;", "&").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query or "")
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    except Exception:
        pass
    return raw


async def _search_duckduckgo_html(query: str, max_results: int) -> list[dict[str, Any]]:
    if max_results <= 0:
        return []
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, trust_env=False, headers=_HTTP_HEADERS) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"fallback search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    html = resp.text or ""
    rows: list[dict[str, Any]] = []
    for link in re.finditer(r"<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>", html, flags=re.IGNORECASE):
        block = html[max(0, link.start() - 1200) : min(len(html), link.end() + 1800)]
        snippet = ""
        sm = re.search(r"class=\"result__snippet\"[^>]*>([\s\S]*?)</a>", block, flags=re.IGNORECASE) or re.search(r"class=\"result__snippet\"[^>]*>([\s\S]*?)</div>", block, flags=re.IGNORECASE)
        if sm:
            snippet = _strip_html(sm.group(1), 600)
        rows.append(
            {
                "source_type": "web_search",
                "title": _strip_html(link.group(2), 180),
                "url": _duckduckgo_result_url(link.group(1)),
                "snippet": snippet,
                "confidence": "C",
                "raw": {"query": query, "provider": "fallback_search"},
            }
        )
        if len(rows) >= max_results:
            break
    return rows


async def _search_bing_html(query: str, max_results: int) -> list[dict[str, Any]]:
    if max_results <= 0:
        return []
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max(1, min(10, max_results))}"
    async with httpx.AsyncClient(timeout=18.0, follow_redirects=True, trust_env=False, headers=_HTTP_HEADERS) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"fallback search HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    html = resp.text or ""
    rows: list[dict[str, Any]] = []
    for block in re.findall(r"<li class=\"b_algo\"[\s\S]*?</li>", html, flags=re.IGNORECASE)[:max_results]:
        link = re.search(r"<a[^>]+href=\"([^\"]+)\"[^>]*>([\s\S]*?)</a>", block, flags=re.IGNORECASE)
        if not link:
            continue
        pm = re.search(r"<p[^>]*>([\s\S]*?)</p>", block, flags=re.IGNORECASE)
        rows.append(
            {
                "source_type": "web_search",
                "title": _strip_html(link.group(2), 180),
                "url": link.group(1),
                "snippet": _strip_html(pm.group(1), 600) if pm else "",
                "confidence": "C",
                "raw": {"query": query, "provider": "fallback_search"},
            }
        )
    return rows


def _stable_search_configured() -> bool:
    return bool(
        _setting("serper_api_key", "SERPER_API_KEY", "GOOGLE_SERPER_API_KEY")
        or (_setting("google_cse_api_key", "GOOGLE_CSE_API_KEY", "GOOGLE_API_KEY") and _setting("google_cse_cx", "GOOGLE_CSE_CX", "GOOGLE_CUSTOM_SEARCH_CX"))
        or _setting("tavily_api_key", "TAVILY_API_KEY")
    )


async def _search_public_web(query: str, max_results: int) -> list[dict[str, Any]]:
    if not query.strip() or max_results <= 0:
        return []
    provider = _setting("customer_research_search_provider", "CUSTOMER_RESEARCH_SEARCH_PROVIDER").lower() or "auto"
    stable = [_search_serper, _search_google_cse, _search_tavily]
    fallback = [_search_duckduckgo_html, _search_bing_html]
    if provider == "serper":
        order = [_search_serper]
    elif provider in {"google_cse", "google"}:
        order = [_search_google_cse]
    elif provider == "tavily":
        order = [_search_tavily]
    elif provider in {"html", "fallback"}:
        order = fallback
    else:
        order = stable + fallback
    for fn in order:
        try:
            rows = await fn(query, max_results)
            if rows:
                return rows
        except Exception as exc:
            logger.info("[ALIBABA-RESEARCH] web search provider failed query=%s err=%s", query[:120], exc)
    return []


async def _discover_official_pages_from_search(body: AlibabaEvidenceBody, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not body.company_name or not rows:
        return []
    ranked = sorted(rows, key=lambda item: _official_candidate_score(body, item), reverse=True)
    for row in ranked[:5]:
        score = _official_candidate_score(body, row)
        if score < 28:
            continue
        host = _domain_from_url(str(row.get("url") or ""))
        if not host:
            continue
        pages = await _fetch_domain_pages(host, field="company_name")
        if pages:
            for item in pages:
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                raw.update({"field": "company_name", "discovered_from": "public_web_search", "candidate_score": score})
                item["raw"] = raw
            return pages
    return []


async def _enrich_search_rows(rows: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=14.0, follow_redirects=True, trust_env=True, headers=_HTTP_HEADERS) as client:
        for row in rows:
            if len(out) >= limit:
                break
            url = str(row.get("url") or "").strip()
            if not re.match(r"^https?://", url, re.IGNORECASE):
                continue
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
            page = await _fetch_web_page(
                client,
                url,
                source_type="web_search",
                field=str(raw.get("field") or ""),
                confidence=str(row.get("confidence") or "C")[:16],
                query=str(raw.get("query") or ""),
            )
            if page:
                merged_raw = page.get("raw") if isinstance(page.get("raw"), dict) else {}
                merged_raw.update(raw)
                merged_raw["body_fetched"] = True
                page["raw"] = merged_raw
                page["title"] = row.get("title") or page.get("title") or url
                out.append(page)
            else:
                out.append(row)
    return out


def _registry_summary(company: dict[str, Any]) -> str:
    pieces = []
    for label, key in (
        ("Name", "name"),
        ("Number", "company_number"),
        ("Jurisdiction", "jurisdiction_string"),
        ("Status", "current_status"),
        ("Type", "company_type"),
        ("Incorporated", "incorporation_date"),
        ("Address", "registered_address_in_full"),
    ):
        value = _clean_text(company.get(key), 260)
        if value:
            pieces.append(f"{label}: {value}")
    return "; ".join(pieces)


async def _lookup_company_registry(body: AlibabaEvidenceBody, max_results: int) -> tuple[list[dict[str, Any]], list[str]]:
    company_name = _clean_text(body.company_name, 180)
    if not company_name:
        return [], ["企业主体库需要明确公司名才能查询。"]
    params: dict[str, Any] = {"q": company_name, "per_page": max(1, min(5, max_results))}
    code = _country_code(body.country)
    if code:
        params["country_code"] = code
    key = _setting("opencorporates_api_key", "OPENCORPORATES_API_KEY")
    if key:
        params["api_token"] = key
    url = "https://api.opencorporates.com/v0.4/companies/search"
    try:
        async with httpx.AsyncClient(timeout=22.0, trust_env=False, headers={"User-Agent": _HTTP_HEADERS["User-Agent"], "Accept": "application/json"}) as client:
            resp = await client.get(url, params=params)
    except Exception as exc:
        logger.info("[ALIBABA-RESEARCH] company registry skipped company=%s err=%s", company_name, exc)
        return [], ["企业主体库暂不可用，无法核验公司注册主体。"]
    if resp.status_code in {401, 403, 429}:
        return [], ["企业主体库访问受限，需要配置正式 API Key 或提高配额。"]
    if resp.status_code >= 400:
        return [], [f"企业主体库查询失败 HTTP {resp.status_code}。"]
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        return [], ["企业主体库返回格式异常。"]
    rows: list[dict[str, Any]] = []
    company_tokens = _company_tokens(company_name)
    for wrapper in (((data.get("results") or {}).get("companies") or []) if isinstance(data, dict) else [])[: max(1, min(5, max_results))]:
        item = wrapper.get("company") if isinstance(wrapper, dict) else None
        if not isinstance(item, dict):
            continue
        summary = _registry_summary(item)
        if not summary:
            continue
        hay = f"{item.get('name') or ''} {item.get('jurisdiction_string') or ''} {item.get('registered_address_in_full') or ''}".lower()
        company_hit = _token_match_count(company_tokens, hay)
        confidence = "A" if company_tokens and company_hit >= min(2, len(company_tokens)) and (not code or str(item.get("jurisdiction_code") or "").lower().startswith(code)) else "B"
        rows.append(
            {
                "source_type": "company_registry",
                "title": f"企业主体库：{_clean_text(item.get('name'), 140) or company_name}",
                "url": item.get("registry_url") or item.get("opencorporates_url") or "",
                "snippet": summary,
                "confidence": confidence,
                "raw": {
                    "field": "company_name",
                    "company_number": item.get("company_number") or "",
                    "jurisdiction_code": item.get("jurisdiction_code") or "",
                    "registry_url": item.get("registry_url") or "",
                    "current_status": item.get("current_status") or "",
                },
            }
        )
    if not rows:
        return [], ["企业主体库没有匹配到可用主体记录。"]
    return rows, []


async def _build_first_priority_evidence(body: AlibabaEvidenceBody) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    required_resources: list[str] = []
    max_results = max(1, min(20, int(body.max_results or 8)))
    domain_candidates = _dedupe_strings(
        [
            _domain_from_url(body.domain),
            _domain_from_email(body.email),
        ],
        3,
    )
    for domain in domain_candidates:
        pages = await _fetch_domain_pages(domain, field="domain" if domain == _domain_from_url(body.domain) else "email")
        items.extend(pages)

    registry_rows, registry_gaps = await _lookup_company_registry(body, max_results)
    items.extend(registry_rows)
    required_resources.extend(registry_gaps)

    if not _stable_search_configured():
        required_resources.append("稳定搜索 API 未配置，当前只能用网页兜底搜索，覆盖率和稳定性不足。")

    public_rows: list[dict[str, Any]] = []
    per_query = max(1, min(4, max_results))
    for query in _archive_public_search_queries(body):
        rows = await _search_public_web(query["query"], per_query)
        rows = [item for item in rows if _public_search_row_relevant(body, item, query.get("field") or "")]
        for item in rows:
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            raw.update({"field": query.get("field") or "", "purpose": query.get("purpose") or "", "query": query.get("query") or raw.get("query") or ""})
            item["raw"] = raw
        enriched = await _enrich_search_rows(rows[:2], limit=2)
        items.extend(enriched)
        public_rows.extend(rows)
        await asyncio_sleep()

    if not any(str(x.get("source_type") or "") == "official_website" for x in items):
        items.extend(await _discover_official_pages_from_search(body, public_rows))

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    priority = {"official_website": 0, "company_registry": 1, "web_search": 2}
    for item in sorted(items, key=lambda x: (priority.get(str(x.get("source_type") or ""), 9), str(x.get("title") or ""))):
        url = str(item.get("url") or "").strip()
        snippet = _clean_text(item.get("snippet"), 2200)
        key = (url or f"{item.get('source_type')}-{item.get('title')}-{snippet[:120]}").lower()
        if not key or key in seen or not snippet:
            continue
        seen.add(key)
        item["snippet"] = snippet
        out.append(item)
        if len(out) >= max(12, min(60, max_results * 5)):
            break

    if not any(str(x.get("source_type") or "") == "official_website" for x in out):
        required_resources.append("未抓取到可核验官网正文，需要官网域名、企业邮箱域名或更稳定的搜索数据。")
    if not any(str(x.get("source_type") or "") == "company_registry" for x in out):
        required_resources.append("未拿到企业主体库记录，无法确认注册主体、状态和辖区。")
    if not any(str(x.get("source_type") or "") == "web_search" for x in out):
        required_resources.append("未拿到第三方公开网页正文，无法补充客户背景和第三方佐证。")

    return {"items": out, "required_resources": _dedupe_strings(required_resources, 10), "skipped_count": max(0, len(required_resources))}


async def asyncio_sleep() -> None:
    # Keep outbound probing slow enough to avoid bursts against search/page hosts.
    import asyncio

    await asyncio.sleep(random.uniform(0.25, 0.65))


async def _call_public_signal(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = _UPSTREAM_TASKS.get(kind)
    if not spec:
        raise HTTPException(status_code=400, detail="不支持的公开资料类型")
    clean_params = {k: v for k, v in (params or {}).items() if k in spec["allowed"] and v not in (None, "")}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=35.0, trust_env=False) as client:
        resp = await client.get(
            f"{_upstream_base()}{spec['path']}",
            headers={"Authorization": f"Bearer {_upstream_key()}", "Accept": "application/json"},
            params=clean_params,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": (resp.text or "")[:4000]}
    if resp.status_code >= 400:
        return {"ok": False, "kind": kind, "reason": f"公开资料接口返回 {resp.status_code}", "latency_ms": latency_ms}
    code = payload.get("code") if isinstance(payload, dict) else None
    if code not in (None, 0, 1, 200, "0", "1", "200"):
        return {"ok": False, "kind": kind, "reason": _clean_text(payload.get("message_zh") or payload.get("message") or "公开资料接口未返回成功", 240), "latency_ms": latency_ms}
    count = _result_count(payload)
    return {
        "ok": True,
        "kind": kind,
        "source_type": kind,
        "title": spec["title"],
        "category": spec["category"],
        "result_count": count,
        "snippet": _payload_snippet(payload),
        "latency_ms": latency_ms,
    }


def _build_tasks(body: AlibabaPublicSignalsBody) -> list[dict[str, Any]]:
    company = _clean_text(body.company_name, 160)
    buyer = _clean_text(body.buyer_name, 160)
    country = _clean_text(body.country, 80)
    products = _clean_list(body.product_keywords, 4)
    is_cn = bool(re.search(r"[\u4e00-\u9fa5]", " ".join([company, buyer, country]))) or _clean_text(body.market_scope).upper() == "CN"
    tasks: list[dict[str, Any]] = []
    if company:
        tasks.extend(
            [
                {
                    "kind": "professional_network_company",
                    "params": {"url": f"https://www.linkedin.com/company/{re.sub(r'[^a-z0-9]+', '-', company.lower()).strip('-')}/"},
                    "field": "company_name",
                },
                {"kind": "short_video_account_search", "params": {"keyword": company}, "field": "company_name"},
                {"kind": "visual_social_search", "params": {"query": company, "select": "users"}, "field": "company_name"},
                {"kind": "public_discussion_search", "params": {"keyword": company, "search_type": "Top"}, "field": "company_name"},
            ]
        )
    if products:
        product = products[0]
        tasks.extend(
            [
                {"kind": "short_video_content_search", "params": {"keyword": product, "count": max(2, min(8, body.max_results)), "offset": 0}, "field": "product_keywords"},
                {"kind": "commerce_product_search", "params": {"search_word": product, "offset": 0}, "field": "product_keywords"},
            ]
        )
    if is_cn and (company or buyer):
        keyword = company or buyer
        tasks.extend(
            [
                {"kind": "local_video_account_search", "params": {"keywords": keyword, "page": 1}, "field": "company_name" if company else "buyer_name"},
                {"kind": "local_video_content_search", "params": {"keywords": keyword}, "field": "company_name" if company else "buyer_name"},
            ]
        )
    return tasks[:8]


@router.post("/api/alibaba-customer-research/public-signals", summary="阿里询盘客户公开资料调研")
async def alibaba_customer_public_signals(
    body: AlibabaPublicSignalsBody,
    current_user: User = Depends(get_current_user),
):
    tasks = _build_tasks(body)
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for task in tasks:
        try:
            result = await _call_public_signal(task["kind"], task.get("params") or {})
            if result.get("ok") and int(result.get("result_count") or 0) > 0 and _clean_text(result.get("snippet"), 20):
                items.append(
                    {
                        "source_type": result["source_type"],
                        "title": result["title"],
                        "category": result["category"],
                        "field": task.get("field") or "",
                        "result_count": int(result.get("result_count") or 0),
                        "snippet": _clean_text(result.get("snippet"), 1200),
                        "confidence": "B",
                        "raw": {
                            "field": task.get("field") or "",
                            "result_count": int(result.get("result_count") or 0),
                            "latency_ms": result.get("latency_ms") or 0,
                        },
                    }
                )
            else:
                skipped.append({"kind": task["kind"], "field": task.get("field") or "", "reason": result.get("reason") or "未获取到有效结果"})
        except Exception as exc:
            logger.info("[ALIBABA-RESEARCH] public signal skipped user=%s kind=%s err=%s", current_user.id, task.get("kind"), exc)
            skipped.append({"kind": task.get("kind") or "", "field": task.get("field") or "", "reason": "公开资料暂不可用"})
    return {"ok": True, "items": items, "skipped_count": len(skipped)}


@router.post("/api/alibaba-customer-research/evidence", summary="客户档案一手证据")
async def alibaba_customer_evidence(
    body: AlibabaEvidenceBody,
    current_user: User = Depends(get_current_user),
):
    """Return normalized first-priority evidence for an Alibaba customer archive."""
    result = await _build_first_priority_evidence(body)
    return {
        "ok": True,
        "items": result.get("items") or [],
        "required_resources": result.get("required_resources") or [],
        "skipped_count": int(result.get("skipped_count") or 0),
        "priority": "first",
        "source_inventory": [
            {"source_type": "official_website", "role": "网页正文与官网主体核验"},
            {"source_type": "web_search", "role": "发现官网、主体关联页面并抓取正文"},
            {"source_type": "company_registry", "role": "企业注册主体、状态、辖区和注册地址"},
        ],
    }
