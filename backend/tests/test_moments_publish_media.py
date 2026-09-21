"""朋友圈发布素材：图文素材类型不硬编码，配图数量写实话。"""

from __future__ import annotations

from pathlib import Path

from backend.app.api import content_records, ip_content_studio


def test_note_is_honest_when_no_images_generated():
    note = ip_content_studio._moments_image_generation_note(
        workflow_node_execution=True, auto_moments=True, image_count=0, image_complete=False
    )
    assert "已自动生成" not in note
    assert "没有生成成功" in note


def test_note_reports_real_count_when_complete():
    note = ip_content_studio._moments_image_generation_note(
        workflow_node_execution=True, auto_moments=True, image_count=3, image_complete=True
    )
    assert "已自动生成 3 张配图" in note


def test_note_manual_branch_keeps_old_copy():
    note = ip_content_studio._moments_image_generation_note(
        workflow_node_execution=False, auto_moments=False, image_count=0, image_complete=False
    )
    assert "手动触发" in note


def test_moments_attachments_do_not_hardcode_image_type():
    """线上事故：把视频素材命名成 moments-N.jpg 且 kind=image，微信直接处理失败。"""
    source = Path(content_records.__file__).read_text(encoding="utf-8")
    assert '"kind": "image"' not in source
    assert "moments-{index + 1}.jpg" not in source
    assert "declared_kind" in source
