from __future__ import annotations

import ast
import asyncio
import io
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
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


def _request_with_installation(installation_id: str, brand_mark: str = "bihuo") -> Request:
    request = _request(brand_mark)
    request.scope["headers"].append((b"x-installation-id", installation_id.encode("ascii")))
    return request


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


def test_hifly_avatar_upload_releases_db_before_upstream(db_session, test_user, monkeypatch):
    from backend.app.api import hifly_assets

    async def fake_upload(*_args, **_kwargs):
        assert not db_session.in_transaction()
        return {
            "file_id": "hifly-file",
            "raw_bytes": b"image",
            "extension": "png",
            "content_type": "image/png",
            "filename": "avatar.png",
            "size": 5,
        }

    async def fake_post(*_args, **_kwargs):
        assert not db_session.in_transaction()
        return {"task_id": "avatar-task"}

    monkeypatch.setattr(hifly_assets, "_upload_file_to_hifly", fake_upload)
    monkeypatch.setattr(hifly_assets, "_post", fake_post)
    async def fake_store(_uploaded):
        return None

    monkeypatch.setattr(hifly_assets, "_store_input_asset", fake_store)
    monkeypatch.setattr(hifly_assets, "_persist_input_asset", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        hifly_assets.create_my_avatar_by_image_upload(
            request=_request(),
            token="token",
            title="测试分身",
            model=2,
            aigc_flag=0,
            file=UploadFile(filename="avatar.png", file=io.BytesIO(b"image")),
            current_user=test_user,
            db=db_session,
        )
    )

    assert result["ok"] is True
    assert result["item"]["task_id"] == "avatar-task"


def test_wecom_config_snapshot_releases_db_before_network(db_session, test_user):
    from backend.app.api.wecom import _require_config_with_secret
    from backend.app.models import WecomConfig

    row = WecomConfig(
        user_id=test_user.id,
        name="测试企微",
        callback_path="test-wecom-callback",
        token="callback-token",
        encoding_aes_key="a" * 43,
        corp_id="corp-id",
        secret="corp-secret",
    )
    db_session.add(row)
    db_session.commit()

    snapshot = _require_config_with_secret(db_session, row.id, test_user.id)

    assert not db_session.in_transaction()
    assert snapshot.corp_id == "corp-id"
    assert snapshot.secret == "corp-secret"


def test_hifly_library_lists_do_not_refresh_upstream_implicitly():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "hifly_assets.py").read_text(encoding="utf-8")
    avatar_list = source.split("def list_my_avatars(", 1)[1].split("def delete_my_avatar(", 1)[0]
    voice_list = source.split("def list_my_voices(", 1)[1].split("def list_h5_digital_library(", 1)[0]
    h5_library = source.split("def list_h5_digital_library(", 1)[1].split("@router", 1)[0]

    assert "_refresh_avatar_asset_from_hifly" not in avatar_list
    assert "_refresh_voice_asset_from_hifly" not in voice_list
    assert "_refresh_avatar_asset_from_hifly" not in h5_library
    assert "_refresh_voice_asset_from_hifly" not in h5_library


def test_streaming_chat_cancels_worker_when_client_disconnects():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "chat.py").read_text(encoding="utf-8")
    stream = source.split("async def _chat_stream_events(", 1)[1].split("@router.post(\"/chat/stream\"", 1)[0]

    assert "if not task.done():" in stream
    assert "task.cancel()" in stream
    assert "await asyncio.gather(task, return_exceptions=True)" in stream


def test_sutui_stream_generator_never_captures_request_db_session():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "sutui_chat_proxy.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    endpoint = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "sutui_chat_completions"
    )
    stream_generator = next(
        node
        for node in endpoint.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "gen"
    )
    captured_names = {node.id for node in ast.walk(stream_generator) if isinstance(node, ast.Name)}
    usage_calls = [
        node
        for node in ast.walk(stream_generator)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "log_model_usage_event"
    ]

    assert "db" not in captured_names
    assert "current_user" not in captured_names
    assert "billing_user_id" in captured_names
    assert len(usage_calls) == 2
    assert all(not call.args for call in usage_calls)


