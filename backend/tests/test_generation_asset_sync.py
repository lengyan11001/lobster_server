from backend.app.api.assets import (
    RegisterAssetBatchReq,
    RegisterAssetUrlReq,
    list_assets,
    register_asset_batch,
)
from backend.app.api.generation_records import (
    GenerationRecordReportBody,
    backfill_generation_records_to_assets,
    repair_generated_asset_origins,
    report_generation_record,
)
from backend.app.models import Asset, DataMigrationMarker, GenerationRecord


def _report_body(**overrides):
    values = {
        "client_asset_id": "online-generated-001",
        "public_url": "https://cdn.example.test/generated-001.png",
        "media_type": "image",
        "filename": "generated-001.png",
        "file_size": 1024,
        "prompt": "product photo on a clean background",
        "model": "image-model",
        "tags": "generated,product",
        "generation_task_id": "task-generated-001",
    }
    values.update(overrides)
    return GenerationRecordReportBody(**values)


def test_generation_report_materializes_shared_asset(db_session, test_user):
    result = report_generation_record(_report_body(), current_user=test_user, db=db_session)

    assert result["ok"] is True
    assert result["asset"]["source_url"] == "https://cdn.example.test/generated-001.png"
    assert result["asset"]["preview_url"] == result["asset"]["source_url"]
    assert result["asset"]["cover_url"] == result["asset"]["source_url"]
    assert db_session.query(GenerationRecord).count() == 1
    assert db_session.query(Asset).count() == 1

    asset = db_session.query(Asset).one()
    assert asset.meta["asset_origin"] == "generated"
    assert asset.meta["source_asset_id"] == "online-generated-001"
    assert asset.meta["generation_record_id"] == db_session.query(GenerationRecord).one().id

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
    assert listing["total"] == 1
    assert listing["assets"][0]["source_url"] == result["asset"]["source_url"]
    assert listing["assets"][0]["preview_url"] == result["asset"]["source_url"]


def test_generation_report_updates_same_online_asset_without_duplicates(db_session, test_user):
    report_generation_record(_report_body(), current_user=test_user, db=db_session)
    second = report_generation_record(
        _report_body(
            public_url="https://cdn.example.test/generated-001-v2.png",
            prompt="updated prompt",
        ),
        current_user=test_user,
        db=db_session,
    )

    assert second["created"] is False
    assert db_session.query(GenerationRecord).count() == 1
    assert db_session.query(Asset).count() == 1
    asset = db_session.query(Asset).one()
    assert asset.source_url == "https://cdn.example.test/generated-001-v2.png"
    assert asset.prompt == "updated prompt"


def test_batch_registration_preserves_media_and_display_metadata(db_session, test_user):
    result = register_asset_batch(
        RegisterAssetBatchReq(assets=[
            RegisterAssetUrlReq(
                url="https://cdn.example.test/batch-image.png",
                media_type="image",
                filename="batch-image.png",
                source_asset_id="local-image-001",
                asset_origin="generated",
                title="Campaign image",
                description="Ready to publish",
                creative_prompt="bright storefront",
                source_created_at="2026-08-08T08:00:00Z",
            ),
            RegisterAssetUrlReq(
                url="https://cdn.example.test/batch-video.mp4",
                media_type="video",
                filename="batch-video.mp4",
                source_asset_id="local-video-001",
                asset_origin="generated",
                prompt="short campaign video",
            ),
        ]),
        current_user=test_user,
        db=db_session,
    )

    assert result["created"] == 2
    assert {item["media_type"] for item in result["items"]} == {"image", "video"}
    image = next(item for item in result["items"] if item["media_type"] == "image")
    video = next(item for item in result["items"] if item["media_type"] == "video")
    assert image["title"] == "Campaign image"
    assert image["cover_url"] == image["source_url"]
    assert video["cover_url"] == ""

    repeated = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url="https://cdn.example.test/batch-image.png",
            media_type="image",
            source_asset_id="local-image-001",
            asset_origin="generated",
        )]),
        current_user=test_user,
        db=db_session,
    )
    assert repeated["created"] == 0
    assert db_session.query(Asset).count() == 2


def test_batch_registration_does_not_reclassify_user_upload_with_same_url(db_session, test_user):
    shared_url = "https://cdn.example.test/shared-image.png"
    upload = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url=shared_url,
            media_type="image",
            source_asset_id="uploaded-image-001",
            asset_origin="user_upload",
        )]),
        current_user=test_user,
        db=db_session,
    )
    generated = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url=shared_url,
            media_type="image",
            source_asset_id="generated-image-001",
            asset_origin="generated",
        )]),
        current_user=test_user,
        db=db_session,
    )

    assert upload["created"] == 1
    assert generated["created"] == 1
    rows = db_session.query(Asset).order_by(Asset.id.asc()).all()
    assert len(rows) == 2
    assert {row.meta["asset_origin"] for row in rows} == {"user_upload", "generated"}


