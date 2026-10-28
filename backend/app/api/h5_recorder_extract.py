"""从录音转写生成数字人口播参数。"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, IPContentScheduleTemplate, OpenClawMemoryDocument, RecorderAudioRecord, User
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


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "是"}
    return bool(value) if value is not None else default


def _normalize_ai_params(
    parsed: dict[str, Any],
    groups: dict[str, list],
    template_catalog: str,
) -> dict[str, Any]:
    """把 AI 返回的松散 JSON 收敛成 mini program 能直接落地的参数。"""
    use_materials = _as_bool(parsed.get("use_materials"), True)
    group_name = str(parsed.get("material_group") or "").strip()
    materials = []
    if use_materials and group_name and group_name in groups:
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
    template_name = str(parsed.get("template_name") or "").strip()
    if template_catalog and template_name not in template_catalog.split("、"):
        template_name = ""

    def _enum(value: Any, allowed: list[str], default: str) -> str:
        text = str(value or "").strip()
        return text if text in allowed else default

    match_way = _enum(parsed.get("match_way"), ["fuzzyMatch", "preciseMatch"], "fuzzyMatch")
    composition = _enum(parsed.get("composition"), ["random", "sequential"], "random")
    preprocess = _enum(parsed.get("preprocess"), ["roughCut", "sliceMerge"], "roughCut")
    emotion = _enum(parsed.get("emotion"), ["happy", "neutral", "sad", "angry"], "happy")
    try:
        speed = round(float(parsed.get("speed") or 1.0), 2)
    except Exception:
        speed = 1.0
    speed = max(0.8, min(1.3, speed))
    return {
        "use_materials": use_materials,
        "material_group": group_name if use_materials else "",
        "materials": materials,
        "template_name": template_name,
        "match_way": match_way,
        "composition": composition,
        "preprocess": preprocess,
        "emotion": emotion,
        "speed": speed,
        "header_switch": _as_bool(parsed.get("header_switch"), True),
        "subtitle_switch": _as_bool(parsed.get("subtitle_switch"), True),
        "keyword_switch": _as_bool(parsed.get("keyword_switch"), True),
        "material_sound_switch": _as_bool(parsed.get("material_sound_switch"), False),
        "watermark_switch": _as_bool(parsed.get("watermark_switch"), False),
    }


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
    groups: dict[str, list] = {}
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

    template_catalog = ""
    try:
        tpl_rows = (
            db.query(IPContentScheduleTemplate)
            .filter(
                IPContentScheduleTemplate.user_id == owner.id,
                IPContentScheduleTemplate.status == "active",
            )
            .order_by(IPContentScheduleTemplate.updated_at.desc())
            .limit(20)
            .all()
        )
        names: list[str] = []
        for tpl in tpl_rows:
            name = str(tpl.name or "").strip()
            if name and name not in names:
                names.append(name)
        template_catalog = "、".join(names)
    except Exception:
        template_catalog = ""

    system_prompt = (
        "你是资深短视频编导，要把一段录音整理成一条数字人口播任务的完整参数。"
        "只输出严格 JSON，字段固定为："
        '{"title":"","script":"","duration_seconds":30,"use_materials":true,"material_group":"",'
        '"template_name":"","match_way":"fuzzyMatch","composition":"random","preprocess":"roughCut",'
        '"emotion":"happy","speed":1.0,"header_switch":true,"subtitle_switch":true,"keyword_switch":true,'
        '"material_sound_switch":false,"watermark_switch":false}。'
        "规则：标题不超过 20 字；口播文案 120-260 字，口语化、开头有钩子、结尾有行动引导；"
        "要结合【个人记忆】里的产品、客户、优势来写；duration_seconds 按文案长度估 15-120 秒；"
        "use_materials 由你自己判断这条口播要不要插混剪素材垫画面：讲到产品、场景、案例、数据就要 true，纯观点或情绪口述填 false；"
        "use_materials 为 true 时，从【可用素材分组】里挑最贴合的一组填 material_group，没有合适分组填空字符串；"
        "template_name 从【可用模板】里挑最贴合的一个，没有合适模板或不需要模板填空字符串；"
        "match_way 只能是 fuzzyMatch 或 preciseMatch；composition 只能是 random 或 sequential；"
        "preprocess 只能是 roughCut 或 sliceMerge；emotion 只能是 happy、neutral、sad、angry；speed 在 0.8-1.3 之间；"
        "有字幕、有片头的短视频一般 header_switch/subtitle_switch/keyword_switch 填 true，watermark_switch 填 false；"
        "不要 Markdown、不要解释、不要编造录音与记忆里都没有的事实。"
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
        + ("\n\n【可用模板】" + template_catalog if template_catalog else "")
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
    params = _normalize_ai_params(parsed, groups, template_catalog)
    return {
        "ok": True,
        "title": title or (row.display_name or row.file_name or "")[:20],
        "script": script[:2000],
        "duration_seconds": duration_seconds,
        **params,
    }