"""画布自有数据：会话 / 项目（作品）/ 模板 / 资产 / 上传 —— 全部由我们自己的服务器实现。

为什么自建（2026-09-29）：
- apiz 的用户数据接口（api/v1/projects/my、api/get_file_list…）用服务器的共享速推 key
  调不通（apiz 直接 500），而且放行会让不同用户互相看到对方的作品与上传；
- 按「速推 key 只用来调用生成」的口径：生成类 → apiz；其余 → 这里自己实现。

存储：同一个库里的 canvas_project / canvas_asset 两张表（首次用到时 CREATE TABLE IF NOT EXISTS），
上传文件落在 data/canvas_uploads/，通过 /canvas-api/media/... 对外提供。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
import mimetypes
import re
import time
import uuid as uuidlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter()

# 上传产物对外的公开只读前缀（apiz 生成时要能直接取图，所以不能挂在需要登录的画布接口下）
PUBLIC_MEDIA_PREFIX = "/canvas-media"

ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = ROOT / "data" / "canvas_uploads"

_tables_ready = False

from sqlalchemy import Column, Float, Integer, String, Table, Text

from ..db import Base

canvas_project_table = Table(
    "canvas_project",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("uuid", String(64), nullable=False, unique=True),
    Column("user_id", Integer, nullable=False, default=0),
    Column("name", String(200), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("is_public", Integer, nullable=False, default=0),
    Column("snapshot", Text),
    Column("thumbnail_url", Text, nullable=False, default=""),
    Column("source_url", Text, nullable=False, default=""),
    Column("sort", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False, default=0.0),
    Column("updated_at", Float, nullable=False, default=0.0),
)

canvas_draft_template_table = Table(
    "canvas_draft_template",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, nullable=False, unique=True),
    Column("title", String(255), nullable=False, default=""),
    Column("img_url", Text, nullable=False, default=""),
    Column("video_url", Text, nullable=False, default=""),
    Column("url", Text, nullable=False, default=""),
    Column("user_name", String(128), nullable=False, default=""),
    Column("sort", Integer, nullable=False, default=0),
    Column("synced_at", Float, nullable=False, default=0.0),
)

canvas_task_table = Table(
    "canvas_task",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("model", String(128), nullable=False, default=""),
    Column("path", String(255), nullable=False, default=""),
    Column("status", String(32), nullable=False, default="submitted"),
    Column("task_id", String(128), nullable=False, default=""),
    Column("result_url", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False, default=0.0),
    Column("updated_at", Float, nullable=False, default=0.0),
)

canvas_remote_cache_table = Table(
    "canvas_remote_cache",
    Base.metadata,
    Column("cache_key", String(128), primary_key=True),
    Column("payload", Text, nullable=False, default=""),
    Column("synced_at", Float, nullable=False, default=0.0),
)

canvas_asset_table = Table(
    "canvas_asset",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("url", Text, nullable=False),
    Column("file_type", String(128), nullable=False, default=""),
    Column("file_size", Integer, nullable=False, default=0),
    Column("name", String(255), nullable=False, default=""),
    Column("created_at", Float, nullable=False, default=0.0),
)


def ensure_tables(db: Session) -> None:
    global _tables_ready
    if _tables_ready:
        return
    Base.metadata.create_all(bind=db.get_bind(),
                             tables=[canvas_project_table, canvas_asset_table, canvas_draft_template_table,
                                    canvas_remote_cache_table, canvas_task_table])
    _tables_ready = True


def public_base() -> str:
    from ..core.config import settings

    for key in ("public_base_url", "lan_public_base_url", "lobster_domestic_server_base"):
        value = str(getattr(settings, key, "") or "").strip().rstrip("/")
        if value and "127.0.0.1" not in value and "localhost" not in value:
            return value
    return "https://bhzn.top"


def snapshot_as_dict(value: Any) -> Dict[str, Any]:
    """把 snapshot 统一成 dict（最多拆两层 JSON 文本）。

    2026-10-01 踩过的坑：克隆时把数据库里的 snapshot（JSON 文本）又 json.dumps 了一次，
    副本里就成了「字符串里再套一层 JSON」。画布前端拿到的 snapshot 不是对象、读不到 nodes，
    于是渲染默认画布并自动保存 —— 表现就是「复制后只剩一组、每个副本内容都一样」。
    写入和读取都过这个函数，保证只存一层。
    """
    for _ in range(2):
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                value = json.loads(text)
            except Exception:
                return {}
            continue
        break
    return value if isinstance(value, dict) else {}


def _now() -> float:
    return time.time()


async def apiz_json(method: str, path: str, body: Optional[Dict[str, Any]] = None,
                    *, timeout: float = 40.0) -> Dict[str, Any]:
    """用服务器的速推 key 池调 apiz（只用于「生成」和「公开内容」拉取）。"""
    import httpx

    from mcp.sutui_tokens import next_sutui_server_token_with_pool

    token, pool = await next_sutui_server_token_with_pool()
    if not token:
        raise RuntimeError(f"速推 key 未配置（pool={pool or 'none'}）")
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        resp = await client.request(method, f"https://api.apiz.ai{path}", json=body,
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        return resp.json()
    except Exception:
        return {"code": resp.status_code, "raw": resp.text[:500]}


_TEMPLATE_SYNC_TTL = 60 * 30
_template_sync_at = 0.0
TEMPLATE_SEED_USER_ID = 0


async def sync_public_templates(db: Session, *, limit: int = 100, force: bool = False) -> int:
    """把 apiz 的公开作品同步进 canvas_project（user_id=0 表示官方模板）。失败不影响首页。"""
    global _template_sync_at
    now = time.time()
    if not force and _template_sync_at and (now - _template_sync_at) < _TEMPLATE_SYNC_TTL:
        return 0
    try:
        payload = await apiz_json("POST", "/api/v1/projects/public", {"skip": 0, "limit": limit})
    except Exception as exc:
        logger.info("[canvas] 同步公开模板失败，继续用库里已有的: %s", exc)
        return 0
    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list):
        return 0
    saved = 0
    for item in projects:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("uuid") or item.get("id") or "").strip()
        if not pid:
            continue
        row = db.execute(text("SELECT id FROM canvas_project WHERE uuid = :u"), {"u": pid}).fetchone()
        params = {
            "u": pid,
            "name": str(item.get("name") or "官方模板")[:200],
            "descr": str(item.get("description") or "")[:1000],
            "pub": 1 if item.get("is_public") else 0,
            "thumb": str(item.get("thumbnail_url") or item.get("cover_url") or "")[:1000],
            "src": str(item.get("canvas_url") or "")[:1000],
            "sort": int(item.get("sort") or 9999),
            "now": now,
        }
        if row is None:
            db.execute(
                text("INSERT INTO canvas_project (uuid, user_id, name, description, is_public, snapshot,"
                     " thumbnail_url, source_url, sort, created_at, updated_at)"
                     " VALUES (:u, %d, :name, :descr, :pub, NULL, :thumb, :src, :sort, :now, :now)"
                     % TEMPLATE_SEED_USER_ID), params)
        else:
            db.execute(text("UPDATE canvas_project SET name = :name, description = :descr, is_public = :pub,"
                            " thumbnail_url = :thumb, source_url = :src, sort = :sort, updated_at = :now"
                            " WHERE uuid = :u"), params)
        saved += 1
    db.commit()
    _template_sync_at = now
    logger.info("[canvas] 公开模板已同步进我们的库：%d 条", saved)
    return saved


_draft_sync_at = 0.0


async def sync_draft_templates(db: Session, *, limit: int = 60, force: bool = False) -> int:
    """Coze 草稿模板也拉进我们自己的库（公开内容，TTL 30 分钟）。"""
    global _draft_sync_at
    now = time.time()
    if not force and _draft_sync_at and (now - _draft_sync_at) < _TEMPLATE_SYNC_TTL:
        return 0
    try:
        payload = await apiz_json("POST", "/api/get_draft_template", {"page": 1, "page_size": limit})
    except Exception as exc:
        logger.info("[canvas] 同步草稿模板失败，继续用库里已有的: %s", exc)
        return 0
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return 0
    saved = 0
    for item in rows:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        params = {
            "sid": int(item.get("id")),
            "title": str(item.get("title") or "")[:255],
            "img": str(item.get("img_url") or ""),
            "video": str(item.get("video_url") or ""),
            "url": str(item.get("url") or ""),
            "user_name": str(item.get("user_name") or "")[:128],
            "sort": int(item.get("sort") or 0),
            "now": now,
        }
        exists = db.execute(text("SELECT id FROM canvas_draft_template WHERE source_id = :sid"), params).fetchone()
        if exists is None:
            db.execute(text("INSERT INTO canvas_draft_template (source_id, title, img_url, video_url, url,"
                            " user_name, sort, synced_at) VALUES (:sid, :title, :img, :video, :url, :user_name,"
                            " :sort, :now)"), params)
        else:
            db.execute(text("UPDATE canvas_draft_template SET title = :title, img_url = :img, video_url = :video,"
                            " url = :url, user_name = :user_name, sort = :sort, synced_at = :now"
                            " WHERE source_id = :sid"), params)
        saved += 1
    db.commit()
    _draft_sync_at = now
    logger.info("[canvas] 草稿模板已入我们的库：%d 条", saved)
    return saved


def list_draft_templates(db: Session, limit: int = 60) -> List[Dict[str, Any]]:
    rows = db.execute(text("SELECT * FROM canvas_draft_template ORDER BY sort DESC, id DESC LIMIT :limit"),
                      {"limit": max(1, min(200, limit))}).fetchall()
    return [{"id": r.source_id, "title": r.title, "img_url": r.img_url, "video_url": r.video_url,
             "url": r.url, "user_name": r.user_name, "sort": r.sort, "status": 1,
             "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.synced_at or 0))} for r in rows]


def _has_canvas(snapshot: Any) -> bool:
    """库里有没有真正的画布内容（空对象/空串都算没有）。"""
    text = snapshot if isinstance(snapshot, str) else json.dumps(snapshot or {}, ensure_ascii=False)
    text = (text or "").strip()
    return bool(text) and text not in ("{}", "null")


def project_row_to_json(row: Any, *, with_snapshot: bool = False) -> Dict[str, Any]:
    # 画布前端（canvas-web bundle）只在 canvas_url 非空时才去调 canvas/load：
    #   if (a.canvas_url) { A = (await loadDrawCanvas(token, n)).snapshot }
    #   if (!A.nodes.length) { A = 默认画布(一组 3 个节点) }
    # 所以自有作品/副本必须给一个非空 canvas_url，否则编辑器永远用默认画布，
    # 再自动保存就把真内容覆盖掉（用户报的「每个画布进去都是同一个内容、只剩一组」）。
    source_url = getattr(row, "source_url", "") or ""
    canvas_url = source_url or (
        f"/canvas-api/api/v1/projects/{row.uuid}/canvas/load" if _has_canvas(row.snapshot) else ""
    )
    data = {
        "id": row.id,
        "uuid": row.uuid,
        "user_id": row.user_id,
        "name": row.name or "",
        "description": row.description or "",
        "is_public": bool(row.is_public),
        "thumbnail_url": row.thumbnail_url or "",
        "cover": row.thumbnail_url or "",
        "cover_url": row.thumbnail_url or "",
        "canvas_url": canvas_url,
        "is_template": int(row.user_id or 0) == TEMPLATE_SEED_USER_ID,
        "sort": row.sort,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if with_snapshot:
        # 兜底拆包：即使历史数据里存的是「字符串套 JSON」，也要能读出真正的画布
        snapshot = snapshot_as_dict(row.snapshot)
        data["snapshot"] = snapshot
        data["canvas"] = snapshot
    return data


def _identifier_clause(identifier: str) -> Tuple[str, Dict[str, Any]]:
    ident = str(identifier or "").strip()
    if ident.isdigit():
        return "(uuid = :ident OR id = :num)", {"ident": ident, "num": int(ident)}
    return "uuid = :ident", {"ident": ident}


def list_projects(db: Session, *, user_id: Optional[int], only_public: bool, skip: int, limit: int,
                  keyword: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM canvas_project WHERE 1=1"
    params: Dict[str, Any] = {"skip": max(0, skip), "limit": max(1, min(200, limit or 50))}
    if user_id is not None:
        sql += " AND user_id = :uid"
        params["uid"] = user_id
    if only_public:
        sql += " AND is_public = 1"
    if keyword:
        sql += " AND (name LIKE :kw OR description LIKE :kw)"
        params["kw"] = f"%{keyword}%"
    sql += " ORDER BY sort DESC, updated_at DESC LIMIT :limit OFFSET :skip"
    rows = db.execute(text(sql), params).fetchall()
    return [project_row_to_json(row) for row in rows]


def create_project(db: Session, *, user_id: int, name: str, description: str, is_public: bool) -> Dict[str, Any]:
    uid = uuidlib.uuid4().hex
    now = _now()
    db.execute(
        text(
            "INSERT INTO canvas_project (uuid, user_id, name, description, is_public, snapshot,"
            " thumbnail_url, source_url, sort, created_at, updated_at)"
            " VALUES (:uuid, :uid, :name, :descr, :pub, NULL, '', '', 0, :now, :now)"
        ),
        {"uuid": uid, "uid": user_id, "name": name[:200] or "未命名作品", "descr": description[:1000],
         "pub": 1 if is_public else 0, "now": now},
    )
    db.commit()
    row = db.execute(text("SELECT * FROM canvas_project WHERE uuid = :u"), {"u": uid}).fetchone()
    return project_row_to_json(row)


async def ensure_snapshot(db: Session, row: Any, user_id: int) -> Any:
    """官方模板：库里没快照时按 canvas_url 拉一次并缓存进我们库。"""
    if row is None or (row.snapshot if isinstance(row.snapshot, str) else None):
        return row
    source = getattr(row, "source_url", "") or ""
    if not source or int(row.user_id or 0) != TEMPLATE_SEED_USER_ID:
        return row
    import httpx

    try:
        async with httpx.AsyncClient(timeout=40.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(source)
        if resp.status_code < 400:
            snapshot = resp.json()
            clause, params = _identifier_clause(str(row.uuid))
            params["snap"] = json.dumps(snapshot_as_dict(snapshot), ensure_ascii=False)
            db.execute(text(f"UPDATE canvas_project SET snapshot = :snap WHERE {clause}"), params)
            db.commit()
            return db.execute(text(f"SELECT * FROM canvas_project WHERE {clause}"), params).fetchone()
    except Exception as exc:
        logger.info("[canvas] 拉模板快照失败 %s: %s", row.uuid, exc)
    return row


def get_project(db: Session, identifier: str, *, user_id: Optional[int] = None) -> Optional[Any]:
    clause, params = _identifier_clause(identifier)
    sql = f"SELECT * FROM canvas_project WHERE {clause}"
    if user_id is not None:
        sql += " AND (user_id = :uid OR is_public = 1)"
        params["uid"] = user_id
    return db.execute(text(sql), params).fetchone()


def update_project(db: Session, identifier: str, user_id: int, fields: Dict[str, Any]) -> Optional[Any]:
    clause, params = _identifier_clause(identifier)
    params["uid"] = user_id
    sets: List[str] = ["updated_at = :now"]
    params["now"] = _now()
    if "name" in fields and fields["name"] is not None:
        sets.append("name = :name")
        params["name"] = str(fields["name"])[:200]
    if "description" in fields and fields["description"] is not None:
        sets.append("description = :descr")
        params["descr"] = str(fields["description"])[:1000]
    if "is_public" in fields and fields["is_public"] is not None:
        sets.append("is_public = :pub")
        params["pub"] = 1 if fields["is_public"] else 0
    if fields.get("thumbnail_url"):
        sets.append("thumbnail_url = :thumb")
        params["thumb"] = str(fields["thumbnail_url"])[:1000]
    db.execute(text(f"UPDATE canvas_project SET {', '.join(sets)} WHERE {clause} AND user_id = :uid"), params)
    db.commit()
    return db.execute(text(f"SELECT * FROM canvas_project WHERE {clause}"), params).fetchone()


def save_snapshot(db: Session, identifier: str, user_id: int, snapshot: Any, thumbnail_url: str = "") -> Optional[Any]:
    clause, params = _identifier_clause(identifier)
    # snapshot 可能是 dict，也可能是数据库里的 JSON 文本（克隆/转发过来的），统一拆成一层
    params.update({"uid": user_id, "snap": json.dumps(snapshot_as_dict(snapshot), ensure_ascii=False), "now": _now()})
    sets = "snapshot = :snap, updated_at = :now"
    if thumbnail_url:
        sets += ", thumbnail_url = :thumb"
        params["thumb"] = str(thumbnail_url)[:1000]
    db.execute(text(f"UPDATE canvas_project SET {sets} WHERE {clause} AND user_id = :uid"), params)
    db.commit()
    return db.execute(text(f"SELECT * FROM canvas_project WHERE {clause}"), params).fetchone()


def delete_project(db: Session, identifier: str, user_id: int) -> int:
    clause, params = _identifier_clause(identifier)
    params["uid"] = user_id
    result = db.execute(text(f"DELETE FROM canvas_project WHERE {clause} AND user_id = :uid"), params)
    db.commit()
    return int(result.rowcount or 0)


def list_assets(db: Session, user_id: int, skip: int, limit: int) -> List[Dict[str, Any]]:
    rows = db.execute(
        text("SELECT * FROM canvas_asset WHERE user_id = :uid ORDER BY created_at DESC LIMIT :limit OFFSET :skip"),
        {"uid": user_id, "limit": max(1, min(200, limit or 20)), "skip": max(0, skip)},
    ).fetchall()
    return [
        {"id": r.id, "file_url": r.url, "url": r.url, "file_type": r.file_type, "type": r.file_type,
         "file_size": r.file_size, "size": r.file_size, "name": r.name, "created_at": r.created_at}
        for r in rows
    ]


def add_asset(db: Session, user_id: int, url: str, file_type: str, file_size: int, name: str = "") -> Dict[str, Any]:
    db.execute(
        text("INSERT INTO canvas_asset (user_id, url, file_type, file_size, name, created_at)"
             " VALUES (:uid, :url, :ft, :fs, :name, :now)"),
        {"uid": user_id, "url": url, "ft": file_type or "", "fs": int(file_size or 0), "name": name or "", "now": _now()},
    )
    db.commit()
    row = db.execute(text("SELECT * FROM canvas_asset WHERE user_id = :uid ORDER BY id DESC LIMIT 1"),
                     {"uid": user_id}).fetchone()
    return {"id": row.id, "file_url": row.url, "url": row.url, "file_type": row.file_type,
            "file_size": row.file_size, "name": row.name, "created_at": row.created_at}


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def store_upload(user_id: int, filename: str, content_type: str, data: bytes) -> str:
    """把上传落盘，返回可对外访问的绝对 URL（这个 URL 会喂给生成接口）。"""
    suffix = Path(filename or "").suffix[:10] or (mimetypes.guess_extension(content_type or "") or ".bin")
    name = str(filename or "file")
    if not name.lower().endswith(suffix.lower()):
        name = f"{name}{suffix}"
    return store_upload_key(new_upload_key(user_id, name), data)


_REMOTE_MEDIA_HOSTS = {"cdn-video.51sux.com", "cdn-hk.51sux.com", "cdn-ali-hk.51sux.com",
                       "st-video.cc", "videos-jp.ss3.life"}


async def remote_media_response(host: str, path: str, query: str = "") -> Response:
    """素材 CDN 由我们服务器中转：前端不再直连外部（白名单限定这几个展示素材域名）。"""
    import httpx
    from fastapi.responses import Response as _Response

    if host not in _REMOTE_MEDIA_HOSTS:
        raise HTTPException(status_code=404, detail="不允许的外部素材来源")
    url = f"https://{host}/{path}"
    if query:
        url += "?" + query
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"取素材失败：{exc}") from exc
    return _Response(content=resp.content, status_code=resp.status_code,
                     media_type=resp.headers.get("content-type") or "application/octet-stream",
                     headers={"Cache-Control": "public, max-age=86400"})


@router.get(PUBLIC_MEDIA_PREFIX + "/{rel:path}", include_in_schema=False)
def public_canvas_media(rel: str) -> FileResponse:
    """公开只读：给 apiz 拉参考图/上传产物用（带随机 key，不含用户隐私列表）。"""
    return media_response(rel)


def add_canvas_task(db: Session, user_id: int, model: str, path: str, response_body: bytes = b"") -> None:
    """生成下单时记一条我们的任务记录（画布「任务记录/最近任务」读这里）。"""
    task_id = ""
    result_url = ""
    try:
        payload = json.loads(response_body.decode("utf-8", "replace")) if response_body else {}
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            task_id = str(data.get("task_id") or data.get("id") or "")
            for key in ("url", "video_url", "image_url", "output", "result_url"):
                if isinstance(data.get(key), str) and data.get(key):
                    result_url = data[key]
                    break
    except Exception:
        pass
    now = time.time()
    db.execute(text("INSERT INTO canvas_task (user_id, model, path, status, task_id, result_url,"
                    " created_at, updated_at) VALUES (:uid, :model, :path, 'submitted', :tid, :url, :now, :now)"),
               {"uid": user_id, "model": model, "path": path, "tid": task_id, "url": result_url, "now": now})
    if result_url:
        db.execute(text("INSERT INTO canvas_asset (user_id, url, file_type, file_size, name, created_at)"
                        " VALUES (:uid, :url, '', 0, :name, :now)"),
                   {"uid": user_id, "url": result_url, "name": (model or "canvas") + " 生成", "now": now})
    db.commit()


def register_content_record(db: Session, user_id: int, url: str, *, media_type: str = "image",
                            title: str = "", task_id: str = "", model: str = "",
                            extra: Optional[Dict[str, Any]] = None) -> bool:
    """生成产物写入「内容记录」（user_content_records）。同一任务幂等。"""
    if not url or not url.startswith("http"):
        return False
    from ..models import UserContentRecord

    source_id = str(task_id or url)[:128]
    try:
        row = (
            db.query(UserContentRecord)
            .filter(UserContentRecord.user_id == user_id,
                    UserContentRecord.source == "canvas",
                    UserContentRecord.source_id == source_id)
            .first()
        )
        meta = {"model": model, "media_type": media_type, "url": url}
        meta.update(extra or {})
        if row is None:
            row = UserContentRecord(
                user_id=user_id,
                source="canvas",
                source_id=source_id,
                kind=("video" if media_type == "video" else "image"),
                title=(title or "画布生成")[:500],
                summary=(title or "")[:180] or None,
                cover_url=(url if media_type != "video" else None),
                file_url=url,
                status="completed",
                meta=meta,
                source_created_at=datetime.utcnow(),
            )
            db.add(row)
        else:
            row.file_url = url
            row.status = "completed"
            row.meta = meta
        db.commit()
        logger.info("[canvas] 生成内容已入内容记录: uid=%s kind=%s %s", user_id, media_type, url[:70])
        return True
    except Exception as exc:  # noqa: BLE001 记内容失败不影响生成
        logger.warning("[canvas] 写入内容记录失败: %s", exc, exc_info=True)
        return False


def _extract_result_url(payload: Any) -> str:
    """从上游响应里挖出产物地址（各家字段名不一样）。"""
    if isinstance(payload, str):
        return payload if payload.startswith("http") else ""
    if isinstance(payload, dict):
        for key in ("video_url", "image_url", "url", "output", "result_url", "file_url"):
            value = payload.get(key)
            found = _extract_result_url(value)
            if found:
                return found
        for value in payload.values():
            found = _extract_result_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_result_url(item)
            if found:
                return found
    return ""


def register_generated_asset(db: Session, user_id: int, url: str, media_type: str = "image",
                             name: str = "", task_id: str = "", status: str = "") -> bool:
    """生成产物入我们的库：canvas_asset + 客户端内容库（assets 表）。返回是否新登记。"""
    if not url or not url.startswith("http"):
        return False
    now = time.time()
    # 注意：素材库只放「用户上传」的东西；生成产物属于「内容记录」，不写 canvas_asset
    if task_id:
        db.execute(text("UPDATE canvas_task SET status = :st, result_url = :url, updated_at = :now"
                        " WHERE task_id = :tid"), {"st": status or "completed", "url": url, "now": now, "tid": task_id})
    db.commit()
    # TODO(下一步)：写入服务器的内容记录（user_content_records，走 api/content_records 的同步逻辑）
    return True


def list_canvas_tasks(db: Session, user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    rows = db.execute(text("SELECT * FROM canvas_task WHERE user_id = :uid ORDER BY id DESC LIMIT :limit"),
                      {"uid": user_id, "limit": max(1, min(100, limit))}).fetchall()
    return [{"id": r.id, "task_id": r.task_id, "app_name": r.model, "model": r.model, "status": r.status,
             "url": r.result_url, "video_url": r.result_url, "image_url": r.result_url,
             "created_at": r.created_at, "updated_at": r.updated_at,
             "created_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.created_at or 0))}
            for r in rows]


async def cached_remote_json(db: Session, cache_key: str, path: str, body: Optional[Dict[str, Any]] = None,
                            *, method: str = "GET", ttl: int = 1800, force: bool = False) -> Any:
    """把 apiz 的公开目录类数据缓存到我们自己的库里，按 TTL 刷新；刷新失败用旧值。"""
    now = time.time()
    row = db.execute(text("SELECT payload, synced_at FROM canvas_remote_cache WHERE cache_key = :k"),
                     {"k": cache_key}).fetchone()
    if row and not force and (now - float(row.synced_at or 0)) < ttl:
        try:
            return json.loads(row.payload)
        except Exception:
            pass
    try:
        payload = await apiz_json(method, path, body)
    except Exception as exc:
        logger.info("[canvas] 拉取 %s 失败，用库里缓存: %s", path, exc)
        if row:
            try:
                return json.loads(row.payload)
            except Exception:
                pass
        return None
    dumped = json.dumps(payload, ensure_ascii=False)
    if row:
        db.execute(text("UPDATE canvas_remote_cache SET payload = :p, synced_at = :t WHERE cache_key = :k"),
                   {"p": dumped, "t": now, "k": cache_key})
    else:
        db.execute(text("INSERT INTO canvas_remote_cache (cache_key, payload, synced_at) VALUES (:k, :p, :t)"),
                   {"k": cache_key, "p": dumped, "t": now})
    db.commit()
    return payload


def new_upload_key(user_id: int, filename: str) -> str:
    suffix = Path(filename or "").suffix[:10] or ".bin"
    safe = _SAFE_NAME.sub("_", Path(filename or "").stem)[:40] or "file"
    return f"{user_id}/{uuidlib.uuid4().hex[:16]}_{safe}{suffix}"


def store_upload_key(key: str, data: bytes) -> str:
    """按给定 key 落盘（key 由我们自己签发），返回公开可取的绝对 URL。"""
    root = UPLOAD_DIR.resolve()
    parts = [part for part in str(key or "").replace(chr(92), "/").split("/") if part]
    if len(parts) < 2 or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="上传 key 不合法")
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return f"{public_base()}{PUBLIC_MEDIA_PREFIX}/" + "/".join(parts)


def resolve_media(rel: str) -> Path:
    root = UPLOAD_DIR.resolve()
    parts = [part for part in str(rel or "").replace(chr(92), "/").split("/") if part]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=404, detail="文件不存在")
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在") from None
    if target.is_file():
        return target
    # 容错：历史链接可能丢了扩展名/后半段（画布上传时名字被截断）——
    # 按前缀在同一个目录里找唯一匹配（例如 31/xxx_abc 命中 31/xxx_abcd.mp4）
    parent = target.parent
    if parent.is_dir():
        matches = sorted(p for p in parent.iterdir() if p.is_file() and p.name.startswith(target.name))
        if len(matches) == 1:
            logger.info("[canvas] 素材按前缀命中: %s -> %s", target.name, matches[0].name)
            return matches[0]
    raise HTTPException(status_code=404, detail="文件不存在")


def media_response(rel: str) -> FileResponse:
    target = resolve_media(rel)
    return FileResponse(str(target), headers={"Cache-Control": "public, max-age=86400"})


async def handle_upload(request: Request, user_id: int) -> Dict[str, Any]:
    """兼容两种上传：multipart 表单，或直接 PUT/POST 原始字节（预签名 URL 那种）。"""
    content_type = request.headers.get("content-type") or ""
    filename = request.query_params.get("name") or request.query_params.get("filename") or ""
    key = request.query_params.get("key") or ""
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload: Optional[UploadFile] = None
        for value in form.values():
            if isinstance(value, UploadFile) or hasattr(value, "read"):
                upload = value  # type: ignore[assignment]
                break
        if upload is None:
            raise HTTPException(status_code=400, detail="没有收到文件")
        filename = filename or getattr(upload, "filename", "") or "file"
        data = await upload.read()
        content_type = getattr(upload, "content_type", "") or content_type
    else:
        data = await request.body()
        content_type = content_type or "application/octet-stream"
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")
    if key:
        url = store_upload_key(key, data)
    else:
        url = store_upload(user_id, filename or "file", content_type, data)
    return {"url": url, "file_url": url, "name": filename or "file", "file_type": content_type,
            "file_size": len(data)}
