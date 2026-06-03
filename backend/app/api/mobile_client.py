from __future__ import annotations

import re
import secrets
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Asset,
    H5ChatDevicePresence,
    H5ChatEvent,
    H5ChatMessage,
    MobileDeviceBinding,
    ScheduledTaskRun,
    SkillUnlock,
    User,
)
from ..core.config import settings
from ..services.sms_ihuyi import send_verify_code_sms as _ihuyi_send
from ..services.sms_aliyun import send_verify_code_sms as _aliyun_send
from .auth import REGISTER_INITIAL_CREDITS, SMS_CODE_TTL_SEC, access_token_claims, create_access_token, get_current_user, get_password_hash
from .auth import _check_and_update_sms_send_limit, _clear_sms_code, _create_auth_challenge, _verify_sms_challenge
from .auth import _get_wechat_access_token
from .installation_slots import parse_installation_id_strict
from .mobile_identity import (
    is_wechat_session_user,
    latest_mobile_binding,
    online_user_for_mobile_binding,
    online_user_for_mobile_user,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_PHONE_EMAIL_SUFFIX = "@sms.lobster.local"
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_MEDIA_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_DEFAULT_PHONE_UNLOCK_PACKAGES = (
    "sutui_mcp",
    "douyin_publish",
    "xiaohongshu_publish",
    "toutiao_publish",
)


class MobileBindRequest(BaseModel):
    phone: Optional[str] = Field(None, max_length=32)
    phone_code: Optional[str] = Field(None, max_length=128)
    sms_code: Optional[str] = Field(None, max_length=16)
    device_id: str = Field(..., min_length=8, max_length=128)
    platform: str = Field("wechat_miniprogram", max_length=32)
    openid: Optional[str] = Field(None, max_length=128)
    display_name: Optional[str] = Field(None, max_length=128)
    ref_agent_user_id: Optional[int] = None
    invite_agent_user_id: Optional[int] = None
    parent_user_id: Optional[int] = None


class MobileSmsSendRequest(BaseModel):
    phone: str = Field(..., max_length=32)


class MobileWechatLoginRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)
    device_id: Optional[str] = Field(None, min_length=8, max_length=128)
    platform: str = Field("wechat_miniprogram", max_length=32)
    display_name: Optional[str] = Field(None, max_length=128)
    ref_agent_user_id: Optional[int] = None
    invite_agent_user_id: Optional[int] = None
    parent_user_id: Optional[int] = None


class MobileShareBindRequest(BaseModel):
    ref_agent_user_id: Optional[int] = None
    invite_agent_user_id: Optional[int] = None
    parent_user_id: Optional[int] = None


class MobileHeartbeatRequest(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)


def _wechat_json(resp: httpx.Response) -> dict:
    """微信接口常以 text/plain 返回 JSON，按 body 解析更稳。"""
    try:
        return resp.json() if (resp.text or "").strip() else {}
    except Exception:
        return {}


def _normalize_cn_mobile(raw: str) -> str:
    mobile = re.sub(r"\D", "", (raw or "").strip())
    if not _CN_MOBILE_RE.match(mobile):
        raise HTTPException(status_code=400, detail="手机号格式无效")
    return mobile


def _phone_email(mobile: str) -> str:
    return f"{mobile}{_PHONE_EMAIL_SUFFIX}"


def _phone_from_user_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value.endswith(_PHONE_EMAIL_SUFFIX):
        return ""
    raw = value[: -len(_PHONE_EMAIL_SUFFIX)]
    return raw if _CN_MOBILE_RE.match(raw) else ""


def _get_or_create_phone_user(db: Session, mobile: str) -> tuple[User, bool]:
    email = _phone_email(mobile)
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user, False
    user = User(
        email=email,
        hashed_password=get_password_hash("phone-code-" + secrets.token_urlsafe(32)),
        credits=REGISTER_INITIAL_CREDITS,
        role="user",
        preferred_model="sutui",
    )
    db.add(user)
    db.flush()
    for pkg_id in _DEFAULT_PHONE_UNLOCK_PACKAGES:
        db.add(SkillUnlock(user_id=user.id, package_id=pkg_id))
    db.flush()
    return user, True


