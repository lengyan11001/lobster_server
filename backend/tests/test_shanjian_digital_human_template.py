import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api import shanjian_digital_human as digital_human_api
from backend.app.api.assets import _asset_hidden_from_library, list_assets
from backend.app.api.shanjian_digital_human import (
    CreateVideoBody,
    _apply_video_duration_limit,
    _requested_video_duration_limit,
    _resolve_video_template_meta,
    _stored_digital_human_template,
)
from backend.app.models import Asset, IPContentScheduleTemplate, ShanjianDigitalHumanVideoTask


def test_h5_app_exposes_digital_human_template_list_route():
    from backend.app.h5_main import app

    paths = {route.path for route in app.routes}
    assert "/api/shanjian-smart-clip/templates" in paths


def test_active_personal_template_supplies_digital_human_editing_template(db_session, test_user):
    current = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="销售内容模板",
        status="active",
        meta={
            "digital_human_template": {
                "scene": "realMan",
                "style_id": "style-current",
                "name": "当前剪辑模板",
                "cover_url": "https://example.test/current.jpg",
            }
        },
    )
    db_session.add(current)
    db_session.flush()
    personal = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="个人默认配置",
        status="active",
        meta={
            "current_template_id": current.id,
            "digital_human_template": {"style_id": "style-stale"},
        },
    )
    db_session.add(personal)
    db_session.commit()

    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, CreateVideoBody())

    assert source == "active_personal_template"
    assert template_meta is not None
    assert template_meta["style_id"] == "style-current"
    assert template_meta["template_scene"] == "realMan"


def test_explicit_video_template_overrides_active_personal_template(db_session, test_user):
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name="个人默认配置",
            status="active",
            meta={"digital_human_template": {"style_id": "style-default"}},
        )
    )
    db_session.commit()

    body = CreateVideoBody(style_id="style-request", template_scene="realMan")
    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, body)

    assert source == "request"
    assert template_meta is not None
    assert template_meta["style_id"] == "style-request"


def test_request_can_disable_active_personal_template(db_session, test_user):
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name="个人默认配置",
            status="active",
            meta={"digital_human_template": {"style_id": "style-default"}},
        )
    )
    db_session.commit()

    template_meta, source = _resolve_video_template_meta(
        db_session,
        test_user.id,
        CreateVideoBody(use_template=False),
    )

    assert template_meta is None
    assert source == "request_disabled"


def test_request_requiring_template_must_supply_selection(db_session, test_user):
    with pytest.raises(HTTPException) as exc_info:
        _resolve_video_template_meta(
            db_session,
            test_user.id,
            CreateVideoBody(use_template=True),
        )

    assert exc_info.value.status_code == 400
    assert "选择具体模板" in str(exc_info.value)


def test_current_template_can_explicitly_disable_automatic_editing(db_session, test_user):
    current = IPContentScheduleTemplate(
        user_id=test_user.id,
        name="不剪辑模板",
        status="active",
        meta={"digital_human_template": None},
    )
    db_session.add(current)
    db_session.flush()
    db_session.add(
        IPContentScheduleTemplate(
            user_id=test_user.id,
            name="个人默认配置",
            status="active",
            meta={
                "current_template_id": current.id,
                "digital_human_template": {"style_id": "style-stale"},
            },
        )
    )
    db_session.commit()

    template_meta, source = _resolve_video_template_meta(db_session, test_user.id, CreateVideoBody())

    assert template_meta is None
    assert source == ""


def test_stored_template_rules_are_normalized_without_trusting_bad_duration():
    configured, template_meta = _stored_digital_human_template(
        {
            "digital_human_template": {
                "id": "style-1",
                "video_duration": "invalid",
                "pack_rules": {"subtitleSwitch": False},
                "process_rules": {"watermarkShow": True},
            }
        }
    )

    assert configured is True
    assert template_meta is not None
    assert template_meta["style_id"] == "style-1"
    assert template_meta["video_duration"] == 30
    assert template_meta["subtitle_switch"] is False
    assert template_meta["watermark_show"] is True


def test_stored_template_accepts_template_id_alias():
    configured, template_meta = _stored_digital_human_template(
        {"digital_human_template": {"templateId": "template-1"}}
    )

    assert configured is True
    assert template_meta is not None
    assert template_meta["style_id"] == "template-1"


