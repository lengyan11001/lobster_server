"""速推/xskill：从官方 docs 拉取 pricing 并估算预扣/结算积分。

接口与字段语义与仓库文档一致：docs/model-pricing-guide.md
（GET /api/v3/models/{model_id}/docs，pricing.price_type / base_price 等）。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from ..core.config import settings
from .credits_amount import quantize_credits

_DOCS_CACHE: Dict[str, Tuple[float, Optional[dict]]] = {}
_MCP_MODELS_PRICING_CACHE: Dict[str, Tuple[float, Dict[str, dict]]] = {}
_CACHE_TTL_SEC = 3600

# 公开 docs 无条目的对话模型：流式常无 x_billing，仅能按 usage×费率估算；费率按与非流式 x_billing 同量级校准，可用 env JSON 覆盖。
_BUILTIN_CHAT_USAGE_CREDITS_PER_1K_BY_MODEL: Dict[str, float] = {
    "deepseek-chat": 0.2,
}

# ---------------------------------------------------------------------------
# DeepSeek 官方定价（1 元 = 100 积分）
# https://api-docs.deepseek.com/quick_start/pricing/
# ---------------------------------------------------------------------------
_DEEPSEEK_OFFICIAL_CREDITS_PER_1M: Dict[str, Dict[str, float]] = {
    "deepseek-chat": {
        "input_cache_miss": 200.0,   # ¥2.0/1M → 200 credits/1M
        "input_cache_hit":   20.0,   # ¥0.2/1M →  20 credits/1M
        "output":           300.0,   # ¥3.0/1M → 300 credits/1M
    },
    "deepseek-reasoner": {
        "input_cache_miss": 400.0,   # ¥4.0/1M
        "input_cache_hit":  100.0,   # ¥1.0/1M
        "output":          1600.0,   # ¥16.0/1M
    },
}


def credits_from_direct_api_usage(model: str, usage: Optional[dict]) -> Decimal:
    """按 DeepSeek 官方定价 + usage 中 cache hit/miss 精确计费。1 元 = 100 积分。"""
    if not usage or not isinstance(usage, dict):
        return Decimal(0)
    mid = (model or "").strip()
    pricing = _DEEPSEEK_OFFICIAL_CREDITS_PER_1M.get(mid)
    if not pricing:
        return Decimal(0)

    cache_hit = 0
    cache_miss = 0
    try:
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    except (TypeError, ValueError):
        pass
    try:
        cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    except (TypeError, ValueError):
        pass
    if cache_hit == 0 and cache_miss == 0:
        try:
            cache_miss = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            pass

    completion = 0
    try:
        completion = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        pass

    cost = (
        cache_hit * pricing["input_cache_hit"] / 1_000_000
        + cache_miss * pricing["input_cache_miss"] / 1_000_000
        + completion * pricing["output"] / 1_000_000
    )
    if cost <= 0:
        return Decimal(0)
    return quantize_credits(cost)


def _usage_credits_per_1k_for_model(model_id: str) -> float:
    """无 docs、无上游价字段时：先查内置/配置的按模型费率，否则 sutui_chat_fallback_credits_per_1k。"""
    mid = (model_id or "").strip()
    try:
        default_rate = float(getattr(settings, "sutui_chat_fallback_credits_per_1k", 0.0) or 0.0)
    except (TypeError, ValueError):
        default_rate = 0.0
    merged: Dict[str, float] = dict(_BUILTIN_CHAT_USAGE_CREDITS_PER_1K_BY_MODEL)
    raw = (getattr(settings, "sutui_chat_usage_credits_per_1k_by_model_json", None) or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
        except json.JSONDecodeError:
            extra = None
        if isinstance(extra, dict):
            for k, v in extra.items():
                ks = str(k).strip()
                if not ks:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv > 0:
                    merged[ks] = fv
    if mid and mid in merged:
        return merged[mid]
    return default_rate


def _api_base() -> str:
    base = (getattr(settings, "sutui_api_base", None) or "https://api.apiz.ai").rstrip("/")
    if base == "https://api.xskill.ai":
        return "https://api.apiz.ai"
    return base


def _quantize_credits(value: float) -> int:
    """与速推侧金额习惯一致：先保留两位小数再取整为积分（避免浮点误差）。"""
    return int(round(float(value) + 1e-9, 2))


def _pricing_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _pricing_base_amount(pricing: dict) -> Optional[float]:
    for key in ("base_price", "amount", "price", "credits", "credit_cost"):
        if key not in pricing:
            continue
        x = _pricing_number(pricing.get(key))
        if x is not None:
            return x
    return None


def _num_outputs_from_params(params: Dict[str, Any]) -> int:
    n = params.get("num_images") or params.get("n") or params.get("batch_size") or params.get("num_outputs") or 1
    try:
        n_int = int(n)
    except (TypeError, ValueError):
        n_int = 1
    return max(1, n_int)


def _duration_seconds_from_params(params: Dict[str, Any]) -> float:
    for key in (
        "duration",
        "duration_sec",
        "duration_seconds",
        "seconds",
        "length",
        "video_length",
        "audio_length",
    ):
        v = params.get(key)
        if v is None:
            continue
        try:
            d = float(v)
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return 0.0


def _pricing_params_with_defaults(pricing: dict, params: Dict[str, Any]) -> Dict[str, Any]:
    defaults = pricing.get("_param_defaults")
    if not isinstance(defaults, dict):
        return params
    merged = dict(defaults)
    merged.update(params)
    return merged


def _duration_from_example(ex: dict) -> float:
    for key in ("duration", "duration_sec", "duration_seconds", "seconds", "length"):
        x = _pricing_number(ex.get(key))
        if x and x > 0:
            return x
    desc = str(ex.get("description") or ex.get("label") or ex.get("name") or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b|sec(?:ond)?s?)", desc, flags=re.IGNORECASE)
    if m:
        x = _pricing_number(m.group(1))
        return x if x and x > 0 else 0.0
    m = re.search(r"(?:时长|duration|dur)\D*(\d+(?:\.\d+)?)", desc, flags=re.IGNORECASE)
    if m:
        x = _pricing_number(m.group(1))
        return x if x and x > 0 else 0.0
    # Matrix examples are often written as "720p + 4"; the value after "+"
    # is the duration, while the resolution number must not be parsed as time.
    if "+" in desc:
        m = re.search(r"\+\s*(\d+(?:\.\d+)?)\b", desc)
        if m:
            x = _pricing_number(m.group(1))
            return x if x and x > 0 else 0.0
    return 0.0


def _normalize_pricing_token(value: Any) -> str:
    s = str(value or "").strip().lower()
    s = s.replace("ｋ", "k").replace("Ｋ", "k")
    return re.sub(r"\s+", "", s)


def _param_token(params: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key not in params:
            continue
        token = _normalize_pricing_token(params.get(key))
        if token and token != "auto":
            return token
    return ""


def _param_tokens(params: Dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for key in keys:
        if key not in params:
            continue
        token = _normalize_pricing_token(params.get(key))
        if token and token != "auto" and token not in out:
            out.append(token)
    return out


def _truthy_param(params: Dict[str, Any], keys: tuple[str, ...]) -> Optional[bool]:
    for key in keys:
        if key not in params:
            continue
        v = params.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"1", "true", "yes", "y", "on"}:
                return True
            if s in {"0", "false", "no", "n", "off", ""}:
                return False
        return bool(v)
    return None


def _example_rows(pricing: dict) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    examples = pricing.get("examples") or pricing.get("duration_prices") or pricing.get("prices") or []
    if not isinstance(examples, list):
        return out
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        price = _pricing_base_amount(ex)
        if price is None:
            continue
        desc = str(ex.get("description") or ex.get("label") or ex.get("name") or "")
        out.append((_duration_from_example(ex), price, desc))
    return out


def _example_prices(pricing: dict) -> list[tuple[float, float]]:
    return [(duration, price) for duration, price, _desc in _example_rows(pricing)]


def _description_audio_state(desc: str) -> str:
    low = desc.lower()
    if any(x in desc for x in ("语音控制", "语音")) or "voice" in low:
        return "voice"
    if any(x in desc for x in ("无音频", "无音效")) or "no audio" in low or "without audio" in low:
        return "no_audio"
    if any(x in desc for x in ("开启音频", "含音频", "音频")) or "+audio" in low or "audio" in low:
        return "audio"
    return "unknown"


def _has_voice_control(params: Dict[str, Any]) -> bool:
    for key in ("voice_ids", "voice_id", "voices"):
        if key not in params:
            continue
        v = params.get(key)
        if isinstance(v, list):
            return len(v) > 0
        if isinstance(v, str):
            return bool(v.strip())
        if v:
            return True
    return False


def _filter_examples_by_params(
    rows: list[tuple[float, float, str]], params: Dict[str, Any]
) -> list[tuple[float, float, str]]:
    candidates = rows
    model_aliases: Dict[str, str] = {
        "seedance2.0fastdirect": "fast",
        "seedance2.0direct": "标准",
        "seedance2.0fastvision": "fastvip",
        "seedance2.0vision": "标准vip",
        "seedance20fastdirect": "fast",
        "seedance20direct": "标准",
        "seedance20fastvision": "fastvip",
        "seedance20vision": "标准vip",
        "seedance20fast": "fast",
        "seedance20fastvip": "fastvip",
        "seedance20vip": "标准vip",
        "seedance20": "标准",
    }

    model_tokens = _param_tokens(params, ("model", "model_type"))
    for token in model_tokens:
        token = model_aliases.get(token.replace("_", "").replace("-", "").replace(".", ""), token)
        if token in {"标准", "fast"}:
            matched = [
                row for row in candidates
                if token in _normalize_pricing_token(row[2])
                and "vip" not in _normalize_pricing_token(row[2])
            ]
            if matched:
                candidates = matched
                continue
        matched = [row for row in candidates if token in _normalize_pricing_token(row[2])]
        if matched:
            candidates = matched

    for keys in (
        ("resolution", "video_resolution", "output_resolution", "size"),
        ("quality", "image_quality", "resolution_quality"),
        ("mode", "variant", "version", "tier"),
    ):
        token = _param_token(params, keys)
        if not token:
            continue
        matched = [row for row in candidates if token in _normalize_pricing_token(row[2])]
        if matched:
            candidates = matched

    if _has_voice_control(params):
        matched = [
            row for row in candidates
            if _description_audio_state(row[2]) == "voice"
        ]
        if matched:
            candidates = matched
    else:
        audio = _truthy_param(params, ("generate_audio", "enable_audio", "with_audio"))
        if audio is True:
            matched = [
                row for row in candidates
                if _description_audio_state(row[2]) in {"audio", "voice"}
            ]
            if matched:
                candidates = matched
        elif audio is False:
            matched = [
                row for row in candidates
                if _description_audio_state(row[2]) in {"no_audio", "unknown"}
            ]
            if matched:
                candidates = matched

    web_search = _truthy_param(params, ("enable_web_search", "web_search", "use_web_search"))
    if web_search is True:
        matched = [
            row for row in candidates
            if "web" in row[2].lower() or "search" in row[2].lower() or "搜索" in row[2]
        ]
        if matched:
            candidates = matched
    elif web_search is False:
        matched = [
            row for row in candidates
            if "web" not in row[2].lower() and "search" not in row[2].lower() and "搜索" not in row[2]
        ]
        if matched:
            candidates = matched

    return candidates


def _price_from_examples_by_params(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    rows = _filter_examples_by_params(_example_rows(pricing), params)
    if not rows:
        return None
    d = _duration_seconds_from_params(params)
    if d > 0:
        with_duration = [row for row in rows if row[0] > 0]
        if with_duration:
            durations = sorted({row[0] for row in with_duration})
            chosen = next((dur for dur in durations if d <= dur + 1e-9), durations[-1])
            bucket = [row for row in with_duration if abs(row[0] - chosen) < 1e-9]
            if bucket:
                return max(row[1] for row in bucket)
    return max(row[1] for row in rows)


def _linear_price_from_examples(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    d = _duration_seconds_from_params(params)
    if d <= 0:
        return None
    rows = _filter_examples_by_params(_example_rows(pricing), params)
    with_duration = [row for row in rows if row[0] > 0 and row[1] > 0]
    if not with_duration:
        return None
    exact = [row[1] for row in with_duration if abs(row[0] - d) < 1e-9]
    if exact:
        return max(exact)
    rates = [row[1] / row[0] for row in with_duration if row[0] > 0]
    if not rates:
        return None
    return math.ceil(d * max(rates))


def _price_from_duration_examples(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    return _price_from_examples_by_params(pricing, params)


def _matrix_price_candidates(node: Any) -> list[float]:
    price = _pricing_number(node)
    if price is not None:
        return [price]
    if isinstance(node, dict):
        direct = _pricing_base_amount(node)
        out = [direct] if direct is not None else []
        for v in node.values():
            out.extend(_matrix_price_candidates(v))
        return out
    if isinstance(node, list):
        out: list[float] = []
        for v in node:
            out.extend(_matrix_price_candidates(v))
        return out
    return []


def _price_from_quality_size_matrix(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    matrix = pricing.get("quality_size_matrix") or pricing.get("matrix") or pricing.get("size_quality_matrix")
    if not isinstance(matrix, dict):
        return None
    qualities = [
        str(params.get(k) or "").strip()
        for k in ("quality", "image_quality", "resolution_quality", "mode")
        if str(params.get(k) or "").strip()
    ]
    sizes = [
        str(params.get(k) or "").strip()
        for k in ("size", "image_size", "resolution", "aspect_ratio", "ratio")
        if str(params.get(k) or "").strip()
    ]
    for q in qualities:
        sub = matrix.get(q)
        if isinstance(sub, dict):
            for s in sizes:
                values = _matrix_price_candidates(sub.get(s))
                if values:
                    return max(values)
            values = _matrix_price_candidates(sub)
            if values:
                return max(values)
    for s in sizes:
        sub = matrix.get(s)
        values = _matrix_price_candidates(sub)
        if values:
            return max(values)
    values = _matrix_price_candidates(matrix)
    return max(values) if values else None


def _price_from_price_factors_quality_matrix(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    factors = pricing.get("price_factors")
    if not isinstance(factors, list):
        return None
    resolution = _param_token(params, ("resolution", "size", "image_size"))
    quality = _param_token(params, ("quality", "image_quality", "resolution_quality")) or "high"
    parsed: list[tuple[str, dict[str, float], list[float]]] = []
    for item in factors:
        text = str(item or "").strip()
        if ":" not in text:
            continue
        key, rest = text.split(":", 1)
        res_key = _normalize_pricing_token(key)
        nums = [
            float(x)
            for x in re.findall(r"\d+(?:\.\d+)?", rest.split("（", 1)[0].split("(", 1)[0])
        ]
        if not nums:
            continue
        labels: list[str] = []
        m = re.search(r"[（(]([^）)]+)[）)]", rest)
        if m:
            labels = [
                _normalize_pricing_token(x)
                for x in re.split(r"[/,，、\s]+", m.group(1))
                if _normalize_pricing_token(x)
            ]
        mapped: dict[str, float] = {}
        for idx, label in enumerate(labels):
            if idx < len(nums):
                mapped[label] = nums[idx]
        if not mapped and len(nums) == 3:
            mapped = {"low": nums[0], "medium": nums[1], "high": nums[2]}
        parsed.append((res_key, mapped, nums))
    if not parsed:
        return None

    rows = [row for row in parsed if resolution and row[0] == resolution]
    if not rows and resolution:
        rows = [row for row in parsed if resolution in row[0] or row[0] in resolution]
    if not rows:
        rows = parsed[:1]
    prices: list[float] = []
    for _res, mapped, nums in rows:
        if quality in mapped:
            prices.append(mapped[quality])
        elif mapped:
            prices.append(max(mapped.values()))
        else:
            prices.append(max(nums))
    return max(prices) if prices else None


def _price_from_table_matrix(pricing: dict, params: Dict[str, Any]) -> Optional[float]:
    table = pricing.get("price_matrix")
    if not isinstance(table, dict):
        return None
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or len(columns) < 2:
        return None
    resolution = _param_token(params, ("resolution", "size", "output_resolution"))
    ratio = _param_token(params, ("image_size", "aspect_ratio", "ratio"))
    if not ratio:
        ratio = "4:3"
    col_tokens = [_normalize_pricing_token(c) for c in columns]
    col_idx = None
    for idx, token in enumerate(col_tokens[1:], start=1):
        if ratio == token:
            col_idx = idx
            break

    row_matches: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        row_token = _normalize_pricing_token(row[0])
        if resolution and row_token == resolution:
            row_matches.append(row)
    if not row_matches and resolution:
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            row_token = _normalize_pricing_token(row[0])
            if resolution in row_token or row_token in resolution:
                row_matches.append(row)
    if not row_matches:
        row_matches = [row for row in rows if isinstance(row, list) and len(row) > 1][:1]

    values: list[float] = []
    for row in row_matches:
        if col_idx is not None and col_idx < len(row):
            x = _pricing_number(row[col_idx])
            if x is not None:
                values.append(x)
        if not values:
            for cell in row[1:]:
                x = _pricing_number(cell)
                if x is not None:
                    values.append(x)
    return max(values) if values else None


def _addon_unit_from_examples(pricing: dict, base: float) -> float:
    deltas = [
        price - base
        for _duration, price, _desc in _example_rows(pricing)
        if price and price > base
    ]
    positives = sorted(x for x in deltas if x > 0)
    return positives[0] if positives else 0.0


def _has_extra_view_input(params: Dict[str, Any]) -> bool:
    view_keys = (
        "back_image_url",
        "left_image_url",
        "right_image_url",
        "top_image_url",
        "bottom_image_url",
        "left_front_image_url",
        "right_front_image_url",
    )
    if any(params.get(key) for key in view_keys):
        return True
    images = params.get("image_urls") or params.get("images")
    if isinstance(images, list) and len([x for x in images if x]) > 1:
        return True
    return False


_MODEL_SHORT_TO_FULL: Dict[str, str] = {
    "flux-2": "fal-ai/flux-2/flash",
    "flux-2/flash": "fal-ai/flux-2/flash",
    "seedream": "fal-ai/bytedance/seedream/v4.5/text-to-image",
    "seedream-4.5": "fal-ai/bytedance/seedream/v4.5/text-to-image",
    "seedream-5": "fal-ai/bytedance/seedream/v5/lite/text-to-image",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
    "nano-banana-2": "fal-ai/nano-banana-2",
    "sora-2": "fal-ai/sora-2/text-to-video",
    "gemini": "kapon/gemini-3-pro-image-preview",
    "qwen-image-edit": "fal-ai/qwen-image-edit-2511-multiple-angles",
}


_LEGACY_PREFIX_REWRITE: Tuple[Tuple[str, str], ...] = (
    ("sora2pub/", "fal-ai/sora-2/"),
    ("sprcra/sora-2-vip/", "fal-ai/sora-2/vip/"),
)


def _resolve_model_alias(mid: str) -> str:
    """Map short/friendly model names to full SuTui model IDs for pricing lookups."""
    if mid in _MODEL_SHORT_TO_FULL:
        return _MODEL_SHORT_TO_FULL[mid]
    low = mid.lower()
    for short, full in _MODEL_SHORT_TO_FULL.items():
        if low == short.lower():
            return full
    for old_prefix, new_prefix in _LEGACY_PREFIX_REWRITE:
        if low.startswith(old_prefix):
            return new_prefix + mid[len(old_prefix):]
    return mid


def fetch_model_docs_data(model_id: str) -> Optional[dict]:
    """GET /api/v3/models/{model_id}/docs 返回的 data 对象（含 pricing）。"""
    if not model_id or not str(model_id).strip():
        return None
    mid = _resolve_model_alias(str(model_id).strip())
    now = time.time()
    if mid in _DOCS_CACHE:
        ts, data = _DOCS_CACHE[mid]
        if now - ts < _CACHE_TTL_SEC and data is not None:
            return data
    safe = quote(mid, safe="")
    url = f"{_api_base()}/api/v3/models/{safe}/docs"
    try:
        r = httpx.get(url, params={"lang": "zh"}, timeout=20.0)
        if r.status_code == 404:
            _DOCS_CACHE[mid] = (now, None)
            return None
        r.raise_for_status()
        j = r.json()
        if not isinstance(j, dict) or int(j.get("code", 0)) != 200:
            _DOCS_CACHE[mid] = (now, None)
            return None
        data = j.get("data")
        if not isinstance(data, dict):
            _DOCS_CACHE[mid] = (now, None)
            return None
        _DOCS_CACHE[mid] = (now, data)
        return data
    except Exception:
        _DOCS_CACHE[mid] = (now, None)
        return None


def _sutui_auth_headers() -> Dict[str, str]:
    token = (
        getattr(settings, "sutui_server_token", None)
        or os.environ.get("SUTUI_SERVER_TOKEN")
        or os.environ.get("APIZ_API_KEY")
        or os.environ.get("XSKILL_API_KEY")
        or ""
    ).strip()
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _mcp_models_urls() -> list[str]:
    base = _api_base().rstrip("/")
    urls = [f"{base}/api/v3/mcp/models?lang=zh-CN"]
    if "api.apiz.ai" not in base:
        urls.append("https://api.apiz.ai/api/v3/mcp/models?lang=zh-CN")
    return urls


def _fetch_mcp_models_pricing_map() -> Dict[str, dict]:
    now = time.time()
    cache_key = "models"
    ent = _MCP_MODELS_PRICING_CACHE.get(cache_key)
    if ent and now - ent[0] < _CACHE_TTL_SEC:
        return ent[1]
    headers = _sutui_auth_headers()
    pricing_map: Dict[str, dict] = {}
    for url in _mcp_models_urls():
        try:
            r = httpx.get(url, headers=headers, timeout=20.0)
            if r.status_code >= 400:
                continue
            j = r.json()
            models = j.get("data", {}).get("models", []) if isinstance(j, dict) else []
            if not isinstance(models, list):
                continue
            for m in models:
                if not isinstance(m, dict):
                    continue
                mid = str(m.get("id") or "").strip()
                p = m.get("pricing")
                if mid and isinstance(p, dict):
                    pricing_map[mid] = p
            if pricing_map:
                break
        except Exception:
            continue
    _MCP_MODELS_PRICING_CACHE[cache_key] = (now, pricing_map)
    return pricing_map


def fetch_mcp_models_pricing(model_id: str) -> Optional[dict]:
    mid = _resolve_model_alias((model_id or "").strip())
    if not mid:
        return None
    pricing = _fetch_mcp_models_pricing_map().get(mid)
    return pricing if isinstance(pricing, dict) else None


def fetch_model_pricing(model_id: str) -> Optional[dict]:
    data = fetch_model_docs_data(model_id)
    if data:
        p = data.get("pricing")
        if isinstance(p, dict):
            out = dict(p)
            schema = data.get("params_schema") if isinstance(data, dict) else None
            props = schema.get("properties") if isinstance(schema, dict) else None
            if isinstance(props, dict):
                defaults = {
                    str(key): spec.get("default")
                    for key, spec in props.items()
                    if isinstance(spec, dict) and "default" in spec and not spec.get("visible_if")
                }
                if defaults:
                    out["_param_defaults"] = defaults
            duration_schema = props.get("duration") if isinstance(props, dict) else None
            if isinstance(duration_schema, dict):
                default_duration = _pricing_number(duration_schema.get("default"))
                if not default_duration:
                    enum_values = duration_schema.get("enum")
                    if isinstance(enum_values, list):
                        candidates = [_pricing_number(v) for v in enum_values]
                        candidates = [v for v in candidates if v and v > 0]
                        if candidates:
                            default_duration = min(candidates)
                if default_duration and default_duration > 0:
                    out["_default_duration_seconds"] = default_duration
            return out
    return fetch_mcp_models_pricing(model_id)


def pricing_is_free_fixed(pricing: dict) -> bool:
    if not pricing:
        return False
    price_type = (pricing.get("price_type") or "").strip().lower()
    amount = _pricing_base_amount(pricing)
    return price_type == "fixed" and amount == 0


def estimate_credits_from_pricing(pricing: dict, params: Optional[dict]) -> int:
    """根据 pricing + 请求参数估算预扣积分（保守估计，避免低估）。"""
    params = _pricing_params_with_defaults(pricing, params or {})
    if not pricing:
        return 0
    price_type = (pricing.get("price_type") or "").strip().lower()
    base_amount = _pricing_base_amount(pricing)
    base = int(base_amount or 0)
    if price_type == "fixed" and base_amount == 0:
        return 0

    # per_second / dynamic_per_second: base_price 可能为 None，用 per_second 字段
    if price_type in ("per_second", "dynamic_per_second", "per_second_actual_duration"):
        if price_type == "dynamic_per_second":
            ex_price = _linear_price_from_examples(pricing, params) or _price_from_examples_by_params(pricing, params)
            if ex_price is not None:
                return _quantize_credits(ex_price)
        try:
            rate = float(pricing.get("per_second") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        if rate <= 0 and base > 0:
            default_duration = _pricing_number(pricing.get("_default_duration_seconds"))
            if price_type == "dynamic_per_second" and default_duration and default_duration > 0:
                rate = float(base) / float(default_duration)
            else:
                rate = float(base)
        if rate <= 0:
            return 0
        d = _duration_seconds_from_params(params)
        if d <= 0:
            d = 5.0
        return _quantize_credits(math.ceil(d * rate))

    if price_type == "per_minute":
        rate = _pricing_number(pricing.get("per_minute"))
        if rate is None or rate <= 0:
            rate = base_amount or 0
        if rate <= 0:
            return 0
        d = _duration_seconds_from_params(params)
        minutes = max(1, math.ceil((d if d > 0 else 60.0) / 60.0))
        return _quantize_credits(minutes * rate)

    # duration_map: base_price 是最短时长的价格，按时长比例估算
    if price_type == "duration_map":
        ex_price = _price_from_examples_by_params(pricing, params)
        if ex_price is not None:
            return _quantize_credits(ex_price)
        return base

    # token_postcharge: 后付费，预扣用 examples 中最低价作保守估计
    if price_type == "token_postcharge":
        examples = pricing.get("examples") or []
        if examples:
            prices = [int(ex.get("price", 0)) for ex in examples if ex.get("price")]
            if prices:
                return min(prices)
        return max(base, 100)

    if price_type == "quantity_based":
        if base <= 0:
            return 0
        n = params.get("num_images") or params.get("n") or params.get("batch_size") or 1
        try:
            n_int = int(n)
        except (TypeError, ValueError):
            n_int = 1
        if n_int < 1:
            n_int = 1
        return base * n_int

    if price_type in ("duration_based", "duration_price"):
        if price_type == "duration_price":
            ex_price = _linear_price_from_examples(pricing, params) or _price_from_examples_by_params(pricing, params)
            if ex_price is not None:
                return _quantize_credits(ex_price)
            rate = _pricing_number(pricing.get("per_second"))
            if rate is not None and rate > 0:
                d = _duration_seconds_from_params(params)
                if d <= 0:
                    d = 5.0
                return _quantize_credits(float(math.ceil(float(d) * rate)))
        if base <= 0:
            return 0
        d = _duration_seconds_from_params(params)
        if d <= 0:
            d = 5.0
        return _quantize_credits(float(math.ceil(float(d) * float(base))))

    if price_type == "fixed":
        return base

    if price_type == "quality_size_matrix":
        price = _price_from_quality_size_matrix(pricing, params)
        if price is None:
            price = _price_from_price_factors_quality_matrix(pricing, params)
        if price is None:
            price = base_amount
        if price is None:
            return 0
        return _quantize_credits(price * _num_outputs_from_params(params))

    if price_type == "matrix":
        matrix_price = _price_from_quality_size_matrix(pricing, params)
        if matrix_price is None:
            matrix_price = _price_from_table_matrix(pricing, params)
        if matrix_price is not None:
            return _quantize_credits(matrix_price * _num_outputs_from_params(params))
        ex_price = _linear_price_from_examples(pricing, params) or _price_from_examples_by_params(pricing, params)
        if ex_price is not None:
            return _quantize_credits(ex_price)
        if base <= 0:
            return 0
        d = _duration_seconds_from_params(params)
        if d > 0:
            return _quantize_credits(float(math.ceil(d * float(base))))
        return base

    if price_type == "resolution_ratio_matrix":
        price = _price_from_table_matrix(pricing, params)
        if price is None:
            price = base_amount
        if price is None:
            return 0
        total = price * _num_outputs_from_params(params)
        if _truthy_param(params, ("enable_prompt_expansion", "prompt_expansion")) is True:
            total += 1
        return _quantize_credits(total)

    if price_type == "fixed_plus_addons":
        if base_amount is None:
            return 0
        addon = _addon_unit_from_examples(pricing, float(base_amount))
        total = float(base_amount)
        if _truthy_param(params, ("enable_pbr", "pbr")) is True:
            total += addon
        if _has_extra_view_input(params):
            total += addon
        face_count = _pricing_number(params.get("face_count"))
        if face_count is not None and abs(face_count - 500000.0) > 1e-9:
            total += addon
        return _quantize_credits(total)

    if price_type == "token_based":
        if base <= 0:
            return 0
        pt = int(params.get("prompt_tokens", 0) or 0)
        ct = int(params.get("completion_tokens", 0) or 0)
        total = pt + ct
        if total > 0:
            units = math.ceil(total / 1000.0)
            raw = units * float(base)
            return _quantize_credits(raw)
        return _quantize_credits(float(base))

    if price_type in ("audio_duration_based", "audio_duration"):
        if base <= 0:
            return 0
        d = _duration_seconds_from_params(params)
        if d <= 0:
            return _quantize_credits(float(base))
        return _quantize_credits(float(math.ceil(d * float(base))))

    if price_type == "char_based":
        if base <= 0:
            return 0
        char_count = 0
        prompt = params.get("prompt") or params.get("text") or ""
        if isinstance(prompt, str):
            char_count = len(prompt)
        if char_count <= 0:
            char_count = 100
        units = math.ceil(char_count / 1000.0)
        return _quantize_credits(float(units * base))

    if price_type in ("resolution_quantity", "size_based"):
        ex_price = _price_from_examples_by_params(pricing, params)
        if ex_price is not None:
            return _quantize_credits(ex_price * _num_outputs_from_params(params))
        if base <= 0:
            return 0
        return base * _num_outputs_from_params(params)

    if base <= 0:
        return 0
    return _quantize_credits(float(base))


def credits_from_chat_usage_when_no_docs_pricing(
    usage: Optional[dict], model_id: Optional[str] = None
) -> Decimal:
    """
    docs 无定价或定价无法用于本次扣费时：按上游 chat/completions 返回的 usage 事后折算积分。
    model_id 用于按模型费率（内置表或 SUTUI_CHAT_USAGE_CREDITS_PER_1K_BY_MODEL_JSON）；预检阶段仍可不拦截。
    """
    rate = _usage_credits_per_1k_for_model(model_id or "")
    if rate <= 0:
        return Decimal(0)
    if not usage or not isinstance(usage, dict):
        return Decimal(0)
    total = 0
    tt = usage.get("total_tokens")
    if tt is not None:
        try:
            total = int(tt)
        except (TypeError, ValueError):
            total = 0
    if total <= 0:
        try:
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            total = pt + ct
        except (TypeError, ValueError):
            total = 0
    if total <= 0:
        return Decimal(0)
    units = math.ceil(total / 1000.0)
    return quantize_credits(units * rate)


def estimate_pre_deduct_credits(model_id: str, params: Optional[dict]) -> Tuple[int, Optional[str]]:
    """
    返回 (预扣积分, 错误文案)。错误非空表示不允许调用（无定价或无法估算）。
    """
    pricing = fetch_model_pricing(model_id)
    if not pricing:
        return 0, "该模型无法在速推获取定价（docs 无 pricing 或未开放），请联系管理员配置。"
    est = estimate_credits_from_pricing(pricing, params)
    if est <= 0:
        if pricing_is_free_fixed(pricing):
            return 0, None
        return 0, "该模型定价无效，请联系管理员配置。"
    return est, None


def _dict_looks_like_account_balance(d: dict) -> bool:
    """含余额语义时，避免把字段名 credits 误当作「本次消耗」（与 mcp/http_server 一致）。"""
    kl = {str(k).lower() for k in d}
    return bool(kl & {"balance", "remaining", "remaining_credits", "total_balance", "available", "points"})


def upstream_numeric_credits_to_decimal(v: Any) -> Decimal:
    """速推常在 x_billing 等字段返回小数积分（如 0.9558）；统一量化为 4 位小数。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return Decimal(0)
    if x <= 0:
        return Decimal(0)
    return quantize_credits(x)


