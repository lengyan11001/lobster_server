"""manage.bhzn.top「发布数据」接口回归测试。

需求（2026-09-16）：online 客户端采集 → 服务器汇总 → 在 manage.bhzn.top 展示（与 H5 无关）。
覆盖点：
1. 只有平台管理员能看（普通用户 403）；
2. 跨客户端/跨用户汇总：总播放量、窗口内新增、环比、按平台、按用户、按账号；
3. 机器视角：安装 ID、最近上报时间、掉线（stale）判定；
4. 明细/曲线：按日 points、窗口内新增、按用户/账号/作品过滤；
5. 路由路径正确（/api/manage/publish-metrics/overview 与 /items）。
"""
from __future__ import annotations

import hashlib
import pathlib
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.api import manage as manage_api
from backend.app.models import PublishMetricEvent, PublishMetricSample, User


def _day(offset: int = 0) -> str:
    return (datetime.utcnow() + timedelta(hours=8) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _key(platform: str, item_id: str, day: str, installation: str) -> str:
    raw = f"{installation}|{platform}|{item_id}|{day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _add_metrics(
    db,
    *,
    user_id: int,
    platform: str,
    item_id: str,
    day: str,
    views: int,
    installation: str,
    account_id: int = 1,
    nickname: str = "阿迪老师",
    reported_at: datetime | None = None,
    likes: int = 0,
) -> None:
    key = _key(platform, item_id, day, installation)
    stamp = reported_at or datetime.utcnow()
    db.add(
        PublishMetricSample(
            user_id=user_id,
            installation_id=installation,
            platform=platform,
            item_id=item_id,
            title=f"{item_id} 的作品",
            account_id=account_id,
            account_nickname=nickname,
            sampled_day=day,
            sample_key=key,
            views=views,
            likes=likes,
            first_seen_at=stamp,
            reported_at=stamp,
        )
    )
    db.add(
        PublishMetricEvent(
            user_id=user_id,
            installation_id=installation,
            platform=platform,
            item_id=item_id,
            account_id=account_id,
            account_nickname=nickname,
            sampled_day=day,
            sample_key=key,
            views=views,
            likes=likes,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    db.commit()


class _Admin:
    id = 0
    email = "platform-admin@local"
    brand_mark = "bihuo"
    role = "admin"


@pytest.fixture
def admin():
    return _Admin()


def test_route_paths_are_registered():
    paths = {getattr(r, "path", "") for r in manage_api.router.routes}
    assert "/api/manage/publish-metrics/overview" in paths
    assert "/api/manage/publish-metrics/items" in paths


def test_manage_page_exposes_publish_metrics_view():
    """manage.bhzn.top 页面上必须有「发布数据」入口，且只对平台管理员显示。"""
    page = pathlib.Path(__file__).resolve().parents[2] / "manage_static" / "index.html"
    assert page.is_file(), f"manage 静态页缺失: {page}"
    html = page.read_text(encoding="utf-8", errors="surrogateescape")
    assert 'data-go="s-metrics"' in html
    assert 'id="s-metrics"' in html
    assert "发布数据" in html
    assert "/api/manage/publish-metrics/overview" in html
    assert "/api/manage/publish-metrics/items" in html
    assert 'data-go="s-metrics" class="hide"' in html, "非管理员默认隐藏"
    assert "朋友圈" in html and "错峰" in html
    assert '"s-metrics": ["发布数据"' in html


def test_only_platform_admin_can_read(db_session, test_user, admin):
    with pytest.raises(HTTPException) as err:
        manage_api.publish_metrics_overview(
            days=7, platform="", user_id=None, q="", stale_hours=26,
            actor=test_user, db=db_session,
        )
    assert err.value.status_code == 403
    assert "管理员" in str(err.value.detail)

    with pytest.raises(HTTPException) as err2:
        manage_api.publish_metrics_items(
            days=30, platform="", user_id=None, account_id=None, item_id="", limit=80,
            actor=test_user, db=db_session,
        )
    assert err2.value.status_code == 403


def test_overview_aggregates_across_clients(db_session, test_user, other_user, admin):
    # user 1（抖音）窗口内从 1000 → 1900，上期 300 → 500
    _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                 day=_day(20), views=1000, installation="inst-a")
    _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                 day=_day(14), views=300, installation="inst-a")
    _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                 day=_day(7), views=500, installation="inst-a")
    _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                 day=_day(1), views=1900, installation="inst-a")
    # user 2（视频号）窗口内 0 → 800，机器已超时未上报（stale）
    _add_metrics(db_session, user_id=other_user.id, platform="wechat_channels", item_id="ch_1",
                 day=_day(1), views=800, installation="inst-b",
                 account_id=2, nickname="诺诺老师",
                 reported_at=datetime.utcnow() - timedelta(hours=40))

    payload = manage_api.publish_metrics_overview(
        days=7, platform="", user_id=None, q="", stale_hours=26, actor=admin, db=db_session,
    )
    assert payload["ok"] is True
    assert payload["window"]["end_day"] == _day(0)
    totals = payload["totals"]
    assert totals["views"] == 1900 + 800
    # 口径：窗口内新增 = 末值 − 窗口前最近一次；首次上报的作品本期不计增长（保守，避免虚高）
    assert totals["views_gain"] == 1400
    assert totals["item_count"] == 2
    assert totals["user_count"] == 2
    assert totals["machine_count"] == 2
    assert totals["stale_machine_count"] == 1

    platforms = {p["platform"]: p for p in payload["platforms"]}
    assert platforms["douyin"]["views"] == 1900
    assert platforms["douyin"]["views_gain"] == 1400
    assert platforms["douyin"]["previous_views_gain"] == 200  # 上期 day13-day7：500-300
    assert platforms["wechat_channels"]["views"] == 800
    assert platforms["wechat_channels"]["views_gain"] == 0  # user2 首次上报，本期不计增长
    assert platforms["wechat_channels"]["platform_label"] == "视频号"

    users = {u["user_id"]: u for u in payload["users"]}
    assert users[test_user.id]["label"].startswith("alice") or users[test_user.id]["label"]
    assert users[test_user.id]["views"] == 1900
    assert users[test_user.id]["growth_pct"] is None or isinstance(users[test_user.id]["growth_pct"], float)
    assert users[other_user.id]["stale"] is True
    assert users[other_user.id]["views_gain"] == 0
    assert users[other_user.id]["accounts"][0]["nickname"] == "诺诺老师"
    assert users[test_user.id]["machines"][0]["installation_id"] == "inst-a"

    machines = {m["installation_id"]: m for m in payload["machines"]}
    assert machines["inst-a"]["stale"] is False
    assert machines["inst-b"]["stale"] is True
    assert machines["inst-b"]["platforms"] == ["wechat_channels"]
    assert round(machines["inst-b"]["hours_since"]) == 40


