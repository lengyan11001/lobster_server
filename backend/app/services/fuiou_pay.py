"""富友互联网扫码支付（PC 主扫）。

文档: http://47.96.154.194/fuiouWposApipay/

加解密体系（RSA 非对称，4 把密钥）：
- 商户私钥 / 商户公钥（商户自持；公钥上交富友）
- 富友公钥 / 富友私钥（富友持有；公钥下发给商户）
- 商户用【富友公钥】加密 message → 富友用富友私钥解密
- 富友用【商户公钥】加密 message 返回 → 商户用商户私钥解密

报文信封：
    请求/响应都是 {"mchnt_cd": "...", "message": "<base64 RSA 密文>"}
    response 还会带 resp_code / resp_desc

支付方式（order_pay_type）默认 FAPPLET（聚合码，一码通吃微信/支付宝）。

注意：
- order_amt 单位是「分」；
- order_id 由商户保证唯一（≤30）；
- order_date YYYYMMDD（商户系统当日北京时间）；
- RSA-2048 单块明文 ≤245 字节，分块加解密；用 PKCS1 v1.5。
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from Crypto.Cipher import PKCS1_v1_5 as PKCS1_v1_5_Cipher
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from ..core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_BJ = ZoneInfo("Asia/Shanghai")

# 进程内密钥缓存：避免每次请求重新读盘解析
_key_cache: dict[str, Any] = {}


def _block_sizes(rsa_key) -> tuple[int, int]:
    """根据密钥位数返回 (明文块大小, 密文块大小)。

    PKCS1 v1.5 padding 占 11 字节：
    - RSA-1024：明文 ≤117 字节，密文 = 128
    - RSA-2048：明文 ≤245 字节，密文 = 256
    富友测试环境为 RSA-1024，未来生产环境如换 2048 此处自动适配。
    """
    n_bytes = (rsa_key.size_in_bits() + 7) // 8
    return n_bytes - 11, n_bytes


# ── 配置读取 ──

def _cfg(name: str, default: Optional[str] = None) -> str:
    return (getattr(settings, name, None) or default or "").strip()


def fuiou_configured() -> bool:
    """有商户号 + 至少一个 .pem 路径可读，认为已配置。"""
    if not _cfg("fuiou_mchnt_cd"):
        return False
    if not _cfg("fuiou_gateway_pay_url"):
        return False
    if not _load_key("merchant_private"):
        return False
    if not _load_key("fuiou_public"):
        return False
    return True


def _resolve_key_path(rel_path: str) -> Optional[Path]:
    """支持 .env 写相对工程根的路径或绝对路径。工程根 = lobster-server/。"""
    if not rel_path:
        return None
    p = Path(rel_path)
    if p.is_absolute():
        return p if p.exists() else None
    root = Path(__file__).resolve().parent.parent.parent.parent
    cand = root / rel_path
    return cand if cand.exists() else None


def _load_key(kind: str):
    """kind ∈ {merchant_private, merchant_public, fuiou_public, fuiou_private}.

    返回 RSA key 对象；缺失或无法解析时返回 None。"""
    if kind in _key_cache:
        return _key_cache[kind]
    if kind == "merchant_private":
        path_setting = "fuiou_merchant_private_key_path"
    elif kind == "fuiou_public":
        path_setting = "fuiou_fuiou_public_key_path"
    else:
        return None
    rel = _cfg(path_setting)
    abspath = _resolve_key_path(rel) if rel else None
    if not abspath:
        _key_cache[kind] = None
        return None
    try:
        key = RSA.import_key(abspath.read_bytes())
        _key_cache[kind] = key
        return key
    except Exception as e:
        logger.error("[fuiou] load key %s from %s failed: %s", kind, abspath, e)
        _key_cache[kind] = None
        return None


# ── RSA 分块加解密 / 签名 / 验签 ──

def _rsa_encrypt(plaintext: str, pub_key) -> str:
    """用富友公钥加密明文（UTF-8）→ 分块 base64。块大小按密钥位数动态计算。"""
    if pub_key is None:
        raise RuntimeError("缺少富友公钥（FUIOU_FUIOU_PUBLIC_KEY_PATH 未配或文件不存在）")
    plain_block, _ = _block_sizes(pub_key)
    cipher = PKCS1_v1_5_Cipher.new(pub_key)
    raw = plaintext.encode("utf-8")
    chunks: list[bytes] = []
    for i in range(0, len(raw), plain_block):
        chunks.append(cipher.encrypt(raw[i : i + plain_block]))
    return base64.b64encode(b"".join(chunks)).decode("ascii")


def _rsa_decrypt(cipher_b64: str, priv_key) -> str:
    """用商户私钥解密富友返回的密文（base64）→ 明文 str。块大小按密钥位数动态计算。"""
    if priv_key is None:
        raise RuntimeError("缺少商户私钥（FUIOU_MERCHANT_PRIVATE_KEY_PATH 未配或文件不存在）")
    _, cipher_block = _block_sizes(priv_key)
    cipher = PKCS1_v1_5_Cipher.new(priv_key)
    sentinel = b"\x00" * 16
    raw = base64.b64decode(cipher_b64)
    out: list[bytes] = []
    for i in range(0, len(raw), cipher_block):
        block = raw[i : i + cipher_block]
        plain = cipher.decrypt(block, sentinel)
        if plain == sentinel:
            raise RuntimeError("富友报文解密失败（密钥不匹配或报文损坏）")
        out.append(plain)
    return b"".join(out).decode("utf-8")


def _rsa_sign(text: str, priv_key) -> str:
    """商户私钥 PKCS1 v1.5 SHA256 签名 → base64。富友异步通知验签场景用。"""
    if priv_key is None:
        raise RuntimeError("缺少商户私钥")
    h = SHA256.new(text.encode("utf-8"))
    return base64.b64encode(pkcs1_15.new(priv_key).sign(h)).decode("ascii")


def _rsa_verify(text: str, sign_b64: str, pub_key) -> bool:
    """用富友公钥验签（异步通知/响应）。"""
    if pub_key is None or not sign_b64:
        return False
    try:
        h = SHA256.new(text.encode("utf-8"))
        pkcs1_15.new(pub_key).verify(h, base64.b64decode(sign_b64))
        return True
    except Exception:
        return False


# ── 报文信封 ──

def _build_envelope(plain_dict: dict[str, Any]) -> dict[str, Any]:
    """{"mchnt_cd": "...", "message": "<RSA(JSON 明文)>"}。"""
    pub_key = _load_key("fuiou_public")
    plain_json = json.dumps(plain_dict, ensure_ascii=False, separators=(",", ":"))
    return {
        "mchnt_cd": _cfg("fuiou_mchnt_cd"),
        "message": _rsa_encrypt(plain_json, pub_key),
    }


def _parse_response(resp_json: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """解析富友返回。返回 (resp_code, resp_desc, plain_message_dict)。

    resp_code != "0000" 时业务失败，调用方按 resp_desc 报错。
    """
    resp_code = (resp_json.get("resp_code") or "").strip()
    resp_desc = (resp_json.get("resp_desc") or "").strip()
    cipher = (resp_json.get("message") or "").strip()
    plain_dict: dict[str, Any] = {}
    if cipher:
        try:
            priv_key = _load_key("merchant_private")
            plain_text = _rsa_decrypt(cipher, priv_key)
            plain_dict = json.loads(plain_text) if plain_text else {}
        except Exception as e:
            logger.warning("[fuiou] decrypt response failed: %s", e)
    return resp_code, resp_desc, plain_dict


# ── HTTP 调用 ──

async def _post_envelope(url: str, plain_dict: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not url:
        raise RuntimeError(f"未配置富友接口 URL（plain={list(plain_dict.keys())}）")
    body = _build_envelope(plain_dict)
    logger.info("[fuiou] POST %s plain_keys=%s", url, list(plain_dict.keys()))
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        resp = await client.post(url, json=body, headers={"Content-Type": "application/json; charset=utf-8"})
    resp.raise_for_status()
    try:
        resp_json = resp.json()
    except Exception:
        raise RuntimeError(f"富友返回非 JSON: {resp.text[:200]}")
    code, desc, plain = _parse_response(resp_json)
    logger.info("[fuiou] resp code=%s desc=%s plain_keys=%s", code, desc, list(plain.keys()))
    return code, desc, plain


# ── 业务接口 ──

def gen_order_id(user_id: int) -> str:
    """生成 ≤30 位的商户订单号：R{uid}{epoch10}{rand6}。"""
    return f"R{user_id}{int(time.time())}{uuid.uuid4().hex[:6]}"[:30]


def today_order_date() -> str:
    """北京时间今日 YYYYMMDD。"""
    return datetime.now(_BJ).strftime("%Y%m%d")


async def fuiou_order_pay(
    *,
    order_id: str,
    order_date: str,
    order_amt_fen: int,
    notify_url: str,
    goods_name: str,
    goods_detail: str,
    order_pay_type: Optional[str] = None,
    order_timeout: Optional[int] = None,
) -> dict[str, Any]:
    """订单支付接口（PC 主扫；下单 → 富友返回二维码 URL，前端转 PNG 让用户扫）。

    Returns:
        {"ok": bool, "qr_url": str, "raw": dict, "resp_code": str, "resp_desc": str}
    """
    pay_type = (order_pay_type or _cfg("fuiou_order_pay_type", "FAPPLET")).upper()
    plain: dict[str, Any] = {
        "mchnt_cd": _cfg("fuiou_mchnt_cd"),
        "order_date": order_date,
        "order_id": order_id,
        "order_amt": str(int(order_amt_fen)),
        "order_pay_type": pay_type,
        "back_notify_url": notify_url[:200],
        "goods_name": (goods_name or "充值积分")[:60],
        "goods_detail": (goods_detail or "充值积分")[:200],
        "ver": _cfg("fuiou_ver", "1.0.0"),
    }
    term_id = _cfg("fuiou_term_id")
    if term_id:
        plain["term_id"] = term_id[:8]
    if order_timeout:
        plain["order_timeout"] = str(int(order_timeout))
    code, desc, ret = await _post_envelope(_cfg("fuiou_gateway_pay_url"), plain)
    ok = code == "0000"
    qr_url = ""
    if ok:
        info = ret.get("order_info") or ""
        if isinstance(info, str):
            qr_url = info.strip()
            if pay_type == "FAPPLET" and qr_url and not qr_url.lower().startswith(("http://", "https://", "weixin://", "alipays://")):
                qr_url = f"https://qr.95516.com/00010000/{qr_url}"
    return {
        "ok": ok,
        "qr_url": qr_url,
        "raw": ret,
        "resp_code": code,
        "resp_desc": desc,
    }


async def fuiou_order_query(*, order_id: str, order_date: str) -> dict[str, Any]:
    """订单支付查询接口。

    Returns:
        {"ok": bool, "order_st": str, "order_amt_fen": int|None, "channel_order_no": str|None,
         "raw": dict, "resp_code": str, "resp_desc": str}
    """
    plain = {
        "mchnt_cd": _cfg("fuiou_mchnt_cd"),
        "order_date": order_date,
        "order_id": order_id,
        "ver": _cfg("fuiou_ver", "1.0.0"),
    }
    code, desc, ret = await _post_envelope(_cfg("fuiou_gateway_query_url"), plain)
    ok = code == "0000"
    order_st = (ret.get("order_st") or "").strip()
    raw_amt = ret.get("order_amt")
    amt_fen: Optional[int] = None
    if raw_amt is not None and str(raw_amt).strip().isdigit():
        amt_fen = int(str(raw_amt).strip())
    channel_no = (ret.get("channel_order_no") or ret.get("channel_no") or ret.get("third_order_id") or "").strip() or None
    return {
        "ok": ok,
        "order_st": order_st,
        "order_amt_fen": amt_fen,
        "channel_order_no": channel_no,
        "raw": ret,
        "resp_code": code,
        "resp_desc": desc,
    }


async def fuiou_order_close(*, order_id: str, order_date: str) -> dict[str, Any]:
    """订单关闭接口（仅未支付订单可关）。"""
    plain = {
        "mchnt_cd": _cfg("fuiou_mchnt_cd"),
        "order_date": order_date,
        "order_id": order_id,
        "channel_tp": "PAY_WP",
    }
    code, desc, ret = await _post_envelope(_cfg("fuiou_gateway_close_url"), plain)
    return {
        "ok": code == "0000",
        "raw": ret,
        "resp_code": code,
        "resp_desc": desc,
    }


# ── 异步通知验签 ──

def parse_notify(envelope: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """解富友异步通知 body（信封形态）→ (ok, plain_dict)。

    成功表示：
    - mchnt_cd 与配置一致；
    - message 能被商户私钥解密为 JSON；
    - 若富友带 sign 字段，再用富友公钥校验签名。
    """
    if not isinstance(envelope, dict):
        return False, {}
    if (envelope.get("mchnt_cd") or "").strip() != _cfg("fuiou_mchnt_cd"):
        logger.warning("[fuiou] notify mchnt_cd mismatch")
        return False, {}
    cipher = (envelope.get("message") or "").strip()
    if not cipher:
        return False, {}
    try:
        priv_key = _load_key("merchant_private")
        plain_text = _rsa_decrypt(cipher, priv_key)
    except Exception as e:
        logger.warning("[fuiou] notify decrypt failed: %s", e)
        return False, {}
    try:
        plain = json.loads(plain_text)
    except Exception as e:
        logger.warning("[fuiou] notify json parse failed: %s text=%r", e, plain_text[:200])
        return False, {}
    sign = (envelope.get("sign") or "").strip()
    if sign:
        pub_key = _load_key("fuiou_public")
        if not _rsa_verify(plain_text, sign, pub_key):
            logger.warning("[fuiou] notify rsa sign mismatch")
            return False, {}
    return True, plain
