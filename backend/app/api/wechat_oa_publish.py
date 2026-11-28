from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, UserWechatOfficialAccount
from ..services import integration_secrets
from .auth import get_current_user
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()
logger = logging.getLogger(__name__)

_WECHAT_API = "https://api.weixin.qq.com"
_DEFAULT_SERVER_IPS = ("42.194.209.150", "43.162.111.36")
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class WechatOaConfigBody(BaseModel):
    app_id: str = Field(..., min_length=6, max_length=64)
    app_secret: str = Field(..., min_length=8, max_length=128)


class WechatOaDraftBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=64)
    content: str = Field(default="", max_length=200000)
    author: str = Field(default="", max_length=8)
    digest: str = Field(default="", max_length=120)
    images: list[str] = Field(default_factory=list)
    source_url: str = Field(default="", max_length=1024)


def _server_ips() -> list[str]:
    raw = (os.environ.get("WECHAT_OA_SERVER_IPS") or "").strip()
    values = raw.split(",") if raw else list(_DEFAULT_SERVER_IPS)
    return [value.strip() for value in values if value.strip()]


def _wechat_error(payload: dict[str, Any], *, action: str = "调用公众号接口") -> str:
    code = int(payload.get("errcode") or 0)
    msg = str(payload.get("errmsg") or "").strip()
    if code == 40164:
        return (
            "服务器 IP 不在公众号白名单（%s）。请到 公众平台 → 开发 → 基本配置 → IP白名单，加入：%s"
            % (msg or "invalid ip", "、".join(_server_ips()))
        )
    if code == 40013:
        return "AppID 无效，请检查公众号后台的 AppID"
    if code == 40125:
        return "AppSecret 无效，请重新生成或复制 AppSecret"
    if code == 48001:
        return "该公众号没有草稿箱/素材接口权限，需要认证服务号或已开通相应接口权限"
    if code == 45009:
        return "公众号接口调用次数超限，请稍后再试"
    if code in {40001, 42001}:
        return "公众号 access_token 失效，请稍后重试"
    return ("%s失败：%s（errcode=%s）" % (action, msg or "未知错误", code or "-"))[:300]


def _mask_app_id(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text
    return text[:6] + "…" + text[-4:]


def _get_access_token(app_id: str, app_secret: str, *, force: bool = False) -> str:
    now = time.time()
    cached = _TOKEN_CACHE.get(app_id)
    if cached and not force and cached[1] > now + 120:
        return cached[0]
    try:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            resp = client.get(
                _WECHAT_API + "/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": app_id, "secret": app_secret},
            )
        payload = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="连接微信服务器失败：%s" % str(exc)[:160])
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail=_wechat_error(payload, action="获取 access_token"))
    _TOKEN_CACHE[app_id] = (token, now + int(payload.get("expires_in") or 7200))
    return token


def _load_config(db: Session, user_id: int) -> Optional[UserWechatOfficialAccount]:
    return (
        db.query(UserWechatOfficialAccount)
        .filter(UserWechatOfficialAccount.user_id == int(user_id))
        .first()
    )


@router.get("/api/wechat-oa/config", summary="读取我的公众号配置")
def get_wechat_oa_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = online_user_for_mobile_user(db, current_user)
    row = _load_config(db, int(user.id))
    secret = integration_secrets.decrypt(row.app_secret_enc) if row and row.app_secret_enc else ""
    return {
        "ok": True,
        "configured": bool(row and row.app_id and row.app_secret_enc),
        "app_id": row.app_id if row else "",
        "app_id_masked": _mask_app_id(row.app_id) if row else "",
        "secret_mask": integration_secrets.mask(secret),
        "nickname": row.nickname if row else "",
        "last_error": row.last_error if row else "",
        "server_ips": _server_ips(),
    }