def test_batch_registration_keeps_same_source_id_isolated_by_origin(db_session, test_user):
    source_id = "shared-client-asset-001"
    uploaded = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url="https://cdn.example.test/input.png",
            media_type="image",
            source_asset_id=source_id,
            asset_origin="user_upload",
        )]),
        current_user=test_user,
        db=db_session,
    )
    generated = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url="https://cdn.example.test/output.png",
            media_type="image",
            source_asset_id=source_id,
            asset_origin="generated",
            generation_task_id="task-output-001",
        )]),
        current_user=test_user,
        db=db_session,
    )

    assert uploaded["created"] == 1
    assert generated["created"] == 1
    rows = db_session.query(Asset).order_by(Asset.id.asc()).all()
    assert len(rows) == 2
    assert {row.meta["asset_origin"] for row in rows} == {"user_upload", "generated"}


def test_generation_metadata_overrides_an_incoming_user_upload_origin(db_session, test_user):
    result = register_asset_batch(
        RegisterAssetBatchReq(assets=[RegisterAssetUrlReq(
            url="https://cdn.example.test/generated.png",
            media_type="image",
            source_asset_id="generated-client-001",
            asset_origin="user_upload",
            generation_task_id="task-generated-001",
        )]),
        current_user=test_user,
        db=db_session,
    )

    assert result["items"][0]["asset_origin"] == "generated"
    assert db_session.query(Asset).one().meta["asset_origin"] == "generated"


def test_batch_registration_keeps_distinct_online_records_that_share_a_url(db_session, test_user):
    shared_url = "https://cdn.example.test/reused-result.png"
    result = register_asset_batch(
        RegisterAssetBatchReq(assets=[
            RegisterAssetUrlReq(
                url=shared_url,
                media_type="image",
                source_asset_id="generated-result-001",
                asset_origin="generated",
                prompt="first result",
            ),
            RegisterAssetUrlReq(
                url=shared_url,
                media_type="image",
                source_asset_id="generated-result-002",
                asset_origin="generated",
                prompt="second result",
            ),
        ]),
        current_user=test_user,
        db=db_session,
    )

    assert result["created"] == 2
    assert {item["source_asset_id"] for item in result["items"]} == {
        "generated-result-001",
        "generated-result-002",
    }
    assert db_session.query(Asset).count() == 2


def test_historical_generation_backfill_runs_once(db_engine, db_session, test_user):
    db_session.add_all([
        GenerationRecord(
            user_id=test_user.id,
            client_asset_id="history-image-001",
            public_url="https://cdn.example.test/history-image.png",
            media_type="image",
            filename="history-image.png",
            prompt="historic image prompt",
            source="save-url",
        ),
        GenerationRecord(
            user_id=test_user.id,
            client_asset_id="history-video-001",
            public_url="https://cdn.example.test/history-video.mp4",
            media_type="video",
            filename="history-video.mp4",
            source="save-url",
        ),
    ])
    db_session.commit()

    first = backfill_generation_records_to_assets(db_engine, batch_size=50)
    second = backfill_generation_records_to_assets(db_engine, batch_size=50)
    db_session.expire_all()

    assert first == {"applied": True, "scanned": 2, "created": 2, "updated": 0}
    assert second == {"applied": False, "scanned": 0, "created": 0, "updated": 0}
    assert db_session.query(Asset).count() == 2
    assert db_session.query(DataMigrationMarker).count() == 1


def test_generation_origin_repair_moves_only_polluted_generated_rows(db_engine, db_session, test_user):
    db_session.add_all([
        Asset(
            asset_id="polluted-generated",
            user_id=test_user.id,
            filename="generated.png",
            media_type="image",
            file_size=1,
            source_url="https://cdn.example.test/polluted-generated.png",
            meta={
                "asset_origin": "user_upload",
                "generation_record_id": 7,
                "registered_from": "online_generation_report",
            },
        ),
        Asset(
            asset_id="real-upload",
            user_id=test_user.id,
            filename="upload.png",
            media_type="image",
            file_size=1,
            source_url="https://cdn.example.test/real-upload.png",
            meta={"asset_origin": "user_upload"},
        ),
    ])
    db_session.commit()

    first = repair_generated_asset_origins(db_engine, batch_size=50)
    second = repair_generated_asset_origins(db_engine, batch_size=50)
    db_session.expire_all()

    assert first == {"applied": True, "scanned": 2, "updated": 1}
    assert second == {"applied": False, "scanned": 0, "updated": 0}
    rows = {row.asset_id: row for row in db_session.query(Asset).all()}
    assert rows["polluted-generated"].meta["asset_origin"] == "generated"
    assert rows["real-upload"].meta["asset_origin"] == "user_upload"
