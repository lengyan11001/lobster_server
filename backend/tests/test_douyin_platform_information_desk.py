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
                    "items": [{"rank": 1, "title": "应被过滤掉的热搜", "metrics": {"hot_value": 10}}],
                    "error": "",
                },
                {
                    "key": "hot_total",
                    "title": "热点总榜",
                    "category": "热点榜",
                    "items": [{"rank": 1, "title": "平台话题", "metrics": {"hot_value": 10},
                               "cover_url": "https://cdn.test/cover.jpg"}],
                    "error": "",
                },
            ],
            endpoint_status=[
                {"key": "hot_search", "status": "success", "http_status": 200},
                {"key": "hot_total", "status": "success", "http_status": 200},
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
    sections = payload["snapshot"]["sections"]
    assert [section["category"] for section in sections] == ["热点榜"]
    assert sections[0]["items"][0]["title"] == "平台话题"
    assert sections[0]["items"][0]["cover_url"] == "https://cdn.test/cover.jpg"
    assert [item["key"] for item in payload["snapshot"]["endpoint_status"]] == ["hot_total"]
    assert "raw" not in payload["snapshot"]
    assert "request_body" not in payload["snapshot"]
    assert {item["category"] for item in payload["catalog"]} == {"热点榜", "内容榜"}
    assert len(payload["catalog"]) == 10


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


def test_hot_search_uses_word_instead_of_numeric_label_and_prefers_main_word_list():
    from backend.app.services.douyin_platform_information_desk import _compact_items

    items = _compact_items(
        {
            "data": {
                "trending_list": [{"word": "实时上升词", "label": "0", "hot_value": "999"}],
                "word_list": [{"word": "真正热搜词", "label": "0", "hot_value": "123456", "view_count": "789"}],
            }
        },
        "hot_search",
    )
    assert items == [
        {
            "rank": 1,
            "title": "真正热搜词",
            "metrics": {"hot_value": 123456, "view_count": 789},
        }
    ]


def test_information_desk_only_requests_hot_and_content_boards():
    """2026-09-27 需求：服务器侧只保留热门榜（热点榜）与内容榜单的请求。"""
    from backend.app.services import douyin_platform_information_desk as service

    categories = {item["category"] for item in service.PUBLIC_DAILY_ENDPOINTS}
    assert categories == {"热点榜", "内容榜"}

    keys = {item["key"] for item in service.PUBLIC_DAILY_ENDPOINTS}
    assert {"hot_rise", "hot_city", "hot_challenge", "hot_total"} <= keys
    assert {"hot_video", "low_fan_video", "high_play_video", "high_like_video", "high_fan_video"} <= keys
    dropped = {"hot_search", "music_hot_search", "hot_accounts", "hot_topic", "hot_total_topic",
               "xingtu_catalog", "creator_hot_music", "rising_search_words", "publish_trend"}
    assert not (dropped & keys)
    assert all(item["key"] != "publish_trend" for item in service.DAILY_COLLECTION_ENDPOINTS)
    assert service.INFORMATION_DESK_ENDPOINT_KEYS == frozenset(keys)


def test_imitation_picker_and_error_hints(monkeypatch):
    """换人链路：从作品详情里挑播放地址 + 上游错误翻成人话 + 时长上限。"""
    from backend.app.services import douyin_imitation_video as imitation

    detail = {
        "video": {
            "duration": 11000,
            "bit_rate": [{"play_addr": {"url_list": ["https://cdn.test/low.mp4"]}}],
            "play_addr": {"url_list": ["https://cdn.test/main.mp4", "https://cdn.test/backup.mp4"]},
        }
    }
    assert imitation.pick_play_url(detail) == "https://cdn.test/main.mp4"
    assert imitation.pick_play_url({"video": {"bit_rate": [{"play_addr": {"url_list": ["https://cdn.test/a.mp4"]}}]}}) == "https://cdn.test/a.mp4"
    assert imitation.pick_play_url({}) == ""

    assert "只有一个人" in imitation.friendly_error("InvalidImage.NoHuman", "The input image has no human body.")
    assert "TOS" in imitation.friendly_error("InvalidURL.ConnectionRefused", "Download https://x refused, please provide available URL")
    assert imitation.friendly_error("", "boom") == "boom"

    monkeypatch.setenv("DOUYIN_IMITATION_MAX_SECONDS", "99")
    assert imitation._max_seconds() == 30          # 官方上限 30 秒
    monkeypatch.setenv("DOUYIN_IMITATION_MAX_SECONDS", "abc")
    assert imitation._max_seconds() == 15          # 兜底默认
    monkeypatch.setenv("DOUYIN_IMITATION_MODEL", "wan2.2-animate-mix")
    assert imitation._model() == "wan2.2-animate-mix"


def test_imitation_endpoint_permission_and_forwarding(db_session, db_session_factory, test_user, monkeypatch):
    """做同款：没权限 403；有权限时提交任务号、再查进度拿视频。"""
    from backend.app.api import douyin_platform_information_desk as desk_api
    from backend.app.models import UserSkillVisibility
    from backend.app.services.user_feature_flags import DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID

    client = _client(db_session_factory, test_user.id)
    denied = client.post(
        "/api/douyin/platform-information-desk/imitation",
        json={"image_url": "https://cdn.test/ref.jpg", "item_id": "7689077964000973561"},
    )
    assert denied.status_code == 403

    db_session.add(
        UserSkillVisibility(
            user_id=test_user.id,
            package_id=DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID,
        )
    )
    db_session.commit()

    captured = {}

    async def fake_prepare(image_url, item_id, prompt=""):
        captured.update(image_url=image_url, item_id=item_id, prompt=prompt)
        return {"ok": True, "task_id": "task-e2e", "model": "wan2.2-animate-mix",
                "video_seconds": 15, "source_desc": "想吃哈哈哈哈"}

    async def fake_query(task_id):
        return {"ok": True, "task_id": task_id, "status": "SUCCESS", "progress": "100%",
                "video_url": "https://cdn.test/out.mp4", "fail_reason": "", "done": True}

    monkeypatch.setattr(desk_api, "prepare_imitation", fake_prepare)
    monkeypatch.setattr(desk_api, "query_imitation", fake_query)

    created = client.post(
        "/api/douyin/platform-information-desk/imitation",
        json={"image_url": "https://tos.test/ref.png", "item_id": "7689077964000973561", "title": "想吃哈哈哈哈"},
    )
    assert created.status_code == 200
    assert created.json()["task_id"] == "task-e2e"
    assert captured["image_url"] == "https://tos.test/ref.png"
    assert captured["item_id"] == "7689077964000973561"

    status = client.get("/api/douyin/platform-information-desk/imitation/task-e2e")
    assert status.status_code == 200
    assert status.json()["video_url"] == "https://cdn.test/out.mp4"

    async def fake_prepare_fail(image_url, item_id, prompt=""):
        return {"ok": False, "error": "这张图里没有检测到人物，请换一张只有一个人的清晰照片"}

    monkeypatch.setattr(desk_api, "prepare_imitation", fake_prepare_fail)
    failed = client.post(
        "/api/douyin/platform-information-desk/imitation",
        json={"image_url": "https://tos.test/ref.png", "item_id": "7689077964000973561"},
    )
    assert failed.status_code == 502
    assert "没有检测到人物" in failed.json()["detail"]

def test_videoedit_submit_payload_and_provider_switch(monkeypatch):
    """主链路必须打到 MaaS 工作空间端点，media=[video, reference_image]，用独立 wan key。"""
    import asyncio

    from backend.app.services import douyin_imitation_video as imitation

    monkeypatch.delenv("DOUYIN_IMITATION_PROVIDER", raising=False)
    monkeypatch.delenv("DOUYIN_IMITATION_MODEL", raising=False)
    monkeypatch.setenv("DOUYIN_IMITATION_API_KEY", "wan-key")
    monkeypatch.delenv("DOUYIN_IMITATION_HOST", raising=False)

    assert imitation._provider() == "videoedit"
    assert imitation._model() == "wan2.7-videoedit"
    assert "替换" in imitation.default_prompt()
    assert imitation._videoedit_host().startswith("https://ws-")
    assert "工作空间端点" in imitation.friendly_error(
        "Endpoint.AccessDenied", "Workspace endpoint access denied.")

    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"output": {"task_id": "vid-1", "task_status": "PENDING"}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, **kw):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(imitation.httpx, "AsyncClient", _Client)
    out = asyncio.run(imitation.submit_imitation("https://tos.test/a.png", "https://tos.test/v.mp4"))
    assert out["ok"] is True and out["task_id"] == "vid-1" and out["provider"] == "videoedit"
    assert captured["url"].endswith("/api/v1/services/aigc/video-generation/video-synthesis")
    assert captured["headers"]["Authorization"] == "Bearer wan-key"
    assert captured["body"]["model"] == "wan2.7-videoedit"
    media = captured["body"]["input"]["media"]
    assert media == [{"type": "video", "url": "https://tos.test/v.mp4"},
                     {"type": "reference_image", "url": "https://tos.test/a.png"}]
    assert captured["body"]["parameters"]["resolution"] == "720P"

    # 切回 animate 兜底时走 image2video 端点 + DASHSCOPE_API_KEY
    monkeypatch.setenv("DOUYIN_IMITATION_PROVIDER", "animate")
    monkeypatch.setenv("DOUYIN_IMITATION_MODEL", "wan2.2-animate-mix")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ds-key")
    captured.clear()
    out2 = asyncio.run(imitation.submit_imitation("https://tos.test/a.png", "https://tos.test/v.mp4"))
    assert out2["ok"] is True and out2["provider"] == "animate"
    assert captured["url"].endswith("/api/v1/services/aigc/image2video/video-synthesis")
    assert captured["body"]["input"]["image_url"] == "https://tos.test/a.png"
    assert captured["headers"]["Authorization"] == "Bearer ds-key"

