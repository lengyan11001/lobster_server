from datetime import datetime, timedelta

from backend.app.api.content_records import (
    ContentRecordSyncBody,
    ContentRecordSyncItem,
    _content_image_urls,
    list_content_records,
    sync_content_records,
)
from backend.app.models import IPContentDraftRecord, UserContentRecord


def test_sync_content_records_is_idempotent(db_session, test_user):
    body = ContentRecordSyncBody(
        records=[
            ContentRecordSyncItem(
                source="online_ppt",
                source_id="ppt-001",
                kind="ppt",
                title="第一版方案",
                file_url="https://cdn.example.test/ppt-001.pptx",
                filename="ppt-001.pptx",
                source_created_at="2026-07-25T08:00:00+08:00",
                meta={"slide_count": 12},
            )
        ]
    )

    first = sync_content_records(body, current_user=test_user, db=db_session)
    body.records[0].title = "第二版方案"
    second = sync_content_records(body, current_user=test_user, db=db_session)

    assert first["created"] == 1
    assert second["updated"] == 1
    assert db_session.query(UserContentRecord).count() == 1
    assert db_session.query(UserContentRecord).one().title == "第二版方案"


def test_article_list_combines_ip_daily_and_synced_records(db_session, test_user):
    now = datetime.utcnow()
    db_session.add(
        IPContentDraftRecord(
            record_id="daily-001",
            user_id=test_user.id,
            task="article",
            platform="wechat",
            title="今日文章",
            content="这是今日生成的正文。",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.add(
        UserContentRecord(
            user_id=test_user.id,
            source="online_writer",
            source_id="article-001",
            kind="article",
            title="Online 文章",
            content="Online 正文",
            status="completed",
            source_created_at=now - timedelta(minutes=1),
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    result = list_content_records(kind="article", limit=20, offset=0, current_user=test_user, db=db_session)

    assert result["pagination"]["total"] == 2
    assert [item["source"] for item in result["items"]] == ["ip_daily", "online_writer"]
    assert result["items"][0]["content"] == "这是今日生成的正文。"


def test_content_image_urls_extract_real_article_images():
    urls = _content_image_urls(
        content=(
            "正文前\n\n![配图](https://cdn.example.test/article-1.jpg)\n\n"
            '<img src="https://cdn.example.test/article-2.png" alt="配图">'
        ),
        meta={"images": [{"url": "https://cdn.example.test/article-3.webp"}]},
    )

    assert urls == [
        "https://cdn.example.test/article-1.jpg",
        "https://cdn.example.test/article-2.png",
        "https://cdn.example.test/article-3.webp",
    ]


def test_wechat_article_list_uses_first_body_image_as_real_cover(db_session, test_user):
    now = datetime.utcnow()
    db_session.add(
        UserContentRecord(
            user_id=test_user.id,
            source="online_wechat_article",
            source_id="wechat-001",
            kind="wechat_article",
            title="带图公众号文章",
            content="正文\n\n![首图](https://cdn.example.test/wechat-cover.jpg)",
            status="completed",
            source_created_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    result = list_content_records(kind="wechat_article", limit=20, offset=0, current_user=test_user, db=db_session)

    assert result["items"][0]["cover_url"] == "https://cdn.example.test/wechat-cover.jpg"
    assert result["items"][0]["image_urls"] == ["https://cdn.example.test/wechat-cover.jpg"]