@router.post("/api/wechat-oa/config", summary="保存并验证我的公众号 AppID/AppSecret")
def save_wechat_oa_config(
    body: WechatOaConfigBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = online_user_for_mobile_user(db, current_user)
    app_id = str(body.app_id or "").strip()
    app_secret = str(body.app_secret or "").strip()
    if not re.match(r"^wx[0-9a-zA-Z]{8,}$", app_id):
        raise HTTPException(status_code=400, detail="AppID 格式不对，应以 wx 开头")
    token = _get_access_token(app_id, app_secret, force=True)
    nickname = ""
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            resp = client.get(_WECHAT_API + "/cgi-bin/account/getaccountinfo", params={"access_token": token})
        info = resp.json() if resp.content else {}
        if not info.get("errcode"):
            nickname = str(info.get("nickname") or "").strip()[:128]
    except Exception:  # noqa: BLE001
        nickname = ""
    row = _load_config(db, int(user.id))
    if row is None:
        row = UserWechatOfficialAccount(user_id=int(user.id))
        db.add(row)
    row.app_id = app_id
    row.app_secret_enc = integration_secrets.encrypt(app_secret)
    row.nickname = nickname
    row.last_error = ""
    db.commit()
    return {
        "ok": True,
        "configured": True,
        "app_id": app_id,
        "app_id_masked": _mask_app_id(app_id),
        "secret_mask": integration_secrets.mask(app_secret),
        "nickname": nickname,
        "server_ips": _server_ips(),
    }


def _download_image(url: str) -> tuple[bytes, str, str]:
    src = str(url or "").strip()
    if not src.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="文章配图地址无效")
    try:
        with httpx.Client(timeout=45.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(src, headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"})
        data = resp.content
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="下载文章配图失败：%s" % str(exc)[:160])
    if resp.status_code != 200 or not data:
        raise HTTPException(status_code=502, detail="下载文章配图失败：HTTP %s" % resp.status_code)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文章配图超过 10MB，公众号无法上传")
    content_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
    if "png" in content_type:
        return data, "image/png", "image.png"
    if "jpeg" in content_type or "jpg" in content_type:
        return data, "image/jpeg", "image.jpg"
    raise HTTPException(status_code=400, detail="公众号配图只支持 jpg/png，请换一张图")


def _upload_content_image(token: str, data: bytes, filename: str, content_type: str) -> str:
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(
                _WECHAT_API + "/cgi-bin/media/uploadimg",
                params={"access_token": token},
                files={"media": (filename, data, content_type)},
            )
        payload = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="上传公众号正文图片失败：%s" % str(exc)[:160])
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail=_wechat_error(payload, action="上传公众号正文图片"))
    return url


def _upload_cover_material(token: str, data: bytes, filename: str, content_type: str) -> str:
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(
                _WECHAT_API + "/cgi-bin/material/add_material",
                params={"access_token": token, "type": "image"},
                files={"media": (filename, data, content_type)},
            )
        payload = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="上传公众号封面失败：%s" % str(exc)[:160])
    media_id = str(payload.get("media_id") or "").strip()
    if not media_id:
        raise HTTPException(status_code=400, detail=_wechat_error(payload, action="上传公众号封面"))
    return media_id