def _coerce_positive_credit_number(v: Any) -> Optional[Decimal]:
    """上游可能返回 float / int / 数字字符串；仅接受正数。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        return upstream_numeric_credits_to_decimal(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            x = float(s)
        except ValueError:
            return None
        if x <= 0:
            return None
        return upstream_numeric_credits_to_decimal(x)
    return None


def extract_upstream_billing_snapshot(data: Optional[dict]) -> dict[str, Any]:
    """
    从 chat/completions 等响应中抽出与计费相关的字段，便于日志对照（含 x_billing、usage 等）。
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    priority = ("x_billing", "X-Billing", "billing", "usage", "service_tier")
    for k in priority:
        if k in data:
            out[k] = data[k]
    for k, v in data.items():
        if k in out:
            continue
        lk = str(k).lower()
        if any(
            x in lk
            for x in (
                "credit",
                "price",
                "cost",
                "bill",
                "charge",
                "usage",
                "x_billing",
                "sutui",
            )
        ):
            out[k] = v
    return out


_SKIP_UPSTREAM_CREDIT_RECURSE_KEYS = frozenset(
    {
        "choices",
        "messages",
        # 已在下面优先分支单独处理，避免在总遍历里与其它字段取 max 混算
        "x_billing",
        "x-billing",
    }
)