def _new_wechat_user(db: Session, openid: str) -> User:
    email = f"wx_{openid[:16]}@wechat.lobster.local"
    if db.query(User).filter(User.email == email).first():
        email = f"wx_{openid}@wechat.lobster.local"
    if db.query(User).filter(User.email == email).first():
        email = f"wx_{openid[:16]}_{secrets.token_hex(4)}@wechat.lobster.local"
    user = User(
        email=email,
        hashed_password=get_password_hash(f"wechat-{openid}"),
        credits=Decimal("0"),
        role="user",
        preferred_model="sutui",
        wechat_openid=openid,
    )
    db.add(user)
    db.flush()
    return user


def _get_or_create_wechat_user(db: Session, openid: str) -> tuple[User, bool, str]:
    legacy_phone = ""
    for user in db.query(User).filter(User.wechat_openid == openid).order_by(User.id.asc()).all():
        phone = _phone_from_user_email(user.email or "")
        if phone:
            legacy_phone = legacy_phone or phone
            user.wechat_openid = None
            db.add(user)
            continue
        return user, False, legacy_phone
    if legacy_phone:
        db.flush()
        user = db.query(User).filter(User.wechat_openid == openid).order_by(User.id.asc()).first()
        if user and not _phone_from_user_email(user.email or ""):
            return user, False, legacy_phone
    user = _new_wechat_user(db, openid)
    return user, True, legacy_phone


def _candidate_ref_agent_user_id(body: Any) -> Optional[int]:
    for key in ("ref_agent_user_id", "invite_agent_user_id", "parent_user_id"):
        try:
            value = getattr(body, key, None)
        except Exception:
            value = None
        try:
            ref_id = int(value or 0)
        except Exception:
            ref_id = 0
        if ref_id > 0:
            return ref_id
    return None


def _agent_level_for_bind(user: Optional[User]) -> int:
    if not user or not getattr(user, "is_agent", False):
        return 0
    try:
        level = int(getattr(user, "agent_level", 0) or 0)
    except Exception:
        level = 0
    return level if level in (1, 2) else 1


def _apply_ref_agent_binding(db: Session, user: User, body: Any, source: str) -> bool:
    """Bind a new/unclaimed mobile user to the sharing agent.

    The share parameter is only a hint.  Server-side rules stay authoritative:
    the inviter must be a level-1 or level-2 agent, users never bind to
    themselves, and an existing parent_user_id is not overwritten.
    """
    ref_id = _candidate_ref_agent_user_id(body)
    if not ref_id or not user or not getattr(user, "id", None):
        return False
    if int(user.id) == int(ref_id):
        return False
    if getattr(user, "parent_user_id", None):
        return False
    agent = db.query(User).filter(User.id == int(ref_id)).first()
    if _agent_level_for_bind(agent) not in (1, 2):
        logger.info(
            "[mobile/share-bind] ignored non-agent ref source=%s user_id=%s ref_id=%s",
            source,
            user.id,
            ref_id,
        )
        return False
    user.parent_user_id = int(agent.id)
    db.add(user)
    logger.info(
        "[mobile/share-bind] bound source=%s user_id=%s parent_user_id=%s parent_level=%s",
        source,
        user.id,
        agent.id,
        _agent_level_for_bind(agent),
    )
    return True


def _exchange_wechat_login_code(js_code: str) -> str:
    code = (js_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 wx.login code")
    app_id = (getattr(settings, "wechat_app_id", None) or "").strip()
    app_secret = (getattr(settings, "wechat_app_secret", None) or "").strip()
    if not app_id or not app_secret:
        raise HTTPException(status_code=503, detail="服务器未配置小程序 AppID/AppSecret")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": app_id,
                    "secret": app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
        data = _wechat_json(resp)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信登录失败: {exc!s}") from exc
    if data.get("errcode"):
        raise HTTPException(status_code=400, detail=data.get("errmsg") or "微信登录失败")
    openid = (data.get("openid") or "").strip()
    if not openid:
        raise HTTPException(status_code=400, detail="未获取到微信 openid")
    return openid


def _exchange_wechat_phone_code(phone_code: str) -> str:
    code = (phone_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少微信手机号授权 code")
    try:
        token = _get_wechat_access_token()
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={quote(token, safe='')}",
                json={"code": code},
            )
        data = _wechat_json(resp)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"微信手机号校验失败: {exc!s}") from exc
    errcode = int(data.get("errcode") or 0)
    if errcode != 0:
        raise HTTPException(status_code=400, detail=data.get("errmsg") or "微信手机号授权无效")
    phone_info = data.get("phone_info") or {}
    raw_phone = phone_info.get("purePhoneNumber") or phone_info.get("phoneNumber") or ""
    return _normalize_cn_mobile(str(raw_phone))


