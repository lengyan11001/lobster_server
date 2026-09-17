"""发布数据（播放量）云端侧：接收客户端上报 + 查询看板/曲线。

链路：客户端（个人发布中心）每天 02:00（北京）采集抖音 / 视频号的已发布作品计数
→ POST /api/publish/metrics:batch 批量上报（100 条/批，幂等键 sample_key）
→ 云端 publish_metrics 保留最新值、publish_metric_events 保留按日轨迹
→ GET /api/publish/metrics 看板、GET /api/publish/metrics/summary 环比、
   GET /api/publish/metrics/items 单作品曲线。

口径：只做抖音与视频号（平台键 douyin / wechat_channels）；朋友圈本轮不做，上报即拒收并回因。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import get_current_user
from ..db import get_db
from ..models import PublishMetricEvent, PublishMetricSample, User

router = APIRouter()

# 只支持这两个平台；朋友圈（moments）不在其中
METRIC_PLATFORMS = ("douyin", "wechat_channels")
PLATFORM_LABELS = {"douyin": "抖音", "wechat_channels": "视频号"}

MAX_SAMPLES_PER_BATCH = 500
BEIJING_TZ = timezone(timedelta(hours=8))

_COUNTER_FIELDS = ("views", "likes", "comments", "shares", "favorites", "impressions")


class PublishMetricSampleItem(BaseModel):
    """单条作品指标；sample_key 缺省时按客户端同一公式推导，兼容旧客户端。"""

    sample_key: str = ""
    platform: str
    item_id: str
    item_url: Optional[str] = None
    title: Optional[str] = None
    account_id: Optional[int] = None
    account_nickname: Optional[str] = None
    published_at: Optional[str] = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0
    impressions: int = 0
    sampled_at: Optional[str] = None
    sampled_day: str = ""
    source: str = "daily_0200"


class PublishMetricBatchBody(BaseModel):
    """客户端批量上报体；installation_id 与 X-Installation-Id 二选一。"""

    installation_id: str = ""
    user_id: int = 0
    samples: list[PublishMetricSampleItem] = Field(default_factory=list)


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    raw = _clean_text(value, 64)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def beijing_day(dt_utc: Optional[datetime]) -> str:
    """裸 UTC → 北京自然日；缺失时用服务器当天（北京时间）。"""
    base = dt_utc or datetime.utcnow()
    if base.tzinfo is not None:
        base = base.astimezone(timezone.utc).replace(tzinfo=None)
    return (base + timedelta(hours=8)).strftime("%Y-%m-%d")


def derive_sample_key(*, installation_id: str, platform: str, item_id: str, sampled_day: str) -> str:
    """与客户端 creator_metrics_collect.sample_key 完全一致的幂等键。"""
    raw = f"{(installation_id or '').strip()}|{platform}|{item_id}|{sampled_day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_optional_str(value: Any) -> Optional[str]:
    """HTTP 走 Query 校验；直接函数调用时默认值可能是 Query 对象，这里只认字符串。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    number = _coerce_optional_int(value)
    if number is None:
        number = default
    return max(minimum, min(maximum, number))


