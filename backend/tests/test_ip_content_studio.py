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
