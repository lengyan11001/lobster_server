from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import get_current_user
from .ip_content_studio import (
    _clean_long_text,
    _clean_text,
    _internal_api_base,
    _personal_default_template_payload_with_resources,
    _post_llm_with_retry,
    _PERSONAL_DEFAULT_TEMPLATE_NAME,
)
from ..db import SessionLocal, get_db
from ..models import IPContentDraftRecord, IPContentScheduleTemplate, MomentsCoachMaterial, MomentsCoachPlan, MomentsCoachPlanItem, ScheduledTaskRun, User

router = APIRouter()
_generation_semaphore = asyncio.Semaphore(4)

_CIRCLE_TYPES = ("生活圈", "咨询圈", "反馈圈", "收款圈", "促成交圈")
_VERSION_TYPES = ("稳妥版", "真人聊天版", "成交推进版")


class MaterialBody(BaseModel):
    title: str = ""
    happened: str = ""
    customer_problem: str = ""
    customer_question: str = ""
    desired_result: str = ""
    current_change: str = ""
    purpose: str = ""
    image_urls: list[str] = Field(default_factory=list)
    notes: str = ""


class GenerateBody(MaterialBody):
    circle_type: str = ""
    count: int = Field(3, ge=1, le=3)


class PlanBody(BaseModel):
    name: str = "朋友圈一周排期"
    items: list[dict[str, Any]] = Field(default_factory=list)


class PublishBody(BaseModel):
    account_id: str = ""
    account_nickname: str = ""
    installation_id: str = ""
    image_urls: list[str] = Field(default_factory=list)
    image_asset_ids: list[str] = Field(default_factory=list)


class ImageBody(BaseModel):
    prompt: str = ""
    model: str = "openai/gpt-image-2"
    size: str = "1024x1024"