def extract_upstream_reported_credits(obj: Any, _depth: int = 0) -> Decimal:
    """
    从速推 chat/completions 或任务类完整 JSON 中解析「本次消耗积分」（4 位小数）。
    与 mcp/http_server 计费解析字段集合对齐；优先于 docs 定价推算。

    注意：不得遍历 OpenAI 样式的 ``choices``：助手/工具正文中常含 JSON，字段名 cost/price
    可能是套餐价、内部参数等，若与全树 max 合并会把「本次消耗」抬到荒谬整数（如 25）。
    速推官方账单一般以顶层 ``x_billing``（或 ``X-Billing``）为准，优先只信该子树。
    """
    if _depth > 42:
        return Decimal(0)
    if isinstance(obj, dict):
        xb = obj.get("x_billing")
        if xb is None:
            xb = obj.get("X-Billing")
        if xb is not None:
            if isinstance(xb, str):
                xs = xb.strip()
                if xs.startswith("{"):
                    try:
                        xb = json.loads(xs)
                    except Exception:
                        xb = None
                else:
                    pnum = _coerce_positive_credit_number(xs)
                    if pnum is not None and pnum > 0:
                        return pnum
                    xb = None
            if xb is not None:
                sub = extract_upstream_reported_credits(xb, _depth + 1)
                if sub > 0:
                    return sub

    best = Decimal(0)
    if isinstance(obj, dict):
        balance_shape = _dict_looks_like_account_balance(obj)
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _SKIP_UPSTREAM_CREDIT_RECURSE_KEYS:
                continue
            if lk in (
                "credits_used",
                "credits_charged",
                "credit_cost",
                "consumed_credits",
                "usage_credits",
                "cost",
                "price",
            ):
                parsed = _coerce_positive_credit_number(v)
                if parsed is not None:
                    best = max(best, parsed)
            elif lk == "credits" and not balance_shape:
                parsed = _coerce_positive_credit_number(v)
                if parsed is not None:
                    best = max(best, parsed)
            elif isinstance(v, (dict, list)):
                best = max(best, extract_upstream_reported_credits(v, _depth + 1))
            elif isinstance(v, str):
                s = v.strip()
                if s.startswith("{"):
                    try:
                        best = max(best, extract_upstream_reported_credits(json.loads(s), _depth + 1))
                    except Exception:
                        pass
    elif isinstance(obj, list):
        for it in obj:
            best = max(best, extract_upstream_reported_credits(it, _depth + 1))
    return best