def normalize_sample_item(
    item: PublishMetricSampleItem,
    *,
    installation_id: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """校验并归一单条上报；返回 (payload, 拒收原因)。"""
    platform = _clean_text(item.platform, 32).lower()
    if platform not in METRIC_PLATFORMS:
        return None, f"暂不支持该平台：{platform or '(空)'}（仅 {'/'.join(METRIC_PLATFORMS)}）"
    item_id = _clean_text(item.item_id, 128)
    if not item_id:
        return None, "缺少 item_id"
    sampled_at = _parse_dt(item.sampled_at)
    sampled_day = _clean_text(item.sampled_day, 10)
    if not sampled_day:
        sampled_day = beijing_day(sampled_at)
    key = _clean_text(item.sample_key, 64) or derive_sample_key(
        installation_id=installation_id,
        platform=platform,
        item_id=item_id,
        sampled_day=sampled_day,
    )
    payload: dict[str, Any] = {
        "sample_key": key,
        "platform": platform,
        "item_id": item_id,
        "item_url": _clean_text(item.item_url, 2000) or None,
        "title": _clean_text(item.title, 500) or None,
        "account_id": int(item.account_id) if item.account_id else None,
        "account_nickname": _clean_text(item.account_nickname, 128) or None,
        "published_at": _parse_dt(item.published_at),
        "sampled_at": sampled_at,
        "sampled_day": sampled_day,
        "source": _clean_text(item.source, 32) or "daily_0200",
    }
    for field in _COUNTER_FIELDS:
        payload[field] = max(0, _as_int(getattr(item, field, 0)))
    return payload, None


def _apply_payload(row: Any, payload: dict[str, Any], *, is_new: bool) -> None:
    for field in _COUNTER_FIELDS:
        setattr(row, field, payload[field])
    row.platform = payload["platform"]
    row.item_id = payload["item_id"]
    row.item_url = payload["item_url"]
    row.title = payload["title"]
    row.account_id = payload["account_id"]
    row.account_nickname = payload["account_nickname"]
    row.published_at = payload["published_at"]
    row.sampled_at = payload["sampled_at"]
    row.sampled_day = payload["sampled_day"]
    row.source = payload["source"]
    if is_new:
        if hasattr(row, "first_seen_at"):
            row.first_seen_at = datetime.utcnow()
        if hasattr(row, "created_at"):
            row.created_at = datetime.utcnow()


def receive_publish_metrics(
    body: PublishMetricBatchBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """客户端批量上报入口（幂等：同一 sample_key 重复上报只更新数值）。"""
    if not body.samples:
        return {"ok": True, "received": 0, "created": 0, "updated": 0, "events": 0, "rejected": []}
    if len(body.samples) > MAX_SAMPLES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"单次最多上报 {MAX_SAMPLES_PER_BATCH} 条")

    installation_id = _clean_text(body.installation_id, 128)
    now = datetime.utcnow()
    created = 0
    updated = 0
    events_written = 0
    rejected: list[dict[str, str]] = []

    for item in body.samples:
        payload, reason = normalize_sample_item(item, installation_id=installation_id)
        if payload is None:
            rejected.append({"item_id": _clean_text(item.item_id, 128), "reason": reason or "无效数据"})
            continue
        key = payload["sample_key"]

        row = (
            db.query(PublishMetricSample)
            .filter(
                PublishMetricSample.user_id == current_user.id,
                PublishMetricSample.sample_key == key,
            )
            .first()
        )
        is_new = row is None
        if is_new:
            row = PublishMetricSample(
                user_id=current_user.id,
                installation_id=installation_id or None,
                sample_key=key,
                sampled_day=payload["sampled_day"],
                platform=payload["platform"],
                item_id=payload["item_id"],
                first_seen_at=now,
            )
            db.add(row)
            created += 1
        else:
            updated += 1
        _apply_payload(row, payload, is_new=is_new)
        if installation_id:
            row.installation_id = installation_id
        row.reported_at = now
        row.report_count = 1 if is_new else int(row.report_count or 1) + 1

        # 时间序列：同一作品同一天只留一行（重复上报覆盖当日数值）
        event = (
            db.query(PublishMetricEvent)
            .filter(
                PublishMetricEvent.user_id == current_user.id,
                PublishMetricEvent.sample_key == key,
            )
            .first()
        )
        if event is None:
            event = PublishMetricEvent(
                user_id=current_user.id,
                installation_id=installation_id or None,
                sample_key=key,
                sampled_day=payload["sampled_day"],
                platform=payload["platform"],
                item_id=payload["item_id"],
                created_at=now,
            )
            db.add(event)
            events_written += 1
        _apply_payload(event, payload, is_new=False)
        if installation_id:
            event.installation_id = installation_id
        event.updated_at = now

    db.commit()
    return {
        "ok": True,
        "received": len(body.samples),
        "created": created,
        "updated": updated,
        "events": events_written,
        "rejected": rejected,
        "platforms": list(METRIC_PLATFORMS),
    }


def _wanted_platforms(platform: Any) -> list[str]:
    raw = _clean_text(_coerce_optional_str(platform), 32).lower()
    if not raw:
        return list(METRIC_PLATFORMS)
    if raw not in METRIC_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform 须为 {' 或 '.join(METRIC_PLATFORMS)}（朋友圈本轮不采集）",
        )
    return [raw]