def test_editing_template_duration_is_not_a_hard_output_limit():
    assert _requested_video_duration_limit(
        {"template": {"style_id": "style-1", "video_duration": 30}}
    ) is None


def test_explicit_hard_output_limit_overrides_template_duration():
    assert _requested_video_duration_limit(
        {
            "output_constraints": {"hard_max_duration": 29},
            "template": {"style_id": "style-1", "video_duration": 60},
        }
    ) == 29.0


def test_long_video_is_not_limited_by_template_duration():
    assert _requested_video_duration_limit(
        {
            "output_constraints": {"duration_mode": "long"},
            "template": {"style_id": "style-1", "video_duration": 30},
        }
    ) is None


@pytest.mark.asyncio
async def test_explicitly_limited_video_is_replaced_with_trimmed_tos_asset(db_session, test_user, monkeypatch):
    row = ShanjianDigitalHumanVideoTask(
        user_id=test_user.id,
        title="朋友圈数字人口播",
        status="succeed",
        task_id="base-task-1",
        submit_payload={
            "output_constraints": {"hard_max_duration": 30},
            "template": {"style_id": "style-1", "video_duration": 30},
        },
    )
    db_session.add(row)
    db_session.flush()

    async def fake_download(_url, *, accept="*/*"):
        assert accept.startswith("video/")
        return b"source-video", "video/mp4"

    monkeypatch.setattr(digital_human_api, "_download_media_bytes", fake_download)
    monkeypatch.setattr(
        digital_human_api,
        "_run_ffmpeg_duration_cap",
        lambda data, limit: (b"trimmed-video", 29.72),
    )
    monkeypatch.setattr(
        digital_human_api,
        "_save_bytes_or_tos",
        lambda data, ext, content_type: ("trimmed123", "assets/trimmed123.mp4", len(data), "https://cdn.test/trimmed123.mp4"),
    )

    final_url, final_duration, postprocess = await _apply_video_duration_limit(
        row=row,
        db=db_session,
        current_user=test_user,
        source_video_url="https://upstream.test/edited.mp4",
        source_duration=32.12,
    )

    assert final_url == "https://cdn.test/trimmed123.mp4"
    assert final_duration == 29.72
    assert postprocess["duration_status"] == "trimmed"
    assert row.submit_payload["output_constraints"]["final_asset_id"] == "trimmed123"
    asset = db_session.query(Asset).filter(Asset.asset_id == "trimmed123").one()
    assert asset.meta["source_duration"] == 32.12


@pytest.mark.asyncio
async def test_template_only_video_is_unbounded_and_not_reencoded(db_session, test_user, monkeypatch):
    row = ShanjianDigitalHumanVideoTask(
        user_id=test_user.id,
        title="短数字人口播",
        status="succeed",
        task_id="base-task-2",
        submit_payload={"template": {"style_id": "style-1", "video_duration": 30}},
    )
    db_session.add(row)
    db_session.flush()

    async def should_not_download(*_args, **_kwargs):
        raise AssertionError("within-limit video should not be downloaded")

    monkeypatch.setattr(digital_human_api, "_download_media_bytes", should_not_download)
    final_url, final_duration, postprocess = await _apply_video_duration_limit(
        row=row,
        db=db_session,
        current_user=test_user,
        source_video_url="https://upstream.test/short.mp4",
        source_duration=24.6,
    )

    assert final_url == "https://upstream.test/short.mp4"
    assert final_duration == 24.6
    assert postprocess["duration_status"] == "unbounded"
    assert postprocess["final_asset_id"]
    final_asset = db_session.query(Asset).filter(Asset.asset_id == postprocess["final_asset_id"]).one()
    assert final_asset.model == "shanjian-digital-human-final"
    assert final_asset.source_url == final_url


