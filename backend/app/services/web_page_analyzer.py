"""Web page fetch / extraction / site crawl.

Server-side only, read-only. All outbound URLs pass SSRF validation before
every request and every redirect.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LobsterBot/1.0"
)
MAX_BYTES = 5_000_000
DEFAULT_TIMEOUT = 15.0
MAX_REDIRECTS = 5
MAX_PAGE_TEXT = 60_000
MAX_SITE_PAGE_TEXT = 12_000

_BLOCKED_HOST_SUFFIXES = (
    ".local", ".internal", ".localhost", ".lan", ".home", ".corp",
)
_BLOCKED_HOSTS = {
    "localhost", "metadata", "metadata.google.internal", "instance-data",
}
_SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".zip", ".rar", ".7z", ".exe", ".dmg", ".mp4", ".mp3", ".wav", ".avi",
    ".woff", ".woff2", ".ttf", ".eot",
)


class WebFetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebFetchError("dns_error", f"域名解析失败：{host}") from exc
    addresses = []
    for info in infos:
        addr = info[4][0]
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise WebFetchError("dns_error", f"域名解析失败：{host}")
    return addresses


def validate_public_url(url: str) -> str:
    """Normalize + validate a URL; raise WebFetchError when it is not safe."""
    raw = str(url or "").strip()
    if not raw:
        raise WebFetchError("invalid_url", "缺少网址")
    if len(raw) > 2048:
        raise WebFetchError("invalid_url", "网址过长")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError("invalid_url", "只支持 http/https 网址")
    if parsed.username or parsed.password:
        raise WebFetchError("invalid_url", "网址不能包含账号密码")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise WebFetchError("invalid_url", "网址缺少域名")
    if host in _BLOCKED_HOSTS or any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise WebFetchError("blocked_host", "该地址不允许访问")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        addresses = _resolve_host(host)
        for address in addresses:
            if not _is_public_ip(address):
                raise WebFetchError("blocked_host", "该地址解析到内网，已拒绝访问")
    else:
        if not _is_public_ip(host):
            raise WebFetchError("blocked_host", "该地址属于内网，已拒绝访问")
    return raw


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

class _PageParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "canvas"}
    _BLOCK_TAGS = {
        "p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "td",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "br", "dd", "dt", "figcaption",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.meta: Dict[str, str] = {}
        self.links: List[Dict[str, str]] = []
        self.images: List[Dict[str, str]] = []
        self.blocks: List[str] = []
        self._skip = 0
        self._in_title = False
        self._buf: List[str] = []
        self._anchor: Optional[Dict[str, str]] = None

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text and len(text) >= 2:
            self.blocks.append(text)

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag in self._SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
            return
        if tag == "link":
            rel = values.get("rel", "").lower()
            if "canonical" in rel and values.get("href"):
                self.meta.setdefault("canonical", values["href"])
            return
        if tag == "a":
            href = values.get("href", "").strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                self._anchor = {"href": href, "text": ""}
            return
        if tag == "img":
            src = (values.get("src") or values.get("data-src") or "").strip()
            if src:
                self.images.append({"src": src, "alt": values.get("alt", "").strip()})
            return
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "a" and self._anchor is not None:
            if self._anchor.get("href"):
                self.links.append(self._anchor)
            self._anchor = None
            return
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if not data.strip():
            if self._buf:
                self._buf.append(" ")
            return
        self._buf.append(data)
        if self._anchor is not None:
            self._anchor["text"] = (self._anchor.get("text", "") + data)[:200]

    def close(self) -> None:
        super().close()
        self._flush()


def _decode_body(raw: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type or "")
    if match:
        charset = match.group(1)
    if not charset:
        head = raw[:4096].decode("ascii", errors="ignore")
        match = re.search(r"charset=[\"']?([a-zA-Z0-9_\-]+)", head, re.I)
        if match:
            charset = match.group(1)
    for encoding in (charset, "utf-8", "gb18030", "latin-1"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_page(url: str, *, timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_BYTES) -> Dict[str, Any]:
    current = validate_public_url(url)
    deadline = time.monotonic() + max(3.0, float(timeout))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            remaining = max(1.0, deadline - time.monotonic())
            try:
                with client.stream("GET", current, headers=_headers(), timeout=remaining) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location:
                            raise WebFetchError("http_error", "重定向缺少 Location")
                        current = validate_public_url(urljoin(current, location))
                        continue
                    if response.status_code >= 400:
                        raise WebFetchError("http_error", f"目标页面返回 HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not any(
                        item in content_type for item in ("text/html", "application/xhtml", "text/plain")
                    ):
                        raise WebFetchError("unsupported_type", f"不支持的内容类型：{content_type.split(';')[0]}")
                    chunks: List[bytes] = []
                    total = 0
                    truncated = False
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            chunks.append(chunk[: max(0, max_bytes - (total - len(chunk)))])
                            truncated = True
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    return {
                        "url": url,
                        "final_url": str(response.url),
                        "status": response.status_code,
                        "content_type": content_type,
                        "html": _decode_body(raw, content_type),
                        "bytes": total,
                        "truncated": truncated,
                    }
            except httpx.TimeoutException as exc:
                raise WebFetchError("timeout", "抓取超时") from exc
            except httpx.HTTPError as exc:
                raise WebFetchError("fetch_error", f"抓取失败：{exc}") from exc
    raise WebFetchError("too_many_redirects", "重定向次数过多")

def _absolute(url: str, base: str) -> str:
    try:
        return urldefrag(urljoin(base, url))[0]
    except Exception:
        return ""


def extract_page(html: str, base_url: str, *, max_chars: int = MAX_PAGE_TEXT) -> Dict[str, Any]:
    parser = _PageParser()
    parser.feed(html or "")
    parser.close()
    title = (parser.meta.get("og:title") or parser.meta.get("twitter:title") or "".join(parser.title_parts)).strip()
    description = (
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
        or ""
    ).strip()
    canonical = _absolute(parser.meta.get("canonical", ""), base_url) or base_url
    blocks: List[str] = []
    seen = set()
    for block in parser.blocks:
        text = re.sub(r"\s+", " ", block).strip()
        if not text:
            continue
        key = text[:120]
        if key in seen:
            continue
        seen.add(key)
        blocks.append(text)
    full_text = "\n".join(blocks)
    truncated = len(full_text) > max_chars
    text = full_text[:max_chars]
    links: List[Dict[str, str]] = []
    link_seen = set()
    for item in parser.links:
        absolute = _absolute(item.get("href", ""), base_url)
        if not absolute or absolute in link_seen:
            continue
        link_seen.add(absolute)
        links.append({"url": absolute, "text": re.sub(r"\s+", " ", item.get("text", "")).strip()[:120]})
        if len(links) >= 300:
            break
    images: List[Dict[str, str]] = []
    image_seen = set()
    for item in parser.images:
        absolute = _absolute(item.get("src", ""), base_url)
        if not absolute or absolute in image_seen:
            continue
        image_seen.add(absolute)
        images.append({"url": absolute, "alt": item.get("alt", "")[:160]})
        if len(images) >= 100:
            break
    return {
        "title": title[:300],
        "description": description[:600],
        "canonical_url": canonical,
        "author": (parser.meta.get("author") or parser.meta.get("article:author") or "").strip()[:200],
        "published_at": (
            parser.meta.get("article:published_time")
            or parser.meta.get("og:published_time")
            or parser.meta.get("datepublished")
            or ""
        ).strip()[:80],
        "text": text,
        "full_text_length": len(full_text),
        "truncated": truncated,
        "word_count": len(re.findall(r"\S+", text)),
        "links": links,
        "images": images,
        "meta": {key: value[:300] for key, value in list(parser.meta.items())[:40]},
    }


def extract_facts(text: str, links: List[Dict[str, str]]) -> Dict[str, Any]:
    raw = text or ""
    latest = ""
    match = re.search(r"Latest:\s*Python\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)", raw, re.I)
    if match:
        latest = match.group(1)
    versions = []
    for item in re.findall(r"Python\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", raw):
        if item not in versions:
            versions.append(item)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    dated = []
    for index, line in enumerate(lines):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line) and index + 1 < len(lines):
            dated.append({"date": line, "title": lines[index + 1][:160]})
        elif re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", line) and index + 1 < len(lines):
            dated.append({"date": line, "title": lines[index + 1][:160]})
        if len(dated) >= 8:
            break
    wanted = {"downloads", "documentation", "docs", "community", "news", "events", "jobs", "about",
              "下载", "文档", "社区", "新闻", "活动", "招聘", "关于"}
    nav = []
    for link in links or []:
        label = str(link.get("text") or "").strip()
        if label.lower() in wanted and all(item["text"] != label for item in nav):
            nav.append({"text": label, "url": link.get("url", "")})
        if len(nav) >= 12:
            break
    return {"latest_version": latest, "versions": versions[:8], "dated_items": dated, "nav": nav}


def split_sections(blocks: List[str], limit: int = 12) -> List[Dict[str, str]]:
    sections: List[Dict[str, str]] = []
    current = {"heading": "", "lines": []}
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped:
            continue
        nxt = blocks[index + 1].strip() if index + 1 < len(blocks) else ""
        looks_heading = (
            len(stripped) <= 60
            and len(nxt) >= 60
            and not stripped.endswith((".", "。", "!", "?", "？", ":", "："))
        )
        if looks_heading:
            if current["lines"]:
                sections.append({"heading": current["heading"], "text": " ".join(current["lines"])[:800]})
            current = {"heading": stripped, "lines": []}
        else:
            current["lines"].append(stripped)
        if len(sections) >= limit:
            break
    if current["lines"] and len(sections) < limit:
        sections.append({"heading": current["heading"], "text": " ".join(current["lines"])[:800]})
    return sections


def analyze_page(url: str, *, timeout: float = DEFAULT_TIMEOUT, max_chars: int = MAX_PAGE_TEXT, text_offset: int = 0) -> Dict[str, Any]:
    fetched = fetch_page(url, timeout=timeout)
    extracted = extract_page(fetched["html"], fetched["final_url"], max_chars=MAX_PAGE_TEXT)
    full_text = extracted["text"]
    offset = max(0, min(int(text_offset or 0), len(full_text)))
    limit = max(1000, min(int(max_chars or MAX_PAGE_TEXT), MAX_PAGE_TEXT))
    sliced = full_text[offset:offset + limit]
    extracted["text"] = sliced
    extracted["full_text_length"] = len(full_text)
    extracted["text_offset"] = offset
    extracted["next_offset"] = (offset + len(sliced)) if offset + len(sliced) < len(full_text) else 0
    extracted["facts"] = extract_facts(full_text, extracted.get("links") or [])
    extracted["sections"] = split_sections([line for line in full_text.splitlines() if line.strip()])
    content_hash = hashlib.sha256(full_text.encode("utf-8", errors="ignore")).hexdigest()[:32]
    site = urlparse(fetched["final_url"]).netloc
    return {
        "url": fetched["url"],
        "final_url": fetched["final_url"],
        "site": site,
        "status": "ok",
        "bytes": fetched["bytes"],
        "body_truncated": fetched["truncated"],
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "content_hash": content_hash,
        **extracted,
    }


def extract_contacts(text: str) -> Dict[str, List[str]]:
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")))
    phones = []
    for match in re.findall(r"(?<![\w-])\+?\d[\d\s\-()]{6,20}\d(?![\w-])", text or ""):
        digits = re.sub(r"\D", "", match)
        if 7 <= len(digits) <= 20:
            phones.append(match.strip())
    return {"emails": emails[:10], "phones": sorted(set(phones))[:10]}

def _host_key(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _fetch_text(url: str, *, timeout: float = 8.0) -> Optional[str]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_headers()) as client:
            response = client.get(url)
            if response.status_code >= 400:
                return None
            return response.text[:1_000_000]
    except Exception:
        return None


def _parse_robots(text: str) -> Tuple[List[str], List[str], List[str], float]:
    """Return (disallow, allow, sitemaps, crawl_delay) for our crawler.

    robots.txt rules are grouped by User-agent.  Only the most specific group
    matching us (or the ``*`` group) may be applied; otherwise a rule that is
    meant for HTTrack/Nutch would block every crawler.
    """
    groups: List[Dict[str, Any]] = []
    sitemaps: List[str] = []
    current: Optional[Dict[str, Any]] = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "sitemap" and value:
            sitemaps.append(value)
            continue
        if key == "user-agent":
            if current is None or current.get("has_rules"):
                current = {"agents": set(), "disallow": [], "allow": [], "delay": 0.0, "has_rules": False}
                groups.append(current)
            current["agents"].add(value.lower())
            continue
        if key in ("disallow", "allow", "crawl-delay"):
            if current is None:
                current = {"agents": {"*"}, "disallow": [], "allow": [], "delay": 0.0, "has_rules": False}
                groups.append(current)
            current["has_rules"] = True
            if key == "disallow" and value:
                current["disallow"].append(value)
            elif key == "allow" and value:
                current["allow"].append(value)
            elif key == "crawl-delay":
                try:
                    current["delay"] = max(0.0, float(value))
                except ValueError:
                    pass

    chosen: Optional[Dict[str, Any]] = None
    for group in groups:
        if any("lobster" in agent for agent in group["agents"]):
            chosen = group
            break
    if chosen is None:
        for group in groups:
            if "*" in group["agents"]:
                chosen = group
                break
    if chosen is None:
        return [], [], sitemaps, 0.0
    return chosen["disallow"], chosen["allow"], sitemaps, float(chosen["delay"] or 0.0)


def _robots_allows(path: str, disallow: List[str], allow: Optional[List[str]] = None) -> bool:
    path = path or "/"
    for rule in allow or []:
        if rule and path.startswith(rule):
            return True
    for rule in disallow or []:
        if not rule:
            continue
        if rule == "/":
            return False
        if path.startswith(rule):
            return False
    return True


def _parse_sitemap(xml: str, *, limit: int = 200) -> List[str]:
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", flags=re.I)
    result: List[str] = []
    for url in urls:
        value = unescape(url.strip())
        if value.startswith(("http://", "https://")):
            result.append(value)
        if len(result) >= limit:
            break
    return result


_POSITIVE_PATTERNS: List[Tuple[str, int]] = [
    (r"about|company|profile|who-we-are|关于|公司|简介", 3),
    (r"product|solution|service|产品|服务|方案", 3),
    (r"contact|support|联系|客服", 3),
    (r"case|news|blog|team|案例|新闻|团队|资讯", 2),
    (r"pricing|price|价格|报价", 1),
]
_NEGATIVE_PATTERNS = re.compile(
    r"login|signin|signup|register|cart|checkout|privacy|terms|cookie|tag/|/page/\d+|"
    r"account|password|logout|wp-admin|search\?|登录|注册|购物车|隐私|条款",
    re.I,
)


def _link_score(url: str, anchor: str, depth: int) -> int:
    haystack = f"{url} {anchor}".lower()
    score = 0
    for pattern, weight in _POSITIVE_PATTERNS:
        if re.search(pattern, haystack, re.I):
            score += weight
    if _NEGATIVE_PATTERNS.search(url):
        score -= 8
    if url.count("/") > 6:
        score -= 1
    score -= depth
    return score


def _normalize_url(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}" + (f"?{parsed.query}" if parsed.query else "")


def _same_site(url: str, seed_host: str, allow_subdomains: bool, allow_domains: List[str]) -> bool:
    host = _host_key(urlparse(url).netloc)
    seed = _host_key(seed_host)
    if host == seed:
        return True
    if host in {_host_key(item) for item in allow_domains if item}:
        return True
    if allow_subdomains and (host.endswith("." + seed) or seed.endswith("." + host)):
        return True
    return False


def crawl_site(
    seed_urls: List[str],
    *,
    max_depth: int = 2,
    max_pages: int = 25,
    max_pages_per_site: int = 25,
    allow_subdomains: bool = True,
    allow_domains: Optional[List[str]] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    time_budget_seconds: int = 180,
    per_host_delay_ms: int = 500,
    respect_robots: bool = True,
    use_sitemap: bool = True,
) -> Dict[str, Any]:
    import heapq

    seeds = [str(item or "").strip() for item in (seed_urls or []) if str(item or "").strip()]
    if not seeds:
        raise WebFetchError("invalid_url", "至少需要一个官网地址")
    max_depth = max(0, min(int(max_depth or 2), 4))
    max_pages = max(1, min(int(max_pages or 25), 100))
    max_pages_per_site = max(1, min(int(max_pages_per_site or max_pages), max_pages))
    time_budget_seconds = max(20, min(int(time_budget_seconds or 180), 300))
    delay_seconds = max(0.0, min(float(per_host_delay_ms or 0) / 1000.0, 3.0))
    include_re = [re.compile(item, re.I) for item in (include_patterns or []) if item]
    exclude_re = [re.compile(item, re.I) for item in (exclude_patterns or []) if item]
    allow_hosts = [str(item or "").strip() for item in (allow_domains or []) if str(item or "").strip()]
    deadline = time.monotonic() + time_budget_seconds

    sites: List[Dict[str, Any]] = []
    total_pages = 0

    for seed in seeds:
        try:
            seed_url = validate_public_url(seed)
        except WebFetchError as exc:
            sites.append({"seed_url": seed, "status": "error", "error": exc.code, "message": str(exc), "pages": []})
            continue
        seed_host = urlparse(seed_url).netloc
        seed_key = _host_key(seed_host)
        disallow: List[str] = []
        allow_rules: List[str] = []
        sitemaps: List[str] = []
        if respect_robots:
            robots_text = _fetch_text(f"{urlparse(seed_url).scheme}://{seed_host}/robots.txt")
            if robots_text:
                disallow, allow_rules, sitemaps, _ = _parse_robots(robots_text)
        heap: List[Tuple[int, int, str]] = []
        heapq.heappush(heap, (-5, 0, _normalize_url(seed_url)))
        if use_sitemap:
            for sitemap_url in [item for item in sitemaps if item][:2]:
                xml = _fetch_text(sitemap_url)
                if not xml:
                    continue
                for candidate in _parse_sitemap(xml):
                    try:
                        validate_public_url(candidate)
                    except WebFetchError:
                        continue
                    if _same_site(candidate, seed_host, allow_subdomains, allow_hosts):
                        heapq.heappush(heap, (-3, 1, _normalize_url(candidate)))
            if not sitemaps:
                for guess in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
                    xml = _fetch_text(f"{urlparse(seed_url).scheme}://{seed_host}{guess}")
                    if xml and "<loc>" in xml:
                        for candidate in _parse_sitemap(xml):
                            try:
                                validate_public_url(candidate)
                            except WebFetchError:
                                continue
                            if _same_site(candidate, seed_host, allow_subdomains, allow_hosts):
                                heapq.heappush(heap, (-3, 1, _normalize_url(candidate)))
                        break

        visited = set()
        content_seen = set()
        pages: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        last_request_at = 0.0

        while heap and len(pages) < min(max_pages_per_site, max_pages) and time.monotonic() < deadline:
            _score, depth, url = heapq.heappop(heap)
            if url in visited:
                continue
            visited.add(url)
            path = urlparse(url).path or "/"
            if respect_robots and not _robots_allows(path, disallow, allow_rules):
                skipped.append({"url": url, "reason": "robots_disallow"})
                continue
            if exclude_re and any(pattern.search(url) for pattern in exclude_re):
                skipped.append({"url": url, "reason": "excluded"})
                continue
            if include_re and not any(pattern.search(url) for pattern in include_re):
                skipped.append({"url": url, "reason": "not_included"})
                continue
            wait = delay_seconds - (time.monotonic() - last_request_at)
            if wait > 0:
                time.sleep(min(wait, 2.0))
            last_request_at = time.monotonic()
            try:
                page = analyze_page(url, timeout=DEFAULT_TIMEOUT, max_chars=MAX_SITE_PAGE_TEXT)
            except WebFetchError as exc:
                skipped.append({"url": url, "reason": exc.code})
                continue
            if page.get("content_hash") in content_seen:
                skipped.append({"url": url, "reason": "duplicate_content"})
                continue
            content_seen.add(page.get("content_hash"))
            page["depth"] = depth
            page["contacts"] = extract_contacts(page.get("text", ""))
            pages.append(page)
            total_pages += 1
            if depth >= max_depth:
                continue
            for link in page.get("links", []):
                candidate = link.get("url", "")
                if not candidate or candidate in visited:
                    continue
                if urlparse(candidate).scheme not in ("http", "https"):
                    continue
                if not _same_site(candidate, seed_host, allow_subdomains, allow_hosts):
                    continue
                parsed_path = urlparse(candidate).path.lower()
                if any(parsed_path.endswith(ext) for ext in _SKIP_EXTENSIONS):
                    continue
                score = _link_score(candidate, link.get("text", ""), depth + 1)
                if score <= -5:
                    continue
                heapq.heappush(heap, (-score, depth + 1, _normalize_url(candidate)))

        contacts = {"emails": [], "phones": []}
        for page in pages:
            for key in ("emails", "phones"):
                for value in page.get("contacts", {}).get(key, []):
                    if value not in contacts[key]:
                        contacts[key].append(value)
        page_rows = [
            {
                "url": page["url"],
                "final_url": page.get("final_url", page["url"]),
                "title": page.get("title", ""),
                "description": page.get("description", ""),
                "depth": page.get("depth", 0),
                "status": "ok",
                "word_count": page.get("word_count", 0),
                "text": page.get("text", ""),
                "content_hash": page.get("content_hash", ""),
            }
            for page in pages
        ]
        sites.append(
            {
                "seed_url": seed_url,
                "domain": seed_key,
                "status": "ok",
                "pages_crawled": len(page_rows),
                "pages": page_rows,
                "contacts": contacts,
                "skipped": skipped[:50],
                "truncated": time.monotonic() >= deadline,
            }
        )
        if total_pages >= max_pages:
            break

    merged_contacts = {"emails": [], "phones": []}
    for site in sites:
        for key in ("emails", "phones"):
            for value in site.get("contacts", {}).get(key, []):
                if value not in merged_contacts[key]:
                    merged_contacts[key].append(value)
    return {
        "seed_urls": seeds,
        "sites": sites,
        "merged": {
            "pages_crawled": sum(int(site.get("pages_crawled", 0) or 0) for site in sites),
            "contacts": merged_contacts,
            "domains": [site.get("domain", "") for site in sites],
        },
        "limits": {
            "max_depth": max_depth,
            "max_pages": max_pages,
            "max_pages_per_site": max_pages_per_site,
            "time_budget_seconds": time_budget_seconds,
        },
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }