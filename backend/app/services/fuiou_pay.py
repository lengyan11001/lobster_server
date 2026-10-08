"""富友聚合支付（PC 主扫 preCreate + 公众号/服务窗 wxPreCreate）。

文档: https://fundwx.fuiou.com/doc/#/aggregatePay/api

签名体系：MD5（字段按文档规定顺序用 "|" 拼接 + mchnt_key，取 MD5）。
报文格式：JSON（HTTP POST, UTF-8）。

接口地址（测试 / 生产）：
- 统一下单（主扫）: /aggregatePay/preCreate
- 订单查询:           /aggregatePay/commonQuery
- 退款:               /aggregatePay/commonRefund
- 回调:               商户提供（富友 POST JSON，成功返回 "1"）

订单号规则: 订单前缀(5位) + 日期(yyyyMMdd) + 随机数(8-17位), 总长 ≤30 位。

order_type: WECHAT / ALIPAY / UNIONPAY 等
order_amt:  以「分」为单位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_BJ = ZoneInfo("Asia/Shanghai")


# ── 配置读取 ──

def _cfg(name: str, default: Optional[str] = None) -> str:
    return (getattr(settings, name, None) or default or "").strip()


def _fuiou_config(brand_mark: Optional[str] = None) -> dict[str, str]:
    """Resolve one OEM's 富友配置; an absent/incomplete OEM entry falls back globally."""
    names = (
        "mchnt_cd", "mchnt_key", "precreate_url", "query_url", "refund_url",
        "term_id", "default_order_type", "order_prefix", "ins_cd", "repeat_order",
    )
    cfg = {name: _cfg(f"fuiou_{name}") for name in names}
    mark = str(brand_mark or "").strip().lower()
    raw = _cfg("fuiou_brand_configs_json")
    if not mark or not raw:
        return cfg
    try:
        parsed = json.loads(raw)
        entry = parsed.get(mark) if isinstance(parsed, dict) else None
        if not isinstance(entry, dict):
            return cfg
    except (TypeError, ValueError):
        logger.warning("[fuiou] invalid FUIOU_BRAND_CONFIGS_JSON")
        return cfg
    # Require both merchant id and key before enabling an OEM override. Other
    # fields may inherit the system-level defaults.
    mchnt_cd = str(entry.get("mchnt_cd") or entry.get("FUIOU_MCHNT_CD") or "").strip()
    mchnt_key = str(entry.get("mchnt_key") or entry.get("FUIOU_MCHNT_KEY") or "").strip()
    if not mchnt_cd or not mchnt_key:
        return cfg
    aliases = {name: (name, f"FUIOU_{name.upper()}") for name in names}
    for name, keys in aliases.items():
        for key in keys:
            value = entry.get(key)
            if value is not None and str(value).strip():
                cfg[name] = str(value).strip()
                break
    cfg["mchnt_cd"] = mchnt_cd
    cfg["mchnt_key"] = mchnt_key
    return cfg


def fuiou_brand_configured(brand_mark: Optional[str]) -> bool:
    cfg = _fuiou_config(brand_mark)
    return bool(cfg["mchnt_cd"] and cfg["mchnt_key"] and cfg["precreate_url"])


def fuiou_configured(brand_mark: Optional[str] = None) -> bool:
    """商户号 + mchnt_key + 下单 URL 齐全即视为已配置。"""
    return fuiou_brand_configured(brand_mark)


# ── MD5 签名 ──

def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _sign_precreate(params: dict[str, str], mchnt_key: str) -> str:
    """preCreate 签名：mchnt_cd|order_type|order_amt|mchnt_order_no|txn_begin_ts|goods_des|term_id|term_ip|notify_url|random_str|version|mchnt_key"""
    parts = [
        params.get("mchnt_cd", ""),
        params.get("order_type", ""),
        params.get("order_amt", ""),
        params.get("mchnt_order_no", ""),
        params.get("txn_begin_ts", ""),
        params.get("goods_des", ""),
        params.get("term_id", ""),
        params.get("term_ip", ""),
        params.get("notify_url", ""),
        params.get("random_str", ""),
        params.get("version", ""),
        mchnt_key,
    ]
    return _md5("|".join(parts))


def _sign_query(params: dict[str, str], mchnt_key: str) -> str:
    """commonQuery 签名：mchnt_cd|order_type|mchnt_order_no|term_id|random_str|version|mchnt_key"""
    parts = [
        params.get("mchnt_cd", ""),
        params.get("order_type", ""),
        params.get("mchnt_order_no", ""),
        params.get("term_id", ""),
        params.get("random_str", ""),
        params.get("version", ""),
        mchnt_key,
    ]
    return _md5("|".join(parts))