_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(\s*<?(https?://[^\s)>]+)>?\s*\)", re.IGNORECASE)
_IMG_HTML_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(https?://.*?)\1[^>]*>", re.IGNORECASE)
_IMG_BARE_RE = re.compile(r"https?://[^\s<>\"')]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>\"')]*)?", re.IGNORECASE)


def _extract_content_images(content: str) -> list[str]:
    text = str(content or "")
    urls: list[str] = []
    for regex in (_IMG_MD_RE, _IMG_HTML_RE, _IMG_BARE_RE):
        for match in regex.finditer(text):
            if regex is _IMG_HTML_RE:
                value = str(match.group(2) or "").strip()
            elif regex is _IMG_MD_RE:
                value = str(match.group(1) or "").strip()
            else:
                value = str(match.group(0) or "").strip()
            if value.startswith(("http://", "https://")) and value not in urls:
                urls.append(value)
    return urls


def _clean_content_text(content: str) -> str:
    text = str(content or "")
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|section|article|h[1-6]|li|tr|blockquote|pre)>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _IMG_MD_RE.sub("", text)
    text = _IMG_HTML_RE.sub("", text)
    text = _IMG_BARE_RE.sub("", text)
    lines: list[str] = []
    skipping = False
    for raw in text.splitlines():
        trimmed = raw.strip()
        if re.match(r"^(配图建议|封面建议|排版建议|图片建议|建议配图|头图建议|插图建议|配图说明|图片素材|标签建议|标题建议)\s*[：:]?", trimmed) or re.match(r"^(配图|封面图?|排版)\s*[：:]", trimmed):
            skipping = True
            continue
        if skipping:
            if not trimmed:
                skipping = False
                continue
            if re.match(r"^(\d+[.、)]|[-*•])\s*", trimmed):
                continue
            if len(trimmed) <= 80:
                continue
            skipping = False
        if re.match(r"^以下是.*(文章|正文|内容)", trimmed):
            continue
        lines.append(raw)
    text = "\n".join(lines)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_content_html(content: str, images: list[str]) -> str:
    text = str(content or "").strip()
    paras = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
    if not paras:
        paras = [""]
    positions: list[int] = []
    for index in range(1, len(images) + 1):
        at = round((index * len(paras)) / (len(images) + 1)) - 1
        positions.append(max(0, min(len(paras) - 1, at)))
    positions.sort()
    blocks: list[str] = []
    image_index = 0
    for index, para in enumerate(paras):
        if para:
            blocks.append("<p>%s</p>" % html.escape(para).replace("\n", "<br/>"))
        while image_index < len(images) and positions[image_index] == index:
            blocks.append('<p><img src="%s" style="max-width:100%%;"/></p>' % html.escape(images[image_index], quote=True))
            image_index += 1
    while image_index < len(images):
        blocks.append('<p><img src="%s" style="max-width:100%%;"/></p>' % html.escape(images[image_index], quote=True))
        image_index += 1
    return "".join(blocks)[:20000]


@router.post("/api/wechat-oa/draft", summary="把一篇文章发送到我的公众号草稿箱")
def send_wechat_oa_draft(
    body: WechatOaDraftBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = online_user_for_mobile_user(db, current_user)
    row = _load_config(db, int(user.id))
    if row is None or not row.app_id or not row.app_secret_enc:
        raise HTTPException(status_code=400, detail="请先在「我的 → 公众号配置」填写 AppID/AppSecret")
    app_secret = integration_secrets.decrypt(row.app_secret_enc)
    if not app_secret:
        raise HTTPException(status_code=400, detail="公众号 AppSecret 解不开，请重新配置")
    token = _get_access_token(row.app_id, app_secret, force=True)
    images: list[str] = []
    seen: set[str] = set()
    for url in list(body.images or []) + _extract_content_images(body.content):
        clean = str(url or "").strip()
        if clean.startswith(("http://", "https://")) and clean not in seen:
            seen.add(clean)
            images.append(clean)
        if len(images) >= 10:
            break
    if not images:
        raise HTTPException(status_code=400, detail="文章没有配图，公众号草稿需要一张封面图")
    first_bytes, first_type, first_name = _download_image(images[0])
    thumb_media_id = _upload_cover_material(token, first_bytes, first_name, first_type)
    content_images: list[str] = []
    for index, url in enumerate(images):
        if index == 0:
            data, content_type, filename = first_bytes, first_type, first_name
        else:
            data, content_type, filename = _download_image(url)
        content_images.append(_upload_content_image(token, data, filename, content_type))
    content_text = _clean_content_text(body.content)
    article = {
        "title": str(body.title or "").strip()[:64],
        "author": str(body.author or "").strip()[:8],
        "digest": str(body.digest or "").strip()[:120],
        "content": _build_content_html(content_text, content_images),
        "content_source_url": str(body.source_url or "").strip()[:1024],
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(
                _WECHAT_API + "/cgi-bin/draft/add",
                params={"access_token": token},
                json={"articles": [article]},
            )
        payload = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="发送公众号草稿失败：%s" % str(exc)[:160])
    media_id = str(payload.get("media_id") or "").strip()
    if not media_id:
        raise HTTPException(status_code=400, detail=_wechat_error(payload, action="发送公众号草稿"))
    logger.info("[wechat-oa] draft created user_id=%s media_id=%s images=%s", user.id, media_id, len(content_images))
    return {
        "ok": True,
        "media_id": media_id,
        "image_count": len(content_images),
        "nickname": row.nickname or "",
    }
