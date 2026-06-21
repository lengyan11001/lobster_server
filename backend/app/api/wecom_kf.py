"""微信客服：客服账号管理、消息收发代理、回调处理。

架构与现有企微模块一致：lobster-server（公网）负责：
  - 接收企微回调 → 通过 sync_msg 拉消息 → 入 KF 消息队列
  - 为 lobster_online（本地）提供 proxy 接口：创建/列表客服账号、拉消息、发消息
  - lobster_online poll → AI → submit → lobster-server 调 kf/send_msg 推送给客户

企微「微信客服」API 文档：
  - 客服账号管理: kf/account/add, kf/account/update, kf/account/del, kf/account/list
  - 接待人员: kf/servicer/add, kf/servicer/del, kf/servicer/list
  - 消息收发: kf/sync_msg, kf/send_msg
  - 会话状态: kf/service_state/trans, kf/service_state/get
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import WecomConfig
from ..services.runtime_cache import cache_delete, cache_delete_prefix, cache_flag_recent, cache_get, cache_set

logger = logging.getLogger(__name__)
router = APIRouter()

QYAPI_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
_KF_PROXY_ENABLED_ENV = "LOBSTER_WECOM_KF_PROXY_ENABLED"
_KF_PROXY_ENABLE_FLAG = Path(os.environ.get("LOBSTER_WECOM_KF_PROXY_ENABLE_FLAG", "/opt/lobster-server/.runtime/wecom_kf_enabled"))

# ── access_token 缓存（corp_id:secret → (token, expire_ts)）─────────────────
_token_cache: dict[str, tuple[str, float]] = {}

# ── KF 事件标记：callback_path → 最新事件时间戳 ──────────────────────────────
_kf_event_flags: dict[str, float] = {}
_KF_EVENT_TTL_SECONDS = 86400
_KF_EMPTY_TTL_SECONDS = 5


def _kf_event_key(callback_path: str) -> str:
    return f"wecom:kf:event:{callback_path or '-'}"


def _kf_empty_key(callback_path: str) -> str:
    return f"wecom:kf:empty:{callback_path or '-'}"


def notify_kf_event(callback_path: str):
    """微信回调到达时调用，标记该 callback_path 有新 KF 消息。"""
    if not _kf_proxy_enabled():
        logger.info("[KF] proxy disabled; ignore event flag for %s", callback_path)
        return
    now = time.time()
    _kf_event_flags[callback_path] = now
    cache_set(_kf_event_key(callback_path), str(now), _KF_EVENT_TTL_SECONDS)
    cache_set("wecom:kf:event:any", str(now), _KF_EVENT_TTL_SECONDS)
    cache_delete(_kf_empty_key(callback_path))
    cache_delete(_kf_empty_key(""))
    logger.info("[KF] event flag set for %s", callback_path)


def _kf_proxy_enabled() -> bool:
    if (os.environ.get(_KF_PROXY_ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return _KF_PROXY_ENABLE_FLAG.is_file()


def _reject_if_kf_proxy_disabled() -> None:
    if not _kf_proxy_enabled():
        raise HTTPException(status_code=503, detail="企业微信客服功能已临时关闭")


async def _get_access_token(corp_id: str, secret: str) -> str:
    cache_key = f"{corp_id}:{secret}"
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{QYAPI_BASE}/gettoken", params={"corpid": corp_id, "corpsecret": secret})
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"企微 gettoken 失败: {data.get('errmsg', '')}")
    token = data["access_token"]
    expires_in = data.get("expires_in", 7200)
    _token_cache[cache_key] = (token, time.time() + expires_in - 300)
    return token


def _check_forward_secret(x_forward_secret: Optional[str] = Header(None, alias="X-Forward-Secret")):
    secret = (settings.wecom_forward_secret or "").strip()
    if secret and x_forward_secret != secret:
        raise HTTPException(status_code=401, detail="X-Forward-Secret invalid")
    return True


def _find_config_by_callback(db: Session, callback_path: str) -> WecomConfig:
    cfg = db.query(WecomConfig).filter(WecomConfig.callback_path == callback_path).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="未找到该 callback_path 的应用配置")
    if not cfg.corp_id or not cfg.secret:
        raise HTTPException(status_code=400, detail="该应用未配置 corp_id 或 secret")
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# 代理接口：供 lobster_online 通过 HTTP 调用
# ═══════════════════════════════════════════════════════════════════════════════


# ── 客服账号管理 ───────────────────────────────────────────────────────────────

class KfAccountAddBody(BaseModel):
    callback_path: str
    name: str
    media_id: str = ""


@router.post("/api/wecom/proxy/kf/account/add", summary="[代理] 创建客服账号")
async def proxy_kf_account_add(
    body: KfAccountAddBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {"name": body.name}
    if body.media_id:
        payload["media_id"] = body.media_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{QYAPI_BASE}/kf/account/add?access_token={token}", json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"创建客服账号失败: {data.get('errmsg', '')}")
    return data


@router.get("/api/wecom/proxy/kf/account/list", summary="[代理] 获取客服账号列表")
async def proxy_kf_account_list(
    callback_path: str,
    offset: int = 0,
    limit: int = 100,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/account/list?access_token={token}",
            json={"offset": offset, "limit": limit},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"获取客服账号列表失败: {data.get('errmsg', '')}")
    return data


class KfAccountUpdateBody(BaseModel):
    callback_path: str
    open_kfid: str
    name: str = ""
    media_id: str = ""


@router.post("/api/wecom/proxy/kf/account/update", summary="[代理] 修改客服账号")
async def proxy_kf_account_update(
    body: KfAccountUpdateBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {"open_kfid": body.open_kfid}
    if body.name:
        payload["name"] = body.name
    if body.media_id:
        payload["media_id"] = body.media_id
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{QYAPI_BASE}/kf/account/update?access_token={token}", json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"修改客服账号失败: {data.get('errmsg', '')}")
    return data


class KfAccountDelBody(BaseModel):
    callback_path: str
    open_kfid: str


@router.post("/api/wecom/proxy/kf/account/del", summary="[代理] 删除客服账号")
async def proxy_kf_account_del(
    body: KfAccountDelBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/account/del?access_token={token}",
            json={"open_kfid": body.open_kfid},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"删除客服账号失败: {data.get('errmsg', '')}")
    return data


# ── 接待人员管理 ───────────────────────────────────────────────────────────────

class KfServicerBody(BaseModel):
    callback_path: str
    open_kfid: str
    userid_list: list[str]


@router.post("/api/wecom/proxy/kf/servicer/add", summary="[代理] 添加接待人员")
async def proxy_kf_servicer_add(
    body: KfServicerBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/servicer/add?access_token={token}",
            json={"open_kfid": body.open_kfid, "userid_list": body.userid_list},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"添加接待人员失败: {data.get('errmsg', '')}")
    return data


@router.post("/api/wecom/proxy/kf/servicer/del", summary="[代理] 删除接待人员")
async def proxy_kf_servicer_del(
    body: KfServicerBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/servicer/del?access_token={token}",
            json={"open_kfid": body.open_kfid, "userid_list": body.userid_list},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"删除接待人员失败: {data.get('errmsg', '')}")
    return data


@router.get("/api/wecom/proxy/kf/servicer/list", summary="[代理] 获取接待人员列表")
async def proxy_kf_servicer_list(
    callback_path: str,
    open_kfid: str,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{QYAPI_BASE}/kf/servicer/list",
            params={"access_token": token, "open_kfid": open_kfid},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"获取接待人员列表失败: {data.get('errmsg', '')}")
    return data


# ── 消息收发 ───────────────────────────────────────────────────────────────────

class KfSyncMsgBody(BaseModel):
    callback_path: str
    open_kfid: str
    cursor: str = ""
    token: str = ""
    limit: int = 1000
    voice_format: int = 0


@router.post("/api/wecom/proxy/kf/sync_msg", summary="[代理] 拉取客服消息")
async def proxy_kf_sync_msg(
    body: KfSyncMsgBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    if not _kf_proxy_enabled():
        _kf_event_flags.pop(body.callback_path, None)
        return {
            "errcode": 0,
            "errmsg": "企业微信客服功能已临时关闭",
            "msg_list": [],
            "has_more": 0,
            "next_cursor": body.cursor or "",
        }
    cfg = _find_config_by_callback(db, body.callback_path)
    access_token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {"open_kfid": body.open_kfid, "limit": body.limit}
    if body.cursor:
        payload["cursor"] = body.cursor
    if body.token:
        payload["token"] = body.token
    if body.voice_format:
        payload["voice_format"] = body.voice_format
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{QYAPI_BASE}/kf/sync_msg?access_token={access_token}", json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"拉取客服消息失败: {data.get('errmsg', '')}")
    return data


class KfSendMsgBody(BaseModel):
    callback_path: str
    touser: str
    open_kfid: str
    msgtype: str = "text"
    content: str = ""
    media_id: str = ""
    msgid: str = ""


@router.post("/api/wecom/proxy/kf/send_msg", summary="[代理] 发送客服消息")
async def proxy_kf_send_msg(
    body: KfSendMsgBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    access_token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {
        "touser": body.touser,
        "open_kfid": body.open_kfid,
        "msgtype": body.msgtype,
    }
    if body.msgid:
        payload["msgid"] = body.msgid
    if body.msgtype == "text":
        payload["text"] = {"content": body.content}
    elif body.msgtype in ("image", "voice", "video", "file"):
        payload[body.msgtype] = {"media_id": body.media_id}
    else:
        payload["text"] = {"content": body.content}

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{QYAPI_BASE}/kf/send_msg?access_token={access_token}", json=payload)
        r.raise_for_status()
        data = r.json()
    logger.info("[KF] send_msg touser=%s msgtype=%s errcode=%s errmsg=%s", body.touser, body.msgtype, data.get("errcode"), data.get("errmsg"))
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"发送客服消息失败: {data.get('errmsg', '')} (errcode={data.get('errcode')})")
    return data


# ── 会话状态 ───────────────────────────────────────────────────────────────────

class KfServiceStateTransBody(BaseModel):
    callback_path: str
    open_kfid: str
    external_userid: str
    service_state: int
    servicer_userid: str = ""


@router.post("/api/wecom/proxy/kf/service_state/trans", summary="[代理] 变更会话状态")
async def proxy_kf_service_state_trans(
    body: KfServiceStateTransBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    access_token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {
        "open_kfid": body.open_kfid,
        "external_userid": body.external_userid,
        "service_state": body.service_state,
    }
    if body.servicer_userid:
        payload["servicer_userid"] = body.servicer_userid
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/service_state/trans?access_token={access_token}",
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"变更会话状态失败: {data.get('errmsg', '')}")
    return data


# ── 获取客户信息 ───────────────────────────────────────────────────────────────

class KfCustomerBatchGetBody(BaseModel):
    callback_path: str
    external_userid_list: list[str]
    need_enter_session_context: int = 0


@router.post("/api/wecom/proxy/kf/customer/batchget", summary="[代理] 批量获取客户信息")
async def proxy_kf_customer_batchget(
    body: KfCustomerBatchGetBody,
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, body.callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {
        "external_userid_list": body.external_userid_list,
    }
    if body.need_enter_session_context:
        payload["need_enter_session_context"] = body.need_enter_session_context
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{QYAPI_BASE}/kf/customer/batchget?access_token={token}",
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"获取客户信息失败: {data.get('errmsg', '')}")
    return data


# ── 获取客服链接 URL（方便前端直接获取二维码入口）────────────────────────────

@router.get("/api/wecom/proxy/kf/account/url", summary="[代理] 获取客服账号二维码链接")
async def proxy_kf_account_url(
    callback_path: str,
    open_kfid: str,
    scene: str = "",
    _auth: bool = Depends(_check_forward_secret),
    db: Session = Depends(get_db),
):
    _reject_if_kf_proxy_disabled()
    cfg = _find_config_by_callback(db, callback_path)
    token = await _get_access_token(cfg.corp_id, cfg.secret)
    payload: dict[str, Any] = {"open_kfid": open_kfid}
    if scene:
        payload["scene"] = scene
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{QYAPI_BASE}/kf/add_contact_way?access_token={token}", json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("errcode") != 0:
        raise HTTPException(status_code=502, detail=f"获取客服链接失败: {data.get('errmsg', '')}")
    return data


# ── KF 事件标记查询/清除（供 lobster_online 高频轮询）──────────────────────────

@router.get("/api/wecom/proxy/kf/has-events", summary="[代理] 检查是否有新 KF 消息事件")
async def proxy_kf_has_events(
    callback_path: str = "",
    _auth: bool = Depends(_check_forward_secret),
):
    if not _kf_proxy_enabled():
        if callback_path:
            _kf_event_flags.pop(callback_path, None)
            cache_delete(_kf_event_key(callback_path))
            cache_delete(_kf_empty_key(callback_path))
        else:
            _kf_event_flags.clear()
            cache_delete("wecom:kf:event:any")
            cache_delete_prefix("wecom:kf:event:")
            cache_delete_prefix("wecom:kf:empty:")
            cache_delete(_kf_empty_key(""))
        return {"has_events": False, "callback_path": callback_path, "ts": 0, "disabled": True}
    if callback_path:
        cached_ts = cache_get(_kf_event_key(callback_path))
        ts = float(cached_ts or _kf_event_flags.get(callback_path, 0) or 0)
        if ts <= 0 and cache_flag_recent(_kf_empty_key(callback_path)):
            return {"has_events": False, "callback_path": callback_path, "ts": 0, "throttled": True}
        if ts <= 0:
            cache_set(_kf_empty_key(callback_path), "1", _KF_EMPTY_TTL_SECONDS)
        return {"has_events": ts > 0, "callback_path": callback_path, "ts": ts}
    cached_any = cache_get("wecom:kf:event:any")
    has_any = bool(cached_any or _kf_event_flags)
    if not has_any and cache_flag_recent(_kf_empty_key("")):
        return {"has_events": False, "flags": {}, "throttled": True}
    if not has_any:
        cache_set(_kf_empty_key(""), "1", _KF_EMPTY_TTL_SECONDS)
    return {"has_events": has_any, "flags": dict(_kf_event_flags)}


@router.post("/api/wecom/proxy/kf/ack-events", summary="[代理] 清除 KF 事件标记")
async def proxy_kf_ack_events(
    callback_path: str = "",
    _auth: bool = Depends(_check_forward_secret),
):
    if callback_path:
        _kf_event_flags.pop(callback_path, None)
        cache_delete(_kf_event_key(callback_path))
        cache_delete(_kf_empty_key(callback_path))
    else:
        _kf_event_flags.clear()
        cache_delete("wecom:kf:event:any")
        cache_delete_prefix("wecom:kf:event:")
        cache_delete_prefix("wecom:kf:empty:")
        cache_delete(_kf_empty_key(""))
    return {"ok": True}
