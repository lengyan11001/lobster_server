"""发布数据（播放量）云端接收 + 看板/环比/曲线 的回归测试。

需求（2026-09-16）：客户端「个人发布中心」每天 02:00（北京时间）把抖音、视频号已发布作品的
播放量上报到服务器；朋友圈本轮不做。云端必须：按 sample_key 幂等 UPSERT 最新值、按日留轨迹
（曲线/环比）、跨用户隔离、拒收非抖音/视频号的平台。

覆盖点：
1. 上报幂等：同一样本重复上报只更新数值，不产生重复行；样本表与事件表各一行；
2. 缺 sample_key 时按客户端同一公式推导（跨端契约）；
3. 朋友圈（moments）等平台被拒收并回原因，不影响同批其他样本；
4. 单批上限与空批；
5. 看板按平台/账号汇总，并给出窗口内新增播放量；
6. 环比：本期 vs 等长上一期的播放量增量与 growth_pct；
7. 曲线：单作品按日轨迹；
8. 路由路径就是客户端写死的 /api/publish/metrics:batch（含冒号）与三个查询接口。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.api import publish_metrics as pm
from backend.app.models import PublishMetricEvent, PublishMetricSample


def _key(day: str, *, platform: str = "douyin", item_id: str = "dy_1", installation_id: str = "inst-a") -> str:
    raw = f"{installation_id}|{platform}|{item_id}|{day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _day(offset: int = 0) -> str:
    """相对「今天（北京）」的日期，避免测试依赖固定日历。"""
    return (datetime.utcnow() + timedelta(hours=8) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _sample(
    *,
    platform: str = "douyin",
    item_id: str = "dy_1",
    sampled_day: str = "",
    views: int = 100,
    likes: int = 10,
    account_id: int = 1,
    account_nickname: str = "阿迪老师",
    sample_key: str = "",
) -> dict:
    sampled_day = sampled_day or _day(1)
    return {
        "sample_key": sample_key,
        "platform": platform,
        "item_id": item_id,
        "title": f"{item_id} 的作品",
        "item_url": f"https://example.test/{item_id}",
        "account_id": account_id,
        "account_nickname": account_nickname,
        "views": views,
        "likes": likes,
        "comments": 3,
        "shares": 2,
        "favorites": 1,
        "impressions": views * 2,
        "sampled_at": f"{sampled_day}T18:00:00",
        "sampled_day": sampled_day,
        "source": "daily_0200",
    }


@pytest.fixture
def metrics_client(db_session_factory, test_user):
    """最小 app：只挂 publish_metrics_router，并 override get_db / get_current_user。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import get_current_user
    from backend.app.api.publish_metrics import router as publish_metrics_router
    from backend.app.db import get_db

    app = FastAPI()
    app.include_router(publish_metrics_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            from backend.app.models import User

            return s.query(User).filter(User.id == test_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override
    return TestClient(app)


# ── 1. 路由契约 ───────────────────────────────────────────────────────────


def test_route_paths_match_client_contract():
    paths = {getattr(r, "path", "") for r in pm.router.routes}
    assert "/api/publish/metrics:batch" in paths
    assert "/api/publish/metrics" in paths
    assert "/api/publish/metrics/summary" in paths
    assert "/api/publish/metrics/items" in paths
    assert pm.METRIC_PLATFORMS == ("douyin", "wechat_channels")
    assert "moments" not in pm.METRIC_PLATFORMS


# ── 2. 上报幂等 ───────────────────────────────────────────────────────────


def test_batch_upload_is_idempotent_and_keeps_latest(db_session, test_user):
    body = pm.PublishMetricBatchBody(
        installation_id="inst-a",
        samples=[
            pm.PublishMetricSampleItem(**_sample()),
            pm.PublishMetricSampleItem(
                **_sample(
                    platform="wechat_channels",
                    item_id="ch_1",
                    account_nickname="诺诺老师",
                    views=5000,
                )
            ),
        ],
    )
    first = pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    assert first["created"] == 2 and first["events"] == 2 and first["rejected"] == []
    assert db_session.query(PublishMetricSample).count() == 2
    assert db_session.query(PublishMetricEvent).count() == 2

    body.samples[0].views = 250
    body.samples[0].likes = 30
    second = pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    assert second["created"] == 0 and second["updated"] == 2 and second["events"] == 0
    assert db_session.query(PublishMetricSample).count() == 2
    assert db_session.query(PublishMetricEvent).count() == 2

    row = (
        db_session.query(PublishMetricSample)
        .filter(PublishMetricSample.item_id == "dy_1")
        .one()
    )
    assert (row.views, row.likes) == (250, 30)
    assert row.report_count == 2
    assert row.installation_id == "inst-a"
    event = (
        db_session.query(PublishMetricEvent)
        .filter(PublishMetricEvent.item_id == "dy_1")
        .one()
    )
    assert event.views == 250, "事件表按日覆盖为最新值，不追加重复行"


def test_batch_derives_sample_key_like_client(db_session, test_user):
    item = pm.PublishMetricSampleItem(**_sample(sample_key=""))
    body = pm.PublishMetricBatchBody(installation_id="inst-a", samples=[item])
    pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    row = db_session.query(PublishMetricSample).one()
    assert row.sample_key == _key(_day(1))
    assert row.sample_key == pm.derive_sample_key(
        installation_id="inst-a", platform="douyin", item_id="dy_1", sampled_day=_day(1)
    )

    # 客户端已带 sample_key 时按客户端的键去重
    client_key = _key(_day(0))
    body2 = pm.PublishMetricBatchBody(
        installation_id="inst-a",
        samples=[
            pm.PublishMetricSampleItem(
                **_sample(sampled_day=_day(0), views=777, sample_key=client_key)
            )
        ],
    )
    again = pm.receive_publish_metrics(body2, current_user=test_user, db=db_session)
    assert again["created"] == 1
    assert db_session.query(PublishMetricSample).count() == 2
    assert {r.sample_key for r in db_session.query(PublishMetricSample).all()} == {
        _key(_day(1)),
        client_key,
    }


# ── 3. 平台口径：朋友圈拒收 ───────────────────────────────────────────────


def test_batch_rejects_moments_and_keeps_valid_samples(db_session, test_user):
    body = pm.PublishMetricBatchBody(
        installation_id="inst-a",
        samples=[
            pm.PublishMetricSampleItem(**_sample()),
            pm.PublishMetricSampleItem(**_sample(platform="moments", item_id="mm_1")),
            pm.PublishMetricSampleItem(**_sample(platform="xiaohongshu", item_id="xhs_1")),
        ],
    )
    result = pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    assert result["created"] == 1
    assert len(result["rejected"]) == 2
    reasons = {r["item_id"]: r["reason"] for r in result["rejected"]}
    assert "moments" in reasons["mm_1"] and "douyin" in reasons["mm_1"]
    assert db_session.query(PublishMetricSample).count() == 1
    assert db_session.query(PublishMetricSample).one().platform == "douyin"


def test_batch_limits_and_empty_body(db_session, test_user):
    empty = pm.PublishMetricBatchBody(installation_id="inst-a", samples=[])
    assert pm.receive_publish_metrics(empty, current_user=test_user, db=db_session)["received"] == 0

    too_many = pm.PublishMetricBatchBody(
        installation_id="inst-a",
        samples=[pm.PublishMetricSampleItem(**_sample(item_id=f"dy_{i}")) for i in range(501)],
    )
    with pytest.raises(HTTPException) as err:
        pm.receive_publish_metrics(too_many, current_user=test_user, db=db_session)
    assert err.value.status_code == 400
    assert "500" in str(err.value.detail)


def test_platform_filter_rejects_moments(db_session, test_user):
    with pytest.raises(HTTPException) as err:
        pm.get_publish_metrics(platform="moments", current_user=test_user, db=db_session)
    assert err.value.status_code == 400
    assert "wechat_channels" in str(err.value.detail)


# ── 4. 跨用户隔离 ─────────────────────────────────────────────────────────


def test_metrics_are_scoped_to_current_user(db_session, test_user, other_user):
    db_session.add(
        PublishMetricSample(
            user_id=other_user.id,
            installation_id="inst-b",
            platform="douyin",
            item_id="other_1",
            title="别人的作品",
            sampled_day=_day(1),
            sample_key=_key(_day(1), item_id="other_1", installation_id="inst-b"),
            views=9999,
            first_seen_at=datetime.utcnow(),
            reported_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    body = pm.PublishMetricBatchBody(installation_id="inst-a", samples=[pm.PublishMetricSampleItem(**_sample())])
    pm.receive_publish_metrics(body, current_user=test_user, db=db_session)

    own = pm.get_publish_metrics(days=30, current_user=test_user, db=db_session)
    douyin = next(p for p in own["platforms"] if p["platform"] == "douyin")
    assert douyin["item_count"] == 1
    assert douyin["views"] == 100

    theirs = pm.get_publish_metrics(days=30, current_user=other_user, db=db_session)
    other_douyin = next(p for p in theirs["platforms"] if p["platform"] == "douyin")
    assert other_douyin["views"] == 9999


# ── 5. 看板：平台 / 账号汇总 + 窗口内新增 ─────────────────────────────────


def test_dashboard_rolls_up_by_platform_and_account(db_session, test_user):
    days = [_day(6), _day(4), _day(1)]
    views = {days[0]: 1000, days[1]: 1400, days[2]: 1900}
    for day in days:
        body = pm.PublishMetricBatchBody(
            installation_id="inst-a",
            samples=[pm.PublishMetricSampleItem(**_sample(sampled_day=day, views=views[day]))],
        )
        pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    body = pm.PublishMetricBatchBody(
        installation_id="inst-a",
        samples=[
            pm.PublishMetricSampleItem(
                **_sample(
                    platform="wechat_channels",
                    item_id="ch_1",
                    sampled_day=_day(1),
                    views=500,
                    account_id=2,
                    account_nickname="诺诺老师",
                )
            )
        ],
    )
    pm.receive_publish_metrics(body, current_user=test_user, db=db_session)

    payload = pm.get_publish_metrics(days=30, current_user=test_user, db=db_session)
    platforms = {p["platform"]: p for p in payload["platforms"]}
    assert set(platforms) == {"douyin", "wechat_channels"}
    assert platforms["douyin"]["views"] == 1900
    assert platforms["douyin"]["views_gain_in_window"] == 900
    assert platforms["douyin"]["accounts"][0]["nickname"] == "阿迪老师"
    assert platforms["wechat_channels"]["views"] == 500
    assert platforms["wechat_channels"]["platform_label"] == "视频号"
    assert payload["uploaders"][0]["installation_id"] == "inst-a"
    assert platforms["douyin"]["items"][0]["sampled_day"] == _day(1)


# ── 6. 环比 ───────────────────────────────────────────────────────────────


def test_summary_compares_current_and_previous_window(db_session, test_user):
    today = datetime.utcnow() + timedelta(hours=8)
    day = lambda offset: (today - timedelta(days=offset)).strftime("%Y-%m-%d")  # noqa: E731
    series = [
        (day(20), 100),
        (day(14), 300),  # 上期窗口的基线/末值（7 天窗口：day14..day8）
        (day(7), 500),
        (day(3), 900),
        (day(0), 1400),  # 本期窗口：day6..day0
    ]
    for sampled_day, views in series:
        body = pm.PublishMetricBatchBody(
            installation_id="inst-a",
            samples=[
                pm.PublishMetricSampleItem(
                    **_sample(sampled_day=sampled_day, views=views)
                )
            ],
        )
        pm.receive_publish_metrics(body, current_user=test_user, db=db_session)

    summary = pm.get_publish_metrics_summary(days=7, current_user=test_user, db=db_session)
    assert summary["current"]["views_gain"] == 900, "本期 day6..day0：1400 - 500"
    assert summary["previous"]["views_gain"] == 200, "上期 day13..day7：500 - 300"
    assert summary["growth_pct"] == 350.0
    douyin = next(p for p in summary["platforms"] if p["platform"] == "douyin")
    assert douyin["views"] == 1400
    assert douyin["views_gain"] == 900
    assert douyin["previous_views_gain"] == 200
    assert douyin["growth_pct"] == 350.0
    assert summary["accounts"][0]["nickname"] == "阿迪老师"
    assert summary["accounts"][0]["views_gain"] == 900


def test_window_views_gain_helper_covers_missing_window():
    series = {
        ("douyin", "a"): [
            PublishMetricEvent(sampled_day="2026-09-01", views=10, sample_key="k1", platform="douyin", item_id="a", user_id=1),
            PublishMetricEvent(sampled_day="2026-09-05", views=40, sample_key="k2", platform="douyin", item_id="a", user_id=1),
        ],
        ("douyin", "b"): [
            PublishMetricEvent(sampled_day="2026-08-20", views=5, sample_key="k3", platform="douyin", item_id="b", user_id=1),
        ],
    }
    assert pm.window_views_gain(series, start_day="2026-09-03", end_day="2026-09-10") == 30
    assert pm.window_views_gain(series, start_day="2026-09-06", end_day="2026-09-10") == 0


# ── 7. 曲线 ───────────────────────────────────────────────────────────────


def test_items_curve_returns_daily_points(db_session, test_user):
    for sampled_day, views in ((_day(3), 100), (_day(2), 180), (_day(1), 260)):
        body = pm.PublishMetricBatchBody(
            installation_id="inst-a",
            samples=[pm.PublishMetricSampleItem(**_sample(sampled_day=sampled_day, views=views))],
        )
        pm.receive_publish_metrics(body, current_user=test_user, db=db_session)
    payload = pm.get_publish_metric_items(days=30, current_user=test_user, db=db_session)
    assert payload["item_total"] == 1
    entry = payload["items"][0]
    assert entry["views"] == 260
    assert entry["views_gain"] == 160
    assert [p["day"] for p in entry["points"]] == [_day(3), _day(2), _day(1)]
    assert [p["views"] for p in entry["points"]] == [100, 180, 260]
    assert entry["title"] == "dy_1 的作品"

    one = pm.get_publish_metric_items(item_id="dy_1", days=30, current_user=test_user, db=db_session)
    assert one["item_total"] == 1
    none = pm.get_publish_metric_items(item_id="nope", days=30, current_user=test_user, db=db_session)
    assert none["item_total"] == 0


# ── 8. HTTP 端到端（含带冒号的路由） ─────────────────────────────────────


def test_http_batch_and_dashboard(metrics_client):
    resp = metrics_client.post(
        "/api/publish/metrics:batch",
        json={"installation_id": "inst-http", "user_id": 0, "samples": [_sample(item_id="dy_http")]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["created"] == 1

    repeat = metrics_client.post(
        "/api/publish/metrics:batch",
        json={
            "installation_id": "inst-http",
            "samples": [_sample(item_id="dy_http", views=321)],
        },
    )
    assert repeat.json()["updated"] == 1

    board = metrics_client.get("/api/publish/metrics", params={"days": 30})
    assert board.status_code == 200
    data = board.json()
    douyin = next(p for p in data["platforms"] if p["platform"] == "douyin")
    assert douyin["views"] == 321
    assert douyin["platform_label"] == "抖音"
    assert data["uploaders"][0]["installation_id"] == "inst-http"

    summary = metrics_client.get("/api/publish/metrics/summary", params={"days": 7})
    assert summary.status_code == 200
    assert summary.json()["ok"] is True

    items = metrics_client.get("/api/publish/metrics/items", params={"days": 30})
    assert items.status_code == 200
    assert items.json()["item_total"] == 1

    bad = metrics_client.get("/api/publish/metrics", params={"platform": "moments"})
    assert bad.status_code == 400