def _seed_snapshot(db, date_text: str = "2026-09-27"):
    from backend.app.models import DouyinPlatformSnapshot

    db.add(
        DouyinPlatformSnapshot(
            snapshot_date=date_text,
            fetched_at=datetime(2026, 9, 27, 1, 0, 0),
            status="success",
            summary={"endpoint_count": 10, "success_count": 10},
            sections=[
                {
                    "key": "hot_total", "title": "热点总榜", "category": "热点榜", "error": "",
                    "items": [{"rank": 1, "id": "111", "title": "城市夜经济回暖", "metrics": {"hot_value": 8}}],
                },
                {
                    "key": "hot_video", "title": "视频热榜", "category": "内容榜", "error": "",
                    "items": [
                        {"rank": 1, "id": "222", "title": "海底捞火锅隐藏吃法", "author": "吃货小王",
                         "cover_url": "https://cdn.test/a.jpg", "url": "https://www.douyin.com/video/222",
                         "metrics": {"like_cnt": 1000}},
                        {"rank": 2, "id": "333", "title": "探店：老巷子小吃", "author": "探店阿飞",
                         "metrics": {"like_cnt": 500}},
                    ],
                },
            ],
            endpoint_status=[{"key": "hot_total", "status": "success"}],
            error_message="",
        )
    )
    db.commit()


