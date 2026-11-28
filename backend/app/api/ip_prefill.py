"""数字人 / 素材混剪 / 创意分镜：录音或 IP 记忆 → 九宫格选题 → 5 条可一键带入的方案。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Asset,
    IPContentScheduleTemplate,
    IPPrefillRecord,
    IPProfileV2Archive,
    OpenClawMemoryDocument,
    RecorderAudioRecord,
    User,
)
from ..services.digital_human_materials import resolve_group_materials
from .auth import access_token_claims, create_access_token, get_current_user
from .h5_personal_settings import _owner_user
from .h5_recorder_extract import _as_bool, _extract_json_object, _normalize_ai_params
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()

_PERSONAL_DEFAULT_TEMPLATE_NAME = "个人默认配置"
_TARGETS = ("digital", "montage", "storyboard")
_TARGET_LABEL = {"digital": "数字人口播", "montage": "素材混剪", "storyboard": "创意分镜"}
_MODES = ("recording", "memory")


def _clean(value: Any, limit: int = 200) -> str:
    return str(value or "").strip().replace("\r", "").replace("\n\n\n", "\n\n")[:limit]


def _model_name() -> str:
    return (
        (os.environ.get("LOBSTER_ORCHESTRATION_SUTUI_CHAT_MODEL") or "").strip()
        or (os.environ.get("LOBSTER_DEFAULT_SUTUI_CHAT_MODEL") or "").strip()
        or "openai/gpt-4.1-mini"
    )


def _internal_base() -> str:
    return (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"


async def _llm_json(system_prompt: str, user_prompt: str, token: str, brand: str, *, temperature: float = 0.5, timeout: float = 200.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if brand:
        headers["X-Lobster-Brand"] = brand
    payload = {
        "model": _model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(_internal_base() + "/api/sutui-chat/completions", json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
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
    return _extract_json_object(content)


def _archive_digest(analysis: Any, limit: int = 3500) -> str:
    data = analysis if isinstance(analysis, dict) else {}
    parts: list[str] = []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    basics = [x for x in [_clean(meta.get("name"), 40), _clean(meta.get("industry"), 40), _clean(meta.get("city"), 30)] if x]
    if basics:
        parts.append("【基础】" + " · ".join(basics))
    for row in (data.get("dimensions") or []):
        if not isinstance(row, dict):
            continue
        summary = _clean(row.get("summary"), 260)
        if summary:
            parts.append("【" + (_clean(row.get("title") or row.get("key"), 20) or "档案") + "】" + summary)
    for row in (data.get("qa") or [])[:6]:
        if isinstance(row, dict) and _clean(row.get("question"), 40):
            parts.append("Q:" + _clean(row.get("question"), 40) + " A:" + _clean(row.get("answer"), 90))
    return "\n".join(parts)[:limit]


def _memory_block(db: Session, owner_id: int) -> str:
    """IP 人设模板关联的记忆文档；没有关联就退回最近几条。"""
    ids: list[str] = []
    try:
        tpl = (
            db.query(IPContentScheduleTemplate)
            .filter(
                IPContentScheduleTemplate.user_id == int(owner_id),
                IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
                IPContentScheduleTemplate.status == "active",
            )
            .order_by(IPContentScheduleTemplate.updated_at.desc())
            .first()
        )
        raw_ids = tpl.memory_doc_ids if tpl and isinstance(tpl.memory_doc_ids, list) else []
        ids = [str(x).strip() for x in raw_ids if str(x).strip()][:12]
    except Exception:
        ids = []
    rows: list[Any] = []
    try:
        query = db.query(OpenClawMemoryDocument).filter(
            OpenClawMemoryDocument.target_user_id == int(owner_id),
            OpenClawMemoryDocument.status == "active",
        )
        if ids:
            rows = query.filter(OpenClawMemoryDocument.doc_id.in_(ids)).all()
        if not rows:
            rows = query.order_by(OpenClawMemoryDocument.updated_at.desc()).limit(6).all()
    except Exception:
        rows = []
    parts: list[str] = []
    for row in rows[:8]:
        content = str(getattr(row, "content_text", "") or "").strip()
        if not content:
            continue
        parts.append("【" + (str(getattr(row, "title", "") or getattr(row, "filename", "") or "个人记忆")) + "】\n" + content[:1500])
    return "\n\n".join(parts)[:6000]


def _asset_groups(db: Session, owner_id: int) -> dict[str, list]:
    groups: dict[str, list] = {}
    try:
        asset_rows = db.query(Asset).filter(Asset.user_id == int(owner_id)).order_by(Asset.created_at.desc()).limit(300).all()
    except Exception:
        return groups
    for item in asset_rows:
        meta = item.meta if isinstance(item.meta, dict) else {}
        names: list[str] = []
        group_name = str(meta.get("creative_candidate_group") or "").strip()
        if group_name:
            names.append(group_name)
        for tag in str(getattr(item, "tags", "") or "").replace("，", ",").split(","):
            tag = tag.strip()
            if tag and tag not in names:
                names.append(tag)
        for name in names:
            bucket = groups.setdefault(name, [])
            if item not in bucket:
                bucket.append(item)
    return groups


async def _template_catalog(token: str, brand: str) -> list[dict[str, str]]:
    base = _internal_base()
    out: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            resp = await client.post(
                base + "/api/shanjian-smart-clip/templates",
                json={"page_size": 40, "scene": "realMan"},
                headers={"Authorization": "Bearer " + token, "X-Lobster-Brand": brand},
            )
        if resp.status_code < 400:
            for item in ((resp.json() or {}).get("results") or [])[:40]:
                style_id = str(item.get("styleId") or item.get("style_id") or item.get("id") or "").strip()
                name = str(item.get("name") or item.get("title") or "").strip()
                if style_id and name:
                    out.append({"style_id": style_id, "name": name[:20]})
    except Exception:
        pass
    return out


_KEYWORD_SYSTEM = (
    "你是短视频选题策划。根据用户素材提炼这个账号该占住的关键词，并用 5x5 宫格拆解。"
    "只输出严格 JSON：" '{"keywords":["5-6个关键词"],"grid":{"center":"主关键词","inner":["8个细分话题/方法/痛点"],"outer":["16个人群+场景组合，每个不超过8字"]}}。'
    "关键词必须是目标用户真实会搜索的词，不要写行业大词；inner 和 outer 都要围绕这个人的真实业务、客户和案例，不要编造。"
)

_TARGET_RULES = {
    "digital": (
        "这是数字人口播任务，每条要给完整参数："
        '{"title":"≤20字","hook":"前3秒钩子≤20字","script":"口播文案180-220字（200字左右），第一人称可直接念","keyword":"","inner":"","outer":"",'
        '"params":{"duration_seconds":30,"use_materials":true,"material_group":"","template_name":"","match_way":"fuzzyMatch",'
        '"composition":"random","preprocess":"roughCut","emotion":"happy","speed":1.0,"header_switch":true,"subtitle_switch":true,'
        '"keyword_switch":true,"material_sound_switch":false,"watermark_switch":false}}'
        "；duration_seconds 按文案长度估 15-120；讲到产品/场景/案例/数据 use_materials 为 true，纯观点为 false；"
        "use_materials 为 true 时从【可用素材分组】里挑最贴合的一组建 material_group；template_name 从【可用模板】里挑，没有就空；"
        "match_way ∈ fuzzyMatch/preciseMatch；composition ∈ random/sequential；preprocess ∈ roughCut/sliceMerge；"
        "emotion ∈ happy/neutral/sad/angry；speed 0.8-1.3。"
    ),
    "montage": (
        "这是素材混剪任务，每条要给完整参数："
        '{"title":"≤20字","hook":"≤20字","script":"口播/字幕文案 80-200 字","keyword":"","inner":"","outer":"",'
        '"params":{"duration":30,"material_group":"","template_name":"","subtitle":true,"muted":false}}'
        "；duration 15-120 秒；material_group 从【可用素材分组】里挑最贴合的一组建；template_name 从【可用模板】里挑，没有就空；"
        "subtitle 一般 true，muted 只在没有口播时 true。"
    ),
    "storyboard": (
        "这是创意分镜任务，每条要给完整分镜："
        '{"title":"≤20字","hook":"≤20字","script":"整条口播文案 180-220 字（200字左右）","keyword":"","inner":"","outer":"",'
        '"params":{"aspect":"9:16","duration":10},"shots":[{"no":1,"seconds":3,"voice":"这一镜的口播","visual":"这一镜的画面描述"}]}'
        "；shots 必须 4-6 镜，seconds 合计等于 duration（总时长 10-30 秒）；"
        "visual 要具体到可生成：画面主体、景别、动作、光线、场景，不要写抽象词；voice 是这一镜对应的口播片段。"
    ),
}

class PrefillBody(BaseModel):
    target: str = Field("digital")
    mode: str = Field("memory")
    record_id: Optional[int] = None
    count: int = Field(5, ge=3, le=5)


def _build_topic_prompt(target: str, context_text: str, keywords: list[str], grid: dict[str, Any], count: int) -> tuple[str, str]:
    inner = [str(x) for x in (grid.get("inner") or []) if str(x).strip()][:8]
    outer = [str(x) for x in (grid.get("outer") or []) if str(x).strip()][:16]
    system_prompt = (
        "你是资深短视频编导。严格用 5x5 宫格法出选题：每条选题必须能看出是【内圈词】×【外圈人群场景】的组合。"
        "只输出严格 JSON：{\"topics\":[...]}。要求 " + str(count) + " 条，按爆款潜力从高到低排序。"
        "必须使用【素材】里真实的身份、产品、客户、案例和原话，素材里没有的绝不编造；不要空话套话。" + _TARGET_RULES[target]
    )
    user_prompt = (
        "【素材】\n" + context_text
        + "\n\n【关键词】" + "、".join(keywords[:6])
        + "\n【宫格·内圈】" + "、".join(inner)
        + "\n【宫格·外圈】" + "、".join(outer)
        + "\n\n请输出 " + str(count) + " 条选题的 JSON。"
    )
    return system_prompt, user_prompt


def _normalize_item(target: str, raw: Any, groups: dict[str, list], style_templates: list[dict[str, str]], template_groups: list[str], resolver) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = _clean(raw.get("title"), 60)
    script = _clean(raw.get("script"), 2000)
    if not title or not script:
        return None
    item: dict[str, Any] = {
        "title": title,
        "hook": _clean(raw.get("hook"), 60),
        "script": script,
        "keyword": _clean(raw.get("keyword"), 40),
        "inner": _clean(raw.get("inner"), 40),
        "outer": _clean(raw.get("outer"), 40),
    }
    raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    if target == "digital":
        params = _normalize_ai_params(raw_params, groups, style_templates, template_groups, resolver=resolver)
        try:
            duration = int(float(raw_params.get("duration_seconds") or 30))
        except Exception:
            duration = 30
        params["duration_seconds"] = max(10, min(180, duration))
        item["params"] = params
    elif target == "montage":
        group = _clean(raw_params.get("material_group"), 40)
        if group not in groups:
            group = max(groups, key=lambda name: len(groups[name])) if groups else ""
        try:
            duration = int(float(raw_params.get("duration") or 30))
        except Exception:
            duration = 30
        params = {
            "duration": max(10, min(120, duration)),
            "material_group": group,
            "template_name": _clean(raw_params.get("template_name"), 20),
            "subtitle": _as_bool(raw_params.get("subtitle"), True),
            "muted": _as_bool(raw_params.get("muted"), False),
            "materials": resolver([group]) if group else [],
        }
        item["params"] = params
    else:
        shots: list[dict[str, Any]] = []
        for idx, shot in enumerate((raw.get("shots") or [])[:8]):
            if not isinstance(shot, dict):
                continue
            try:
                seconds = int(float(shot.get("seconds") or 3))
            except Exception:
                seconds = 3
            voice = _clean(shot.get("voice"), 300)
            visual = _clean(shot.get("visual"), 400)
            if not voice and not visual:
                continue
            shots.append({"no": idx + 1, "seconds": max(1, min(15, seconds)), "voice": voice, "visual": visual})
        try:
            duration = int(float(raw_params.get("duration") or sum(s["seconds"] for s in shots) or 10))
        except Exception:
            duration = 10
        item["params"] = {
            "aspect": _clean(raw_params.get("aspect"), 10) or "9:16",
            "duration": max(5, min(60, duration)),
        }
        item["shots"] = shots
    return item


@router.post("/api/ip-prefill/generate", summary="按录音或 IP 记忆出 5 条选题并带全部参数")
async def generate_prefill(
    body: PrefillBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target = _clean(body.target, 20).lower() or "digital"
    mode = _clean(body.mode, 20).lower() or "memory"
    if target not in _TARGETS:
        raise HTTPException(status_code=400, detail="不支持的目标：" + target)
    if mode not in _MODES:
        raise HTTPException(status_code=400, detail="不支持的模式：" + mode)
    current_user = online_user_for_mobile_user(db, current_user)
    owner = _owner_user(db, current_user)

    record_name = ""
    transcript = ""
    if mode == "recording":
        if not body.record_id:
            raise HTTPException(status_code=400, detail="请选择一条录音")
        row = (
            db.query(RecorderAudioRecord)
            .filter(RecorderAudioRecord.id == int(body.record_id), RecorderAudioRecord.user_id == int(current_user.id))
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="录音不存在")
        # 转写只做一次：只要库里已经有逐字稿就直接用，不再触发任何 STT
        transcript = str(row.transcript_text or "").strip()
        if not transcript:
            raise HTTPException(status_code=409, detail="这条录音还没转写，先去 AI 秘书转写一次（转写结果会保留，后面直接复用）")
        record_name = str(row.display_name or row.file_name or "")
        context_text = "【录音内容】\n" + transcript[:12000]
    else:
        memory = _memory_block(db, owner.id)
        archive = db.query(IPProfileV2Archive).filter(IPProfileV2Archive.user_id == int(owner.id)).first()
        digest = _archive_digest(archive.analysis if archive else {})
        if not memory and not digest:
            raise HTTPException(status_code=400, detail="还没有 IP 记忆或 IP 档案，先做一次录音分析或补充个人IP设置")
        context_text = ("【IP 人设档案】\n" + digest if digest else "") + ("\n\n【IP 记忆】\n" + memory if memory else "")

    try:
        owner_token = create_access_token(access_token_claims(owner))
    except Exception:
        owner_token = ""

    groups = _asset_groups(db, owner.id)
    group_catalog = "、".join(f"{name}({len(rows)}个)" for name, rows in list(groups.items())[:20])
    style_templates = await _template_catalog(owner_token, "")
    template_catalog = "、".join(row["name"] for row in style_templates)
    template_groups: list[str] = []
    try:
        tpl = (
            db.query(IPContentScheduleTemplate)
            .filter(
                IPContentScheduleTemplate.user_id == int(owner.id),
                IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
                IPContentScheduleTemplate.status == "active",
            )
            .first()
        )
        meta = tpl.meta if tpl and isinstance(tpl.meta, dict) else {}
        raw_groups = meta.get("digital_human_asset_groups")
        if isinstance(raw_groups, list):
            template_groups = [str(x).strip() for x in raw_groups if str(x).strip()][:20]
    except Exception:
        template_groups = []

    def resolver(names: list[str]) -> list[dict[str, str]]:
        try:
            return resolve_group_materials(db, [owner.id], names)
        except Exception:
            return []

    extra_hint = ""
    if group_catalog:
        extra_hint += "\n【可用素材分组】" + group_catalog
    if template_catalog:
        extra_hint += "\n【可用模板】" + template_catalog

    # 两次 LLM 要跑 40-90 秒，先把读事务提交掉，否则 PG 会在 idle-in-transaction 里掐断连接
    try:
        db.commit()
    except Exception:
        db.rollback()

    keyword_parsed = await _llm_json(
        _KEYWORD_SYSTEM,
        context_text + extra_hint + "\n\n请输出关键词与宫格 JSON。",
        owner_token,
        "",
        temperature=0.4,
        timeout=90.0,
    )
    keywords = [_clean(x, 30) for x in (keyword_parsed.get("keywords") or []) if _clean(x, 30)][:6]
    grid_raw = keyword_parsed.get("grid") if isinstance(keyword_parsed.get("grid"), dict) else {}
    grid = {
        "center": _clean(grid_raw.get("center"), 30) or (keywords[0] if keywords else ""),
        "inner": [_clean(x, 30) for x in (grid_raw.get("inner") or []) if _clean(x, 30)][:8],
        "outer": [_clean(x, 30) for x in (grid_raw.get("outer") or []) if _clean(x, 30)][:16],
    }
    if not keywords and not grid["inner"]:
        raise HTTPException(status_code=502, detail="AI 没能拆出关键词，请换一条录音或补充 IP 资料")

    system_prompt, user_prompt = _build_topic_prompt(target, context_text + extra_hint, keywords, grid, int(body.count))
    topic_parsed = await _llm_json(system_prompt, user_prompt, owner_token, "", temperature=0.65, timeout=150.0)
    items: list[dict[str, Any]] = []
    for raw in (topic_parsed.get("topics") or []):
        item = _normalize_item(target, raw, groups, style_templates, template_groups, resolver)
        if item is None:
            continue
        item["id"] = len(items) + 1
        items.append(item)
        if len(items) >= int(body.count):
            break
    if not items:
        raise HTTPException(status_code=502, detail="AI 没有返回可用的选题，请重试")

    payload = {
        "target": target,
        "target_label": _TARGET_LABEL.get(target, target),
        "mode": mode,
        "record_id": int(body.record_id or 0) or None,
        "record_name": record_name,
        "keywords": keywords,
        "grid": grid,
        "items": items,
        "generated_at": datetime.utcnow().isoformat(),
    }
    record = IPPrefillRecord(
        user_id=int(current_user.id),
        target=target,
        mode=mode,
        record_id=int(body.record_id or 0) or None,
        items=payload,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"ok": True, "id": int(record.id), **payload}


@router.get("/api/ip-prefill/records", summary="选题生成历史列表")
def list_prefill_records(
    target: str = "",
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_user = online_user_for_mobile_user(db, current_user)
    query = db.query(IPPrefillRecord).filter(IPPrefillRecord.user_id == int(current_user.id))
    if _clean(target, 20):
        query = query.filter(IPPrefillRecord.target == _clean(target, 20))
    rows = query.order_by(IPPrefillRecord.created_at.desc()).limit(max(1, min(50, int(limit or 20)))).all()
    items = []
    for row in rows:
        payload = row.items if isinstance(row.items, dict) else {}
        generated = payload.get("items") or []
        first = generated[0] if generated and isinstance(generated[0], dict) else {}
        items.append({
            "id": int(row.id),
            "target": row.target,
            "mode": row.mode,
            "record_name": payload.get("record_name") or "",
            "count": len(generated),
            "first_title": first.get("title") or "",
            "created_at": row.created_at.isoformat() if row.created_at else "",
        })
    return {"ok": True, "items": items}


@router.get("/api/ip-prefill/records/{record_id}", summary="选题生成历史详情")
def get_prefill_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_user = online_user_for_mobile_user(db, current_user)
    row = (
        db.query(IPPrefillRecord)
        .filter(IPPrefillRecord.id == int(record_id), IPPrefillRecord.user_id == int(current_user.id))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    payload = row.items if isinstance(row.items, dict) else {}
    return {"ok": True, "id": int(row.id), "created_at": row.created_at.isoformat() if row.created_at else "", **payload}


@router.delete("/api/ip-prefill/records/{record_id}", summary="删除一条选题生成历史")
def delete_prefill_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_user = online_user_for_mobile_user(db, current_user)
    row = (
        db.query(IPPrefillRecord)
        .filter(IPPrefillRecord.id == int(record_id), IPPrefillRecord.user_id == int(current_user.id))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


class KeywordSuggestBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=60)


@router.post("/api/ip-content/keywords/suggest", summary="根据关键词让 AI 给出相关关键词")
async def suggest_keywords(
    body: KeywordSuggestBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    seed = _clean(body.keyword, 60)
    if not seed:
        raise HTTPException(status_code=400, detail="请先输入关键词")
    current_user = online_user_for_mobile_user(db, current_user)
    owner = _owner_user(db, current_user)
    try:
        token = create_access_token(access_token_claims(owner))
    except Exception:
        token = ""
    system_prompt = (
        "你是中文短视频选题助手。根据用户给的一个关键词，给出 6-8 个相关、可直接用于搜索和选题的关键词。"
        '只输出严格 JSON：{"keywords":["..."]}。'
        "要求：每个 2-12 个字；包含同义词、使用场景、人群、痛点等角度；不要编号、不要解释、不要重复原词。"
    )
    parsed = await _llm_json(system_prompt, "关键词：" + seed, token, "", temperature=0.7, timeout=90.0)
    items: list[str] = []
    for raw in (parsed.get("keywords") or []):
        word = _clean(raw, 20)
        if word and word != seed and word not in items:
            items.append(word)
    return {"ok": True, "keyword": seed, "items": items[:8]}
