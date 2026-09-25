"""闪剪素材超限兜底：能压就压，压不了就丢，不因为单个素材导致数字人不出片。"""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from backend.app.api import shanjian_digital_human as dh


def test_shanjian_target_dimensions_caps_longest_edge_and_keeps_even():
    assert dh._shanjian_target_dimensions(1080, 2400, 2000) == (900, 2000)
    width, height = dh._shanjian_target_dimensions(2048, 1365, 2000)
    assert max(width, height) == 2000
    assert width % 2 == 0 and height % 2 == 0
    assert dh._shanjian_target_dimensions(1918, 1038, 2000) == (1918, 1038)


def test_filter_materials_shrinks_oversized_material(monkeypatch):
    async def fake_download(_url, **_kwargs):
        return b"original-video", "video/mp4"

    def fake_shrink(**_kwargs):
        return b"shrunk-video"

    def fake_save(data, ext, content_type):
        assert data == b"shrunk-video"
        assert ext == ".mp4"
        return "asset-1", "asset-1.mp4", len(data), "https://tos.example/asset-1.mp4"

    monkeypatch.setattr(dh, "_probe_material_dimensions", lambda _url: (1080, 2400))
    monkeypatch.setattr(dh, "_download_media_bytes", fake_download)
    monkeypatch.setattr(dh, "_shrink_material_bytes", fake_shrink)
    monkeypatch.setattr(dh, "_save_bytes_or_tos", fake_save)

    kept, report = asyncio.run(
        dh._filter_materials_within_shanjian_limit(
            [{"type": "video", "fileUrl": "https://tos.example/big.mp4"}],
            label="测试素材",
        )
    )

    assert report["shrunk"] == 1
    assert report["dropped"] == []
    assert kept == [{"type": "video", "fileUrl": "https://tos.example/asset-1.mp4"}]


def test_filter_materials_drops_material_when_shrink_fails(monkeypatch):
    async def fake_download(_url, **_kwargs):
        raise HTTPException(status_code=400, detail="下载媒体失败")

    monkeypatch.setattr(dh, "_probe_material_dimensions", lambda _url: (2048, 1365))
    monkeypatch.setattr(dh, "_download_media_bytes", fake_download)

    kept, report = asyncio.run(
        dh._filter_materials_within_shanjian_limit(
            [{"type": "image", "fileUrl": "https://tos.example/big.jpg"}],
            label="测试素材",
        )
    )

    assert kept == []
    assert len(report["dropped"]) == 1
    assert report["dropped"][0]["reason"].startswith("shrink_failed")
    assert report["dropped"][0]["type"] == "image"


def test_filter_materials_keeps_within_limit_and_unknown_probe(monkeypatch):
    dimensions = {
        "https://tos.example/ok.mp4": (1918, 1038),
        "https://tos.example/unknown.mp4": None,
    }
    monkeypatch.setattr(dh, "_probe_material_dimensions", lambda url: dimensions.get(url))

    kept, report = asyncio.run(
        dh._filter_materials_within_shanjian_limit(
            [
                {"type": "video", "fileUrl": "https://tos.example/ok.mp4"},
                {"type": "video", "fileUrl": "https://tos.example/unknown.mp4"},
            ],
            label="测试素材",
        )
    )

    assert [item["fileUrl"] for item in kept] == [
        "https://tos.example/ok.mp4",
        "https://tos.example/unknown.mp4",
    ]
    assert report["dropped"] == []
    assert report["unknown"] == 1


def test_is_shanjian_material_rejection_detects_upstream_error():
    message = "视频素材分辨率不能超过2000x2000"
    data = {
        "taskId": "clip-1",
        "status": "failed",
        "errorCode": "InvalidFile.Resolution",
        "errorMessage": message,
    }
    assert dh._is_shanjian_material_rejection(message, data) is True
    assert dh._is_shanjian_material_rejection("", {"errorCode": "InvalidFile.Resolution"}) is True
    assert dh._is_shanjian_material_rejection("云渲染失败", {"errorCode": "Other"}) is False


def test_retry_clip_without_materials_strips_groups_and_materials(monkeypatch):
    captured = {}

    async def fake_submit(*, body, db, current_user, row, template_meta, base_result_payload):
        captured["template"] = template_meta
        captured["base"] = base_result_payload
        return {"clip_task_id": "clip-retry-1", "raw": {"ok": True}}

    class FakeDB:
        def add(self, _row):
            return None

        def commit(self):
            return None

        def refresh(self, _row):
            return None

    monkeypatch.setattr(dh, "_submit_realman_clip_task", fake_submit)
    monkeypatch.setattr(dh, "_video_task_to_dict", lambda _row: {"id": 1})

    row = SimpleNamespace(
        task_id="base-1",
        title="数字人口播",
        status="failed",
        error_message="视频素材分辨率不能超过2000x2000",
        updated_at=None,
        video_url="https://tos.example/base.mp4",
        submit_payload={
            "template": {
                "style_id": "style-1",
                "asset_groups": ["数字人口播素材"],
                "materials": [{"type": "video", "fileUrl": "https://tos.example/big.mp4"}],
                "clip_task_id": "clip-old",
            },
            "base_result": {"result": {"videoUrl": "https://tos.example/base.mp4"}},
        },
    )

    result = asyncio.run(
        dh._retry_clip_without_materials(
            body=SimpleNamespace(token="token-1"),
            db=FakeDB(),
            current_user=SimpleNamespace(id=54),
            row=row,
            submit_payload=row.submit_payload,
            error_message=row.error_message,
        )
    )

    assert result is not None
    assert result["ok"] is True
    assert result["clip_task_id"] == "clip-retry-1"
    assert captured["template"]["materials"] == []
    assert "asset_groups" not in captured["template"]
    assert "clip_task_id" not in captured["template"]
    assert captured["template"]["material_retry_dropped"] == 1
    assert row.submit_payload["material_retry_done"] is True
    assert row.error_message is None
    assert row.status == "processing"