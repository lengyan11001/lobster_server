from __future__ import annotations

import json
import logging
import re
from html import unescape
from html.parser import HTMLParser
from decimal import Decimal

import httpx

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
from ..services.douyin_platform_information_desk import (VIDEO_SEARCH_MAX_KEYWORDS, information_desk_response,
                                                        search_information_desk, search_tikhub_douyin_videos,
                                                        tikhub_video_search_price)
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
    video_seconds: float = 0
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
    input_guess = float(body.video_seconds or 0)
    if input_guess > 0:
        output_guess = float(body.duration_seconds or 0) or input_guess
        cap_seconds = min(float(_max_seconds()), input_guess) + output_guess
    else:
        cap_seconds = float(_max_seconds())
    cap_plan = billing.estimate_imitation(cap_seconds, body.resolution, sides=billing.Decimal("1"))
    billing.check_balance(db, current_user, billing.Decimal(str(cap_plan["credits"])))
    # 2026-11-07 线上事故：check_balance 会开启一个只读事务，随后 prepare_imitation
    # 下载/裁剪/上传原视频可能耗时 1-3 分钟；Postgres 的
    # idle_in_transaction_session_timeout 会在这个空等期间杀掉连接，
    # 等上游任务已提交、再扣费时就会 PendingRollbackError -> 500。
    # 在慢调用前显式结束事务，扣费/落库时重新开新事务。
    try:
        db.commit()
    except Exception:
        db.rollback()
    result = await prepare_imitation(body.image_url, body.item_id, body.prompt,
                                     video_url=body.video_url, mode=body.mode,
                                     resolution=body.resolution,
                                     duration_seconds=body.duration_seconds)
    if not result.get("ok"):
        logger.warning(
            "[douyin-imitation] submit failed user_id=%s item_id=%s video=%s image=%s error=%s",
            int(current_user.id),
            str(body.item_id or "")[:64],
            str(body.video_url or "")[:160],
            str(body.image_url or "")[:160],
            str(result.get("error") or "")[:500],
        )
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
    except Exception as exc:  # 2026-11-07: 数据库事务异常也不能丢已提交的上游任务
        logger.exception("[douyin-imitation] 扣费异常（任务已提交）: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
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
                meta={
                    "asset_origin": "generated",
                    "source": "douyin_imitation",
                    "task_id": str(row.task_id or ""),
                },
            ))
    db.commit()
    if asset_id:
        # 以前只写 assets，不写内容记录：客户端「内容记录」里永远看不到跟创成片。
        try:
            from . import canvas_hub

            canvas_hub.register_content_record(
                db,
                int(user.id),
                public_url,
                media_type="video",
                title=str(row.title or "") or "热门视频跟创",
                task_id=str(row.task_id or ""),
                model=str(row.model or ""),
                prompt=str(row.prompt or ""),
            )
        except Exception as exc:  # noqa: BLE001 内容记录失败不能影响入库结果
            logger.warning("[douyin-imitation] 写内容记录失败 task=%s: %s", row.task_id, exc)
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


