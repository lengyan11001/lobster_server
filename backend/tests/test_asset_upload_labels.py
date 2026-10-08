import asyncio
import io
import json
from datetime import datetime

from starlette.datastructures import UploadFile

from backend.app.models import Asset, H5ChatDevicePresence, H5ChatMessage


def _save_png(file_obj, ext, content_type):
    file_obj.seek(0)
    data = file_obj.read()
    return "labeled-upload", "assets/labeled-upload.png", len(data), "https://cdn.example.com/labeled-upload.png"


def test_upload_without_labels_does_not_store_form_defaults(db_session, test_user, monkeypatch):
    from fastapi import Form
    from backend.app.api import assets

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", _save_png)
    upload = UploadFile(filename="demo.png", file=io.BytesIO(b"image-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=False,
            current_user=test_user,
            db=db_session,
        )
    )

    row = db_session.query(Asset).filter(Asset.asset_id == result["asset_id"]).one()
    assert not isinstance(row.tags, type(Form("")))
    assert row.tags is None
    assert "creative_candidate_group" not in (row.meta or {})
    assert "creative_candidate_groups" not in (row.meta or {})
    assert result["creative_candidate_group"] == ""
    assert result["tags"] == ""


def test_upload_stores_optional_group_and_tags(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", _save_png)
    upload = UploadFile(filename="demo.png", file=io.BytesIO(b"image-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=False,
            creative_candidate_group="  spring   hero  ",
            tags="hot, hot, cover; hero",
            current_user=test_user,
            db=db_session,
        )
    )

    row = db_session.query(Asset).filter(Asset.asset_id == result["asset_id"]).one()
    assert row.tags == "hot,cover,hero"
    assert row.meta["creative_candidate_group"] == "spring hero"
    assert row.meta["creative_candidate_groups"] == ["spring hero"]
    assert result["creative_candidate_group"] == "spring hero"
    assert result["tags"] == "hot,cover,hero"


def test_blank_upload_labels_do_not_fail_or_write_empty_group(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", _save_png)
    upload = UploadFile(filename="demo.png", file=io.BytesIO(b"image-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=False,
            creative_candidate_group="   ",
            tags="  ",
            current_user=test_user,
            db=db_session,
        )
    )

    row = db_session.query(Asset).filter(Asset.asset_id == result["asset_id"]).one()
    assert row.tags is None
    assert "creative_candidate_group" not in (row.meta or {})


def test_split_command_carries_labels_but_intermediate_source_does_not(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    device = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="online-label-device",
        display_name="Online",
        account_payload={"capabilities": ["asset_video_split_v1"]},
        last_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add(device)
    db_session.commit()

    def fake_save(file_obj, ext, content_type):
        file_obj.seek(0)
        data = file_obj.read()
        return "split-source", "assets/split-source.mp4", len(data), "https://cdn.example.com/source.mp4"

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)
    upload = UploadFile(filename="demo.mp4", file=io.BytesIO(b"video-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=True,
            creative_candidate_group="spring hero",
            tags="hot,cover",
            current_user=test_user,
            db=db_session,
        )
    )

    source = db_session.query(Asset).filter(Asset.asset_id == "split-source").one()
    assert source.meta["content_visibility"] == "intermediate"
    assert source.tags is None
    assert "creative_candidate_group" not in source.meta
    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    command = json.loads(message.content.removeprefix("__LOBSTER_H5_CLIENT_COMMAND__"))
    assert command["creative_candidate_group"] == "spring hero"
    assert command["tags"] == "hot,cover"
    assert command["segment_seconds"] == 3
    assert "keep_source" not in command


def test_video_segment_upload_stores_shared_labels(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    def fake_save(file_obj, ext, content_type):
        file_obj.seek(0)
        data = file_obj.read()
        return "segment-asset", "assets/segment-asset.mp4", len(data), "https://cdn.example.com/segment.mp4"

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)
    upload = UploadFile(filename="segment_000.mp4", file=io.BytesIO(b"video-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=False,
            video_segment=True,
            segment_index=1,
            split_job_id="job-1",
            source_upload_filename="demo.mp4",
            creative_candidate_group="spring hero",
            tags="hot,cover",
            current_user=test_user,
            db=db_session,
        )
    )

    row = db_session.query(Asset).filter(Asset.asset_id == result["asset_id"]).one()
    assert row.tags == "hot,cover"
    assert row.meta["creative_candidate_group"] == "spring hero"
    assert row.meta["video_segment"] is True
    assert row.meta["segment_index"] == 1


def test_creative_group_list_counts_images_only(db_session, test_user):
    from backend.app.api import assets

    rows = [
        Asset(asset_id="img-a", user_id=test_user.id, filename="a.png", media_type="image", file_size=1, source_url="https://cdn.example.com/a.png", meta={"asset_origin": "user_upload", "creative_candidate_group": "A"}),
        Asset(asset_id="vid-a", user_id=test_user.id, filename="a.mp4", media_type="video", file_size=1, source_url="https://cdn.example.com/a.mp4", meta={"asset_origin": "user_upload", "creative_candidate_group": "A"}),
        Asset(asset_id="doc-d", user_id=test_user.id, filename="d.pdf", media_type="document", file_size=1, source_url="https://cdn.example.com/d.pdf", meta={"asset_origin": "user_upload", "creative_candidate_group": "D"}),
        Asset(asset_id="hid-b", user_id=test_user.id, filename="b.png", media_type="image", file_size=1, source_url="https://cdn.example.com/b.png", meta={"asset_origin": "user_upload", "content_visibility": "hidden", "creative_candidate_group": "B"}),
        Asset(asset_id="tpl-c", user_id=test_user.id, filename="c.png", media_type="image", file_size=1, source_url="https://cdn.example.com/c.png", model="shanjian-digital-human-template-media", meta={"asset_origin": "user_upload", "creative_candidate_group": "C"}),
    ]
    db_session.add_all(rows)
    db_session.commit()

    result = assets.list_creative_candidate_groups(current_user=test_user, db=db_session)
    groups = {item["name"]: item for item in result["groups"]}

    assert groups["A"]["count"] == 1
    assert groups["D"]["count"] == 0
    assert "B" not in groups
    assert "C" not in groups
def test_update_asset_labels_sets_and_clears_non_image_asset(db_session, test_user):
    from backend.app.api import assets

    row = Asset(
        asset_id="doc-labels",
        user_id=test_user.id,
        filename="notes.pdf",
        media_type="document",
        file_size=12,
        source_url="https://cdn.example.com/notes.pdf",
        meta={"asset_origin": "generated", "keep_me": "yes"},
        created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    auto = "auto," + ("y" * 3000)
    result = assets.update_asset_labels(
        asset_id=row.asset_id,
        body=assets.AssetLabelsReq(creative_candidate_group="  spring   hero  ", tags=auto),
        current_user=test_user,
        db=db_session,
    )

    db_session.refresh(row)
    assert result["creative_candidate_group"] == "spring hero"
    assert result["creative_candidate_groups"] == ["spring hero"]
    assert result["tags"] == auto[:2048]
    assert row.tags == auto[:2048]
    assert row.meta["creative_candidate_group"] == "spring hero"
    assert row.meta["creative_candidate_groups"] == ["spring hero"]
    assert row.meta["asset_origin"] == "generated"
    assert row.meta["keep_me"] == "yes"

    cleared = assets.update_asset_labels(
        asset_id=row.asset_id,
        body=assets.AssetLabelsReq(creative_candidate_group="   ", tags="  "),
        current_user=test_user,
        db=db_session,
    )

    db_session.refresh(row)
    assert cleared["creative_candidate_group"] == ""
    assert cleared["creative_candidate_groups"] == []
    assert cleared["tags"] == ""
    assert row.tags is None
    assert "creative_candidate_group" not in row.meta
    assert "creative_candidate_groups" not in row.meta
    assert row.meta["asset_origin"] == "generated"
    assert row.meta["keep_me"] == "yes"
def test_existing_asset_split_keeps_source_seconds_and_labels(db_session, test_user):
    from backend.app.api import assets

    device = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="online-label-device",
        display_name="Online",
        account_payload={"capabilities": ["asset_video_split_v1"]},
        last_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add(device)
    db_session.add(
        Asset(
            asset_id="library-video",
            user_id=test_user.id,
            filename="library.mp4",
            media_type="video",
            file_size=10,
            source_url="https://cdn.example.com/library.mp4",
            tags="hot,cover",
            meta={
                "asset_origin": "user_upload",
                "creative_candidate_group": "spring hero",
                "creative_candidate_groups": ["spring hero"],
            },
        )
    )
    db_session.commit()

    result = assets.split_saved_asset(
        asset_id="library-video",
        body=assets.AssetSplitReq(segment_seconds=8),
        current_user=test_user,
        db=db_session,
    )

    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    command = json.loads(message.content.removeprefix("__LOBSTER_H5_CLIENT_COMMAND__"))
    assert command["action"] == "split_uploaded_video_asset"
    assert command["segment_seconds"] == 8
    assert command["creative_candidate_group"] == "spring hero"
    assert command["tags"] == "hot,cover"
    assert command["keep_source"] is True
    source = db_session.query(Asset).filter(Asset.asset_id == "library-video").one()
    assert source.tags == "hot,cover"


def test_existing_video_ai_tags_command_omits_old_tags(db_session, test_user):
    from backend.app.api import assets

    device = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="online-ai-device",
        display_name="Online",
        account_payload={"capabilities": ["asset_video_split_v1"]},
        last_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add(device)
    db_session.add(
        Asset(
            asset_id="library-video-ai",
            user_id=test_user.id,
            filename="library-ai.mp4",
            media_type="video",
            file_size=10,
            source_url="https://cdn.example.com/library-ai.mp4",
            tags="old,tags",
            meta={"creative_candidate_group": "spring hero"},
        )
    )
    db_session.commit()

    result = assets.fill_saved_asset_ai_tags(
        asset_id="library-video-ai",
        current_user=test_user,
        db=db_session,
    )
    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    command = json.loads(message.content.removeprefix("__LOBSTER_H5_CLIENT_COMMAND__"))
    assert command["action"] == "fill_asset_ai_tags"
    assert command["creative_candidate_group"] == "spring hero"
    assert command["media_type"] == "video"
    assert "tags" not in command
