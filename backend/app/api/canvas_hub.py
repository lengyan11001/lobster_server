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
import mimetypes
import re
import time
import uuid as uuidlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = ROOT / "data" / "canvas_uploads"

_tables_ready = False

_PROJECT_DDL = """
CREATE TABLE IF NOT EXISTS canvas_project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    is_public INTEGER NOT NULL DEFAULT 0,
    snapshot TEXT,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_ASSET_DDL = """
CREATE TABLE IF NOT EXISTS canvas_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
)
"""


def ensure_tables(db: Session) -> None:
    global _tables_ready
    if _tables_ready:
        return
    db.execute(text(_PROJECT_DDL))
    db.execute(text(_ASSET_DDL))
    db.commit()
    _tables_ready = True


def public_base() -> str:
    from ..core.config import settings

    for key in ("public_base_url", "lan_public_base_url", "lobster_domestic_server_base"):
        value = str(getattr(settings, key, "") or "").strip().rstrip("/")
        if value and "127.0.0.1" not in value and "localhost" not in value:
            return value
    return "https://bhzn.top"


def _now() -> float:
    return time.time()


def project_row_to_json(row: Any, *, with_snapshot: bool = False) -> Dict[str, Any]:
    data = {
        "id": row.id,
        "uuid": row.uuid,
        "user_id": row.user_id,
        "name": row.name or "",
        "description": row.description or "",
        "is_public": bool(row.is_public),
        "thumbnail_url": row.thumbnail_url or "",
        "cover": row.thumbnail_url or "",
        "sort": row.sort,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if with_snapshot:
        snapshot = row.snapshot
        if isinstance(snapshot, str) and snapshot:
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = None
        data["snapshot"] = snapshot or {}
        data["canvas"] = snapshot or {}
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
            " thumbnail_url, sort, created_at, updated_at)"
            " VALUES (:uuid, :uid, :name, :descr, :pub, NULL, '', 0, :now, :now)"
        ),
        {"uuid": uid, "uid": user_id, "name": name[:200] or "未命名作品", "descr": description[:1000],
         "pub": 1 if is_public else 0, "now": now},
    )
    db.commit()
    row = db.execute(text("SELECT * FROM canvas_project WHERE uuid = :u"), {"u": uid}).fetchone()
    return project_row_to_json(row)


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
    params.update({"uid": user_id, "snap": json.dumps(snapshot or {}, ensure_ascii=False), "now": _now()})
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
    target_dir = UPLOAD_DIR / str(user_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = _SAFE_NAME.sub("_", Path(filename or "").stem)[:40] or "file"
    name = f"{int(time.time())}_{uuidlib.uuid4().hex[:8]}_{safe}{suffix}"
    (target_dir / name).write_bytes(data)
    return f"{public_base()}/canvas-api/media/{user_id}/{name}"


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
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def media_response(rel: str) -> FileResponse:
    target = resolve_media(rel)
    return FileResponse(str(target), headers={"Cache-Control": "public, max-age=86400"})


async def handle_upload(request: Request, user_id: int) -> Dict[str, Any]:
    """兼容两种上传：multipart 表单，或直接 PUT/POST 原始字节（预签名 URL 那种）。"""
    content_type = request.headers.get("content-type") or ""
    filename = request.query_params.get("name") or request.query_params.get("filename") or ""
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
    url = store_upload(user_id, filename or "file", content_type, data)
    return {"url": url, "file_url": url, "name": filename or "file", "file_type": content_type,
            "file_size": len(data)}
