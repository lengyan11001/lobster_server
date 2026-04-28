"""Cloud distribution APIs for OpenClaw memory documents.

The online client usually runs without a public IP. Agents/admins upload docs to
this server for one user installation, and the client pulls them during sync.
"""
from __future__ import annotations

import html
import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OpenClawMemoryDocument, User, UserInstallation
from .admin import AdminContext, _agent_sub_user_ids, _require_admin, _verify_admin_token
from .auth import get_current_user
from .installation_slots import (
    INSTALLATION_ID_HEADER,
    ensure_installation_slot,
    optional_installation_id_from_request,
    parse_installation_id_strict,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 30 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 500_000
_ALLOWED_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".log",
}
_ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xlsx", ".xlsm", ".xls", ".pptx"}
_SUPPORTED_SUFFIXES = _ALLOWED_TEXT_SUFFIXES | _ALLOWED_DOCUMENT_SUFFIXES


class SetAgentOpenClawMemoryBody(BaseModel):
    user_id: int
    enabled: bool


class UserMemoryMirrorBody(BaseModel):
    doc_id: str
    title: str = ""
    filename: str = "document.txt"
    notes: str = ""
    size: Optional[int] = None
    sha256: Optional[str] = None
    content_text: str


def _normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _limit_text(text: str) -> str:
    text = _normalize_text(text)
    if len(text) <= _MAX_EXTRACTED_CHARS:
        return text
    return text[:_MAX_EXTRACTED_CHARS].rstrip() + "\n\n[系统提示] 原文件文本过长，后续内容已截断。"


def _zip_xml_text(data: bytes, names: list[str]) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in names:
            if name not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(name))
            buf: list[str] = []
            for elem in root.iter():
                tag = elem.tag.rsplit("}", 1)[-1]
                if tag == "t" and elem.text:
                    buf.append(elem.text)
                elif tag == "tab":
                    buf.append("\t")
                elif tag in {"br", "cr", "p", "tr"}:
                    buf.append("\n")
                elif tag == "tc":
                    buf.append("\t")
            chunk = "".join(buf).strip()
            if chunk:
                parts.append(chunk)
    return "\n\n".join(parts)


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = ["word/document.xml"]
        names.extend(
            sorted(n for n in zf.namelist() if re.match(r"word/(header|footer)\d+\.xml$", n))
        )
    return _zip_xml_text(data, names)


def _extract_pptx_text(data: bytes) -> str:
    slide_texts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
        for idx, name in enumerate(names, start=1):
            root = ET.fromstring(zf.read(name))
            texts = [
                elem.text
                for elem in root.iter()
                if elem.tag.rsplit("}", 1)[-1] == "t" and elem.text
            ]
            if texts:
                slide_texts.append(f"## Slide {idx}\n" + "\n".join(texts))
    return "\n\n".join(slide_texts)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root:
        texts = [elem.text or "" for elem in si.iter() if elem.tag.rsplit("}", 1)[-1] == "t"]
        out.append("".join(texts))
    return out


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> dict[str, str]:
    if "xl/workbook.xml" not in zf.namelist() or "xl/_rels/workbook.xml.rels" not in zf.namelist():
        return {}
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets: dict[str, str] = {}
    for rel in rels_root:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid and target:
            rel_targets[rid] = "xl/" + target.lstrip("/")
    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    names: dict[str, str] = {}
    for elem in wb_root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        rid = elem.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheet_name = elem.attrib.get("name") or rid or "Sheet"
        target = rel_targets.get(rid or "")
        if target:
            names[target] = sheet_name
    return names


def _extract_xlsx_text(data: bytes) -> str:
    sheets: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = _xlsx_sheet_names(zf)
        paths = sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        for idx, name in enumerate(paths, start=1):
            root = ET.fromstring(zf.read(name))
            rows: list[str] = []
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                vals: list[str] = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    cell_type = cell.attrib.get("t", "")
                    value = ""
                    if cell_type == "inlineStr":
                        texts = [
                            elem.text or ""
                            for elem in cell.iter()
                            if elem.tag.rsplit("}", 1)[-1] == "t"
                        ]
                        value = "".join(texts)
                    else:
                        v = next((elem for elem in cell if elem.tag.rsplit("}", 1)[-1] == "v"), None)
                        raw = (v.text or "") if v is not None else ""
                        if cell_type == "s":
                            try:
                                value = shared[int(raw)]
                            except Exception:
                                value = raw
                        else:
                            value = raw
                    vals.append(str(value).strip())
                if any(vals):
                    rows.append("\t".join(vals).rstrip())
            if rows:
                sheets.append(f"## {sheet_names.get(name, f'Sheet{idx}')}\n" + "\n".join(rows))
    return "\n\n".join(sheets)


