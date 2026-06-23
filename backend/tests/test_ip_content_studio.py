from __future__ import annotations


def test_collects_tikhub_billboard_search_list():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "code": 0,
            "message": "success",
            "data": {
                "page_num": 1,
                "page_size": 20,
                "total_count": 1,
                "search_list": [
                    {
                        "key_word": "算力龙头股",
                        "search_score": 253804,
                        "trends": [{"date": "20260607", "value": 253804}],
                    }
                ],
            },
        },
    }

    items = studio._collect_items(payload)
    normalized = studio._normalize_item(
        items[0],
        user_id=1,
        query_id="query-id",
        platform="douyin",
        source_type="billboard_search",
        idx=0,
    )

    assert len(items) == 1
    assert normalized["title"] == "算力龙头股"
    assert normalized["metrics"] == {"search_score": 253804}


def test_collects_tikhub_video_search_aweme_info():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "business_data": [
                {
                    "data_id": "0",
                    "type": 1,
                    "data": {
                        "aweme_info": {
                            "aweme_id": "7450001",
                            "desc": "算力中心怎么选址？三个指标决定成本。",
                            "create_time": 1780000000,
                            "author": {"sec_uid": "MS4wLjABAAAA", "nickname": "算力研究员"},
                            "statistics": {"digg_count": 1200, "comment_count": 88, "collect_count": 300},
                            "share_info": {"share_url": "https://www.douyin.com/video/7450001"},
                        }
                    },
                }
            ]
        },
    }

    items = studio._collect_items(payload)
    normalized = studio._normalize_item(
        items[0],
        user_id=1,
        query_id="query-id",
        platform="douyin",
        source_type="keyword_video",
        idx=0,
    )

    assert len(items) == 1
    assert normalized["item_key"] == "7450001"
    assert normalized["title"] == "算力中心怎么选址？三个指标决定成本。"
    assert normalized["author_name"] == "算力研究员"
    assert normalized["public_url"] == "https://www.douyin.com/video/7450001"
    assert normalized["metrics"]["digg_count"] == 1200
    assert normalized["metrics"]["comment_count"] == 88
    assert normalized["metrics"]["collect_count"] == 300


def test_normalizes_douyin_user_search_candidates():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "user_list": [
                {
                    "user_info": {
                        "uid": "123",
                        "sec_uid": "MS4wLjABAAAA-user",
                        "nickname": "测试账号",
                        "unique_id": "test_account_001",
                        "signature": "6月9号 周二活动...",
                        "follower_count": 7132000,
                        "aweme_count": 424,
                        "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
                    }
                }
            ]
        },
    }

    items = studio._collect_items(payload)
    candidate = studio._normalize_douyin_user(items[0], 0)

    assert len(items) == 1
    assert candidate["sec_user_id"] == "MS4wLjABAAAA-user"
    assert candidate["display_name"] == "测试账号"
    assert candidate["unique_id"] == "test_account_001"
    assert candidate["follower_count"] == 7132000
    assert candidate["aweme_count"] == 424
    assert candidate["avatar_url"] == "https://example.com/avatar.jpg"


def test_normalizes_douyin_user_search_v2_candidates():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "data": {
                "user_list": [
                    {
                        "user_id": "MS4wLjABAAAA-v2-user",
                        "nick_name": "测试账号",
                        "avatar_url": "https://example.com/avatar-v2.jpg",
                        "fans_cnt": 7132043,
                        "like_cnt": 51415427,
                        "publish_cnt": 499,
                    }
                ]
            }
        },
    }

    candidates, raw_count = studio._normalize_douyin_users_from_payload(payload)

    assert raw_count == 1
    assert candidates[0]["sec_user_id"] == "MS4wLjABAAAA-v2-user"
    assert candidates[0]["display_name"] == "测试账号"
    assert candidates[0]["follower_count"] == 7132043
    assert candidates[0]["aweme_count"] == 499
    assert candidates[0]["like_count"] == 51415427
    assert candidates[0]["avatar_url"] == "https://example.com/avatar-v2.jpg"


def test_normalizes_wechat_channels_user_search_candidates():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "items": [
                {
                    "title": "<em class=\"highlight\">channels account</em>",
                    "desc": "growth <em class=\"highlight\">content</em>",
                    "thumbUrl": "https://example.com/channels.jpg",
                    "authInfo": "verified company",
                    "jumpInfo": {"userName": "sph_test_user"},
                    "noticeParam": {"finderUsername": "sph_test_user"},
                }
            ]
        },
    }

    candidates, raw_count = studio._normalize_wechat_channels_users_from_payload(payload)

    assert raw_count == 1
    assert candidates[0]["username"] == "sph_test_user"
    assert candidates[0]["display_name"] == "channels account"
    assert candidates[0]["signature"] == "growth content"
    assert candidates[0]["avatar_url"] == "https://example.com/channels.jpg"
    assert candidates[0]["verify_info"] == "verified company"