def _sign_refund(params: dict[str, str], mchnt_key: str) -> str:
    """commonRefund 签名：mchnt_cd|order_type|mchnt_order_no|refund_order_no|total_amt|refund_amt|term_id|random_str|version|mchnt_key"""
    parts = [
        params.get("mchnt_cd", ""),
        params.get("order_type", ""),
        params.get("mchnt_order_no", ""),
        params.get("refund_order_no", ""),
        params.get("total_amt", ""),
        params.get("refund_amt", ""),
        params.get("term_id", ""),
        params.get("random_str", ""),
        params.get("version", ""),
        mchnt_key,
    ]
    return _md5("|".join(parts))


def _verify_precreate_resp(resp: dict[str, Any], mchnt_key: str) -> bool:
    """验签 preCreate 响应：result_code|result_msg|mchnt_cd|reserved_fy_trace_no|random_str|mchnt_key"""
    expected_sign = resp.get("sign", "")
    if not expected_sign:
        return True
    parts = [
        resp.get("result_code", ""),
        resp.get("result_msg", ""),
        resp.get("mchnt_cd", ""),
        resp.get("reserved_fy_trace_no", ""),
        resp.get("random_str", ""),
        mchnt_key,
    ]
    return _md5("|".join(parts)) == expected_sign


def _verify_query_resp(resp: dict[str, Any], mchnt_key: str) -> bool:
    """验签 commonQuery 响应。"""
    expected_sign = resp.get("sign", "")
    if not expected_sign:
        return True
    parts = [
        resp.get("result_code", ""),
        resp.get("result_msg", ""),
        resp.get("mchnt_cd", ""),
        resp.get("order_amt", ""),
        resp.get("transaction_id", ""),
        resp.get("mchnt_order_no", ""),
        resp.get("reserved_fy_settle_dt", ""),
        resp.get("trans_stat", ""),
        resp.get("random_str", ""),
        mchnt_key,
    ]
    return _md5("|".join(parts)) == expected_sign


def _verify_notify(data: dict[str, Any], mchnt_key: str) -> bool:
    """验签回调通知（优先用 full_sign，否则 sign）。"""
    full_sign = (data.get("full_sign") or "").strip()
    if full_sign:
        parts = [
            data.get("result_code", ""),
            data.get("result_msg", ""),
            data.get("mchnt_cd", ""),
            data.get("mchnt_order_no", ""),
            data.get("settle_order_amt", ""),
            data.get("order_amt", ""),
            data.get("txn_fin_ts", ""),
            data.get("reserved_fy_settle_dt", ""),
            data.get("random_str", ""),
            mchnt_key,
        ]
        return _md5("|".join(parts)) == full_sign

    sign = (data.get("sign") or "").strip()
    if sign:
        parts = [
            data.get("mchnt_cd", ""),
            data.get("mchnt_order_no", ""),
            data.get("settle_order_amt", ""),
            data.get("order_amt", ""),
            data.get("txn_fin_ts", ""),
            data.get("reserved_fy_settle_dt", ""),
            data.get("random_str", ""),
            mchnt_key,
        ]
        return _md5("|".join(parts)) == sign

    return False


# ── 工具 ──

def _random_str(length: int = 32) -> str:
    return uuid.uuid4().hex[:length]


def gen_order_no(brand_mark: Optional[str] = None) -> str:
    """生成符合富友规范的商户订单号: 订单前缀(5位) + 日期(8位) + 随机数(最多17位), ≤30。"""
    prefix = (_fuiou_config(brand_mark)["order_prefix"] or "10000")[:5].ljust(5, "0")
    date_str = datetime.now(_BJ).strftime("%Y%m%d")
    rand_part = str(int(time.time() * 1000))[-10:] + uuid.uuid4().hex[:7]
    return (prefix + date_str + rand_part)[:30]


def _now_ts() -> str:
    """当前北京时间 yyyyMMddHHmmss。"""
    return datetime.now(_BJ).strftime("%Y%m%d%H%M%S")


# ── HTTP 调用 ──

async def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    logger.info("[fuiou] POST %s keys=%s", url, list(body.keys()))
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        resp = await client.post(url, json=body, headers={"Content-Type": "application/json; charset=utf-8"})
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"富友返回非 JSON: {resp.text[:300]}")
    logger.info("[fuiou] resp result_code=%s result_msg=%s", data.get("result_code"), data.get("result_msg"))
    return data


# ── 业务接口 ──