def _material_payload(row: MomentsCoachMaterial) -> dict[str, Any]:
    return {"id": row.id, "title": row.title, "happened": row.happened, "customer_problem": row.customer_problem,
            "customer_question": row.customer_question, "desired_result": row.desired_result, "current_change": row.current_change,
            "purpose": row.purpose, "image_urls": row.image_urls or [], "notes": row.notes, "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _snapshot(body: MaterialBody) -> dict[str, Any]:
    return {"title": _clean_text(body.title, 180), "happened": _clean_long_text(body.happened, 5000),
            "customer_problem": _clean_long_text(body.customer_problem, 5000), "customer_question": _clean_long_text(body.customer_question, 5000),
            "desired_result": _clean_long_text(body.desired_result, 5000), "current_change": _clean_long_text(body.current_change, 5000),
            "purpose": _clean_text(body.purpose, 64), "image_urls": [str(x).strip()[:4096] for x in body.image_urls[:9] if str(x).strip()],
            "notes": _clean_long_text(body.notes, 5000)}


@router.get("/api/moments-coach/config")
def coach_config(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = (db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.user_id == current_user.id,
        IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME, IPContentScheduleTemplate.status == "active").order_by(IPContentScheduleTemplate.id.desc()).first())
    return {"ok": True, "persona": _personal_default_template_payload_with_resources(db, row), "circle_types": list(_CIRCLE_TYPES), "version_types": list(_VERSION_TYPES)}


@router.get("/api/moments-coach/materials")
def list_materials(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(MomentsCoachMaterial).filter(MomentsCoachMaterial.user_id == current_user.id, MomentsCoachMaterial.status == "active").order_by(MomentsCoachMaterial.created_at.desc()).limit(200).all()
    return {"ok": True, "items": [_material_payload(row) for row in rows]}


@router.post("/api/moments-coach/materials")
def create_material(body: MaterialBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = _snapshot(body)
    row = MomentsCoachMaterial(user_id=current_user.id, **data)
    db.add(row); db.commit(); db.refresh(row)
    return {"ok": True, "item": _material_payload(row)}


@router.patch("/api/moments-coach/materials/{material_id}")
def update_material(material_id: int, body: MaterialBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(MomentsCoachMaterial).filter(MomentsCoachMaterial.id == material_id, MomentsCoachMaterial.user_id == current_user.id).first()
    if row is None: raise HTTPException(404, "素材不存在")
    for key, value in _snapshot(body).items(): setattr(row, key, value)
    row.updated_at = datetime.utcnow(); db.commit(); db.refresh(row)
    return {"ok": True, "item": _material_payload(row)}


@router.delete("/api/moments-coach/materials/{material_id}")
def delete_material(material_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(MomentsCoachMaterial).filter(MomentsCoachMaterial.id == material_id, MomentsCoachMaterial.user_id == current_user.id).first()
    if row is None: raise HTTPException(404, "素材不存在")
    row.status = "deleted"; row.updated_at = datetime.utcnow(); db.commit()
    return {"ok": True}


async def _generate_now(body: GenerateBody, request: Request, current_user: User, db: Session, token_override: str = ""):
    material = _snapshot(body)
    if not any(material.get(key) for key in ("happened", "customer_problem", "customer_question", "desired_result", "current_change")):
        raise HTTPException(400, "请至少填写一项真实素材")
    persona_row = (db.query(IPContentScheduleTemplate).filter(IPContentScheduleTemplate.user_id == current_user.id,
        IPContentScheduleTemplate.name == _PERSONAL_DEFAULT_TEMPLATE_NAME, IPContentScheduleTemplate.status == "active").order_by(IPContentScheduleTemplate.id.desc()).first())
    persona = _personal_default_template_payload_with_resources(db, persona_row)
    if not persona_row or not persona.get("requirements"):
        raise HTTPException(400, "请先在个人设置中完成 IP 人设定位")
    circle = body.circle_type if body.circle_type in _CIRCLE_TYPES else "自动判断"
    system = """你是朋友圈成交文案教练。只能依据真实素材写作，不得编造成交、反馈或客户隐私。固定五种圈型：生活圈、咨询圈、反馈圈、收款圈、促成交圈；一条朋友圈只完成一个任务。禁止稳赚、保证、100%、第一等绝对化表达。先判断圈型，再输出三个版本：稳妥版、真人聊天版、成交推进版。输出严格 JSON：{\"items\":[{\"circle_type\":\"\",\"version_type\":\"\",\"title\":\"\",\"body\":\"\",\"image_suggestion\":\"\",\"suggested_publish_at\":\"\",\"transition\":\"\",\"compliance_warnings\":[]}]}。"""
    user = json.dumps({"persona": persona.get("requirements") or {}, "material": material, "requested_circle": circle, "count": body.count}, ensure_ascii=False)
    token = (token_override or request.headers.get("Authorization") or "").strip()
    if not token: raise HTTPException(401, "缺少登录凭证")
    data = await _post_llm_with_retry(payload={"model": "deepseek-chat", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "stream": False, "temperature": 0.72}, headers={"Authorization": token, "Content-Type": "application/json", "Accept": "application/json"}, attempts=2, timeout_seconds=240)
    try: raw = data["choices"][0]["message"]["content"]
    except Exception: raw = ""
    try:
        parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        items = parsed.get("items") if isinstance(parsed, dict) else []
    except Exception: items = []
    if not isinstance(items, list) or not items: raise HTTPException(502, "AI 未返回有效文案")
    group_id = uuid.uuid4().hex
    saved = []
    for idx, item in enumerate(items[:3]):
        if not isinstance(item, dict): continue
        circle_value = item.get("circle_type") if item.get("circle_type") in _CIRCLE_TYPES else (body.circle_type if body.circle_type in _CIRCLE_TYPES else "生活圈")
        version = item.get("version_type") if item.get("version_type") in _VERSION_TYPES else _VERSION_TYPES[min(idx, 2)]
        meta = {"group_id": group_id, "circle_type": circle_value, "version_type": version, "material_snapshot": material, "persona_snapshot": persona.get("requirements") or {}, "image_suggestion": _clean_long_text(item.get("image_suggestion"), 2000), "suggested_publish_at": _clean_text(item.get("suggested_publish_at"), 80), "transition": _clean_long_text(item.get("transition"), 2000), "compliance_warnings": item.get("compliance_warnings") if isinstance(item.get("compliance_warnings"), list) else [], "human_confirmed": False}
        row = IPContentDraftRecord(record_id=uuid.uuid4().hex, user_id=current_user.id, task="moments_sales_coach", platform="wechat_moments", title=_clean_text(item.get("title"), 500), content=_clean_long_text(item.get("body"), 8000), image_prompt=meta["image_suggestion"], meta=meta)
        db.add(row); saved.append(row)
    if not saved: raise HTTPException(502, "AI 返回内容为空")
    db.commit()
    return {"ok": True, "group_id": group_id, "items": [{"record_id": row.record_id, "title": row.title or "", "body": row.content or "", **(row.meta or {})} for row in saved]}


async def _run_generation_job(job_id: str, body_data: dict[str, Any], user_id: int, token: str) -> None:
    """Run LLM generation after the HTTP request has returned."""
    db = SessionLocal()
    try:
        job = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.record_id == job_id, IPContentDraftRecord.task == "moments_sales_coach_job").first()
        if not job:
            return
        meta = dict(job.meta or {}); meta.update({"status": "processing"}); job.meta = meta; job.updated_at = datetime.utcnow(); db.commit()
        async with _generation_semaphore:
            body = GenerateBody.model_validate(body_data)
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise RuntimeError("用户不存在")
            result = await _generate_now(body, _RequestHeaders(token), user, db, token_override=token)
        meta = dict(job.meta or {}); meta.update({"status": "completed", "group_id": result.get("group_id"), "items": result.get("items", [])}); job.meta = meta; job.updated_at = datetime.utcnow(); db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.record_id == job_id, IPContentDraftRecord.task == "moments_sales_coach_job").first()
        if job:
            job.meta = {**(job.meta or {}), "status": "failed", "error": str(exc)[:1000]}; job.updated_at = datetime.utcnow(); db.commit()
    finally:
        db.close()


class _RequestHeaders:
    def __init__(self, token: str):
        self.headers = {"Authorization": token}


@router.post("/api/moments-coach/generate")
async def generate(body: GenerateBody, request: Request, background: BackgroundTasks, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    material = _snapshot(body)
    if not any(material.get(key) for key in ("happened", "customer_problem", "customer_question", "desired_result", "current_change")):
        raise HTTPException(400, "请至少填写一项真实素材")
    token = (request.headers.get("Authorization") or "").strip()
    if not token:
        raise HTTPException(401, "缺少登录凭证")
    job_id = uuid.uuid4().hex
    job = IPContentDraftRecord(record_id=job_id, user_id=current_user.id, task="moments_sales_coach_job", platform="wechat_moments", title="朋友圈文案生成", content="", meta={"status": "pending", "created_at": datetime.utcnow().isoformat()})
    db.add(job); db.commit()
    background.add_task(_run_generation_job, job_id, body.model_dump(), int(current_user.id), token)
    return {"ok": True, "job_id": job_id, "status": "pending"}


@router.get("/api/moments-coach/generate/{job_id}")
def generation_status(job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.record_id == _clean_text(job_id, 64), IPContentDraftRecord.user_id == current_user.id, IPContentDraftRecord.task == "moments_sales_coach_job").first()
    if row is None:
        raise HTTPException(404, "生成任务不存在")
    meta = row.meta or {}
    return {"ok": True, "job_id": row.record_id, "status": meta.get("status", "pending"), "items": meta.get("items", []), "error": meta.get("error", "")}


@router.get("/api/moments-coach/history")
def history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == current_user.id, IPContentDraftRecord.task == "moments_sales_coach").order_by(IPContentDraftRecord.created_at.desc()).limit(200).all()
    return {"ok": True, "items": [{"record_id": row.record_id, "title": row.title or "", "body": row.content or "", "created_at": row.created_at.isoformat() if row.created_at else None, **(row.meta or {})} for row in rows]}


@router.post("/api/moments-coach/plans")
def save_plan(body: PlanBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = MomentsCoachPlan(user_id=current_user.id, name=_clean_text(body.name, 180) or "朋友圈一周排期", meta={"human_confirmation_required": True})
    db.add(plan); db.flush()
    for index, item in enumerate(body.items[:14]):
        db.add(MomentsCoachPlanItem(plan_id=plan.id, user_id=current_user.id, draft_record_id=_clean_text(item.get("draft_record_id"), 64) or None, circle_type=_clean_text(item.get("circle_type"), 32) or "生活圈", publish_at=_parse_dt(item.get("publish_at")), sort_order=index, meta={"title": _clean_text(item.get("title"), 500)}))
    db.commit(); db.refresh(plan)
    return {"ok": True, "plan_id": plan.id}


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception: return None


@router.get("/api/moments-coach/plans")
def list_plans(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plans = db.query(MomentsCoachPlan).filter(MomentsCoachPlan.user_id == current_user.id).order_by(MomentsCoachPlan.created_at.desc()).limit(50).all()
    result = []
    for plan in plans:
        items = db.query(MomentsCoachPlanItem).filter(MomentsCoachPlanItem.plan_id == plan.id).order_by(MomentsCoachPlanItem.sort_order.asc()).all()
        result.append({"id": plan.id, "name": plan.name, "status": plan.status, "items": [{"id": i.id, "draft_record_id": i.draft_record_id, "circle_type": i.circle_type, "publish_at": i.publish_at.isoformat() if i.publish_at else None, "meta": i.meta or {}} for i in items]})
    return {"ok": True, "items": result}


@router.post("/api/moments-coach/{record_id}/publish-request")
def publish_request(record_id: str, body: PublishBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == current_user.id, IPContentDraftRecord.record_id == _clean_text(record_id, 64), IPContentDraftRecord.task == "moments_sales_coach").first()
    if row is None: raise HTTPException(404, "文案记录不存在")
    if not body.account_id and not body.account_nickname: raise HTTPException(400, "请选择微信朋友圈账号")
    meta = dict(row.meta or {}); meta["human_confirmed"] = True; row.meta = meta
    draft = {"platform": "wechat_moments", "platform_name": "微信朋友圈", "account_id": body.account_id, "account_nickname": body.account_nickname, "installation_id": body.installation_id, "title": row.title or "", "description": row.content or "", "content": row.content or "", "image_urls": body.image_urls or meta.get("material_snapshot", {}).get("image_urls", []), "image_asset_ids": body.image_asset_ids, "media_type": "image_text", "status": "pending", "coach_record_id": row.record_id}
    if not draft["image_urls"] and not draft["image_asset_ids"]: raise HTTPException(400, "请先为朋友圈文案选择图片")
    run = ScheduledTaskRun(id=uuid.uuid4().hex, task_id=None, user_id=current_user.id, created_by_user_id=current_user.id, created_by_role="user", installation_id=body.installation_id or None, title=("朋友圈发布：" + (row.title or "朋友圈文案"))[:160], task_kind="content_publish", content=row.content or "", payload={"action": "publish_content", "source": "moments_coach", "record_id": row.record_id}, status="completed", progress={"status": "completed"}, result_text="等待客户端发布", result_payload={"publish_draft": draft}, created_at=datetime.utcnow(), updated_at=datetime.utcnow(), started_at=datetime.utcnow(), finished_at=datetime.utcnow())
    db.add(run); db.commit()
    return {"ok": True, "status": "pending", "run_id": run.id}


@router.post("/api/moments-coach/{record_id}/generate-image")
async def generate_image(record_id: str, body: ImageBody, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(IPContentDraftRecord).filter(IPContentDraftRecord.user_id == current_user.id, IPContentDraftRecord.record_id == _clean_text(record_id, 64), IPContentDraftRecord.task == "moments_sales_coach").first()
    if row is None: raise HTTPException(404, "文案记录不存在")
    prompt = _clean_long_text(body.prompt or (row.meta or {}).get("image_suggestion") or row.content or row.title, 2000)
    if not prompt: raise HTTPException(400, "缺少配图提示")
    token = (request.headers.get("Authorization") or "").strip()
    if not token: raise HTTPException(401, "缺少登录凭证")
    payload = {"model": _clean_text(body.model, 120) or "openai/gpt-image-2", "prompt": prompt, "size": _clean_text(body.size, 32) or "1024x1024", "n": 1}
    timeout = httpx.Timeout(240.0, connect=15.0, read=240.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(f"{_internal_api_base()}/api/comfly-proxy/v1/images/generations", json=payload, headers={"Authorization": token, "Content-Type": "application/json", "Accept": "application/json"})
    try: data = response.json()
    except Exception: data = {"error": response.text[:1000]}
    if response.status_code >= 400: raise HTTPException(response.status_code, data.get("error") or data.get("detail") or "配图生成失败")
    images = data.get("data") if isinstance(data, dict) else []
    first = images[0] if isinstance(images, list) and images else {}
    image_url = (first.get("url") or first.get("b64_json") or "") if isinstance(first, dict) else ""
    if not image_url: raise HTTPException(502, "图片路由未返回图片")
    meta = dict(row.meta or {}); meta["generated_images"] = [image_url] + [x for x in (meta.get("generated_images") or [])[1:] if x != image_url]; row.meta = meta; row.image_url = image_url; row.updated_at = datetime.utcnow(); db.commit()
    return {"ok": True, "record_id": row.record_id, "image_url": image_url, "images": meta["generated_images"]}