@router.get("/api/douyin/platform-information-desk/search", summary="按关键词搜抖音（默认实时走 TikHub）")
async def search_douyin_platform_information_desk(
    q: str = Query(..., min_length=1, max_length=120, description="关键词，逗号 / 空格分隔多个"),
    limit: int = Query(60, ge=1, le=200),
    days: int = Query(7, ge=1, le=30, description="snapshot 模式：检索最近几天的榜单快照"),
    source: str = Query("tikhub", description="tikhub=实时搜抖音（TikHub，按次计费）；snapshot=只搜服务端已入库快照"),
    sort_type: str = Query("0", description="0 综合 / 1 最多点赞 / 2 最新发布（tikhub 模式）"),
    publish_time: str = Query("0", description="0 不限 / 1 最近一天 / 7 最近一周 / 180 最近半年（tikhub 模式）"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """2026-10-11：搜索改成「拿用户关键词实时问 TikHub」，而不是只翻服务端当天快照。

    - tikhub（默认）：`POST /api/v1/douyin/search/fetch_video_search_v2`，每个关键词 1 次请求（0.01 USD/次），
      按上游真实成本扣费（≈7.5 算力/次）；结果自带 aweme_id，卡片上可以直接「跟创」。
    - snapshot：沿用本地已入库快照（1 算力/次），作为上游不可用时的兜底。
    """
    _require_information_desk_access(current_user, db)
    keywords = [part for part in q.replace(",", " ").replace("，", " ").split(" ") if part.strip()]
    mode = "snapshot" if str(source or "").strip().lower() == "snapshot" else "tikhub"

    if mode == "snapshot":
        charged = billing.deduct(db, current_user, billing.search_credits(), reason="douyin_desk_search")
        payload = search_information_desk(db, keywords, limit, days)
        payload["source"] = "snapshot"
        payload["billing"] = {"credits_charged": credits_json_float(charged),
                              "unit_price_credits": credits_json_float(billing.search_credits())}
        return payload

    price = tikhub_video_search_price()
    per_call = Decimal(str(price.get("lobster_credits") or 0))
    cap = min(VIDEO_SEARCH_MAX_KEYWORDS, max(1, len(keywords)))
    billing.check_balance(db, current_user, per_call * cap)      # 先确认余额够，别白跑上游
    live = await search_tikhub_douyin_videos(keywords, limit=limit, sort_type=sort_type, publish_time=publish_time)
    calls = int(live.get("calls") or 0)

    if calls <= 0:
        # 没配 TIKHUB_API_KEY / 关键词为空：回退本地快照，别让用户点了没反应
        charged = billing.deduct(db, current_user, billing.search_credits(), reason="douyin_desk_search")
        payload = search_information_desk(db, keywords, limit, days)
        payload["source"] = "snapshot"
        payload["notice"] = "；".join(str(item) for item in (live.get("errors") or [])) or "TikHub 不可用，已回退本地快照"
        payload["billing"] = {"credits_charged": credits_json_float(charged),
                              "unit_price_credits": credits_json_float(billing.search_credits())}
        return payload

    charged = billing.deduct(db, current_user, per_call * calls, reason="douyin_desk_search_tikhub")
    payload = {
        "source": "tikhub",
        "endpoint": live.get("endpoint"),
        "keywords": live.get("keywords"),
        "snapshot_date": None,
        "days": days,
        "count": int(live.get("count") or 0),
        "items": live.get("items") or [],
        "provider_calls": calls,
        "provider_cost_usd": live.get("provider_cost_usd"),
        "errors": live.get("errors") or [],
        "paging": live.get("paging") or {},
    }
    if not payload["items"]:
        # 抖音搜不到：拿本地快照给推荐词 + 当天热榜兜底（本地检索不额外花钱）
        local = search_information_desk(db, keywords, limit, days)
        payload["snapshot_date"] = local.get("snapshot_date")
        payload["fallback_items"] = local.get("fallback_items") or []
        payload["suggestions"] = local.get("suggestions") or []
        payload["fallback_reason"] = "抖音实时搜索没有匹配「%s」的作品" % "、".join(live.get("keywords") or keywords)
    payload["billing"] = {
        "credits_charged": credits_json_float(charged),
        "unit_price_credits": credits_json_float(per_call),
        "provider_calls": calls,
        "provider_cost_usd": live.get("provider_cost_usd"),
    }
    return payload


# 热门视频跟创：分享链接解析（抖音 / 视频号 / 快手等，与 Online 同一个解析服务）
_VIRAL_SHARE_API = "https://watermark-api.hlyphp.top/Watermark/Index"
_VIRAL_SHARE_APPID = "682e90c19520118284FGb2"
_URL_IN_TEXT_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&'*+,;=%]+", re.I)


class ShareVideoResolveBody(BaseModel):
    share_text: str = Field(..., min_length=1, max_length=4000)


@router.post("/api/viral-video-remix/share-video/resolve", summary="解析抖音/视频号分享链接为视频直链")
async def resolve_viral_share_video(
    body: ShareVideoResolveBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_information_desk_access(current_user, db)
    text = str(body.share_text or "").strip().replace("\\_", "_")
    match = _URL_IN_TEXT_RE.search(text)
    if not match:
        raise HTTPException(status_code=400, detail="未识别到分享链接，请粘贴包含 http/https 的完整分享文案")
    share_url = match.group(0).rstrip("，。,.);）]】」》")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(
                _VIRAL_SHARE_API,
                params={"appid": _VIRAL_SHARE_APPID, "link": share_url},
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="视频解析超时，请稍后重试") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[viral-share] request failed user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail=f"视频解析请求失败：{exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"视频解析服务 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="视频解析服务返回格式异常") from exc
    if not isinstance(payload, dict) or payload.get("code") != 1:
        code = payload.get("code") if isinstance(payload, dict) else None
        mapping = {
            -1: "该账号已被禁用",
            -100: "当前 IP 不在解析服务白名单",
            109: "解析套餐次数不足",
            301: "未检测到链接，请检查分享文案是否完整",
            400: "提取链接无效或暂不支持该平台",
        }
        detail = mapping.get(code) or str((payload or {}).get("msg") or "视频解析失败")
        raise HTTPException(status_code=502, detail=detail)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    direct = str(data.get("videoSrc") or data.get("videourl") or "").strip()
    if not direct.startswith(("http://", "https://")):
        raise HTTPException(status_code=502, detail="解析成功但没有返回可用的视频地址")
    # 下载并转存 TOS，避免抖音/视频号 CDN 的 Referer 限制，后续跟创提交也快
    source_url = direct
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0), follow_redirects=True, trust_env=False) as client:
            video_resp = await client.get(
                direct,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
                    "Referer": "https://www.douyin.com/",
                },
            )
        if video_resp.status_code == 200 and video_resp.content:
            import asyncio as _asyncio
            import secrets as _secrets
            from .assets import _upload_to_tos as _tos_upload

            key = "assets/share_%s.mp4" % _secrets.token_hex(8)
            stored = await _asyncio.to_thread(_tos_upload, video_resp.content, key, "video/mp4")
            if stored:
                source_url = stored
    except Exception:  # noqa: BLE001 - 转存失败时回退到直链
        logger.warning("[viral-share] tos store failed, fallback to direct url", exc_info=True)
    return {
        "ok": True,
        "share_url": share_url,
        "direct_video_url": direct,
        "source_url": source_url,
        "url": source_url,
        "title": str(data.get("title") or ""),
        "cover_url": str(data.get("imageSrc") or ""),
    }