def _resolve_bind_phone(body: MobileBindRequest, db: Session) -> tuple[str, bool]:
    plain = (body.phone or "").strip()
    verified = False
    if (body.phone_code or "").strip():
        verified_phone = _exchange_wechat_phone_code(body.phone_code or "")
        if plain:
            plain_phone = _normalize_cn_mobile(plain)
            if plain_phone != verified_phone:
                raise HTTPException(status_code=400, detail="填写手机号与微信授权手机号不一致")
        return verified_phone, True
    if (body.sms_code or "").strip():
        if not plain:
            raise HTTPException(status_code=400, detail="请填写手机号")
        mobile = _normalize_cn_mobile(plain)
        _verify_mobile_sms_code(db, mobile, body.sms_code or "")
        return mobile, True
    if not plain:
        raise HTTPException(status_code=400, detail="请提供手机号或微信手机号授权 code")
    return _normalize_cn_mobile(plain), verified


def _sms_channel_ready() -> tuple[bool, bool]:
    aliyun_ak = (getattr(settings, "aliyun_sms_access_key_id", None) or "").strip()
    aliyun_sk = (getattr(settings, "aliyun_sms_access_key_secret", None) or "").strip()
    ihuyi_acc = (getattr(settings, "ihuyi_sms_account", None) or "").strip()
    ihuyi_pwd = (getattr(settings, "ihuyi_sms_password", None) or "").strip()
    return bool(aliyun_ak and aliyun_sk), bool(ihuyi_acc and ihuyi_pwd)


def _send_mobile_sms_code(db: Session, mobile: str) -> None:
    use_aliyun, use_ihuyi = _sms_channel_ready()
    if not use_aliyun and not use_ihuyi:
        raise HTTPException(status_code=503, detail="未配置短信通道")
    _check_and_update_sms_send_limit(db, mobile)
    code = f"{secrets.randbelow(1000000):06d}"
    _create_auth_challenge(db, kind="sms", target=mobile, answer=code, ttl_seconds=SMS_CODE_TTL_SEC)
    try:
        if use_aliyun:
            _aliyun_send(
                access_key_id=(getattr(settings, "aliyun_sms_access_key_id", None) or "").strip(),
                access_key_secret=(getattr(settings, "aliyun_sms_access_key_secret", None) or "").strip(),
                sign_name=getattr(settings, "aliyun_sms_sign_name", "深圳市必火智能信息技术"),
                template_code=getattr(settings, "aliyun_sms_template_code", "SMS_333406023"),
                mobile=mobile,
                code=code,
            )
        else:
            _ihuyi_send(
                account=(getattr(settings, "ihuyi_sms_account", None) or "").strip(),
                api_key=(getattr(settings, "ihuyi_sms_password", None) or "").strip(),
                mobile=mobile,
                code=code,
            )
    except RuntimeError as exc:
        _clear_sms_code(db, mobile)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _verify_mobile_sms_code(db: Session, mobile: str, code: str) -> None:
    code_in = (code or "").strip()
    if not code_in or len(code_in) > 8:
        raise HTTPException(status_code=400, detail="短信验证码无效")
    if not _verify_sms_challenge(db, mobile, code_in):
        raise HTTPException(status_code=400, detail="短信验证码错误或已过期，请重新获取")


def _media_type_from_url(url: str, fallback: str = "") -> str:
    ext = Path(urlparse(url).path or "").suffix.lower()
    if ext in {".mp4", ".webm", ".mov", ".m4v"}:
        return "video"
    if ext in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        return "audio"
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    fb = (fallback or "").lower()
    if fb in {"video", "audio", "image"}:
        return fb
    return "media"


def _public_media_url(request: Request, raw_url: str, disposition: str = "inline", filename: str = "") -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    base = _request_public_base_url(request)
    name = (filename or Path(urlparse(url).path).name or "lobster-media").strip()
    return f"{base}/api/h5-chat/media?url={quote(url, safe='')}&disposition={quote(disposition)}&filename={quote(name)}"