def test_overview_filters_by_user_platform_and_keyword(db_session, test_user, other_user, admin):
    _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                 day=_day(1), views=120, installation="inst-a")
    _add_metrics(db_session, user_id=other_user.id, platform="wechat_channels", item_id="ch_1",
                 day=_day(1), views=340, installation="inst-b")

    only_channels = manage_api.publish_metrics_overview(
        days=7, platform="wechat_channels", user_id=None, q="", stale_hours=26,
        actor=admin, db=db_session,
    )
    assert [p["platform"] for p in only_channels["platforms"]] == ["wechat_channels"]
    assert only_channels["totals"]["views"] == 340
    assert only_channels["totals"]["user_count"] == 1

    mine = manage_api.publish_metrics_overview(
        days=7, platform="", user_id=test_user.id, q="", stale_hours=26,
        actor=admin, db=db_session,
    )
    assert mine["totals"]["views"] == 120
    assert [u["user_id"] for u in mine["users"]] == [test_user.id]

    by_install = manage_api.publish_metrics_overview(
        days=7, platform="", user_id=None, q="inst-b", stale_hours=26,
        actor=admin, db=db_session,
    )
    assert by_install["totals"]["views"] == 340
    assert by_install["machines"][0]["installation_id"] == "inst-b"

    by_email = manage_api.publish_metrics_overview(
        days=7, platform="", user_id=None, q="alice@test.local", stale_hours=26,
        actor=admin, db=db_session,
    )
    assert by_email["totals"]["views"] == 120


def test_items_curve_and_filters(db_session, test_user, admin):
    for offset, views in ((3, 100), (2, 260), (1, 400)):
        _add_metrics(db_session, user_id=test_user.id, platform="douyin", item_id="dy_1",
                     day=_day(offset), views=views, installation="inst-a", likes=views // 10)
    _add_metrics(db_session, user_id=test_user.id, platform="wechat_channels", item_id="ch_1",
                 day=_day(1), views=50, installation="inst-a", account_id=9, nickname="诺诺老师")

    payload = manage_api.publish_metrics_items(
        days=30, platform="", user_id=None, account_id=None, item_id="", limit=80,
        actor=admin, db=db_session,
    )
    assert payload["item_total"] == 2
    top = payload["items"][0]
    assert top["item_id"] == "dy_1"
    assert top["views"] == 400
    assert top["views_gain"] == 300
    assert [p["day"] for p in top["points"]] == [_day(3), _day(2), _day(1)]
    assert [p["views"] for p in top["points"]] == [100, 260, 400]
    assert top["points"][-1]["likes"] == 40
    assert top["title"] == "dy_1 的作品"

    one = manage_api.publish_metrics_items(
        days=30, platform="", user_id=test_user.id, account_id=9, item_id="ch_1", limit=80,
        actor=admin, db=db_session,
    )
    assert one["item_total"] == 1
    assert one["items"][0]["platform"] == "wechat_channels"
    assert one["items"][0]["account_nickname"] == "诺诺老师"
