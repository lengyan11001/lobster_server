from __future__ import annotations

import asyncio
import io
import threading
from datetime import datetime
from types import SimpleNamespace

from starlette.requests import Request
from starlette.datastructures import UploadFile


def _request(brand_mark: str = "bihuo") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "headers": [(b"x-lobster-brand", brand_mark.encode("ascii"))],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_authentication_releases_its_read_transaction(db_session, test_user):
    from backend.app.api.auth import access_token_claims, create_access_token, get_current_user

    user_id = int(test_user.id)
    token = create_access_token(access_token_claims(test_user))
    db_session.rollback()

    loaded = asyncio.run(get_current_user(_request(), token, db_session))

    assert not db_session.in_transaction()
    assert int(loaded.id) == user_id


def test_voice_preview_releases_db_before_tts(db_session, test_user, monkeypatch):
    from backend.app.api import hifly_assets
    from backend.app.models import UserHiflyVoiceAsset

    user_id = int(test_user.id)
    row = UserHiflyVoiceAsset(
        user_id=user_id,
        title="test voice",
        status="completed",
        hifly_task_id="qwen_voice_test_task",
        hifly_voice_id="qwen-test-voice",
        meta={"provider": "qwen", "qwen_voice_id": "qwen-test-voice"},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()

    async def fake_preview_tts_audio(**_kwargs):
        assert not db_session.in_transaction()
        return {
            "audio_bytes": b"test-audio",
            "duration_seconds": 1,
            "segments": ["test"],
            "extra_info": {},
        }

    monkeypatch.setattr(hifly_assets, "_preview_tts_audio", fake_preview_tts_audio)
    body = hifly_assets.HiflyVoicePreviewTtsBody(voice="qwen-test-voice", text="test")

    result = asyncio.run(
        hifly_assets.preview_my_voice_tts(
            body,
            current_user=SimpleNamespace(id=user_id),
            db=db_session,
        )
    )

    assert result["ok"] is True
    assert result["audio_url"].startswith("data:audio/mpeg;base64,")


def test_save_url_releases_db_and_offloads_tos_upload(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    caller_thread = threading.get_ident()

    class FakeResponse:
        content = b"video-bytes"
        headers = {"content-type": "video/mp4"}

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            assert not db_session.in_transaction()
            return FakeResponse()

    def fake_save(data, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        assert data == b"video-bytes"
        assert ext == ".mp4"
        assert content_type == "video/mp4"
        return "asset-save-url", "assets/asset-save-url.mp4", len(data), "https://cdn.example.com/asset-save-url.mp4"

    monkeypatch.setattr(assets.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(assets, "_get_tos_config", lambda: {"bucket_name": "test"})
    monkeypatch.setattr(assets, "_save_bytes_or_tos", fake_save)

    result = asyncio.run(
        assets.save_asset_from_url(
            assets.SaveAssetReq(
                url="https://source.example.com/result.mp4",
                media_type="video",
            ),
            current_user=SimpleNamespace(id=int(test_user.id)),
            db=db_session,
        )
    )

    assert result["asset_id"] == "asset-save-url"
    assert result["source_url"] == "https://cdn.example.com/asset-save-url.mp4"


def test_asset_upload_releases_db_and_offloads_tos_upload(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    caller_thread = threading.get_ident()

    def fake_save(data, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        assert data == b"image-bytes"
        return "asset-upload", "assets/asset-upload.png", len(data), "https://cdn.example.com/asset-upload.png"

    monkeypatch.setattr(assets, "_save_bytes_or_tos", fake_save)
    upload = UploadFile(filename="demo.png", file=io.BytesIO(b"image-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=False,
            current_user=test_user,
            db=db_session,
        )
    )

    assert result["asset_id"] == "asset-upload"
    assert result["source_url"] == "https://cdn.example.com/asset-upload.png"


def test_split_video_upload_offloads_ffmpeg_and_tos(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    caller_thread = threading.get_ident()

    def fake_split(data, ext):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        assert data == b"video-bytes"
        assert ext == ".mp4"
        return [("segment_001.mp4", b"one"), ("segment_002.mp4", b"two")]

    def fake_save(data, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        asset_id = f"asset-{data.decode('ascii')}"
        return asset_id, f"assets/{asset_id}.mp4", len(data), f"https://cdn.example.com/{asset_id}.mp4"

    monkeypatch.setattr(assets, "_split_video_bytes", fake_split)
    monkeypatch.setattr(assets, "_save_bytes_or_tos", fake_save)
    upload = UploadFile(filename="demo.mp4", file=io.BytesIO(b"video-bytes"))

    result = asyncio.run(
        assets.upload_asset(
            file=upload,
            split_video=True,
            current_user=test_user,
            db=db_session,
        )
    )

    assert result["split_video"] is True
    assert result["total"] == 2


def test_h5_composer_limits_parallel_asset_uploads():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    upload_many = script.split("async function uploadComposerFiles(files)", 1)[1].split(
        "function hasUploadingImages", 1
    )[0]

    assert "index += 2" in upload_many
    assert "queue.slice(index, index + 2)" in upload_many


def test_h5_background_run_refresh_is_compact_and_non_overlapping():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    load_runs = script.split("async function loadRuns(options = {})", 1)[1].split(
        "async function loadWorkflowRunsForDate", 1
    )[0]
    init = script.split("(async function init()", 1)[1]

    assert "if (state.runListLoading) return false;" in load_runs
    assert "preserveExisting" in load_runs
    assert 'compact: true' in init
    assert 'preserveExisting: true' in init
    assert '}, 15000);' in init
    assert 'loadRuns({ reset: true });\n      }, 5000);' not in init


def test_h5_background_refresh_does_not_rebuild_hidden_resource_pages():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    refresh_devices = script.split("async function refreshDeviceStatus()", 1)[1].split(
        "function normalizeAvatarRows", 1
    )[0]
    load_runs = script.split("async function loadRuns(options = {})", 1)[1].split(
        "async function loadWorkflowRunsForDate", 1
    )[0]
    init = script.split("(async function init()", 1)[1]

    assert "if (state.deviceStatusPromise) return state.deviceStatusPromise;" in refresh_devices
    assert "loadMountedAccounts" not in refresh_devices
    assert 'if (view === "office") renderOfficeEmployees();' in refresh_devices
    assert 'if (!box || !document.querySelector("#runListView.active")) return true;' in load_runs
    assert 'if (activeViewKey() === "office") renderOfficeEmployees();' in load_runs
    assert '["assetLibrary", "mountedAccounts"].includes(activeViewKey())' in init
    assert '["office", "workflow", "workList", "runList", "runDetail", "department", "secretary"]' in init


def test_h5_mounted_accounts_refresh_once_on_entry_or_manual_action():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    switch_tab = script.split("function switchTab(tab)", 1)[1].split("function openDepartmentView", 1)[0]

    assert 'if (key === "mountedAccounts")' in switch_tab
    assert "refreshMountedAccounts().catch(() => {});" in switch_tab
    assert '$("mountedAccountRefreshBtn")?.addEventListener("click", () => {' in script
    assert 'refreshMountedAccounts().catch((err) => toast(err.message || "刷新失败"));' in script


def test_h5_closing_asset_preview_releases_media_resources():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    close_preview = script.split("function closeAssetPreviewDialog()", 1)[1].split(
        "function closeLeadDetailDialog", 1
    )[0]

    assert 'media.removeAttribute("src");' in close_preview
    assert "media.load();" in close_preview
    assert "body.replaceChildren();" in close_preview


def test_h5_run_list_requests_compactness_explicitly():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert '&compact=${compact ? "1" : "0"}' in script


def test_old_h5_run_poll_defaults_to_compact_response():
    from backend.app.api.scheduled_tasks import _run_list_uses_compact_response

    h5_request = _request()
    h5_request.scope["headers"] = [(b"referer", b"https://h5.bhzn.top/")]
    online_request = _request()

    assert _run_list_uses_compact_response(h5_request, None) is True
    assert _run_list_uses_compact_response(h5_request, False) is False
    assert _run_list_uses_compact_response(online_request, None) is False
