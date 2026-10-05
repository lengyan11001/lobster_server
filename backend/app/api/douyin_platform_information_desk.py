from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Asset, DouyinImitationTask, User
from ..services.credits_amount import credits_json_float
from ..services import douyin_desk_billing as billing
from ..services.douyin_imitation_video import (_max_seconds, fetch_video_bytes, prepare_imitation,
                                              query_imitation, store_generated_video)
from ..services.douyin_platform_information_desk import information_desk_response, search_information_desk
from ..services.user_feature_flags import (
    DOUYIN_PLATFORM_INFORMATION_DESK_ACCESS_KEY,
    DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID,
    user_has_feature,
)
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_information_desk_access(user: User, db: Session) -> None:
    if str(getattr(user, "role", "") or "").strip().lower() == "admin":
        return
    if not (
        user_has_feature(db, int(user.id), DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID)
        or user_has_feature(db, int(user.id), DOUYIN_PLATFORM_INFORMATION_DESK_ACCESS_KEY)
    ):
        raise HTTPException(status_code=403, detail="未开通抖音平台信息台权限")


@router.get("/api/douyin/platform-information-desk", summary="读取 TikHub 抖音平台公共数据快照")
def get_douyin_platform_information_desk(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the latest server snapshot; this endpoint never calls TikHub."""
    _require_information_desk_access(current_user, db)
    return information_desk_response(db)


class ImitationIn(BaseModel):
    """跟创入参：参考图 + 视频来源（榜单作品 id 或用户给的视频地址）+ 模式。

    2026-10-05：信息台改名「热门视频跟创」；视频来源除了榜单作品，还支持用户
    粘贴视频直链或上传本地视频（上传后拿到的公网地址同样走 video_url）。
    """

    image_url: str = Field(default="", max_length=2000)
    item_id: str = Field(default="", max_length=64)
    video_url: str = Field(default="", max_length=2000)
    mode: str = "person_swap"
    resolution: str = "720P"
    # 2026-10-05：成片时长放给用户选（秒）。0 = 跟输入视频一样长；
    # 上游/手册限制：有视频输入时「输入 + 输出 ≤ 30 秒」，最短 2 秒。
    duration_seconds: int = 0
    title: str = ""
    prompt: str = ""


@router.post("/api/douyin/platform-information-desk/imitation", summary="抖音信息台做同款：一张参考图生成同款风格视频")
async def create_information_desk_imitation(
    body: ImitationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_information_desk_access(current_user, db)
    if not str(body.video_url or "").strip() and not str(body.item_id or "").strip():
        raise HTTPException(status_code=400, detail="请先填视频链接、上传本地视频，或从榜单里点「跟创」")
    if not str(body.image_url or "").strip():
        raise HTTPException(status_code=400, detail="请上传一张参考图（不传的话先在「IP人设定位」里放一张形象照）")
    # 上游按「输入视频 + 输出视频」秒数计费，这里先按裁剪后的秒数预扣，失败全额退
    # 2026-10-05：改成按「原视频实际时长」计费 —— 先只按上限校验余额（不扣），
    # 等 prepare 探到真实时长后再按实际秒数扣费（失败/提交不成功不扣）。
    cap_plan = billing.estimate_imitation(_max_seconds(), body.resolution)
    billing.check_balance(db, current_user, billing.Decimal(str(cap_plan["credits"])))
    result = await prepare_imitation(body.image_url, body.item_id, body.prompt,
                                     video_url=body.video_url, mode=body.mode,
                                     resolution=body.resolution,
                                     duration_seconds=body.duration_seconds)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "做同款提交失败")
    # 计费口径：输入视频秒数 + 输出视频秒数（上游就是这么计的）。用户选了成片时长时，
    # 输出按用户选的算；没选就按输入等长（跟老逻辑一致）。
    input_seconds = int(result.get("input_seconds") or result.get("video_seconds") or _max_seconds())
    output_seconds = int(result.get("output_seconds") or input_seconds)
    total_seconds = input_seconds + output_seconds
    plan = billing.estimate_imitation(total_seconds, result.get("resolution") or body.resolution,
                                      sides=billing.Decimal("1"))
    # 兼容老字段语义：seconds=输入秒数，billable_seconds=输入+输出，另给 total_seconds
    plan["seconds"] = input_seconds
    plan["total_seconds"] = total_seconds
    try:
        charged = billing.deduct(db, current_user, billing.Decimal(str(plan["credits"])),
                                 reason=f"douyin_imitation:{body.item_id or str(body.video_url)[:80]}")
    except HTTPException as exc:  # 提交前已校验过余额，这里兜底不把已提交的任务搞丢
        logger.warning("[douyin-imitation] 扣费失败（任务已提交）: %s", getattr(exc, "detail", exc))
        charged = billing.Decimal("0")
    row = DouyinImitationTask(
        user_id=int(current_user.id), task_id=str(result.get("task_id") or ""),
        item_id=str(body.item_id or ""), title=str(body.title or "")[:255],
        source_desc=(f"{result.get('resolution') or body.resolution} · "
                     f"{result.get('mode_label') or '复刻人物（换人）'} · "
                     f"{output_seconds}s · "
                     f"{result.get('source_desc') or ''}")[:255],
        provider=str(result.get("provider") or ""), model=str(result.get("model") or ""),
        prompt=str(result.get("prompt") or ""), status="RUNNING",
        upstream_request=json.dumps(result.get("request_body") or {}, ensure_ascii=False)[:20000],
        upstream_response=json.dumps({"submit": result.get("response_body") or ""}, ensure_ascii=False)[:20000],
        image_url=str(result.get("image_url") or ""),
        source_video_url=str(result.get("video_url") or ""),
        billable_seconds=int(plan["billable_seconds"]),
        credits_charged=charged,
    )
    db.add(row)
    db.commit()
    result["history_id"] = row.id
    result["input_seconds"] = input_seconds
    result["output_seconds"] = output_seconds
    result["billing"] = {**plan, "input_seconds": input_seconds, "output_seconds": output_seconds,
                         "credits_charged": credits_json_float(charged)}
    return result


def _task_payload(row: DouyinImitationTask) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "item_id": row.item_id,
        "title": row.title,
        "source_desc": row.source_desc,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "progress": row.progress,
        "video_url": row.video_url,
        "fail_reason": row.fail_reason,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "asset_id": row.asset_id or "",
        "download_url": (f"/api/douyin/platform-information-desk/imitation/{row.task_id}/download"
                         if row.task_id else ""),
        "billable_seconds": int(row.billable_seconds or 0),
        "credits_charged": credits_json_float(row.credits_charged or 0),
        "credits_refunded": credits_json_float(row.credits_refunded or 0),
    }


async def _refresh_task(db: Session, row: DouyinImitationTask) -> DouyinImitationTask:
    """非终态的任务顺手刷一下状态（只刷这一条，成本可控）。"""
    if row.status not in {"RUNNING", ""}:
        return row
    result = await query_imitation(row.task_id)
    if not result.get("ok"):
        return row
    row.status = str(result.get("status") or row.status)
    row.progress = str(result.get("progress") or "")
    row.video_url = str(result.get("video_url") or row.video_url or "")
    row.fail_reason = str(result.get("fail_reason") or "")[:255]
    db.commit()
    return row


@router.get("/api/douyin/platform-information-desk/imitation/history", summary="做同款生成历史")
async def list_information_desk_imitation(
    limit: int = Query(20, ge=1, le=50),
    refresh: int = Query(1, ge=0, le=1, description="是否顺带刷新未完成的任务状态"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出自己发起过的做同款任务；点击记录可以回到那次的成片。"""
    _require_information_desk_access(current_user, db)
    rows = (db.query(DouyinImitationTask)
            .filter(DouyinImitationTask.user_id == int(current_user.id))
            .order_by(desc(DouyinImitationTask.id))
            .limit(limit).all())
    if refresh:
        refreshed = 0
        for row in rows:
            if refreshed >= 3:
                break
            if row.status in {"RUNNING", ""}:
                await _refresh_task(db, row)
                refreshed += 1
    return {"items": [_task_payload(row) for row in rows], "count": len(rows)}


async def _store_task_video(db: Session, user: User, row: DouyinImitationTask) -> dict:
    """把成片转存进我们自己的存储（TOS）并登记到素材库；阿里云出的链接会过期。"""
    source = str(row.stored_url or row.video_url or "").strip()
    if not source or row.asset_id:
        return {"ok": bool(row.asset_id), "url": row.stored_url or row.video_url, "asset_id": row.asset_id}
    stored = await store_generated_video(source, title=row.title or row.task_id)
    if not stored.get("ok"):
        return {"ok": False, "error": stored.get("error") or "成片入库失败"}
    public_url = str(stored.get("public_url") or "")
    asset_id = str(stored.get("asset_id") or "")
    row.stored_url = public_url
    row.video_url = public_url
    row.asset_id = asset_id
    row.file_size = int(stored.get("file_size") or 0)
    if asset_id:
        exists = db.query(Asset).filter(Asset.asset_id == asset_id).first()
        if exists is None:
            db.add(Asset(
                asset_id=asset_id,
                user_id=int(user.id),
                filename=f"douyin-imitation-{row.task_id}.mp4",
                media_type="video",
                file_size=int(stored.get("file_size") or 0),
                source_url=public_url,
                tags="douyin,imitation,generated",
            ))
    db.commit()
    return {"ok": True, "url": public_url, "asset_id": asset_id}


@router.get("/api/douyin/platform-information-desk/imitation/{task_id}/download",
            summary="下载做同款成片（未入库会先入库）")
async def download_information_desk_imitation(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_information_desk_access(current_user, db)
    row = (db.query(DouyinImitationTask)
           .filter(DouyinImitationTask.task_id == str(task_id),
                   DouyinImitationTask.user_id == int(current_user.id))
           .first())
    if row is None:
        raise HTTPException(status_code=404, detail="没有这条生成记录")
    if not row.asset_id and row.status == "SUCCESS":
        stored = await _store_task_video(db, current_user, row)
        if not stored.get("ok"):
            raise HTTPException(status_code=502, detail=stored.get("error") or "成片入库失败")
    source = str(row.stored_url or row.video_url or "").strip()
    if not source:
        raise HTTPException(status_code=409, detail="这次任务还没有成片（生成中或已失败）")
    data, err = await fetch_video_bytes(source)
    if err or not data:
        raise HTTPException(status_code=502, detail=err or "取成片失败")
    filename = f"douyin-imitation-{row.id or task_id}.mp4"
    return Response(content=data, media_type="video/mp4",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Content-Length": str(len(data))})


@router.get("/api/douyin/platform-information-desk/imitation/{task_id}", summary="查询做同款任务进度")
async def get_information_desk_imitation(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_information_desk_access(current_user, db)
    result = await query_imitation(task_id)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "做同款查询失败")
    row = (db.query(DouyinImitationTask)
           .filter(DouyinImitationTask.task_id == str(task_id),
                   DouyinImitationTask.user_id == int(current_user.id))
           .first())
    if row is not None:
        row.status = str(result.get("status") or row.status)
        row.progress = str(result.get("progress") or "")
        row.video_url = str(result.get("video_url") or row.video_url or "")
        row.fail_reason = str(result.get("fail_reason") or "")[:255]
        # 管理后台要看上游每次返回了什么：把最新一次查询结果并进 upstream_response
        try:
            history = json.loads(row.upstream_response or "{}")
        except Exception:  # noqa: BLE001
            history = {}
        if not isinstance(history, dict):
            history = {}
        if result.get("response_body"):
            history["last_query"] = result.get("response_body")
        row.upstream_response = json.dumps(history, ensure_ascii=False)[:20000]
        db.commit()
        if row.status == "FAILED" and row.credits_charged and not row.credits_refunded:
            billing.refund(db, current_user, billing.Decimal(str(row.credits_charged)),
                           reason="douyin_imitation_failed")
            row.credits_refunded = row.credits_charged
            db.commit()
        if row.status == "SUCCESS" and not row.asset_id:
            stored = await _store_task_video(db, current_user, row)
            if stored.get("ok"):
                result["video_url"] = stored.get("url") or result.get("video_url")
                result["stored_url"] = stored.get("url")
                result["asset_id"] = stored.get("asset_id")
                result["download_url"] = f"/api/douyin/platform-information-desk/imitation/{task_id}/download"
    return result


@router.get("/api/douyin/platform-information-desk/search", summary="按关键词搜自己关注的榜单数据")
def search_douyin_platform_information_desk(
    q: str = Query(..., min_length=1, max_length=120, description="关键词，逗号 / 空格分隔多个"),
    limit: int = Query(60, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """只检索服务器已入库的最新快照（内容榜 + 热点榜），不再额外请求 TikHub。"""
    _require_information_desk_access(current_user, db)
    keywords = [part for part in q.replace(",", " ").replace("，", " ").split(" ") if part.strip()]
    charged = billing.deduct(db, current_user, billing.search_credits(), reason="douyin_desk_search")
    payload = search_information_desk(db, keywords, limit)
    payload["billing"] = {"credits_charged": credits_json_float(charged),
                          "unit_price_credits": credits_json_float(billing.search_credits())}
    return payload