# --------------- 批量拉取所有多媒体模型列表 + 预填充定价缓存 ---------------

import logging as _logging

_models_log = _logging.getLogger(__name__ + ".models_catalog")

_XSKILL_MODELS_URL = "https://api.apiz.ai/api/v3/mcp/models?lang=zh-CN"
_ALL_MODELS_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_ALL_MODELS_CACHE_TTL = 3600


def fetch_all_media_models() -> list:
    """拉取 xSkill 全部多媒体模型列表，同时预填充 _DOCS_CACHE 供 billing 使用。

    返回的每个 model dict 含 id/name/category/task_type/description/isHot/isNew/pricing（原始）。
    """
    now = time.time()
    if _ALL_MODELS_CACHE["data"] is not None and now - _ALL_MODELS_CACHE["ts"] < _ALL_MODELS_CACHE_TTL:
        return _ALL_MODELS_CACHE["data"]

    try:
        r = httpx.get(_XSKILL_MODELS_URL, timeout=20.0)
        r.raise_for_status()
        models = r.json().get("data", {}).get("models", [])
    except Exception as exc:
        _models_log.warning("拉取模型列表失败: %s", exc)
        cached = _ALL_MODELS_CACHE.get("data")
        return cached if cached else []

    media = [m for m in models if m.get("category") in ("video", "image", "audio")]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    pricing_map: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_model_pricing, m["id"]): m["id"] for m in media}
        for fut in as_completed(futures):
            mid = futures[fut]
            try:
                pricing_map[mid] = fut.result()
            except Exception:
                pricing_map[mid] = None

    results = []
    for m in media:
        mid = m["id"]
        results.append({
            "id": mid,
            "name": m.get("name", ""),
            "category": m.get("category", ""),
            "task_type": m.get("task_type", ""),
            "description": m.get("description", ""),
            "isHot": m.get("isHot", False),
            "isNew": m.get("isNew", False),
            "pricing": pricing_map.get(mid),
        })

    _ALL_MODELS_CACHE["data"] = results
    _ALL_MODELS_CACHE["ts"] = time.time()
    _models_log.info("已缓存 %d 个多媒体模型定价", len(results))
    return results
