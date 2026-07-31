from backend.app.api.assets import get_asset, list_assets
from backend.app.models import Asset, GenerationRecord, ScheduledTaskRun


def test_asset_detail_recovers_copy_and_prompt_from_generation_run(db_session, test_user):
    source_asset_id = "online-image-001"
    db_session.add(
        GenerationRecord(
            user_id=test_user.id,
            client_asset_id=source_asset_id,
            public_url="https://cdn.example.test/online-image-001.png",
            media_type="image",
            filename="online-image-001.png",
            prompt="生成记录里的画面提示词",
            source="save-url",
        )
    )
    db_session.add(
        ScheduledTaskRun(
            id="run-image-001",
            user_id=test_user.id,
            title="创作图片",
            task_kind="capability",
            content="",
            status="completed",
            result_text="生成完成。",
            result_payload={
                "caption": "适合直接发布的正文",
                "skill_prompt": "任务结果里的画面提示词",
                "mcp_result": {"plan": {"title": "真实发布标题", "copy": "备用发布正文"}},
                "result_refs": {"asset_ids": [source_asset_id]},
            },
        )
    )
    db_session.add(
        Asset(
            asset_id="server-image-001",
            user_id=test_user.id,
            filename="online-image-001.png",
            media_type="image",
            source_url="https://cdn.example.test/online-image-001.png",
            meta={
                "asset_origin": "generated",
                "registered_from": "online",
                "source_asset_id": source_asset_id,
                "existing_marker": "keep-me",
            },
        )
    )
    db_session.commit()

    detail = get_asset("server-image-001", current_user=test_user, db=db_session)

    assert detail["title"] == "真实发布标题"
    assert detail["description"] == "适合直接发布的正文"
    assert detail["creative_prompt"] == "生成记录里的画面提示词"
    assert detail["content_context"]["source"] == "generation_record"
    stored = db_session.query(Asset).filter(Asset.asset_id == "server-image-001").one()
    assert stored.meta["existing_marker"] == "keep-me"

    listing = list_assets(
        media_type="image",
        q=None,
        source=None,
        origin="generated",
        asset_origin=None,
        limit=20,
        offset=0,
        current_user=test_user,
        db=db_session,
    )
    assert listing["assets"][0]["title"] == "真实发布标题"
    assert listing["assets"][0]["description"] == "适合直接发布的正文"