def test_information_desk_content_board_first(db_session, db_session_factory, test_user):
    """内容榜要排在热点榜前面。"""
    from backend.app.models import UserSkillVisibility
    from backend.app.services.user_feature_flags import DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID

    db_session.add(UserSkillVisibility(user_id=test_user.id, package_id=DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID))
    _seed_snapshot(db_session)

    payload = _client(db_session_factory, test_user.id).get("/api/douyin/platform-information-desk").json()
    assert [section["category"] for section in payload["snapshot"]["sections"]] == ["内容榜", "热点榜"]


def test_search_information_desk_matches_keywords(db_session, test_user):
    """关键词搜索：命中标题/作者，返回所属榜单，且不打 TikHub。"""
    from backend.app.services import douyin_platform_information_desk as service

    _seed_snapshot(db_session, "2026-09-28")

    hit = service.search_information_desk(db_session, ["火锅"])
    assert hit["count"] == 1
    assert hit["items"][0]["id"] == "222"
    assert hit["items"][0]["section_title"] == "视频热榜"
    assert hit["items"][0]["category"] == "内容榜"
    assert hit["items"][0]["matched"] == ["火锅"]

    multi = service.search_information_desk(db_session, ["探店", "夜经济"])
    assert multi["count"] == 2
    assert {item["id"] for item in multi["items"]} == {"111", "333"}

    assert service.search_information_desk(db_session, [])["count"] == 0
    assert service.search_information_desk(db_session, ["完全不存在的词"])["count"] == 0


