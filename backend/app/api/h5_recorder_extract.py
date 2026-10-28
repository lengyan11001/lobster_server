"""从录音转写生成数字人口播参数。"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, OpenClawMemoryDocument, RecorderAudioRecord, User
from .auth import access_token_claims, create_access_token, get_current_user
from .h5_personal_settings import _owner_user
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start:end + 1])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
@router.post("/api/h5/recorder/files/{record_id}/extract-digital-human", summary="从录音生成数字人口播参数")
async def extract_digital_human_params(
    record_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user = online_user_for_mobile_user(db, current_user)
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    text = str(row.transcript_text or "").strip()
    if row.status != "completed" or not text:
        raise HTTPException(status_code=409, detail="转写完成后才能生成口播参数")

    owner = _owner_user(db, current_user)
    try:
        owner_token = create_access_token(access_token_claims(owner))
    except Exception:
        owner_token = ""

    base = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"
    model = (
        (os.environ.get("LOBSTER_ORCHESTRATION_SUTUI_CHAT_MODEL") or "").strip()
        or (os.environ.get("LOBSTER_DEFAULT_SUTUI_CHAT_MODEL") or "").strip()
        or "openai/gpt-4.1-mini"
    )
    groups = {}
    try:
        asset_rows = (
            db.query(Asset)
            .filter(Asset.user_id == owner.id)
            .order_by(Asset.created_at.desc())
            .limit(300)
            .all()
        )
        for item in asset_rows:
            meta = item.meta if isinstance(item.meta, dict) else {}
            group_name = str(meta.get("creative_candidate_group") or "").strip()
            if not group_name:
                continue
            groups.setdefault(group_name, []).append(item)
    except Exception:
        groups = {}
    group_catalog = "、".join(f"{name}({len(rows)}个)" for name, rows in list(groups.items())[:20])

    system_prompt = (
        "你是资深短视频编导，要把一段录音整理成一条可以直接给数字人口播的文案。"
        '只输出严格 JSON：{"title":"标题","script":"口播文案","duration_seconds":30,"material_group":"素材分组名或空"}。'
        "标题不超过 20 字；口播文案 120-260 字，口语化、开头有钩子、结尾有行动引导；"
        "要结合【个人记忆】里的产品、客户、优势来写；如果给了素材分组清单，选一个最贴合内容的分组填到 material_group，没有合适的就填空字符串；不要 Markdown、不要解释、不要编造录音与记忆里都没有的事实。"
    )
    memory_block = ""
    try:
        mem_rows = (
            db.query(OpenClawMemoryDocument)
            .filter(
                OpenClawMemoryDocument.target_user_id == owner.id,
                OpenClawMemoryDocument.status == "active",
            )
            .order_by(OpenClawMemoryDocument.updated_at.desc())
            .limit(6)
            .all()
        )
        parts = []
        for mem in mem_rows:
            content = str(mem.content_text or "").strip()
            if not content:
                continue
            parts.append("【" + str(mem.title or mem.filename or "个人记忆") + "】\n" + content[:1500])
        memory_block = "\n\n".join(parts)[:6000]
    except Exception:
        memory_block = ""

    user_prompt = (
        "【录音名称】" + str(row.display_name or row.file_name or "")
        + ("\n\n【个人记忆】\n" + memory_block if memory_block else "")
        + ("\n\n【可用素材分组】" + group_catalog if group_catalog else "")
        + "\n\n【录音内容】\n" + text[:20000]
    )
    headers = {"Content-Type": "application/json"}
    if owner_token:
        headers["Authorization"] = "Bearer " + owner_token
    brand = request.headers.get("X-Lobster-Brand") or ""
    if brand:
        headers["X-Lobster-Brand"] = brand
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            resp = await client.post(base + "/api/sutui-chat/completions", json=payload, headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI 生成失败：" + str(exc)[:200]) from exc
    if resp.status_code >= 400:
        detail = ""
        try:
            raw = resp.json()
            detail = str(raw.get("detail") or raw.get("message") or "")
        except Exception:
            detail = (resp.text or "")[:200]
        raise HTTPException(status_code=502, detail="AI 生成失败：" + (detail or str(resp.status_code)))
    data = resp.json() if resp.content else {}
    content = ""
    try:
        content = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        content = str(data.get("text") or "")
    parsed = _extract_json_object(content)
    title = str(parsed.get("title") or "").strip()[:60]
    script = str(parsed.get("script") or "").strip()
    if not script:
        raise HTTPException(status_code=502, detail="AI 没有返回可用的口播文案")
    try:
        duration_seconds = int(parsed.get("duration_seconds") or 30)
    except Exception:
        duration_seconds = 30
    duration_seconds = max(10, min(180, duration_seconds))
    group_name = str(parsed.get("material_group") or "").strip()
    materials = []
    if group_name and group_name in groups:
        for item in groups[group_name][:8]:
            media_type = str(item.media_type or "").lower()
            if media_type not in ("image", "video"):
                continue
            materials.append({
                "asset_id": item.asset_id,
                "type": media_type,
                "fileUrl": str(item.source_url or ""),
                "name": str(item.filename or item.asset_id),
            })
    return {
        "ok": True,
        "title": title or (row.display_name or row.file_name or "")[:20],
        "script": script[:2000],
        "duration_seconds": duration_seconds,
        "material_group": group_name if materials else "",
        "materials": materials,
    }