def test_normalizes_wechat_search_nested_channels_candidates_only():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "results": {
                "data": [
                    {
                        "subBoxes": [
                            {
                                "items": [
                                    {
                                        "accTypeName": "视频号",
                                        "authInfo": "摄影博主",
                                        "desc": "惟有被记录的才能真正成为回忆",
                                        "jumpInfo": {"userName": "v2_xugongzi@finder"},
                                        "noticeParam": {"finderUsername": "v2_xugongzi@finder"},
                                        "thumbUrl": "https://example.com/avatar.jpg",
                                        "title": "Peri<em class=\"highlight\">徐公子</em>",
                                    }
                                ]
                            },
                            {
                                "items": [
                                    {
                                        "accTypeName": "公众号",
                                        "jumpInfo": {"userName": "gh_xugongzi"},
                                        "title": "<em class=\"highlight\">徐公子</em>私人订阅号",
                                    }
                                ]
                            },
                        ]
                    }
                ]
            }
        },
    }

    candidates, raw_count = studio._normalize_wechat_channels_users_from_payload(payload)

    assert raw_count == 1
    assert len(candidates) == 1
    assert candidates[0]["username"] == "v2_xugongzi@finder"
    assert candidates[0]["display_name"] == "Peri徐公子"
    assert candidates[0]["signature"] == "惟有被记录的才能真正成为回忆"
    assert candidates[0]["avatar_url"] == "https://example.com/avatar.jpg"
    assert candidates[0]["verify_info"] == "摄影博主"


def test_has_wechat_channels_channel_id_convert_endpoint():
    from backend.app.api import ip_content_studio as studio

    spec = studio._ENDPOINTS["wechat_channels_channel_id_to_username_v2"]

    assert spec["method"] == "POST"
    assert spec["path"] == "/api/v1/wechat_channels/v2/fetch_channel_id_to_username"
    assert "channel_id" in spec["allowed_body"]


def test_has_wechat_channels_video_detail_endpoint():
    from backend.app.api import ip_content_studio as studio

    spec = studio._ENDPOINTS["wechat_channels_video_detail_v2"]

    assert spec["method"] == "POST"
    assert spec["path"] == "/api/v1/wechat_channels/v2/fetch_video_detail"
    assert {"object_id", "export_id", "share_url"} <= spec["allowed_body"]


def test_wechat_channels_transcript_extracts_finder_username_from_video_detail():
    from backend.app.api import wechat_channels_transcript as transcript

    payload = {
        "data": {
            "object": {
                "objectDesc": {
                    "description": "factory tour",
                    "contact": {
                        "username": "v2_test_user@finder",
                        "nickname": "Factory IP",
                        "signature": "daily videos",
                        "headUrl": "https://example.com/avatar.jpg",
                    },
                }
            }
        }
    }

    username = transcript._find_finder_username(payload)
    account = transcript._account_from_username(username, raw=payload, source="video_detail")

    assert username == "v2_test_user@finder"
    assert account["username"] == "v2_test_user@finder"
    assert account["display_name"] == "Factory IP"
    assert account["signature"] == "daily videos"
    assert account["avatar_url"] == "https://example.com/avatar.jpg"


def test_wechat_channels_transcript_accepts_direct_inputs():
    from backend.app.api import wechat_channels_transcript as transcript

    assert transcript._is_finder_username("v2_test_user@finder")
    assert transcript._looks_like_video_detail_input("https://channels.weixin.qq.com/mobile-support/pages/live-notice/index?exportid=abc")
    assert transcript._looks_like_video_detail_input("object_id:1234567890123")


