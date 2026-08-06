from backend.app.api import scheduled_tasks


def test_wechat_channels_publish_draft_limits_short_title_to_16_characters():
    draft = scheduled_tasks._normalize_publish_draft(
        {
            "platform": "wechat_channels",
            "title": "每天2000万人在豆包看病AI大健康要变天！",
            "description": "视频号发布正文。",
        }
    )

    assert draft["title"] == "每天2000万人在豆包看病AI大"
    assert len(draft["title"]) == 16


def test_wechat_channels_publish_draft_keeps_title_and_short_title_aligned():
    draft = scheduled_tasks._normalize_publish_draft(
        {
            "platform": "channels",
            "title": "旧标题",
            "short_title": "AI健康｜增长！2026版本发布说明",
        }
    )

    assert draft["title"] == "AI健康增长2026版本发布说明"
    assert draft["short_title"] == draft["title"]
