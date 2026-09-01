from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(db_session_factory, user_id: int):
    from backend.app.api.auth import get_current_user
    from backend.app.api.douyin_platform_information_desk import router
    from backend.app.db import get_db
    from backend.app.models import User

    app = FastAPI()
    app.include_router(router)

    def get_db_override():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    def current_user_override():
        session = db_session_factory()
        try:
            return session.query(User).filter(User.id == user_id).first()
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_db_override
    app.dependency_overrides[get_current_user] = current_user_override
    return TestClient(app)


def test_information_desk_requires_explicit_permission_for_regular_user(
    db_session_factory, test_user
):
    response = _client(db_session_factory, test_user.id).get(
        "/api/douyin/platform-information-desk"
    )

    assert response.status_code == 403


def test_information_desk_returns_compact_snapshot_after_permission_grant(
    db_session, db_session_factory, test_user
):
    from backend.app.models import DouyinPlatformSnapshot, UserSkillVisibility
    from backend.app.services.user_feature_flags import DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID

    db_session.add(
        UserSkillVisibility(
            user_id=test_user.id,
            package_id=DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID,
        )
    )
    db_session.add(
        DouyinPlatformSnapshot(
            snapshot_date="2026-09-01",
            fetched_at=datetime(2026, 9, 1, 1, 0, 0),
            status="partial",
            summary={"endpoint_count": 2, "success_count": 1, "failed_count": 1, "item_count": 1},
            sections=[
                {
                    "key": "hot_search",
                    "title": "实时热搜",
                    "category": "热搜",
                    "items": [{"rank": 1, "title": "平台话题", "metrics": {"hot_value": 10}}],
                    "error": "",
                }
            ],
            endpoint_status=[
                {"key": "hot_search", "status": "success", "http_status": 200}
            ],
            error_message="一个接口失败",
        )
    )
    db_session.commit()

    response = _client(db_session_factory, test_user.id).get(
        "/api/douyin/platform-information-desk"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["status"] == "partial"
    assert payload["snapshot"]["sections"][0]["items"][0]["title"] == "平台话题"
    assert "raw" not in payload["snapshot"]
    assert "request_body" not in payload["snapshot"]
    assert any(item["key"] == "publish_trend" and item["daily"] is False and item["requires_parameters"] is True for item in payload["catalog"])


def test_information_desk_admin_can_read_without_feature_row(
    db_session, db_session_factory
):
    from backend.app.models import User

    admin = User(
        email="information-desk-admin@test.local",
        hashed_password="x",
        credits=Decimal("0.0000"),
        role="admin",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(admin)
    db_session.commit()

    response = _client(db_session_factory, admin.id).get(
        "/api/douyin/platform-information-desk"
    )

    assert response.status_code == 200


def test_tikhub_requests_follow_documented_parameters(monkeypatch):
    import asyncio

    from backend.app.services import douyin_platform_information_desk as service

    hot_total = next(item for item in service.DAILY_COLLECTION_ENDPOINTS if item["key"] == "hot_total")
    hot_video = next(item for item in service.DAILY_COLLECTION_ENDPOINTS if item["key"] == "hot_video")
    total_params, total_body = service._endpoint_request(hot_total, "2026-09-01")
    video_params, video_body = service._endpoint_request(hot_video, "2026-09-01")

    assert total_body == {}
    assert total_params["type"] == "range"
    assert total_params["start_date"] == "20260831"
    assert total_params["end_date"] == "20260831"
    assert video_params == {}
    assert video_body == {
        "page": 1,
        "page_size": 20,
        "date_window": 24,
        "sub_type": 1001,
        "keyword": "",
        "tags": [],
    }

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "code": 200,
                "data": {"list": [{"desc": "公开热点", "hot_value": 99}]},
                "raw_secret": "must never persist",
            }

    class Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, *, params):
            self.calls.append(("GET", url, params))
            return Response()

        async def post(self, url, *, json):
            self.calls.append(("POST", url, json))
            return Response()

    client = Client()
    result = asyncio.run(service._fetch_endpoint(client, hot_total, service.asyncio.Semaphore(1), "2026-09-01"))

    assert result["status"] == "success"
    assert result["items"] == [{"rank": 1, "title": "公开热点", "metrics": {"hot_value": 99}}]
    assert "raw_secret" not in result
    assert client.calls[0][2]["start_date"] == "20260831"


