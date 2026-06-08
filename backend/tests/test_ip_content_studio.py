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