@pytest.mark.asyncio
async def test_video_task_poll_releases_db_transaction_during_external_io(db_session, test_user, monkeypatch):
    db_session.expire_on_commit = False
    row = ShanjianDigitalHumanVideoTask(
        user_id=test_user.id,
        title="transaction boundary",
        status="processing",
        task_id="base-task-transaction",
        submit_payload={},
    )
    db_session.add(row)
    db_session.commit()

    async def fake_get(path, token, params):
        assert path == "/v1/task/info"
        assert params == {"taskId": "base-task-transaction"}
        assert not db_session.in_transaction()
        return {
            "data": {
                "status": "succeed",
                "result": {
                    "videoUrl": "https://cdn.test/final.mp4",
                    "duration": 24.2,
                },
            }
        }

    async def fake_finalize(**_kwargs):
        assert not db_session.in_transaction()

    monkeypatch.setattr(digital_human_api, "_get", fake_get)
    monkeypatch.setattr(digital_human_api, "_finalize_row_billing", fake_finalize)

    result = await digital_human_api.query_video_task(
        digital_human_api.VideoTaskBody(record_id=row.id),
        Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
        test_user,
        db_session,
    )

    assert result["ok"] is True
    assert result["status"] == "succeed"
    assert result["video_url"] == "https://cdn.test/final.mp4"
    assert db_session.query(Asset).filter(Asset.user_id == test_user.id).count() == 1


@pytest.mark.asyncio
async def test_template_clip_submit_releases_db_transaction_during_external_io(db_session, test_user, monkeypatch):
    db_session.expire_on_commit = False
    row = ShanjianDigitalHumanVideoTask(
        user_id=test_user.id,
        title="clip transaction boundary",
        status="succeed",
        task_id="base-task-clip-transaction",
        video_url="https://upstream.test/base.mp4",
        submit_payload={"template": {"style_id": "style-1"}},
    )
    db_session.add(row)
    db_session.commit()

    async def fake_download(url, *, accept="*/*"):
        assert url == "https://upstream.test/base.mp4"
        assert accept.startswith("video/")
        assert not db_session.in_transaction()
        return b"base-video", "video/mp4"

    def fake_save(data, ext, content_type):
        assert data == b"base-video"
        assert ext == ".mp4"
        assert content_type == "video/mp4"
        return "base-asset", "assets/base-asset.mp4", len(data), "https://cdn.test/base-asset.mp4"

    async def fake_post(path, token, payload):
        assert path == "/v1/clip/video/realman_broadcast"
        assert payload["videoUrl"] == "https://cdn.test/base-asset.mp4"
        assert not db_session.in_transaction()
        return {"requestId": "clip-request", "data": {"taskId": "clip-task"}}

    monkeypatch.setattr(digital_human_api, "_download_media_bytes", fake_download)
    monkeypatch.setattr(digital_human_api, "_save_bytes_or_tos", fake_save)
    monkeypatch.setattr(digital_human_api, "_post", fake_post)

    result = await digital_human_api._submit_realman_clip_task(
        body=digital_human_api.VideoTaskBody(),
        db=db_session,
        current_user=test_user,
        row=row,
        template_meta={"style_id": "style-1", "materials": []},
        base_result_payload={},
    )

    assert result["clip_task_id"] == "clip-task"
    assert row.status == "processing"
    assert row.submit_payload["template"]["base_asset_id"] == "base-asset"


def test_shanjian_template_intermediate_asset_is_hidden_from_content_library(db_session, test_user):
    historical = Asset(
        asset_id="historical-intermediate",
        user_id=test_user.id,
        filename="base.mp4",
        media_type="video",
        model="shanjian-digital-human-template-media",
    )
    explicit = Asset(
        asset_id="explicit-intermediate",
        user_id=test_user.id,
        filename="base-2.mp4",
        media_type="video",
        model="other",
        meta={"asset_origin": "intermediate", "content_visibility": "hidden"},
    )
    final = Asset(
        asset_id="visible-final",
        user_id=test_user.id,
        filename="final.mp4",
        media_type="video",
        model="shanjian-digital-human-final",
        meta={"asset_origin": "generated", "content_visibility": "visible"},
    )

    assert _asset_hidden_from_library(historical) is True
    assert _asset_hidden_from_library(explicit) is True
    assert _asset_hidden_from_library(final) is False

    db_session.add_all([historical, explicit, final])
    db_session.commit()
    payload = list_assets(
        media_type="video",
        q=None,
        source=None,
        origin="generated",
        asset_origin=None,
        limit=50,
        offset=0,
        current_user=test_user,
        db=db_session,
    )
    assert payload["total"] == 1
    assert [item["asset_id"] for item in payload["assets"]] == ["visible-final"]