def _direct_mobile_media_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    return url if url.startswith("https://") else ""


def _mobile_media_urls(request: Request, raw_url: str, filename: str) -> Dict[str, str]:
    direct = _direct_mobile_media_url(raw_url)
    proxy_preview = _public_media_url(request, raw_url, "inline", filename)
    proxy_download = _public_media_url(request, raw_url, "attachment", filename)
    return {
        "preview_url": direct or proxy_preview,
        "download_url": direct or proxy_download,
        "proxy_preview_url": proxy_preview,
        "proxy_download_url": proxy_download,
    }


def _request_public_base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",", 1)[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or "bhzn.top"
    ).split(",", 1)[0].strip()
    if proto == "http" and host not in {"testserver"} and not host.startswith(("127.0.0.1", "localhost")):
        proto = "https"
    return f"{proto}://{host}".rstrip("/")


def _asset_download_item(request: Request, asset: Asset) -> Optional[Dict[str, Any]]:
    url = (asset.source_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    filename = asset.filename or Path(urlparse(url).path).name or f"{asset.asset_id}"
    media_type = _media_type_from_url(url, asset.media_type)
    item = {
        "id": f"asset:{asset.asset_id}",
        "source": "asset",
        "asset_id": asset.asset_id,
        "title": filename,
        "media_type": media_type,
        "file_size": asset.file_size or 0,
        "url": url,
        "created_at": asset.created_at.isoformat() if asset.created_at else "",
        "prompt": asset.prompt or "",
        "model": asset.model or "",
        "tags": asset.tags or "",
    }
    item.update(_mobile_media_urls(request, url, filename))
    return item


def _add_url_item(
    request: Request,
    items: List[Dict[str, Any]],
    seen: set[str],
    *,
    url: str,
    source: str,
    title: str,
    created_at: Optional[datetime],
    context_id: str,
    fallback_media_type: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    clean = _unwrap_media_proxy_url(url).strip().rstrip("，。；;)")
    if not clean.startswith(("http://", "https://")) or clean in seen:
        return
    ext = Path(urlparse(clean).path or "").suffix.lower()
    if ext and ext not in _MEDIA_EXTS:
        return
    seen.add(clean)
    filename = Path(urlparse(clean).path).name or "lobster-media"
    media_type = _media_type_from_url(clean, fallback_media_type)
    item = {
        "id": f"{source}:{context_id}:{len(seen)}",
        "source": source,
        "title": title or filename,
        "media_type": media_type,
        "url": clean,
        "created_at": created_at.isoformat() if created_at else "",
    }
    if extra:
        item.update(extra)
    item.update(_mobile_media_urls(request, clean, filename))
    items.append(item)


def _unwrap_media_proxy_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.path == "/api/h5-chat/media":
        inner = (parse_qs(parsed.query).get("url") or [""])[0].strip()
        if inner.startswith(("http://", "https://")):
            return inner
    return url


def _walk_media_urls(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield from _URL_RE.findall(value)
        return
    if isinstance(value, dict):
        for key in ("url", "source_url", "public_url", "preview_url", "download_url", "video_url", "audio_url", "image_url"):
            val = value.get(key)
            if isinstance(val, str):
                yield val
        for val in value.values():
            yield from _walk_media_urls(val)
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_media_urls(item)


def _current_binding(db: Session, current_user: User, device_id: str) -> MobileDeviceBinding:
    device = parse_installation_id_strict(device_id)
    row = (
        db.query(MobileDeviceBinding)
        .filter(MobileDeviceBinding.user_id == current_user.id, MobileDeviceBinding.device_id == device)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=403, detail="当前手机设备未绑定该账号")
    row.last_seen_at = datetime.utcnow()
    db.commit()
    return row


def _asset_owner_for_mobile_request(db: Session, current_user: User, binding: MobileDeviceBinding) -> User:
    return online_user_for_mobile_binding(db, current_user, binding)


@router.get("/api/mobile/phone/status", summary="手机端：检查手机号是否已有账号")
def mobile_phone_status(phone: str = Query(...), db: Session = Depends(get_db)):
    mobile = _normalize_cn_mobile(phone)
    user = db.query(User).filter(User.email == _phone_email(mobile)).first()
    return {
        "ok": True,
        "registered": bool(user),
        "has_online": bool(user),
        "message": "该手机号已注册，可直接登录" if user else "该手机号未注册，验证码通过后会自动创建账号",
    }


@router.post("/api/mobile/sms/send", summary="手机端：发送绑定手机号短信验证码")
def send_mobile_bind_sms(
    body: MobileSmsSendRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mobile = _normalize_cn_mobile(body.phone)
    user = db.query(User).filter(User.email == _phone_email(mobile)).first()
    current_openid = (getattr(current_user, "wechat_openid", None) or "").strip()
    is_wechat_session = bool(current_openid) or str(current_user.email or "").endswith("@wechat.lobster.local")
    if user and user.id != current_user.id and not is_wechat_session:
        raise HTTPException(status_code=403, detail="当前登录账号与手机号不一致，请先用该手机号登录")
    _send_mobile_sms_code(db, mobile)
    return {"ok": True}


@router.post("/api/mobile/share-bind", summary="手机端：按分享来源绑定代理关系")
def mobile_share_bind(
    body: MobileShareBindRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    ref_bound = _apply_ref_agent_binding(db, user, body, "share_bind")
    if ref_bound:
        db.commit()
        db.refresh(user)
    return {
        "ok": True,
        "user_id": user.id,
        "parent_user_id": user.parent_user_id,
        "ref_agent_bound": ref_bound,
    }


@router.post("/api/mobile/wechat-login", summary="手机端：小程序 wx.login 登录")
def mobile_wechat_login(
    body: MobileWechatLoginRequest,
    db: Session = Depends(get_db),
):
    openid = _exchange_wechat_login_code(body.code)
    user, created_temp_user, legacy_phone = _get_or_create_wechat_user(db, openid)
    if legacy_phone:
        _get_or_create_phone_user(db, legacy_phone)
    db.commit()
    db.refresh(user)
    ref_bound = _apply_ref_agent_binding(db, user, body, "wechat_login")
    if ref_bound:
        db.commit()
        db.refresh(user)
    latest_binding = latest_mobile_binding(db, user)
    phone = (legacy_phone or (latest_binding.phone if latest_binding else "") or _phone_from_user_email(user.email)).strip()
    token = create_access_token(data=access_token_claims(user))
    if phone and body.device_id:
        device_id = parse_installation_id_strict(body.device_id)
        now = datetime.utcnow()
        existing_for_device = (
            db.query(MobileDeviceBinding)
            .filter(MobileDeviceBinding.device_id == device_id, MobileDeviceBinding.user_id != user.id)
            .first()
        )
        if existing_for_device:
            db.delete(existing_for_device)
            db.flush()
        row = (
            db.query(MobileDeviceBinding)
            .filter(MobileDeviceBinding.user_id == user.id, MobileDeviceBinding.device_id == device_id)
            .first()
        )
        if row is None:
            db.add(
                MobileDeviceBinding(
                    user_id=user.id,
                    phone=phone,
                    device_id=device_id,
                    platform=(body.platform or "wechat_miniprogram").strip()[:32] or "wechat_miniprogram",
                    openid=openid[:128],
                    display_name=(body.display_name or "").strip()[:128] or None,
                    created_at=now,
                    last_seen_at=now,
                )
            )
        else:
            row.last_seen_at = now
            if body.display_name is not None:
                row.display_name = body.display_name.strip()[:128] or None
        db.commit()
    return {
        "ok": True,
        "access_token": token,
        "token_type": "bearer",
        "openid_bound": True,
        "phone_bound": bool(phone),
        "phone": phone,
        "user_id": user.id,
        "parent_user_id": user.parent_user_id,
        "ref_agent_bound": ref_bound,
        "needs_phone_bind": not bool(phone),
        "created_temp_user": created_temp_user,
        "message": "已绑定手机号账号" if phone else "请授权手机号以登录或创建账号",
    }


@router.post("/api/mobile/devices/bind", summary="手机端：绑定当前手机设备到手机号账号")
def bind_mobile_device(
    body: MobileBindRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mobile, phone_verified = _resolve_bind_phone(body, db)
    phone_user, created_phone_user = _get_or_create_phone_user(db, mobile)
    bind_user = current_user
    current_openid = (getattr(current_user, "wechat_openid", None) or "").strip()
    incoming_openid = (body.openid or "").strip()
    openid = current_openid or incoming_openid
    if phone_user.id != current_user.id:
        if not is_wechat_session_user(current_user):
            raise HTTPException(status_code=403, detail="当前登录账号与手机号不一致，请先用该手机号登录")
        if not phone_verified:
            raise HTTPException(status_code=400, detail="绑定手机号账号需要使用微信手机号授权 code")
    ref_bound = _apply_ref_agent_binding(db, phone_user, body, "phone_bind")
    if not ref_bound and not getattr(phone_user, "parent_user_id", None) and getattr(bind_user, "parent_user_id", None):
        phone_user.parent_user_id = int(bind_user.parent_user_id)
        ref_bound = True
    db.add(phone_user)
    db.add(bind_user)
    db.flush()

    device_id = parse_installation_id_strict(body.device_id)
    now = datetime.utcnow()
    existing_for_device = (
        db.query(MobileDeviceBinding)
        .filter(MobileDeviceBinding.device_id == device_id, MobileDeviceBinding.user_id != bind_user.id)
        .first()
    )
    if existing_for_device:
        db.delete(existing_for_device)
        db.flush()
    row = (
        db.query(MobileDeviceBinding)
        .filter(MobileDeviceBinding.user_id == bind_user.id, MobileDeviceBinding.device_id == device_id)
        .first()
    )
    if row is None:
        row = MobileDeviceBinding(
            user_id=bind_user.id,
            phone=mobile,
            device_id=device_id,
            platform=(body.platform or "wechat_miniprogram").strip()[:32] or "wechat_miniprogram",
            openid=openid[:128] or None,
            display_name=(body.display_name or "").strip()[:128] or None,
            created_at=now,
            last_seen_at=now,
        )
        db.add(row)
    else:
        row.phone = mobile
        row.platform = (body.platform or row.platform or "wechat_miniprogram").strip()[:32]
        row.openid = (openid or row.openid or "").strip()[:128] or None
        if body.display_name is not None:
            row.display_name = body.display_name.strip()[:128] or None
        row.last_seen_at = now
    db.commit()
    access_token = create_access_token(data=access_token_claims(bind_user))
    return {
        "ok": True,
        "user_id": bind_user.id,
        "phone": mobile,
        "phone_verified": phone_verified,
        "device_id": device_id,
        "bound_at": now.isoformat(),
        "access_token": access_token,
        "token_type": "bearer",
        "created_user": created_phone_user,
        "phone_user_id": phone_user.id,
        "parent_user_id": phone_user.parent_user_id or bind_user.parent_user_id,
        "wechat_parent_user_id": bind_user.parent_user_id,
        "ref_agent_bound": ref_bound,
    }


@router.get("/api/mobile/devices", summary="手机端：查看当前账号的 online 与手机设备")
def list_mobile_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    online_user = online_user_for_mobile_user(db, current_user)
    online_rows = (
        db.query(H5ChatDevicePresence)
        .filter(H5ChatDevicePresence.user_id == online_user.id)
        .order_by(H5ChatDevicePresence.last_seen_at.desc())
        .limit(20)
        .all()
    )
    mobile_rows = (
        db.query(MobileDeviceBinding)
        .filter(MobileDeviceBinding.user_id == current_user.id)
        .order_by(MobileDeviceBinding.last_seen_at.desc())
        .limit(20)
        .all()
    )
    online_ids = {r.installation_id for r in online_rows}
    return {
        "ok": True,
        "online_available": bool(online_rows),
        "online_devices": [
            {
                "installation_id": r.installation_id,
                "display_name": r.display_name or "",
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else "",
                "online": ((now - r.last_seen_at).total_seconds() <= 20) if r.last_seen_at else False,
            }
            for r in online_rows
        ],
        "online_installation_ids": list(online_ids),
        "mobile_devices": [
            {
                "device_id": r.device_id,
                "platform": r.platform,
                "display_name": r.display_name or "",
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else "",
            }
            for r in mobile_rows
        ],
    }


@router.post("/api/mobile/devices/{device_id}/heartbeat", summary="手机端：刷新手机设备在线状态")
def mobile_device_heartbeat(
    device_id: str,
    body: MobileHeartbeatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _current_binding(db, current_user, device_id)
    if body.display_name is not None:
        row.display_name = body.display_name.strip()[:128] or None
    row.last_seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "device_id": row.device_id, "last_seen_at": row.last_seen_at.isoformat()}


@router.get("/api/mobile/downloads", summary="手机端：素材下载列表")
def mobile_downloads(
    request: Request,
    device_id: str = Query(...),
    media_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    binding = _current_binding(db, current_user, device_id)
    owner_user = _asset_owner_for_mobile_request(db, current_user, binding)
    wanted = (media_type or "").strip().lower()
    if wanted and wanted not in {"image", "video", "audio", "media"}:
        raise HTTPException(status_code=400, detail="media_type 无效")

    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    q = db.query(Asset).filter(Asset.user_id == owner_user.id, Asset.source_url.isnot(None))
    if wanted in {"image", "video", "audio"}:
        q = q.filter(Asset.media_type == wanted)
    for row in q.order_by(Asset.created_at.desc()).limit(limit).all():
        item = _asset_download_item(request, row)
        if not item:
            continue
        if wanted and wanted != "media" and item["media_type"] != wanted:
            continue
        seen.add(item["url"])
        items.append(item)

    remaining = max(0, limit - len(items))
    if remaining:
        runs = (
            db.query(ScheduledTaskRun)
            .filter(ScheduledTaskRun.user_id == owner_user.id, ScheduledTaskRun.status == "completed")
            .order_by(ScheduledTaskRun.created_at.desc())
            .limit(100)
            .all()
        )
        for run in runs:
            before = len(items)
            result_payload = run.result_payload if isinstance(run.result_payload, dict) else {}
            draft = result_payload.get("publish_draft") if isinstance(result_payload.get("publish_draft"), dict) else None
            publish_extra = {"run_id": run.id, "publish_draft": draft} if draft else {"run_id": run.id}
            for url in _walk_media_urls(run.result_payload):
                _add_url_item(
                    request,
                    items,
                    seen,
                    url=url,
                    source="scheduled_task",
                    title=run.title,
                    created_at=run.finished_at or run.created_at,
                    context_id=run.id,
                    fallback_media_type=wanted,
                    extra=publish_extra,
                )
            for url in _walk_media_urls(run.result_text or ""):
                _add_url_item(
                    request,
                    items,
                    seen,
                    url=url,
                    source="scheduled_task",
                    title=run.title,
                    created_at=run.finished_at or run.created_at,
                    context_id=run.id,
                    fallback_media_type=wanted,
                    extra=publish_extra,
                )
            if wanted and wanted != "media":
                items[:] = [x for x in items if x.get("media_type") == wanted or x.get("source") == "asset"]
            if len(items) >= limit:
                break

    remaining = max(0, limit - len(items))
    if remaining:
        messages = (
            db.query(H5ChatMessage)
            .filter(H5ChatMessage.user_id == owner_user.id, H5ChatMessage.status == "completed")
            .order_by(H5ChatMessage.created_at.desc())
            .limit(100)
            .all()
        )
        for msg in messages:
            for url in _walk_media_urls(msg.reply_text or ""):
                _add_url_item(
                    request,
                    items,
                    seen,
                    url=url,
                    source="h5_chat",
                    title="手机会话结果",
                    created_at=msg.finished_at or msg.created_at,
                    context_id=msg.id,
                    fallback_media_type=wanted,
                )
            if len(items) >= limit:
                break

    if len(items) < limit:
        events = (
            db.query(H5ChatEvent)
            .filter(H5ChatEvent.user_id == owner_user.id, H5ChatEvent.event_type == "final")
            .order_by(H5ChatEvent.created_at.desc())
            .limit(100)
            .all()
        )
        for ev in events:
            for url in _walk_media_urls(ev.payload or {}):
                _add_url_item(
                    request,
                    items,
                    seen,
                    url=url,
                    source="h5_event",
                    title="生成结果",
                    created_at=ev.created_at,
                    context_id=f"{ev.message_id}-{ev.id}",
                    fallback_media_type=wanted,
                )
            if len(items) >= limit:
                break

    if wanted and wanted != "media":
        items = [x for x in items if x.get("media_type") == wanted]
    items = sorted(items, key=lambda x: x.get("created_at") or "", reverse=True)[:limit]
    return {"ok": True, "total": len(items), "items": items}