def _sample_rows(
    db: Session,
    user_id: int,
    *,
    platforms: list[str],
    account_id: Optional[int] = None,
    installation_id: Optional[str] = None,
    since_day: Optional[str] = None,
) -> list[PublishMetricSample]:
    query = db.query(PublishMetricSample).filter(
        PublishMetricSample.user_id == user_id,
        PublishMetricSample.platform.in_(platforms),
    )
    if account_id is not None:
        query = query.filter(PublishMetricSample.account_id == int(account_id))
    if installation_id:
        query = query.filter(PublishMetricSample.installation_id == _clean_text(installation_id, 128))
    if since_day:
        query = query.filter(PublishMetricSample.sampled_day >= since_day)
    return query.all()


def _latest_per_item(rows: list[PublishMetricSample]) -> dict[tuple[str, str], PublishMetricSample]:
    latest: dict[tuple[str, str], PublishMetricSample] = {}
    for row in rows:
        key = (row.platform, row.item_id)
        current = latest.get(key)
        if current is None or (row.sampled_day, row.id or 0) > (current.sampled_day, current.id or 0):
            latest[key] = row
    return latest


def _sum_counters(rows: list[Any]) -> dict[str, int]:
    return {field: sum(max(0, _as_int(getattr(r, field, 0))) for r in rows) for field in _COUNTER_FIELDS}


def _item_payload(row: PublishMetricSample, *, baseline_views: Optional[int] = None) -> dict[str, Any]:
    views = max(0, _as_int(row.views))
    data: dict[str, Any] = {
        "platform": row.platform,
        "platform_label": PLATFORM_LABELS.get(row.platform, row.platform),
        "item_id": row.item_id,
        "item_url": row.item_url,
        "title": row.title,
        "account_id": row.account_id,
        "account_nickname": row.account_nickname,
        "installation_id": row.installation_id,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "sampled_day": row.sampled_day,
        "sampled_at": row.sampled_at.isoformat() + "Z" if row.sampled_at else None,
        "reported_at": row.reported_at.isoformat() + "Z" if row.reported_at else None,
        "report_count": int(row.report_count or 0),
    }
    for field in _COUNTER_FIELDS:
        data[field] = max(0, _as_int(getattr(row, field, 0)))
    if baseline_views is not None:
        data["views_gain"] = max(0, views - max(0, int(baseline_views)))
    return data


def _account_rollup(rows: list[PublishMetricSample]) -> list[dict[str, Any]]:
    buckets: dict[Any, dict[str, Any]] = {}
    for row in rows:
        bucket = buckets.setdefault(
            row.account_id,
            {
                "account_id": row.account_id,
                "nickname": row.account_nickname,
                "item_count": 0,
                **{field: 0 for field in _COUNTER_FIELDS},
            },
        )
        bucket["item_count"] += 1
        for field in _COUNTER_FIELDS:
            bucket[field] += max(0, _as_int(getattr(row, field, 0)))
    return sorted(buckets.values(), key=lambda b: (-int(b["views"]), str(b.get("nickname") or "")))


