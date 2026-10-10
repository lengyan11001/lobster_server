"""设备语音回包（TTS）：按需把 AI 回复合成语音（2026-10-09）。

通道：速推/apiz 的 minimax t2a（HTTP，不引 SDK）
  POST {base}/api/v3/minimax/t2a  {"text","voice_id","model","speed"}  ->  audio_url
  POST {base}/api/v3/minimax/voices?status=active  {}                  ->  音色列表

默认（用户已确认）：
- 模型 speech-2.8-turbo（20 积分/1000 字符，起步 1 积分）
- 官方公共音色（可用环境变量 DEVICE_TTS_VOICE_ID 覆盖）
- 单条最多合成前 300 字符（DEVICE_TTS_MAX_CHARS）
- 从"绑定该设备的账号"扣积分，走 credit_ledger
"""
from __future__ import annotations

import math
import os
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .credit_ledger import append_credit_ledger
from .credits_amount import credits_json_float, quantize_credits, user_balance_decimal

_DEFAULT_MODEL = "speech-2.8-turbo"
_DEFAULT_CREDITS_PER_1K = Decimal("20")
_DEFAULT_MAX_CHARS = 300
_voice_cache: Dict[str, str] = {}

# 逐句 TTS（设备流式回复）：短于这个字数的句子不单独合成
SENTENCE_MIN_CHARS = 20
SENTENCE_END_CHARS = "。！？!?…\n；;"


def _base() -> str:
    return str(
        os.environ.get("CANVAS_APIZ_BASE_URL")
        or os.environ.get("APIZ_BASE_URL")
        or "https://api.apiz.ai"
    ).strip().rstrip("/")


def model_name() -> str:
    return (os.environ.get("DEVICE_TTS_MODEL") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def max_chars() -> int:
    try:
        return max(1, int(os.environ.get("DEVICE_TTS_MAX_CHARS") or _DEFAULT_MAX_CHARS))
    except Exception:  # noqa: BLE001
        return _DEFAULT_MAX_CHARS


def credits_per_1k() -> Decimal:
    raw = (os.environ.get("DEVICE_TTS_CREDITS_PER_1K") or "").strip()
    if raw:
        try:
            value = Decimal(raw)
            if value >= 0:
                return value
        except Exception:  # noqa: BLE001
            pass
    return _DEFAULT_CREDITS_PER_1K


def price_for(text: str) -> Decimal:
    chars = len(str(text or "").strip())
    if chars <= 0:
        return Decimal("0")
    raw = (Decimal(chars) / Decimal(1000)) * credits_per_1k()
    value = quantize_credits(raw)
    minimum = quantize_credits(Decimal("1"))  # 起步 1 积分
    return value if value and value > minimum else minimum


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": "Bearer %s" % token, "Accept": "application/json"}


def _pick_from_payload(data: Any) -> str:
    """从各种可能的返回结构里取 audio_url。"""
    if isinstance(data, dict):
        for key in ("audio_url", "url", "file_url", "output_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for key in ("data", "output", "result"):
            found = _pick_from_payload(data.get(key))
            if found:
                return found
    return ""


def pick_voice_id(token: str) -> str:
    env_voice = (os.environ.get("DEVICE_TTS_VOICE_ID") or "").strip()
    if env_voice:
        return env_voice
    cached = _voice_cache.get("default")
    if cached:
        return cached
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                _base() + "/api/v3/minimax/voices",
                params={"status": "active"},
                json={},
                headers={**_headers(token), "Content-Type": "application/json"},
            )
            payload = resp.json() if resp.status_code < 400 else {}
    except Exception:  # noqa: BLE001
        payload = {}
    candidates: list[Any] = []
    if isinstance(payload, dict):
        node = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        for key in ("public_voices", "user_voices", "voices", "items"):
            value = node.get(key) if isinstance(node, dict) else None
            if isinstance(value, list):
                candidates.extend(value)
        if isinstance(payload.get("public_voices"), list):
            candidates.extend(payload["public_voices"])
        if isinstance(payload.get("user_voices"), list):
            candidates.extend(payload["user_voices"])
    for item in candidates:
        if isinstance(item, dict):
            vid = str(item.get("voice_id") or item.get("id") or "").strip()
            if vid:
                _voice_cache["default"] = vid
                return vid
        elif isinstance(item, str) and item.strip():
            _voice_cache["default"] = item.strip()
            return item.strip()
    return ""


def synthesize(text: str, *, token: str, voice_id: str = "") -> str:
    """合成并返回上游音频地址（失败抛 HTTPException 502）。"""
    clean = str(text or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="TTS 文本为空")
    voice = voice_id or pick_voice_id(token)
    if not voice:
        raise HTTPException(status_code=502, detail="TTS 音色不可用（请配置 DEVICE_TTS_VOICE_ID）")
    body = {"text": clean[: max_chars()], "voice_id": voice, "model": model_name(), "speed": 1.0}
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                _base() + "/api/v3/minimax/t2a",
                json=body,
                headers={**_headers(token), "Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="TTS 上游不可达：%s" % str(exc)[:160]) from exc
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="TTS 上游失败 HTTP %s：%s" % (resp.status_code, resp.text[:200]),
        )
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="TTS 返回不是 JSON") from exc
    url = _pick_from_payload(payload)
    if not url:
        raise HTTPException(status_code=502, detail="TTS 返回里没有音频地址：%s" % str(payload)[:200])
    return url