def test_compact_items_handles_tikhub_douyin_nested_payloads_and_public_links():
    from backend.app.services.douyin_platform_information_desk import _compact_items

    music = _compact_items(
        {
            "data": {
                "music_list": [
                    {
                        "music_info": {
                            "id": 7612843324035729446,
                            "title": "示例音乐",
                            "author": "示例作者",
                            "cover_hd": {"url_list": ["https://img.example/music.jpg"]},
                        }
                    }
                ]
            }
        },
        "music_hot_search",
    )
    assert music[0]["id"] == "7612843324035729446"
    assert music[0]["title"] == "示例音乐"
    assert music[0]["author"] == "示例作者"
    assert music[0]["cover_url"] == "https://img.example/music.jpg"
    assert music[0]["url"] == "https://www.douyin.com/music/7612843324035729446"

    content = _compact_items(
        {
            "data": {
                "objs": [
                    {
                        "item_id": "7679759751320942761",
                        "item_title": "示例作品",
                        "item_url": "https://cdn.example/video.mp4",
                        "nick_name": "示例账号",
                    }
                ]
            }
        },
        "hot_video",
    )
    assert content[0]["title"] == "示例作品"
    assert content[0]["author"] == "示例账号"
    assert content[0]["url"] == "https://www.douyin.com/video/7679759751320942761"

    brand = _compact_items(
        {
            "data": {
                "banner_url": {"url_list": ["https://img.example/1.jpg", "https://img.example/2.jpg", "https://img.example/3.jpg"]},
                "category_list": [{"id": 10, "name": "汽车"}, {"id": 11, "name": "手机"}],
            }
        },
        "brand_hot_categories",
    )
    assert [item["title"] for item in brand] == ["汽车", "手机"]

    xingtu = _compact_items(
        {
            "data": {
                "catalog": {
                    "1": [
                        {"code": 1, "display_name": "品牌种草榜", "qualifier": "食品饮料", "qualifier_id": "1903", "period": "30"}
                    ]
                }
            }
        },
        "xingtu_catalog",
    )
    assert xingtu[0]["id"] == "1"
    assert xingtu[0]["title"] == "品牌种草榜"


def test_compact_items_keeps_real_billboard_fields_and_uses_meaningful_fallback_titles():
    from backend.app.services.douyin_platform_information_desk import _compact_items

    content = _compact_items(
        {
            "data": {
                "objs": [
                    {
                        "item_id": "767",
                        "item_title": "",
                        "nick_name": "示例账号",
                        "fans_cnt": "5719",
                        "play_cnt": "33409932",
                        "like_cnt": "1079196",
                        "follow_cnt": "3428",
                        "score": "1568135",
                    }
                ]
            }
        },
        "hot_video",
    )
    assert content[0]["title"] == "示例账号 的热门视频"
    assert content[0]["metrics"] == {
        "fans_cnt": 5719,
        "play_cnt": 33409932,
        "like_cnt": 1079196,
        "follow_cnt": 3428,
        "score": 1568135,
    }
    assert "抖音作品" not in content[0]["title"]

    accounts = _compact_items(
        {"data": {"user_list": [{"user_id": "sec", "nick_name": "示例账号", "fans_cnt": "1000", "new_fans_cnt": "20"}]}},
        "hot_accounts",
    )
    assert accounts[0]["title"] == "示例账号"
    assert accounts[0]["metrics"] == {"fans_cnt": 1000, "new_fans_cnt": 20}