async def fuiou_order_pay(
    *,
    mchnt_order_no: str,
    order_amt_fen: int,
    notify_url: str,
    goods_des: str,
    order_type: Optional[str] = None,
    term_ip: str = "127.0.0.1",
    brand_mark: Optional[str] = None,
) -> dict[str, Any]:
    """统一下单（主扫 preCreate）→ 返回二维码 URL。

    Returns:
        {"ok": bool, "qr_code": str, "raw": dict, "result_code": str, "result_msg": str}
    """
    cfg = _fuiou_config(brand_mark)
    url = cfg["precreate_url"]
    if not url:
        raise RuntimeError("未配置 FUIOU_PRECREATE_URL")
    mchnt_key = cfg["mchnt_key"]
    if not mchnt_key:
        raise RuntimeError("未配置 FUIOU_MCHNT_KEY")

    otype = (order_type or cfg["default_order_type"] or "WECHAT").upper()
    params: dict[str, str] = {
        "version": "1.0",
        "mchnt_cd": cfg["mchnt_cd"],
        "random_str": _random_str(),
        "order_type": otype,
        "order_amt": str(int(order_amt_fen)),
        "mchnt_order_no": mchnt_order_no[:30],
        "txn_begin_ts": _now_ts(),
        "goods_des": (goods_des or "recharge")[:128],
        "term_id": (cfg["term_id"] or "88888888")[:8],
        "term_ip": term_ip[:16],
        "notify_url": notify_url[:256],
    }
    params["sign"] = _sign_precreate(params, mchnt_key)

    ins_cd = cfg["ins_cd"]
    if ins_cd:
        params["ins_cd"] = ins_cd

    repeat = cfg["repeat_order"]
    if repeat:
        params["Reserved_repeat_order"] = "1"

    data = await _post_json(url, params)
    code = (data.get("result_code") or "").strip()
    msg = (data.get("result_msg") or "").strip()
    ok = code == "000000"
    qr_code = (data.get("qr_code") or "").strip() if ok else ""
    return {
        "ok": ok,
        "qr_code": qr_code,
        "raw": data,
        "result_code": code,
        "result_msg": msg,
    }


async def fuiou_order_query(
    *,
    mchnt_order_no: str,
    order_type: Optional[str] = None,
    brand_mark: Optional[str] = None,
) -> dict[str, Any]:
    """订单查询 commonQuery。

    Returns:
        {"ok": bool, "trans_stat": str, "order_amt_fen": int|None, "transaction_id": str|None,
         "raw": dict, "result_code": str, "result_msg": str}
    """
    cfg = _fuiou_config(brand_mark)
    url = cfg["query_url"]
    if not url:
        raise RuntimeError("未配置 FUIOU_QUERY_URL")
    mchnt_key = cfg["mchnt_key"]
    if not mchnt_key:
        raise RuntimeError("未配置 FUIOU_MCHNT_KEY")

    otype = (order_type or cfg["default_order_type"] or "WECHAT").upper()
    params: dict[str, str] = {
        "version": "1.0",
        "mchnt_cd": cfg["mchnt_cd"],
        "random_str": _random_str(),
        "order_type": otype,
        "mchnt_order_no": mchnt_order_no[:30],
        "term_id": (cfg["term_id"] or "88888888")[:8],
    }
    params["sign"] = _sign_query(params, mchnt_key)

    data = await _post_json(url, params)
    code = (data.get("result_code") or "").strip()
    msg = (data.get("result_msg") or "").strip()
    ok = code == "000000"
    trans_stat = (data.get("trans_stat") or "").strip() if ok else ""
    raw_amt = data.get("order_amt")
    amt_fen: Optional[int] = None
    if raw_amt is not None and str(raw_amt).strip().isdigit():
        amt_fen = int(str(raw_amt).strip())
    txn_id = (data.get("transaction_id") or "").strip() or None
    return {
        "ok": ok,
        "trans_stat": trans_stat,
        "order_amt_fen": amt_fen,
        "transaction_id": txn_id,
        "raw": data,
        "result_code": code,
        "result_msg": msg,
    }


# ── 异步通知验签 ──

def parse_notify(data: dict[str, Any], brand_mark: Optional[str] = None) -> tuple[bool, dict[str, Any]]:
    """验签富友异步回调 JSON → (ok, data)。

    成功条件：
    - mchnt_cd 与配置一致
    - MD5 签名校验通过（full_sign 或 sign）
    - result_code == "000000"
    """
    if not isinstance(data, dict):
        return False, {}
    cfg = _fuiou_config(brand_mark)
    if (data.get("mchnt_cd") or "").strip() != cfg["mchnt_cd"]:
        logger.warning("[fuiou] notify mchnt_cd mismatch: got %s", data.get("mchnt_cd"))
        return False, {}
    mchnt_key = cfg["mchnt_key"]
    if not mchnt_key:
        logger.warning("[fuiou] notify: mchnt_key not configured")
        return False, {}
    if not _verify_notify(data, mchnt_key):
        logger.warning("[fuiou] notify sign mismatch")
        return False, {}
    if (data.get("result_code") or "").strip() != "000000":
        logger.info("[fuiou] notify non-success result_code=%s", data.get("result_code"))
        return False, data
    return True, data
