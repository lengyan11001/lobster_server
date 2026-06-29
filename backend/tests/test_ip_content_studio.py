from __future__ import annotations

import asyncio


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


def test_ip_content_wechat_channels_user_search_accepts_direct_username(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    async def fake_query(*args, **kwargs):
        raise AssertionError("direct finder username should not call TikHub")

    monkeypatch.setattr(studio, "_execute_query_with_retry", fake_query)

    result = asyncio.run(
        studio.search_wechat_channels_users(
            q="v2_test_user@finder",
            current_user=SimpleNamespace(credits=12),
            db=SimpleNamespace(),
        )
    )

    assert result["ok"] is True
    assert result["items"][0]["username"] == "v2_test_user@finder"
    assert result["query"]["source"] == "direct_username"


def test_ip_content_wechat_channels_user_search_prefers_wechat_search_v2(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    calls = []

    async def fake_query(*, query_type, body, **kwargs):
        calls.append({"query_type": query_type, "body": body, "kwargs": kwargs})
        return {
            "ok": True,
            "raw_response": {
                "code": 200,
                "data": {
                    "items": [
                        {
                            "accTypeName": "视频号",
                            "authInfo": "教育自媒体",
                            "desc": "视频号增长案例",
                            "jumpInfo": {"userName": "v2_runyu@finder"},
                            "noticeParam": {"finderUsername": "v2_runyu@finder"},
                            "thumbUrl": "https://example.com/runyu.jpg",
                            "title": "<em class=\"highlight\">润宇新流量</em>",
                        }
                    ]
                },
            },
            "query": {"query_type": query_type},
            "balance_after": 9,
        }

    monkeypatch.setattr(studio, "_execute_query_with_retry", fake_query)

    result = asyncio.run(
        studio.search_wechat_channels_users(
            q="润宇新流量",
            current_user=SimpleNamespace(credits=10),
            db=SimpleNamespace(),
        )
    )

    assert result["ok"] is True
    assert result["items"][0]["username"] == "v2_runyu@finder"
    assert result["items"][0]["display_name"] == "润宇新流量"
    assert [call["query_type"] for call in calls] == ["wechat_search_v2"]
    assert calls[0]["body"]["business_type"] == "account"
    assert calls[0]["body"]["raw"] is True


def test_ip_content_wechat_channels_user_search_returns_empty_without_legacy_fallback(monkeypatch):
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    calls = []

    async def fake_query(*, query_type, body, **kwargs):
        calls.append({"query_type": query_type, "body": body, "kwargs": kwargs})
        return {
            "ok": True,
            "raw_response": {
                "code": 200,
                "data": {
                    "results": {
                        "data": [
                            {
                                "subBoxes": [
                                    {
                                        "items": [
                                            {
                                                "accTypeName": "公众号",
                                                "jumpInfo": {"userName": "gh_test"},
                                                "title": "<em class=\"highlight\">亲爱的安先生</em>",
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
            "query": {"query_type": query_type},
            "balance_after": 9,
        }

    monkeypatch.setattr(studio, "_execute_query_with_retry", fake_query)

    result = asyncio.run(
        studio.search_wechat_channels_users(
            q="新爱的安先生",
            current_user=SimpleNamespace(credits=10),
            db=SimpleNamespace(),
        )
    )

    assert result["ok"] is True
    assert result["items"] == []
    assert result["raw_item_count"] == 1
    assert [call["query_type"] for call in calls] == ["wechat_search_v2"]


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


def test_wechat_channels_transcript_channel_id_variants_cover_i_l_confusion():
    from backend.app.api import wechat_channels_transcript as transcript

    variants = transcript._channel_id_variants("sphUMeQgnZCIOqr")

    assert variants[0] == "sphUMeQgnZCIOqr"
    assert "sphUMeQgnZClOqr" in variants


def test_wechat_channels_transcript_normalizes_v2_user_videos_shape():
    from backend.app.api import wechat_channels_transcript as transcript

    payload = {
        "data": {
            "videos": [
                {
                    "id": "14779988301712792068",
                    "title": [{"shortTitle": "干大健康的就是在拉人头？"}],
                    "create_time": 1761911905,
                    "read_count": 12,
                    "like_count": 32,
                    "comment_count": 7,
                    "forward_count": 72,
                    "media": {
                        "url": "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=abc",
                        "url_token": "&token=token-value",
                        "full_url": "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=abc&token=token-value",
                        "decode_key": "8667923",
                        "cover_url": "https://example.com/cover.jpg",
                    },
                }
            ],
            "last_buffer": "next-page",
        }
    }

    videos = transcript._normalize_videos_from_payload(payload)

    assert len(videos) == 1
    assert videos[0]["item_key"] == "14779988301712792068"
    assert videos[0]["title"] == "干大健康的就是在拉人头？"
    assert videos[0]["video_url"] == "http://wxapp.tc.qq.com/251/20302/stodownload?encfilekey=abc&token=token-value"
    assert videos[0]["cover_url"] == "https://example.com/cover.jpg"
    assert videos[0]["decode_key"] == "8667923"
    assert videos[0]["metrics"]["like_count"] == 32
    assert transcript._extract_last_buffer(payload) == "next-page"


def test_ip_content_normalizes_wechat_channels_v2_user_videos_shape():
    from backend.app.api import ip_content_studio as studio

    payload = {
        "data": {
            "videos": [
                {
                    "id": "14779988301712792068",
                    "title": [{"shortTitle": "干大健康的就是在拉人头？"}],
                    "create_time": 1761911905,
                    "read_count": 12,
                    "like_count": 32,
                    "comment_count": 7,
                    "forward_count": 72,
                    "username": "v2_test_user@finder",
                    "media": {
                        "full_url": "http://wxapp.tc.qq.com/video.mp4?token=token-value",
                        "cover_url": "https://example.com/cover.jpg",
                    },
                }
            ]
        }
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
    assert normalized["item_key"] == "14779988301712792068"
    assert normalized["author_key"] == "v2_test_user@finder"
    assert normalized["title"] == "干大健康的就是在拉人头？"
    assert normalized["public_url"] == "http://wxapp.tc.qq.com/video.mp4?token=token-value"
    assert normalized["cover_url"] == "https://example.com/cover.jpg"
    assert normalized["publish_time"] == "1761911905"
    assert normalized["metrics"]["like_count"] == 32
    assert normalized["metrics"]["read_count"] == 12


def test_collects_legacy_wechat_channels_objects_as_posts():
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


def test_sync_wechat_channels_competitor_uses_v2_user_videos_without_legacy_fallback(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    calls = []

    async def fake_query(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "items": [{"item_key": "video-1"}],
            "raw_item_count": 1,
            "query": {"query_type": kwargs["query_type"]},
        }

    class DummyDb:
        def commit(self):
            pass

        def refresh(self, row):
            pass

    row = SimpleNamespace(
        id=9,
        platform="wechat_channels",
        account_key="v2_test_user@finder",
        display_name="测试视频号",
        homepage_url="",
        industry_tags="",
        status="active",
        last_seen_item_key="",
        last_fetch_at=None,
        meta={},
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(studio, "_execute_query_with_retry", fake_query)

    result = asyncio.run(
        studio._sync_competitor_row(
            db=DummyDb(),
            current_user=SimpleNamespace(id=1, credits=10),
            row=row,
            count=20,
            last_buffer="next-page",
        )
    )

    assert result["ok"] is True
    assert row.last_seen_item_key == "video-1"
    assert [call["query_type"] for call in calls] == ["wechat_channels_user_videos_v2"]
    assert calls[0]["body"] == {"username": "v2_test_user@finder", "last_buffer": "next-page", "raw": True}


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


def test_schedule_template_payload_includes_memory_doc_ids():
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    row = SimpleNamespace(
        id=1,
        user_id=7,
        name="daily-template",
        keyword_ids=[1],
        competitor_ids=[2],
        memory_doc_ids=["doc-a", "doc-b", "doc-a", ""],
        memory_docs=[{"id": "doc-a", "title": "A"}],
        requirements={},
        status="active",
        meta={},
        created_at=None,
        updated_at=None,
    )

    payload = studio._template_payload(row)

    assert payload["memory_doc_ids"] == ["doc-a", "doc-b"]


def test_schedule_template_payload_falls_back_to_memory_docs_ids():
    from types import SimpleNamespace

    from backend.app.api import ip_content_studio as studio

    row = SimpleNamespace(
        id=2,
        user_id=7,
        name="legacy-template",
        keyword_ids=[],
        competitor_ids=[],
        memory_doc_ids=None,
        memory_docs=[{"id": "doc-a"}, {"doc_id": "doc-b"}, {"title": "legacy title"}],
        requirements={},
        status="active",
        meta={},
        created_at=None,
        updated_at=None,
    )

    payload = studio._template_payload(row)

    assert payload["memory_doc_ids"] == ["doc-a", "doc-b", "legacy title"]


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
                "body": (
                    "今天聊一个真实案例。\n"
                    "配图提示：办公室白板\n"
                    "你的产品定价是多少？评论区告诉我。\n\n"
                    "想拥有你的增长系统？\n"
                    "私信我，聊聊你的生意。🎯\n\n"
                    "私信我，聊聊你的生意。"
                ),
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
    assert "想拥有" not in records[0].content
    assert "增长系统" not in records[0].content
    assert "私信" not in records[0].content
    assert "聊聊你的生意" not in records[0].content
    assert records[0].meta["image_prompts"] == ["办公室白板", "客户现场", "便签特写"]


def test_moments_leadgen_sentence_filter_keeps_normal_content():
    from backend.app.api import ip_content_studio as studio

    text = (
        "增长从来不是靠努力，而是靠系统。\n\n"
        "我见过太多老板，每天忙到凌晨，业绩却毫无起色。\n"
        "他们问我，AI到底怎么落地？\n\n"
        "来找我拿一套获客方案。\n"
        "如果你也想搭建自动增长系统，可以私信我。\n"
        "方向对了，努力才有意义。"
    )

    cleaned = studio._strip_moments_comment_bait(text)

    assert "来找我" not in cleaned
    assert "获客方案" not in cleaned
    assert "私信" not in cleaned
    assert "自动增长系统" not in cleaned
    assert "他们问我" in cleaned
    assert "方向对了" in cleaned


def test_clean_scheduled_daily_tasks_keeps_allowed_unique_values():
    from backend.app.api import ip_content_studio as studio

    tasks = studio._clean_scheduled_daily_tasks(
        ["moments_candidate", "bad", "industry_hot_oral", "moments_candidate", "professional_ip_oral"]
    )

    assert tasks == ["moments_candidate", "industry_hot_oral", "professional_ip_oral"]
    assert studio._clean_scheduled_daily_tasks("moments_candidate") == []
