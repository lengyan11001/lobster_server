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
from ..services.digital_human_materials import MATERIAL_PICK_LIMIT, broad_pick
from .auth import access_token_claims, create_access_token, get_current_user
from .h5_personal_settings import _owner_user
from .mobile_identity import online_user_for_mobile_user

router = APIRouter()

# 与 ip_content_studio 同一份「个人默认配置」行名。
_PERSONAL_DEFAULT_TEMPLATE_NAME = "个人默认配置"


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
    style_templates: list[dict[str, str]],
    template_groups: list[str] | None = None,
    resolver=None,
) -> dict[str, Any]:
    """把 AI 返回的松散 JSON 收敛成 mini program 能直接落地的参数。"""
    use_materials = _as_bool(parsed.get("use_materials"), True)
    requested_group = str(parsed.get("material_group") or "").strip()
    # 统一宽松策略：AI 点名了分组就用它；没点名/点错了就用当前模板的分组；
    # 再兜底到素材最多的分组。绝不因为分组名为空就交白卷。
    chosen_groups: list[str] = []
    if requested_group and requested_group in groups:
        chosen_groups = [requested_group]
    elif template_groups:
        chosen_groups = [name for name in template_groups if name in groups]
    if not chosen_groups and groups:
        chosen_groups = [max(groups, key=lambda name: len(groups[name]))]
    materials = []
    if use_materials and chosen_groups and resolver is not None:
        # 和云端数字人成片共用同一个取数实现（全库按分组取，不受 300 条窗口限制）。
        materials = resolver(chosen_groups)
    elif use_materials and chosen_groups:
        pool = []
        seen: set[str] = set()
        for name in chosen_groups:
            for item in groups[name]:
                asset_id = str(getattr(item, "asset_id", "") or "")
                media_type = str(getattr(item, "media_type", "") or "").lower()
                if media_type not in ("image", "video"):
                    continue
                url = str(getattr(item, "source_url", "") or "").strip()
                if not url or (asset_id and asset_id in seen):
                    continue
                if asset_id:
                    seen.add(asset_id)
                pool.append({
                    "asset_id": asset_id,
                    "type": media_type,
                    "fileUrl": url,
                    "name": str(getattr(item, "filename", "") or asset_id),
                })
        materials = broad_pick(pool, MATERIAL_PICK_LIMIT)
    group_name = chosen_groups[0] if chosen_groups else ""
    template_name = str(parsed.get("template_name") or "").strip()
    template_style_id = ""
    if template_name:
        for row in style_templates:
            if row.get("name") == template_name:
                template_style_id = row.get("style_id", "")
                break
        else:
            template_name = ""
    elif len(style_templates) == 1:
        template_style_id = style_templates[0].get("style_id", "")
        template_name = style_templates[0].get("name", "")

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
        "template_style_id": template_style_id,
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

    brand_value = request.headers.get("X-Lobster-Brand") or ""
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
            names: list[str] = []
            group_name = str(meta.get("creative_candidate_group") or "").strip()
            if group_name:
                names.append(group_name)
            # 素材库分组标签（tags 逗号分隔）也作为可选分组
            raw_tags = str(getattr(item, "tags", "") or "")
            for tag in raw_tags.replace("，", ",").split(","):
                tag = tag.strip()
                if tag and tag not in names:
                    names.append(tag)
            for name in names:
                bucket = groups.setdefault(name, [])
                if item not in bucket:
                    bucket.append(item)
    except Exception:
        groups = {}
    group_catalog = "、".join(f"{name}({len(rows)}个)" for name, rows in list(groups.items())[:20])

    style_templates: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as tpl_client:
            tpl_resp = await tpl_client.post(
                base + "/api/shanjian-smart-clip/templates",
                json={"page_size": 40, "scene": "realMan"},
                headers={"Authorization": "Bearer " + owner_token, "X-Lobster-Brand": brand_value},
            )
        if tpl_resp.status_code < 400:
            tpl_data = tpl_resp.json() or {}
            for item in (tpl_data.get("results") or [])[:40]:
                style_id = str(item.get("styleId") or item.get("style_id") or item.get("id") or "").strip()
                style_name = str(item.get("name") or item.get("title") or "").strip()
                if style_id and style_name:
                    style_templates.append({"style_id": style_id, "name": style_name[:20]})
    except Exception:
        style_templates = []
    template_catalog = "、".join(row["name"] for row in style_templates)

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
    if brand_value:
        headers["X-Lobster-Brand"] = brand_value
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
    template_groups: list[str] = []
    try:
        tpl_row = (
            db.query(IPContentScheduleTemplate)
            .filter(
                IPContentScheduleTemplate.user_id == owner.id,
                IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME,
                IPContentScheduleTemplate.status == "active",
            )
            .order_by(IPContentScheduleTemplate.updated_at.desc())
            .first()
        )
        tpl_meta = tpl_row.meta if tpl_row and isinstance(tpl_row.meta, dict) else {}
        raw_groups = tpl_meta.get("digital_human_asset_groups")
        if isinstance(raw_groups, list):
            template_groups = [str(item).strip() for item in raw_groups if str(item).strip()][:20]
    except Exception:
        template_groups = []
    from ..services.digital_human_materials import resolve_group_materials

    params = _normalize_ai_params(
        parsed,
        groups,
        style_templates,
        template_groups,
        resolver=lambda names: resolve_group_materials(db, [owner.id], names),
    )
    return {
        "ok": True,
        "title": title or (row.display_name or row.file_name or "")[:20],
        "script": script[:2000],
        "duration_seconds": duration_seconds,
        **params,
    }