def ensure_balance(db: Session, user: Any, credits: Decimal) -> None:
    need = quantize_credits(credits or 0)
    if need <= 0:
        return
    balance = user_balance_decimal(user)
    if balance < need:
        raise HTTPException(
            status_code=402,
            detail="算力不足：这次语音合成需要 %s 积分，当前余额 %s"
            % (credits_json_float(need), credits_json_float(balance)),
        )


def charge(db: Session, user: Any, credits: Decimal, *, ref_id: str, description: str) -> Decimal:
    amount = quantize_credits(credits or 0)
    if amount <= 0:
        return Decimal("0")
    balance = user_balance_decimal(user)
    if balance < amount:
        raise HTTPException(
            status_code=402,
            detail="算力不足：这次语音合成需要 %s 积分，当前余额 %s"
            % (credits_json_float(amount), credits_json_float(balance)),
        )
    user.credits = quantize_credits(balance - amount)
    append_credit_ledger(
        db,
        int(getattr(user, "id", 0) or 0),
        -amount,
        "deduct",
        quantize_credits(user.credits),
        description=description or "设备语音回包(TTS)",
        ref_type="device_tts",
        ref_id=str(ref_id),
    )
    return amount


# ── 逐句朗读：把回复切成「够长就切」的片段（设备流式回复用） ────────────
def sentence_min_chars() -> int:
    """低于这个字数的句子不单独合成语音（可用 DEVICE_TTS_SENTENCE_MIN_CHARS 覆盖）。"""
    try:
        return max(1, int(os.environ.get("DEVICE_TTS_SENTENCE_MIN_CHARS") or SENTENCE_MIN_CHARS))
    except Exception:  # noqa: BLE001
        return SENTENCE_MIN_CHARS


def split_sentences(text: str, *, min_chars: int = 0) -> list:
    """把一条回复切成适合逐句朗读的片段。

    规则（左到右贪心，保证「前缀稳定」：同一段文字多切几次结果一致，
    流式增量时才能放心把已经封口的句子拿去合成）：
    - 遇到句末标点（。！？!?… 换行 ；;）且这一句已攒够 min_chars 字 → 切开；
    - 一直没遇到标点 → 攒到 max_chars()（上游单次合成上限 300）强制切开；
    - 末尾不足 min_chars 的残句照常返回，由调用方决定要不要单独合成。
    """
    clean = str(text or "").strip()
    if not clean:
        return []
    floor = max(1, int(min_chars or sentence_min_chars()))
    limit = max(1, max_chars())
    out: list = []
    buf = ""
    for ch in clean:
        buf += ch
        if ch in SENTENCE_END_CHARS and len(buf.strip()) >= floor:
            out.append(buf.strip())
            buf = ""
        elif len(buf) >= limit:
            out.append(buf.strip())
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)
    return [item for item in out if item]
