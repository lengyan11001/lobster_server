from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..services.douyin_imitation_video import prepare_imitation, query_imitation
from ..services.douyin_platform_information_desk import information_desk_response
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
    return result


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
    return result