def test_collects_wechat_channels_home_page_objects_as_posts():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "code": 200,
        "data": {
            "object": [
                {
                    "id": "14816524853645875504",
                    "createtime": 1766267401,
                    "nickname": "channels account",
                    "username": "sph_test_user",
                    "like_count": 33,
                    "comment_count": 3,
                    "forward_count": 118,
                    "fav_count": 41,
                    "contact": {
                        "nickname": "channels account",
                        "username": "sph_test_user",
                        "cover_img_url": "https://example.com/cover.jpg",
                    },
                    "object_desc": {
                        "description": "factory visit #growth",
                        "media": [{"url": "https://example.com/video.mp4", "cover_url": "https://example.com/post-cover.jpg"}],
                    },
                }
            ]
        },
    }

    items = studio._collect_items(payload)
    normalized = studio._normalize_item(
        items[0],
        user_id=1,
        query_id="query-id",
        platform="wechat_channels",
        source_type="home_page",
        idx=0,
    )

    assert len(items) == 1
    assert normalized["item_key"] == "14816524853645875504"
    assert normalized["author_key"] == "sph_test_user"
    assert normalized["author_name"] == "channels account"
    assert normalized["title"] == "factory visit #growth"
    assert normalized["description"] == "factory visit #growth"
    assert normalized["public_url"] == "https://example.com/video.mp4"
    assert normalized["cover_url"] == "https://example.com/post-cover.jpg"
    assert normalized["publish_time"] == "1766267401"
    assert normalized["metrics"]["like_count"] == 33
    assert normalized["metrics"]["forward_count"] == 118
    assert normalized["metrics"]["fav_count"] == 41


def test_draft_record_payload_includes_image_list():
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    row = SimpleNamespace(
        id=1,
        record_id="rec-1",
        task="moments_candidate",
        platform="wechat_moments",
        title="朋友圈文案",
        content="正文",
        image_prompt="配图提示",
        image_url="https://example.com/1.jpg",
        image_asset_id="asset-1",
        selected=True,
        source_item_ids=[],
        memory_doc_ids=[],
        meta={
            "image_batch_id": "moment_img_batch_1",
            "image_batch_created_at": "2026-06-08T10:00:00",
            "images": [{"image_url": "https://example.com/1.jpg"}, {"image_url": "https://example.com/2.jpg"}],
        },
        created_at=None,
        updated_at=None,
    )

    payload = studio._draft_record_payload(row)

    assert len(payload["images"]) == 2
    assert payload["images"][1]["image_url"] == "https://example.com/2.jpg"
    assert payload["meta"]["image_batch_id"] == "moment_img_batch_1"


def test_oral_records_drop_image_prompts(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    saved = []

    class DummyDb:
        def add(self, row):
            saved.append(row)

        def flush(self):
            pass

        def commit(self):
            pass

        def refresh(self, row):
            pass

    monkeypatch.setattr(studio, "_mark_source_rows_used", lambda *args, **kwargs: None)

    records = studio._save_draft_records(
        DummyDb(),
        current_user=SimpleNamespace(id=1),
        task="industry_hot_oral",
        platform="douyin",
        drafts=[{"title": "口播", "body": "正文", "image_prompt": "不该保存", "image_prompts": ["不该保存"]}],
        rows=[],
        memories=[],
        extra_requirements="",
        group_id="group-1",
    )

    assert records[0].image_prompt is None
    assert records[0].meta["image_prompts"] == []


def test_moments_records_strip_comment_bait_and_embedded_image_prompt(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    saved = []

    class DummyDb:
        def add(self, row):
            saved.append(row)

        def flush(self):
            pass

        def commit(self):
            pass

        def refresh(self, row):
            pass

    monkeypatch.setattr(studio, "_mark_source_rows_used", lambda *args, **kwargs: None)

    records = studio._save_draft_records(
        DummyDb(),
        current_user=SimpleNamespace(id=1),
        task="moments_candidate",
        platform="wechat_moments",
        drafts=[
            {
                "title": "朋友圈",
                "body": "今天聊一个真实案例。\n配图提示：办公室白板\n你的产品定价是多少？评论区告诉我。",
                "image_prompts": ["办公室白板", "客户现场", "便签特写"],
            }
        ],
        rows=[],
        memories=[],
        extra_requirements="",
        group_id="group-2",
    )

    assert "配图提示" not in records[0].content
    assert "评论区" not in records[0].content
    assert records[0].meta["image_prompts"] == ["办公室白板", "客户现场", "便签特写"]


def test_clean_scheduled_daily_tasks_keeps_allowed_unique_values():
    from backend.app.api import ip_content_studio as studio

    tasks = studio._clean_scheduled_daily_tasks(
        ["moments_candidate", "bad", "industry_hot_oral", "moments_candidate", "professional_ip_oral"]
    )

    assert tasks == ["moments_candidate", "industry_hot_oral", "professional_ip_oral"]
    assert studio._clean_scheduled_daily_tasks("moments_candidate") == []