# 公众号文章复刻：抓取 mp.weixin.qq.com 正文
class WechatArticleFetchBody(BaseModel):
    url: str = Field("", max_length=2000)


class _WechatArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.in_title = False
        self.title_parts = []
        self.content_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "js_content":
            self.in_content = True
        if tag == "title":
            self.in_title = True
        if self.in_content and tag in ("p", "br", "div", "section", "h1", "h2", "h3", "li", "blockquote", "tr"):
            self.content_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.in_content and tag in ("p", "div", "section", "h1", "h2", "h3", "li", "blockquote", "tr"):
            self.content_parts.append("\n")

    def handle_data(self, data):
        text = str(data or "").strip()
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self.in_content:
            self.content_parts.append(text)


@router.post("/api/wechat-article/fetch", summary="抓取公众号文章正文（复刻用）")
async def fetch_wechat_article_body(
    body: WechatArticleFetchBody,
    current_user: User = Depends(get_current_user),
):
    url = str(body.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请粘贴完整的公众号文章链接")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                                  "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[wechat-article] fetch failed user_id=%s", current_user.id)
        raise HTTPException(status_code=502, detail=f"抓取文章失败：{exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"抓取文章失败：HTTP {resp.status_code}")
    html_text = resp.text or ""
    parser = _WechatArticleParser()
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001
        pass
    content = re.sub(r"\n{3,}", "\n\n", "".join(parser.content_parts)).strip()
    title = "".join(parser.title_parts).strip()
    if not title:
        match = re.search(r'property="og:title"\s+content="([^"]+)"', html_text)
        if match:
            title = unescape(match.group(1)).strip()
    if not content:
        raise HTTPException(status_code=422, detail="没有从这篇文章里读到正文，请确认链接是公众号文章页")
    return {
        "ok": True,
        "url": url,
        "title": title,
        "content": content[:12000],
        "length": len(content),
    }