# ── 朋友圈图文 / 公众号文案：录音 → 表单字段 ──────────────────────────────
# H5 里这两个功能的输入（h5-app.js）：
#   朋友圈图文：主题/卖点（moments idea）+ 配图数量
#   公众号文章：公众号主题(idea) + 目标读者 + 写作风格(专业观点/简洁商业/故事叙事/实用教程)
# 小程序页面字段较少，这里按小程序现有字段出结果，语义与 H5 对齐。
_COPY_TARGETS = {
    "moments": {
        "schema": '{"topic":"主题或卖点","count":3,"title":""}',
        "rules": (
            "topic 是朋友圈图的主题/卖点，30-80 字，写清产品、卖点、场景或优惠；"
            "count 是建议配图张数，1-9 之间的整数，一般 3；title 可留空。"
        ),
    },
    "image": {
        "schema": '{"prompt":"画面描述","ratio":"9:16","resolution":"2k","count":1,"usage":"海报"}',
        "rules": (
            "prompt 是可直接用于文生图的画面描述，60-200 字：主体、场景、光线、风格、镜头，"
            "并明确写“不要文字、不要 logo、不要水印”；"
            "ratio 只能是 1:1、16:9、9:16、4:3、3:4；resolution 只能是 1k、2k、4k；"
            "count 是张数，1-9 的整数，一般 1-3；usage 只能是 海报、详情页、朋友圈配图 之一。"
        ),
    },
    "article": {
        "schema": '{"title":"","topic":"文章主题","points":"要点与素材","tone":"专业干货"}',
        "rules": (
            "title 是任务名称，可留空；topic 是一句话文章主题（不超过 40 字）；"
            "points 用换行列出录音里可用的卖点、案例、数据或观点，每条一行，最多 6 条；"
            "tone 只能是「专业干货」「真诚故事」「犀利观点」之一。"
        ),
    },
}

_COPY_TONES = ("专业干货", "真诚故事", "犀利观点")


@router.post(
    "/api/h5/recorder/files/{record_id}/extract-copy-fields",
    summary="从录音提取朋友圈/公众号文案的填写字段",
)
async def extract_copy_fields(
    record_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json() if await request.body() else {}
    target = str((body or {}).get("target") or "article").strip().lower()
    if target not in _COPY_TARGETS:
        raise HTTPException(status_code=400, detail="target 只能是 moments / article / image")

    current_user = online_user_for_mobile_user(db, current_user)
    row = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == record_id,
        RecorderAudioRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="录音不存在")
    text = str(row.transcript_text or "").strip()
    if row.status != "completed" or not text:
        raise HTTPException(status_code=409, detail="转写完成后才能生成填写内容")

    owner = _owner_user(db, current_user)
    try:
        owner_token = create_access_token(access_token_claims(owner))
    except Exception:
        owner_token = ""

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

    spec = _COPY_TARGETS[target]
    base = (os.environ.get("LOBSTER_INTERNAL_API_BASE") or "").strip().rstrip("/") or "http://127.0.0.1:8000"
    model = (
        (os.environ.get("LOBSTER_ORCHESTRATION_SUTUI_CHAT_MODEL") or "").strip()
        or (os.environ.get("LOBSTER_DEFAULT_SUTUI_CHAT_MODEL") or "").strip()
        or "openai/gpt-4.1-mini"
    )
    system_prompt = (
        "你是一名中文内容编导，要把一段录音整理成可直接填进表单的字段。"
        "只输出严格 JSON：" + spec["schema"] + "。"
        + spec["rules"]
        + "要结合【个人记忆】里的产品、客户、优势；不要 Markdown、不要解释、不要编造录音与记忆里都没有的事实。"
    )
    user_prompt = (
        "【录音名称】" + str(row.display_name or row.file_name or "")
        + ("\n\n【个人记忆】\n" + memory_block if memory_block else "")
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
        "temperature": 0.4,
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

    if target == "moments":
        topic = str(parsed.get("topic") or "").strip()[:300]
        if not topic:
            raise HTTPException(status_code=502, detail="AI 没有返回朋友圈主题")
        try:
            count = int(parsed.get("count") or 3)
        except Exception:
            count = 3
        count = max(1, min(9, count))
        return {
            "ok": True,
            "target": "moments",
            "topic": topic,
            "count": count,
            "title": str(parsed.get("title") or "").strip()[:60],
        }

    if target == "image":
        prompt = str(parsed.get("prompt") or "").strip()[:1200]
        if not prompt:
            raise HTTPException(status_code=502, detail="AI 没有返回画面描述")
        ratio = str(parsed.get("ratio") or "").strip()
        if ratio not in ("1:1", "16:9", "9:16", "4:3", "3:4"):
            ratio = "9:16"
        resolution = str(parsed.get("resolution") or "").strip().lower()
        if resolution not in ("1k", "2k", "4k"):
            resolution = "2k"
        try:
            count = int(parsed.get("count") or 1)
        except Exception:
            count = 1
        count = max(1, min(9, count))
        usage = str(parsed.get("usage") or "").strip()
        if usage not in ("海报", "详情页", "朋友圈配图"):
            usage = "海报"
        return {
            "ok": True,
            "target": "image",
            "prompt": prompt,
            "ratio": ratio,
            "resolution": resolution,
            "count": count,
            "usage": usage,
        }

    topic = str(parsed.get("topic") or "").strip()[:200]
    if not topic:
        raise HTTPException(status_code=502, detail="AI 没有返回文章主题")
    points = str(parsed.get("points") or "").strip()[:1200]
    tone = str(parsed.get("tone") or "").strip()
    if tone not in _COPY_TONES:
        tone = _COPY_TONES[0]
    return {
        "ok": True,
        "target": "article",
        "title": str(parsed.get("title") or "").strip()[:60],
        "topic": topic,
        "points": points,
        "tone": tone,
    }