def _extract_xls_text(data: bytes) -> str:
    try:
        import xlrd  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=400, detail="读取 .xls 需要安装 xlrd；建议另存为 .xlsx 后上传。") from exc
    book = xlrd.open_workbook(file_contents=data)
    chunks: list[str] = []
    for sheet in book.sheets():
        rows: list[str] = []
        for r in range(sheet.nrows):
            vals = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
            if any(vals):
                rows.append("\t".join(vals).rstrip())
        if rows:
            chunks.append(f"## {sheet.name}\n" + "\n".join(rows))
    return "\n\n".join(chunks)


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=400, detail="读取 PDF 需要安装 pypdf。") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [f"## Page {idx}\n{txt}" for idx, page in enumerate(reader.pages, start=1) if (txt := page.extract_text() or "").strip()]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 文本抽取失败：{exc}") from exc
    return "\n\n".join(pages)


def _decode_text_payload(data: bytes, filename: str) -> str:
    suffix = (filename and "." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in _SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="资料记忆支持 txt/md/csv/json/yaml/html、PDF、Word(docx)、Excel(xlsx/xls)、PPT(pptx) 等文件。",
        )
    text = ""
    if suffix in _ALLOWED_TEXT_SUFFIXES:
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            raise HTTPException(status_code=400, detail="文件内容无法按文本解析。")
        if suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except Exception:
                pass
        elif suffix in {".html", ".htm"}:
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
    elif suffix == ".pdf":
        text = _extract_pdf_text(data)
    elif suffix == ".docx":
        text = _extract_docx_text(data)
    elif suffix == ".doc":
        raise HTTPException(status_code=400, detail="旧版 .doc 暂不支持云端抽取；请另存为 .docx 或 PDF 后上传。")
    elif suffix in {".xlsx", ".xlsm"}:
        text = _extract_xlsx_text(data)
    elif suffix == ".xls":
        text = _extract_xls_text(data)
    elif suffix == ".pptx":
        text = _extract_pptx_text(data)
    text = _limit_text(text)
    if not text:
        raise HTTPException(status_code=400, detail="文件没有可写入记忆库的文本内容。")
    return text


def _short_title(raw: str, fallback: str) -> str:
    title = (raw or "").strip() or (fallback or "").strip() or "OpenClaw 资料"
    title = re.sub(r"[\x00-\x1f]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:120] or "OpenClaw 资料"


def _sanitize_doc_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "", (raw or "").strip())[:64]
    return s


def _doc_id_for(user_id: int, installation_id: str, filename: str, sha256: str, created_at: str) -> str:
    raw = f"{user_id}\0{installation_id}\0{filename}\0{sha256}\0{created_at}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:24]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _doc_summary(row: OpenClawMemoryDocument, *, include_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "doc_id": row.doc_id,
        "target_user_id": row.target_user_id,
        "installation_id": row.installation_id,
        "origin": row.origin,
        "uploader_user_id": row.uploader_user_id,
        "uploader_role": row.uploader_role,
        "title": row.title,
        "filename": row.filename,
        "notes": row.notes or "",
        "size": row.size,
        "sha256": row.sha256,
        "status": row.status,
        "meta": row.meta or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "deleted_at": _iso(row.deleted_at),
    }
    if include_content and row.status == "active":
        data["content_text"] = row.content_text
    else:
        data["content_preview"] = (row.content_text or "")[:160]
    return data


def _require_memory_operator(ctx: AdminContext, db: Session) -> None:
    if ctx.role == "admin":
        return
    user = db.query(User).filter(User.id == ctx.user_id).first()
    if not user or not user.is_agent:
        raise HTTPException(status_code=403, detail="代理商账号无效")
    if not bool(getattr(user, "agent_openclaw_memory_enabled", False)):
        raise HTTPException(status_code=403, detail="未开通 OpenClaw 资料记忆下发权限")