def test_save_url_releases_db_and_offloads_tos_upload(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    caller_thread = threading.get_ident()

    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        @staticmethod
        def raise_for_status():
            return None

        async def aiter_bytes(self, **_kwargs):
            yield b"video-bytes"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            assert not db_session.in_transaction()
            return FakeResponse()

    def fake_save(file_obj, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        file_obj.seek(0)
        data = file_obj.read()
        assert data == b"video-bytes"
        assert ext == ".mp4"
        assert content_type == "video/mp4"
        return "asset-save-url", "assets/asset-save-url.mp4", len(data), "https://cdn.example.com/asset-save-url.mp4"

    monkeypatch.setattr(assets.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(assets, "_get_tos_config", lambda: {"bucket_name": "test"})
    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)

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

    def fake_save(file_obj, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        file_obj.seek(0)
        data = file_obj.read()
        assert data == b"image-bytes"
        return "asset-upload", "assets/asset-upload.png", len(data), "https://cdn.example.com/asset-upload.png"

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)
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


def test_split_video_upload_queues_online_without_server_ffmpeg(db_session, test_user, monkeypatch):
    from backend.app.api import assets
    from backend.app.models import Asset, H5ChatDevicePresence, H5ChatMessage

    caller_thread = threading.get_ident()
    device = H5ChatDevicePresence(
        user_id=test_user.id,
        installation_id="online-video-device",
        display_name="Online",
        account_payload={"capabilities": ["asset_video_split_v1"]},
        last_seen_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db_session.add(device)
    db_session.commit()

    def fake_save(file_obj, ext, content_type):
        assert not db_session.in_transaction()
        assert threading.get_ident() != caller_thread
        file_obj.seek(0)
        data = file_obj.read()
        assert data == b"video-bytes"
        assert ext == ".mp4"
        return "online-split-source", "assets/online-split-source.mp4", len(data), "https://cdn.example.com/source.mp4"

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)
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
    assert result["processing"] == "online"
    assert result["assets"] == []
    assert result["installation_id"] == device.installation_id
    source = db_session.query(Asset).filter(Asset.asset_id == "online-split-source").one()
    assert source.meta["content_visibility"] == "intermediate"
    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    assert message.mode == "client_command"
    assert message.installation_id == device.installation_id
    assert '"action":"split_uploaded_video_asset"' in message.content


def test_memory_document_upload_queues_online_without_server_parser(db_session, test_user, monkeypatch):
    from backend.app.api import h5_chat, h5_personal_settings
    from backend.app.models import Asset, H5ChatDevicePresence, H5ChatMessage

    installation_id = "online-memory-device"
    db_session.add(
        H5ChatDevicePresence(
            user_id=test_user.id,
            installation_id=installation_id,
            display_name="Online",
            account_payload={"capabilities": ["memory_document_parse_v1"]},
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    async def fake_upload(_func, data, ext, content_type):
        assert not db_session.in_transaction()
        assert data == b"ppt-content"
        assert ext == ".pptx"
        assert content_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        return "memory-source", "assets/memory-source.pptx", len(data), "https://cdn.example.com/source.pptx"

    monkeypatch.setattr(h5_personal_settings, "_run_asset_upload_io", fake_upload)
    monkeypatch.setattr(h5_chat, "_clear_pending_empty_for_target", lambda *_args: None)
    upload = UploadFile(
        filename="intro.pptx",
        file=io.BytesIO(b"ppt-content"),
        headers={"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    )

    result = asyncio.run(
        h5_personal_settings.save_uploaded_memory_document(
            request=_request_with_installation(installation_id),
            files=[upload],
            title="产品介绍",
            notes="资料",
            raw_text="",
            urls="",
            mode="new",
            target_doc_id="",
            current_user=test_user,
            db=db_session,
        )
    )

    assert result["processing"] == "online"
    source = db_session.query(Asset).filter(Asset.asset_id == "memory-source").one()
    assert source.meta["content_visibility"] == "intermediate"
    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    assert message.installation_id == installation_id
    assert '"action":"parse_uploaded_memory_document"' in message.content


def test_memory_document_upload_requires_capable_online(db_session, test_user, monkeypatch):
    from backend.app.api import h5_personal_settings

    async def fail_upload(*_args):
        pytest.fail("must not upload before Online capability check")

    monkeypatch.setattr(h5_personal_settings, "_run_asset_upload_io", fail_upload)
    upload = UploadFile(filename="intro.pdf", file=io.BytesIO(b"pdf"))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            h5_personal_settings.save_uploaded_memory_document(
                request=_request_with_installation("missing-memory-device"),
                files=[upload],
                title="资料",
                notes="",
                raw_text="",
                urls="",
                mode="new",
                target_doc_id="",
                current_user=test_user,
                db=db_session,
            )
        )
    assert exc_info.value.status_code == 409
    assert "启动并登录 Online" in str(exc_info.value.detail)


def test_online_memory_parse_callback_is_idempotent_and_cleanup_is_explicit(db_session, test_user, monkeypatch):
    import json

    from backend.app.api import h5_personal_settings
    from backend.app.models import Asset, H5ChatMessage, OpenClawMemoryDocument

    installation_id = "online-memory-callback"
    message_id = "memory-callback-message"
    source_asset_id = "memory-callback-source"
    command = {
        "action": "parse_uploaded_memory_document",
        "source_asset_id": source_asset_id,
        "source_filename": "intro.pdf",
        "title": "产品资料",
        "notes": "上传资料",
        "mode": "new",
        "target_doc_id": "",
    }
    db_session.add(
        Asset(
            asset_id=source_asset_id,
            user_id=test_user.id,
            filename="assets/memory-callback-source.pdf",
            media_type="document",
            file_size=10,
            source_url="https://cdn.example.com/source.pdf",
            meta={"online_memory_parse_source": True, "content_visibility": "intermediate"},
        )
    )
    db_session.add(
        H5ChatMessage(
            id=message_id,
            user_id=test_user.id,
            installation_id=installation_id,
            mode="client_command",
            content=h5_personal_settings._H5_CLIENT_COMMAND_PREFIX + json.dumps(command),
            status="processing",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    deleted = []

    async def fake_io(func, object_key):
        deleted.append((func, object_key))
        return True

    monkeypatch.setattr(h5_personal_settings, "_run_asset_upload_io", fake_io)
    body = h5_personal_settings.OnlineMemoryParseCompleteBody(
        message_id=message_id,
        source_asset_id=source_asset_id,
        filename="intro.pdf",
        content_text="解析后的产品资料",
        sha256="a" * 64,
    )

    first = asyncio.run(
        h5_personal_settings.complete_online_memory_document(
            body,
            _request_with_installation(installation_id),
            current_user=test_user,
            db=db_session,
        )
    )
    second = asyncio.run(
        h5_personal_settings.complete_online_memory_document(
            body,
            _request_with_installation(installation_id),
            current_user=test_user,
            db=db_session,
        )
    )

    assert first["document"]["doc_id"] == second["document"]["doc_id"]
    assert db_session.query(OpenClawMemoryDocument).count() == 1
    assert db_session.query(Asset).filter(Asset.asset_id == source_asset_id).first() is not None
    assert not deleted

    cleanup = asyncio.run(
        h5_personal_settings.delete_online_memory_upload_source(
            source_asset_id,
            current_user=test_user,
            db=db_session,
        )
    )

    assert cleanup["ok"] is True
    assert db_session.query(Asset).filter(Asset.asset_id == source_asset_id).first() is None
    assert len(deleted) == 1


def test_memory_generation_upload_queues_all_sources_for_online(db_session, test_user, monkeypatch):
    import json

    from backend.app.api import h5_chat, h5_personal_settings
    from backend.app.models import Asset, H5ChatDevicePresence, H5ChatMessage

    installation_id = "online-memory-generate"
    db_session.add(
        H5ChatDevicePresence(
            user_id=test_user.id,
            installation_id=installation_id,
            display_name="Online",
            account_payload={"capabilities": ["memory_document_generate_v1"]},
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    uploaded = []

    async def fake_io(func, data, ext, content_type):
        assert func is h5_personal_settings._save_bytes_or_tos
        assert not db_session.in_transaction()
        index = len(uploaded) + 1
        uploaded.append((data, ext, content_type))
        return (
            f"memory-generate-source-{index}",
            f"assets/memory-generate-source-{index}{ext}",
            len(data),
            f"https://cdn.example.com/memory-generate-source-{index}{ext}",
        )

    monkeypatch.setattr(h5_personal_settings, "_run_asset_upload_io", fake_io)
    monkeypatch.setattr(h5_chat, "_clear_pending_empty_for_target", lambda *_args: None)
    uploads = [
        (UploadFile(filename="intro.pdf", file=io.BytesIO(b"pdf-content")), "source"),
        (UploadFile(filename="layout.pptx", file=io.BytesIO(b"ppt-content")), "reference"),
    ]

    result = asyncio.run(
        h5_personal_settings._queue_online_memory_generation(
            db=db_session,
            owner_user_id=test_user.id,
            installation_id=installation_id,
            files=uploads,
            direct_text="补充资料",
            doc_types=["brand_product_intro"],
            reference_doc_ids="",
        )
    )

    assert result["processing"] == "online"
    assert len(uploaded) == 2
    assert db_session.query(Asset).filter(Asset.user_id == test_user.id).count() == 2
    message = db_session.query(H5ChatMessage).filter(H5ChatMessage.id == result["message_id"]).one()
    command = json.loads(message.content.split(h5_personal_settings._H5_CLIENT_COMMAND_PREFIX, 1)[1])
    assert command["action"] == "generate_memory_documents_from_upload"
    assert [item["role"] for item in command["sources"]] == ["source", "reference"]
    assert command["doc_types"] == ["brand_product_intro"]


def test_memory_generation_upload_cleans_tos_when_dispatch_persistence_fails(
    db_session,
    test_user,
    monkeypatch,
):
    from backend.app.api import h5_personal_settings
    from backend.app.models import Asset, H5ChatDevicePresence, H5ChatMessage

    installation_id = "online-memory-rollback"
    db_session.add(
        H5ChatDevicePresence(
            user_id=test_user.id,
            installation_id=installation_id,
            display_name="Online",
            account_payload={"capabilities": ["memory_document_generate_v1"]},
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    deleted = []

    async def fake_io(func, *args):
        if func is h5_personal_settings._save_bytes_or_tos:
            data, ext, _content_type = args
            return "rollback-source", f"assets/rollback-source{ext}", len(data), "https://cdn.example.com/source.pdf"
        if func is h5_personal_settings._delete_tos_object:
            deleted.append(args[0])
            return True
        pytest.fail(f"unexpected IO function: {func}")

    real_commit = db_session.commit
    commit_calls = 0

    def fail_dispatch_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("database unavailable")
        return real_commit()

    monkeypatch.setattr(h5_personal_settings, "_run_asset_upload_io", fake_io)
    monkeypatch.setattr(db_session, "commit", fail_dispatch_commit)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            h5_personal_settings._queue_online_memory_generation(
                db=db_session,
                owner_user_id=test_user.id,
                installation_id=installation_id,
                files=[(UploadFile(filename="intro.pdf", file=io.BytesIO(b"pdf-content")), "source")],
                direct_text="",
                doc_types=["brand_product_intro"],
                reference_doc_ids="",
            )
        )

    assert deleted == ["assets/rollback-source.pdf"]
    assert db_session.query(Asset).filter(Asset.asset_id == "rollback-source").first() is None
    assert db_session.query(H5ChatMessage).filter(H5ChatMessage.installation_id == installation_id).first() is None


def test_online_memory_generation_callback_releases_db_and_is_idempotent(db_session, test_user, monkeypatch):
    import json

    from backend.app.api import h5_personal_settings
    from backend.app.models import Asset, H5ChatEvent, H5ChatMessage

    installation_id = "online-memory-generation-callback"
    message_id = "memory-generation-message"
    sources = [
        {
            "source_asset_id": "generation-source-pdf",
            "source_filename": "intro.pdf",
            "role": "source",
        },
        {
            "source_asset_id": "generation-reference-pptx",
            "source_filename": "layout.pptx",
            "role": "reference",
        },
    ]
    for item in sources:
        db_session.add(
            Asset(
                asset_id=item["source_asset_id"],
                user_id=test_user.id,
                filename=f"assets/{item['source_filename']}",
                media_type="document",
                file_size=10,
                source_url=f"https://cdn.example.com/{item['source_filename']}",
                meta={"online_memory_parse_source": True, "content_visibility": "intermediate"},
            )
        )
    command = {
        "action": "generate_memory_documents_from_upload",
        "sources": sources,
        "direct_text": "补充说明",
        "doc_types": ["brand_product_intro"],
        "reference_doc_ids": [],
    }
    db_session.add(
        H5ChatMessage(
            id=message_id,
            user_id=test_user.id,
            installation_id=installation_id,
            mode="client_command",
            content=h5_personal_settings._H5_CLIENT_COMMAND_PREFIX + json.dumps(command),
            status="processing",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    llm_calls = []

    async def fake_llm(_request, received_installation_id, messages, **_kwargs):
        assert received_installation_id == installation_id
        assert not db_session.in_transaction()
        llm_calls.append(messages)
        return "<<<brand_product_intro>>>\n整理后的企业资料"

    monkeypatch.setattr(h5_personal_settings, "_call_llm", fake_llm)
    body = h5_personal_settings.OnlineMemoryGenerateCompleteBody(
        message_id=message_id,
        sources=[
            {
                "source_asset_id": "generation-source-pdf",
                "filename": "intro.pdf",
                "content_text": "企业原始资料",
                "sha256": "a" * 64,
            },
            {
                "source_asset_id": "generation-reference-pptx",
                "filename": "layout.pptx",
                "content_text": "参考排版结构",
                "sha256": "b" * 64,
            },
        ],
    )

    first = asyncio.run(
        h5_personal_settings.complete_online_memory_generation(
            body,
            _request_with_installation(installation_id),
            current_user=test_user,
            db=db_session,
        )
    )
    second = asyncio.run(
        h5_personal_settings.complete_online_memory_generation(
            body,
            _request_with_installation(installation_id),
            current_user=test_user,
            db=db_session,
        )
    )

    assert first["documents"] == second["documents"] == {"brand_product_intro": "整理后的企业资料"}
    assert len(llm_calls) == 1
    assert (
        db_session.query(H5ChatEvent)
        .filter(H5ChatEvent.message_id == message_id, H5ChatEvent.event_type == "memory_generation_ready")
        .count()
        == 1
    )


def test_split_video_upload_requires_online_device(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", lambda *_args: pytest.fail("must not upload before device check"))
    upload = UploadFile(filename="demo.mp4", file=io.BytesIO(b"video-bytes"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            assets.upload_asset(
                file=upload,
                split_video=True,
                current_user=test_user,
                db=db_session,
            )
        )

    assert exc_info.value.status_code == 409
    assert "启动并登录 Online" in str(exc_info.value.detail)


def test_split_video_upload_rejects_online_without_capability(db_session, test_user, monkeypatch):
    from backend.app.api import assets
    from backend.app.models import H5ChatDevicePresence

    db_session.add(
        H5ChatDevicePresence(
            user_id=test_user.id,
            installation_id="old-online-device",
            display_name="Old Online",
            account_payload={"capabilities": []},
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()
    monkeypatch.setattr(assets, "_save_upload_file_or_tos", lambda *_args: pytest.fail("must not upload before capability check"))
    upload = UploadFile(filename="demo.mp4", file=io.BytesIO(b"video-bytes"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            assets.upload_asset(
                file=upload,
                split_video=True,
                current_user=test_user,
                db=db_session,
            )
        )

    assert exc_info.value.status_code == 409
    assert "升级最新 OTA" in str(exc_info.value.detail)


def test_online_split_segment_upload_is_idempotent(db_session, test_user, monkeypatch):
    from backend.app.api import assets

    save_calls = []

    def fake_save(file_obj, _ext, _content_type):
        file_obj.seek(0)
        data = file_obj.read()
        save_calls.append(data)
        return "segment-asset", "assets/segment-asset.mp4", len(data), "https://cdn.example.com/segment-asset.mp4"

    monkeypatch.setattr(assets, "_save_upload_file_or_tos", fake_save)

    async def upload_once():
        return await assets.upload_asset(
            file=UploadFile(filename="segment_001.mp4", file=io.BytesIO(b"segment-bytes")),
            split_video=False,
            source_upload_filename="source.mp4",
            video_segment=True,
            segment_index=1,
            split_job_id="split-job-id",
            current_user=test_user,
            db=db_session,
        )

    first = asyncio.run(upload_once())
    second = asyncio.run(upload_once())

    assert first["asset_id"] == second["asset_id"] == "segment-asset"
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert save_calls == [b"segment-bytes"]


def test_h5_composer_limits_parallel_asset_uploads():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    upload_many = script.split("async function uploadComposerFiles(files)", 1)[1].split(
        "function hasUploadingImages", 1
    )[0]

    assert "index += 2" in upload_many
    assert "queue.slice(index, index + 2)" in upload_many


def test_large_cutcli_and_recorder_writes_are_offloaded_from_event_loop():
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    cutcli_source = (api_dir / "cutcli_templates.py").read_text(encoding="utf-8")
    recorder_source = (api_dir / "h5_recorder.py").read_text(encoding="utf-8")

    resolve_source = cutcli_source.split("async def _resolve_source_video(", 1)[1].split("\ndef ", 1)[0]
    upload_source = recorder_source.split("async def upload_recording(", 1)[1].split("\n@router", 1)[0]
    transcribe_source = recorder_source.split("async def transcribe_memory_audio_file(", 1)[1].split("\n@router", 1)[0]

    assert "await asyncio.to_thread(output.write, chunk)" in resolve_source
    assert "_CUTCLI_UPLOAD_MAX_BYTES" in resolve_source
    assert "await asyncio.to_thread(out.write, chunk)" in upload_source
    assert "await asyncio.to_thread(shutil.rmtree, target_dir" in upload_source
    assert "await asyncio.to_thread(target.write_bytes, audio_data)" in transcribe_source


def test_large_upload_entrypoints_use_bounded_streaming_paths():
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
    assets_source = (api_dir / "assets.py").read_text(encoding="utf-8")
    hifly_source = (api_dir / "hifly_assets.py").read_text(encoding="utf-8")
    comfly_source = (api_dir / "comfly_proxy.py").read_text(encoding="utf-8")

    asset_upload = assets_source.split("async def upload_asset(", 1)[1].split("\n# ", 1)[0]
    hifly_upload = hifly_source.split("async def _upload_file_to_hifly(", 1)[1].split("\ndef ", 1)[0]
    comfly_file_proxy = comfly_source.split("async def proxy_files_upload(", 1)[1].split("\n@router", 1)[0]

    assert "await file.read()" not in asset_upload
    assert "_save_upload_file_or_tos" in asset_upload
    assert "_HIFLY_IN_MEMORY_UPLOAD_BYTES" in hifly_upload
    assert "_put_upload_to_url" in hifly_upload
    assert "await value.read()" not in comfly_file_proxy
    assert "value.file" in comfly_file_proxy


def test_h5_memory_generation_waits_for_online_review_payload():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    wait_online = script.split("async function waitForOnlineMemoryGeneration(messageId)", 1)[1].split(
        "async function generatePersonalMemoryDocs", 1
    )[0]
    generate = script.split("async function generatePersonalMemoryDocs(btn)", 1)[1].split(
        "async function savePersonalGeneratedDocuments", 1
    )[0]

    assert "payload.documents" in wait_online
    assert '["failed", "cancelled"]' in wait_online
    assert 'message.status === "completed"' in wait_online
    assert "waitForOnlineMemoryGeneration(data.message_id)" in generate
    assert "state.personalGeneratedDocuments = data.documents || {}" in generate


def test_h5_asset_video_split_is_monitored_as_online_task():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    upload_library = script.split("async function monitorOnlineVideoSplit(messageId)", 1)[1].split(
        "function closeAssetUploadModal", 1
    )[0]

    assert 'data.processing === "online"' in upload_library
    assert "monitorOnlineVideoSplit(data.message_id)" in upload_library
    assert 'loadAssetLibrary("user_upload", { force: true })' in upload_library


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
