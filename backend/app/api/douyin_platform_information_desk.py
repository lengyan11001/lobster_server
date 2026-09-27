from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DouyinImitationTask, User
from ..services.douyin_imitation_video import prepare_imitation, query_imitation
from ..services.douyin_platform_information_desk import information_desk_response, search_information_desk
from ..services.user_feature_flags import (
    DOUYIN_PLATFORM_INFORMATION_DESK_ACCESS_KEY,
    DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID,
    user_has_feature,
)
from .auth import get_current_user

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
    """做同款（换人）入参：榜单作品 id + 用户上传的单人参考图公网地址。"""

    image_url: str = Field(min_length=8, max_length=2000)
    item_id: str = Field(min_length=6, max_length=40)
    title: str = ""
    prompt: str = ""


@router.post("/api/douyin/platform-information-desk/imitation", summary="抖音信息台做同款：一张参考图生成同款风格视频")
async def create_information_desk_imitation(
    body: ImitationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_information_desk_access(current_user, db)
    result = await prepare_imitation(body.image_url, body.item_id, body.prompt)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "做同款提交失败")
    row = DouyinImitationTask(
        user_id=int(current_user.id), task_id=str(result.get("task_id") or ""),
        item_id=str(body.item_id or ""), title=str(body.title or "")[:255],
        source_desc=str(result.get("source_desc") or "")[:255],
        provider=str(result.get("provider") or ""), model=str(result.get("model") or ""),
        prompt=str(result.get("prompt") or ""), status="RUNNING",
        image_url=str(result.get("image_url") or ""),
        source_video_url=str(result.get("video_url") or ""),
    )
    db.add(row)
    db.commit()
    result["history_id"] = row.id
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
        db.commit()
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
    return search_information_desk(db, keywords, limit)