def _ensure_target_allowed(ctx: AdminContext, db: Session, target_user_id: int) -> User:
    target = db.query(User).filter(User.id == target_user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if ctx.role == "agent":
        sub_ids = _agent_sub_user_ids(db, ctx.user_id or 0)
        if target_user_id not in sub_ids:
            raise HTTPException(status_code=403, detail="无权操作此用户")
    return target


def _ensure_installation_for_user(db: Session, user_id: int, installation_id: str) -> None:
    iid = parse_installation_id_strict(installation_id)
    row = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == user_id, UserInstallation.installation_id == iid)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="该用户设备不存在或尚未登录过")


@router.post("/admin/api/set-agent-openclaw-memory")
def admin_set_agent_openclaw_memory(
    body: SetAgentOpenClawMemoryBody,
    ctx: AdminContext = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not user.is_agent and body.enabled:
        raise HTTPException(status_code=400, detail="请先将该用户设为代理商")
    user.agent_openclaw_memory_enabled = bool(body.enabled)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "ok": True,
        "user_id": user.id,
        "email": user.email,
        "agent_openclaw_memory_enabled": bool(user.agent_openclaw_memory_enabled),
    }


@router.get("/admin/api/openclaw-memory/installations/{user_id}")
def admin_openclaw_memory_installations(
    user_id: int,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _require_memory_operator(ctx, db)
    _ensure_target_allowed(ctx, db, user_id)
    rows = (
        db.query(UserInstallation)
        .filter(UserInstallation.user_id == user_id)
        .order_by(UserInstallation.last_seen_at.desc())
        .all()
    )
    counts = {
        iid: cnt
        for iid, cnt in (
            db.query(OpenClawMemoryDocument.installation_id, func.count(OpenClawMemoryDocument.id))
            .filter(
                OpenClawMemoryDocument.target_user_id == user_id,
                OpenClawMemoryDocument.status == "active",
            )
            .group_by(OpenClawMemoryDocument.installation_id)
            .all()
        )
    }
    return {
        "ok": True,
        "installations": [
            {
                "installation_id": r.installation_id,
                "created_at": _iso(r.created_at),
                "last_seen_at": _iso(r.last_seen_at),
                "active_documents": int(counts.get(r.installation_id, 0) or 0),
            }
            for r in rows
        ],
    }


@router.get("/admin/api/openclaw-memory/documents")
def admin_openclaw_memory_documents(
    target_user_id: int,
    installation_id: str = "",
    include_deleted: bool = False,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _require_memory_operator(ctx, db)
    _ensure_target_allowed(ctx, db, target_user_id)
    q = db.query(OpenClawMemoryDocument).filter(OpenClawMemoryDocument.target_user_id == target_user_id)
    iid = (installation_id or "").strip()
    if iid:
        q = q.filter(OpenClawMemoryDocument.installation_id == iid)
    if not include_deleted:
        q = q.filter(OpenClawMemoryDocument.status == "active")
    rows = q.order_by(OpenClawMemoryDocument.updated_at.desc()).limit(200).all()
    return {"ok": True, "documents": [_doc_summary(r) for r in rows]}


@router.post("/admin/api/openclaw-memory/upload")
async def admin_openclaw_memory_upload(
    target_user_id: int = Form(...),
    installation_id: str = Form(...),
    title: str = Form(""),
    notes: str = Form(""),
    file: UploadFile = File(...),
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _require_memory_operator(ctx, db)
    _ensure_target_allowed(ctx, db, target_user_id)
    iid = parse_installation_id_strict(installation_id)
    _ensure_installation_for_user(db, target_user_id, iid)
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 30MB，请拆分后上传")
    filename = (file.filename or "document.txt").strip() or "document.txt"
    text = _decode_text_payload(data, filename)
    sha256 = hashlib.sha256(data).hexdigest()
    now = datetime.utcnow()
    doc_id = _doc_id_for(target_user_id, iid, filename, sha256, now.isoformat())
    row = OpenClawMemoryDocument(
        doc_id=doc_id,
        target_user_id=target_user_id,
        installation_id=iid,
        origin="admin" if ctx.role == "admin" else "agent",
        uploader_user_id=ctx.user_id,
        uploader_role=ctx.role,
        title=_short_title(title, filename),
        filename=filename,
        notes=(notes or "").strip()[:1000],
        content_text=text,
        size=len(data),
        sha256=sha256,
        status="active",
        meta={"content_type": file.content_type or ""},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "[openclaw-memory-cloud] upload operator=%s/%s target_user_id=%s installation=%s doc_id=%s",
        ctx.role,
        ctx.user_id,
        target_user_id,
        iid[:24],
        doc_id,
    )
    return {"ok": True, "document": _doc_summary(row)}


@router.delete("/admin/api/openclaw-memory/documents/{doc_id}")
def admin_openclaw_memory_delete(
    doc_id: str,
    ctx: AdminContext = Depends(_verify_admin_token),
    db: Session = Depends(get_db),
):
    _require_memory_operator(ctx, db)
    clean = _sanitize_doc_id(doc_id)
    row = db.query(OpenClawMemoryDocument).filter(OpenClawMemoryDocument.doc_id == clean).first()
    if not row:
        raise HTTPException(status_code=404, detail="资料不存在")
    _ensure_target_allowed(ctx, db, row.target_user_id)
    if ctx.role == "agent" and row.origin == "admin":
        raise HTTPException(status_code=403, detail="代理商不能删除管理员下发的资料")
    row.status = "deleted"
    row.deleted_at = datetime.utcnow()
    row.updated_at = row.deleted_at
    db.add(row)
    db.commit()
    return {"ok": True, "deleted": row.doc_id}


@router.get("/api/openclaw-memory/sync")
def sync_openclaw_memory_for_installation(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    iid_raw = (
        request.headers.get(INSTALLATION_ID_HEADER)
        or request.headers.get("x-installation-id")
        or ""
    )
    iid = parse_installation_id_strict(iid_raw)
    ensure_installation_slot(db, current_user.id, iid)
    rows = (
        db.query(OpenClawMemoryDocument)
        .filter(
            OpenClawMemoryDocument.target_user_id == current_user.id,
            OpenClawMemoryDocument.installation_id == iid,
        )
        .order_by(OpenClawMemoryDocument.updated_at.desc())
        .limit(500)
        .all()
    )
    return {
        "ok": True,
        "installation_id": iid,
        "documents": [_doc_summary(r, include_content=True) for r in rows],
    }


@router.post("/api/openclaw-memory/user-documents")
def mirror_user_openclaw_memory_document(
    body: UserMemoryMirrorBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    iid = optional_installation_id_from_request(request)
    if not iid:
        raise HTTPException(status_code=400, detail="缺少 X-Installation-Id")
    ensure_installation_slot(db, current_user.id, iid)
    content = _limit_text(body.content_text or "")
    if not content:
        raise HTTPException(status_code=400, detail="资料内容为空")
    clean_doc_id = _sanitize_doc_id(body.doc_id) or hashlib.sha256(
        f"{current_user.id}\0{iid}\0{body.filename}\0{content}".encode("utf-8", "ignore")
    ).hexdigest()[:24]
    row = db.query(OpenClawMemoryDocument).filter(OpenClawMemoryDocument.doc_id == clean_doc_id).first()
    now = datetime.utcnow()
    payload = {
        "target_user_id": current_user.id,
        "installation_id": iid,
        "origin": "user",
        "uploader_user_id": current_user.id,
        "uploader_role": "user",
        "title": _short_title(body.title, body.filename),
        "filename": (body.filename or "document.txt").strip()[:255] or "document.txt",
        "notes": (body.notes or "").strip()[:1000],
        "content_text": content,
        "size": body.size,
        "sha256": (body.sha256 or hashlib.sha256(content.encode("utf-8")).hexdigest())[:64],
        "status": "active",
        "meta": {"mirrored_from": "online_local_upload"},
        "updated_at": now,
        "deleted_at": None,
    }
    if row:
        if row.target_user_id != current_user.id or row.installation_id != iid:
            raise HTTPException(status_code=409, detail="资料 ID 冲突")
        for key, value in payload.items():
            setattr(row, key, value)
    else:
        row = OpenClawMemoryDocument(doc_id=clean_doc_id, created_at=now, **payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "document": _doc_summary(row)}