def get_publish_metrics(
    platform: Optional[str] = Query(None, description="douyin 或 wechat_channels；省略=两者"),
    account_id: Optional[int] = Query(None, description="按客户端发布账号过滤"),
    installation_id: Optional[str] = Query(None, description="按上报机器过滤"),
    days: int = Query(30, ge=1, le=365, description="统计窗口（天，按北京自然日）"),
    limit: int = Query(200, ge=1, le=2000, description="每个平台返回的作品明细上限"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """看板：每个作品的最新播放/互动数 + 按平台、按账号汇总。"""
    platforms = _wanted_platforms(platform)
    account_id = _coerce_optional_int(account_id)
    installation_id = _coerce_optional_str(installation_id)
    days = _coerce_int(days, 30, minimum=1, maximum=365)
    limit = _coerce_int(limit, 200, minimum=1, maximum=2000)
    since_day = (datetime.utcnow() + timedelta(hours=8) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = _sample_rows(
        db,
        current_user.id,
        platforms=platforms,
        account_id=account_id,
        installation_id=installation_id,
        since_day=since_day,
    )
    latest = _latest_per_item(rows)

    # 窗口内的第一条（作增长基线），用于给出「窗口内新增播放量」
    first_by_item: dict[tuple[str, str], PublishMetricSample] = {}
    for row in rows:
        key = (row.platform, row.item_id)
        current = first_by_item.get(key)
        if current is None or (row.sampled_day, row.id or 0) < (current.sampled_day, current.id or 0):
            first_by_item[key] = row

    platforms_out: list[dict[str, Any]] = []
    for plat in platforms:
        items = [row for (p, _), row in latest.items() if p == plat]
        items.sort(key=lambda r: (-max(0, _as_int(r.views)), r.item_id))
        totals = _sum_counters(items)
        gains = [
            max(0, _as_int(row.views) - _as_int(first_by_item[(row.platform, row.item_id)].views))
            for row in items
            if (row.platform, row.item_id) in first_by_item
        ]
        platforms_out.append(
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_count": len(items),
                **totals,
                "views_gain_in_window": sum(gains),
                "accounts": _account_rollup(items),
                "items": [
                    _item_payload(
                        row,
                        baseline_views=_as_int(
                            first_by_item[(row.platform, row.item_id)].views
                        )
                        if (row.platform, row.item_id) in first_by_item
                        else None,
                    )
                    for row in items[:limit]
                ],
            }
        )

    uploaders = sorted(
        {
            (row.installation_id, row.reported_at.isoformat() + "Z" if row.reported_at else "")
            for row in rows
            if row.installation_id
        }
    )
    return {
        "ok": True,
        "platforms": platforms_out,
        "window_days": days,
        "since_day": since_day,
        "detail_limit": limit,
        "uploaders": [{"installation_id": ins, "last_reported_at": ts} for ins, ts in uploaders],
        "note": (
            "数据来自客户端每天 02:00-06:00（北京时间）错峰本机采集后上报；仅抖音与视频号，"
            "朋友圈（朋友圈视频）本轮不采集。同一作品同一天重复上报只更新数值。"
            "views_gain=窗口内新增（末值−窗口前基线），首次上报的作品本期不计增长。"
        ),
    }


def _series_by_item(
    db: Session,
    user_id: int,
    *,
    platforms: list[str],
    account_id: Optional[int] = None,
    installation_id: Optional[str] = None,
    since_day: Optional[str] = None,
) -> dict[tuple[str, str], list[PublishMetricEvent]]:
    query = db.query(PublishMetricEvent).filter(
        PublishMetricEvent.user_id == user_id,
        PublishMetricEvent.platform.in_(platforms),
    )
    if account_id is not None:
        query = query.filter(PublishMetricEvent.account_id == int(account_id))
    if installation_id:
        query = query.filter(PublishMetricEvent.installation_id == _clean_text(installation_id, 128))
    if since_day:
        query = query.filter(PublishMetricEvent.sampled_day >= since_day)
    series: dict[tuple[str, str], list[PublishMetricEvent]] = {}
    for row in query.order_by(PublishMetricEvent.sampled_day.asc(), PublishMetricEvent.id.asc()).all():
        series.setdefault((row.platform, row.item_id), []).append(row)
    return series


def window_views_gain(
    series: dict[tuple[str, str], list[PublishMetricEvent]],
    *,
    start_day: str,
    end_day: str,
) -> int:
    """窗口内播放量增量 = Σ max(0, 窗口末值 − 窗口前最近一次值)。"""
    total = 0
    for rows in series.values():
        inside = [r for r in rows if start_day <= r.sampled_day <= end_day]
        if not inside:
            continue
        before = [r for r in rows if r.sampled_day < start_day]
        base = _as_int(before[-1].views) if before else _as_int(inside[0].views)
        total += max(0, _as_int(inside[-1].views) - base)
    return total


def get_publish_metrics_summary(
    platform: Optional[str] = Query(None, description="douyin 或 wechat_channels；省略=两者"),
    account_id: Optional[int] = Query(None, description="按客户端发布账号过滤"),
    installation_id: Optional[str] = Query(None, description="按上报机器过滤"),
    days: int = Query(7, ge=1, le=365, description="本期窗口天数；环比对象为紧邻的等长上一期"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """环比：本期（近 days 天）vs 上期（再往前 days 天）的播放量增量。"""
    platforms = _wanted_platforms(platform)
    account_id = _coerce_optional_int(account_id)
    installation_id = _coerce_optional_str(installation_id)
    days = _coerce_int(days, 7, minimum=1, maximum=365)
    today = datetime.utcnow() + timedelta(hours=8)
    end_day = today.strftime("%Y-%m-%d")
    start_day = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    prev_end_day = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    prev_start_day = (today - timedelta(days=2 * days - 1)).strftime("%Y-%m-%d")

    # 取到上一期起点之前，才能算出本期「窗口前基线」；多留 7 天缓冲
    fetch_since = (today - timedelta(days=2 * days + 7)).strftime("%Y-%m-%d")
    series = _series_by_item(
        db,
        current_user.id,
        platforms=platforms,
        account_id=account_id,
        installation_id=installation_id,
        since_day=fetch_since,
    )
    current_gain = window_views_gain(series, start_day=start_day, end_day=end_day)
    previous_gain = window_views_gain(series, start_day=prev_start_day, end_day=prev_end_day)

    per_platform: list[dict[str, Any]] = []
    accounts: dict[Any, dict[str, Any]] = {}
    for (plat, _item_id), rows in series.items():
        inside = [r for r in rows if start_day <= r.sampled_day <= end_day]
        if not inside:
            continue
        before = [r for r in rows if r.sampled_day < start_day]
        base = _as_int(before[-1].views) if before else _as_int(inside[0].views)
        gain = max(0, _as_int(inside[-1].views) - base)
        bucket = accounts.setdefault(
            (plat, inside[-1].account_id),
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "account_id": inside[-1].account_id,
                "nickname": inside[-1].account_nickname,
                "item_count": 0,
                "views": 0,
                "views_gain": 0,
            },
        )
        bucket["item_count"] += 1
        bucket["views"] += _as_int(inside[-1].views)
        bucket["views_gain"] += gain

    for plat in platforms:
        plat_items = [
            (key, rows)
            for key, rows in series.items()
            if key[0] == plat and any(start_day <= r.sampled_day <= end_day for r in rows)
        ]
        plat_gain = window_views_gain(
            {key: rows for key, rows in plat_items}, start_day=start_day, end_day=end_day
        )
        plat_prev = window_views_gain(
            {key: rows for key, rows in plat_items},
            start_day=prev_start_day,
            end_day=prev_end_day,
        )
        latest_views = sum(
            _as_int([r for r in rows if start_day <= r.sampled_day <= end_day][-1].views)
            for _key, rows in plat_items
        )
        per_platform.append(
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_count": len(plat_items),
                "views": latest_views,
                "views_gain": plat_gain,
                "previous_views_gain": plat_prev,
                "growth_pct": round((plat_gain - plat_prev) / plat_prev * 100, 1) if plat_prev else None,
            }
        )

    return {
        "ok": True,
        "current": {
            "start_day": start_day,
            "end_day": end_day,
            "views_gain": current_gain,
        },
        "previous": {
            "start_day": prev_start_day,
            "end_day": prev_end_day,
            "views_gain": previous_gain,
        },
        "growth_pct": round((current_gain - previous_gain) / previous_gain * 100, 1)
        if previous_gain
        else None,
        "platforms": per_platform,
        "accounts": sorted(
            accounts.values(), key=lambda a: (-int(a["views_gain"]), str(a.get("nickname") or ""))
        ),
        "note": "views_gain=窗口内新增播放量（末值-窗口前基线），不是累计总量；总量看 /api/publish/metrics。",
    }


def get_publish_metric_items(
    platform: Optional[str] = Query(None, description="douyin 或 wechat_channels；省略=两者"),
    account_id: Optional[int] = Query(None),
    installation_id: Optional[str] = Query(None),
    item_id: Optional[str] = Query(None, description="只看某个作品"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500, description="返回多少个作品的曲线"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """单作品曲线：按北京自然日的播放量轨迹。"""
    platforms = _wanted_platforms(platform)
    account_id = _coerce_optional_int(account_id)
    installation_id = _coerce_optional_str(installation_id)
    item_id = _coerce_optional_str(item_id)
    days = _coerce_int(days, 30, minimum=1, maximum=365)
    limit = _coerce_int(limit, 50, minimum=1, maximum=500)
    since_day = (datetime.utcnow() + timedelta(hours=8) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    series = _series_by_item(
        db,
        current_user.id,
        platforms=platforms,
        account_id=account_id,
        installation_id=installation_id,
        since_day=since_day,
    )
    wanted_item = _clean_text(item_id, 128)
    entries: list[dict[str, Any]] = []
    for (plat, iid), rows in series.items():
        if wanted_item and iid != wanted_item:
            continue
        if not rows:
            continue
        first = rows[0]
        last = rows[-1]
        entries.append(
            {
                "platform": plat,
                "platform_label": PLATFORM_LABELS.get(plat, plat),
                "item_id": iid,
                "title": None,
                "account_id": last.account_id,
                "account_nickname": last.account_nickname,
                "views": _as_int(last.views),
                "views_gain": max(0, _as_int(last.views) - _as_int(first.views)),
                "first_day": first.sampled_day,
                "last_day": last.sampled_day,
                "points": [
                    {
                        "day": r.sampled_day,
                        "views": _as_int(r.views),
                        "likes": _as_int(r.likes),
                        "comments": _as_int(r.comments),
                        "shares": _as_int(r.shares),
                        "favorites": _as_int(r.favorites),
                    }
                    for r in rows
                ],
            }
        )
    entries.sort(key=lambda e: (-int(e["views"]), str(e["item_id"])))
    sample_titles = {
        (row.platform, row.item_id): row.title
        for row in _sample_rows(db, current_user.id, platforms=platforms, since_day=since_day)
    }
    for entry in entries:
        entry["title"] = sample_titles.get((entry["platform"], entry["item_id"]))
    return {
        "ok": True,
        "window_days": days,
        "since_day": since_day,
        "items": entries[:limit],
        "item_total": len(entries),
    }


router.add_api_route(
    "/api/publish/metrics:batch",
    receive_publish_metrics,
    methods=["POST"],
    summary="客户端上报发布数据（播放量）：抖音 / 视频号",
)
router.add_api_route(
    "/api/publish/metrics",
    get_publish_metrics,
    methods=["GET"],
    summary="发布数据看板：按平台/账号的最新播放量与互动数",
)
router.add_api_route(
    "/api/publish/metrics/summary",
    get_publish_metrics_summary,
    methods=["GET"],
    summary="发布数据环比：本期 vs 上期播放量增量",
)
router.add_api_route(
    "/api/publish/metrics/items",
    get_publish_metric_items,
    methods=["GET"],
    summary="发布数据曲线：单作品按日播放量轨迹",
)
