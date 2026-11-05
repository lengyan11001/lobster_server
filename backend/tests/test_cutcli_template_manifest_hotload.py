import json
import os

import pytest

from backend.app.api import cutcli_templates as templates
from backend.app.models import CutcliTemplate


def _write(path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def manifest(tmp_path):
    original = templates._DESIGNER_MANIFEST
    original_local = templates._DESIGNER_MANIFEST_LOCAL
    path = tmp_path / "designer_templates.json"
    templates._DESIGNER_MANIFEST = path
    templates._DESIGNER_MANIFEST_LOCAL = path.with_name("designer_templates.local.json")
    try:
        yield path
    finally:
        templates._DESIGNER_MANIFEST = original
        templates._DESIGNER_MANIFEST_LOCAL = original_local
        templates._refresh_designer_manifest(force=True)


def _template(tid, name, preview="https://cdn/1.mp4", meta=None):
    return {
        "id": tid,
        "kind": "auto_caption",
        "name": name,
        "description": "desc",
        "aspect_ratio": "9:16",
        "tags": ["hot"],
        "overlay_fields": [{"key": "text_1", "default": "x"}],
        "caption_style": {
            "id": tid,
            "ass_layout": "designer_timeline",
            "overlay_style": {"layout": "designer_timeline", "text_tracks": []},
        },
        "meta": meta or {"style_version": "v1", "skip_stt": True},
        "preview_url": preview,
        "sample_video_url": preview,
    }


def test_manifest_reloads_on_file_change_without_restart(manifest):
    _write(manifest, [_template("designer_hot_01", "A")])
    templates._refresh_designer_manifest()
    assert templates._TEMPLATES["designer_hot_01"]["name"] == "A"

    _write(manifest, [_template("designer_hot_01", "B")])
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns + 5_000_000_000, stat.st_mtime_ns + 5_000_000_000))
    templates._refresh_designer_manifest()
    assert templates._TEMPLATES["designer_hot_01"]["name"] == "B"

    _write(manifest, [])
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns + 10_000_000_000, stat.st_mtime_ns + 10_000_000_000))
    templates._refresh_designer_manifest()
    assert "designer_hot_01" not in templates._TEMPLATES


def test_manifest_edits_sync_to_db_rows(manifest, db_session):
    _write(manifest, [_template("designer_hot_02", "old")])
    templates._refresh_designer_manifest(force=True)
    templates._ensure_cutcli_template_seeds(db_session)
    db_session.expire_all()
    row = db_session.get(CutcliTemplate, "designer_hot_02")
    assert row is not None and row.name == "old" and row.enabled is True

    # 同样 style_version，只改名字/封面，也要跟着服务器清单走
    _write(manifest, [_template("designer_hot_02", "new", preview="https://cdn/2.mp4")])
    templates._refresh_designer_manifest(force=True)
    templates._ensure_cutcli_template_seeds(db_session)
    db_session.expire_all()
    row = db_session.get(CutcliTemplate, "designer_hot_02")
    assert row.name == "new"
    assert row.preview_url == "https://cdn/2.mp4"

    # 从清单移除即下架，无需改代码
    _write(manifest, [])
    templates._refresh_designer_manifest(force=True)
    templates._ensure_cutcli_template_seeds(db_session)
    db_session.expire_all()
    row = db_session.get(CutcliTemplate, "designer_hot_02")
    assert row.enabled is False


def test_server_local_overlay_adds_and_overrides(manifest):
    _write(manifest, [_template("designer_hot_03", "base")])
    overlay = manifest.with_name("designer_templates.local.json")
    _write(overlay, [_template("designer_hot_03", "overridden"), _template("designer_hot_04", "extra")])
    templates._refresh_designer_manifest(force=True)
    assert templates._TEMPLATES["designer_hot_03"]["name"] == "overridden"
    assert templates._TEMPLATES["designer_hot_04"]["name"] == "extra"