def test_search_endpoint_requires_permission(db_session, db_session_factory, test_user):
    _seed_snapshot(db_session, "2026-09-29")
    client = _client(db_session_factory, test_user.id)
    assert client.get("/api/douyin/platform-information-desk/search?q=火锅").status_code == 403

def test_imitation_status_normalized_for_both_clients():
    """online 老版本轮询只认 SUCCESS，DashScope 返回 SUCCEEDED，必须归一化。"""
    from backend.app.services import douyin_imitation_video as imitation

    assert imitation.normalize_status("SUCCEEDED") == "SUCCESS"
    assert imitation.normalize_status("success") == "SUCCESS"
    assert imitation.normalize_status("RUNNING") == "RUNNING"
    assert imitation.normalize_status("PENDING") == "RUNNING"
    assert imitation.normalize_status("NOT_START") == "RUNNING"
    assert imitation.normalize_status("") == "RUNNING"
    assert imitation.normalize_status("FAILED") == "FAILED"
    assert imitation.normalize_status("CANCELED") == "FAILED"
    assert imitation.normalize_status("WeIrD") == "FAILED"

def test_imitation_history_records_and_refreshes(db_session, db_session_factory, test_user, monkeypatch):
    """做同款要落库，历史列表能列出来，查询任务后状态与成片要回写。"""
    from backend.app.api import douyin_platform_information_desk as desk_api
    from backend.app.models import DouyinImitationTask, UserSkillVisibility
    from backend.app.services.user_feature_flags import DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID

    db_session.add(UserSkillVisibility(user_id=test_user.id,
                                       package_id=DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID))
    db_session.commit()

    async def fake_prepare(image_url, item_id, prompt=""):
        return {"ok": True, "task_id": "his-1", "model": "wan2.7-videoedit", "provider": "videoedit",
                "prompt": prompt or "换人", "source_desc": "想吃哈哈哈哈", "video_seconds": 15,
                "image_url": "https://tos.test/a.png", "video_url": "https://tos.test/v.mp4"}

    monkeypatch.setattr(desk_api, "prepare_imitation", fake_prepare)
    client = _client(db_session_factory, test_user.id)

    created = client.post(
        "/api/douyin/platform-information-desk/imitation",
        json={"image_url": "https://tos.test/a.png", "item_id": "7689077964000973561", "title": "想吃哈哈哈哈"},
    )
    assert created.status_code == 200
    assert created.json()["history_id"]

    row = db_session.query(DouyinImitationTask).filter(DouyinImitationTask.task_id == "his-1").first()
    assert row is not None and row.user_id == test_user.id and row.status == "RUNNING"

    hist = client.get("/api/douyin/platform-information-desk/imitation/history?refresh=0")
    assert hist.status_code == 200
    items = hist.json()["items"]
    assert len(items) == 1
    assert items[0]["task_id"] == "his-1" and items[0]["status"] == "RUNNING"
    assert items[0]["title"] == "想吃哈哈哈哈"

    async def fake_query(task_id):
        return {"ok": True, "task_id": task_id, "status": "SUCCESS", "progress": "100%",
                "video_url": "https://cdn.test/out.mp4", "fail_reason": "", "done": True}

    monkeypatch.setattr(desk_api, "query_imitation", fake_query)
    assert client.get("/api/douyin/platform-information-desk/imitation/his-1").status_code == 200

    hist2 = client.get("/api/douyin/platform-information-desk/imitation/history?refresh=0").json()["items"]
    assert hist2[0]["status"] == "SUCCESS"
    assert hist2[0]["video_url"] == "https://cdn.test/out.mp4"


def test_imitation_history_is_per_user(db_session, db_session_factory, test_user, monkeypatch):
    from backend.app.api import douyin_platform_information_desk as desk_api
    from backend.app.models import DouyinImitationTask, UserSkillVisibility
    from backend.app.services.user_feature_flags import DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID

    db_session.add(UserSkillVisibility(user_id=test_user.id,
                                       package_id=DOUYIN_PLATFORM_INFORMATION_DESK_FEATURE_ID))
    db_session.add(DouyinImitationTask(user_id=test_user.id + 1, task_id="other-1", title="别人的"))
    db_session.commit()

    rows = _client(db_session_factory, test_user.id).get(
        "/api/douyin/platform-information-desk/imitation/history?refresh=0").json()["items"]
    assert rows == []
