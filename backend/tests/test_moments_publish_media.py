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

def test_publish_ref_video_detection():
    """朋友圈图文兜底：视频素材（按声明或 URL 后缀）必须能被识别出来。"""
    assert content_records._publish_ref_looks_like_video({"kind": "video"}) is True
    assert content_records._publish_ref_looks_like_video(
        {"image_url": "https://x.example/a/b/c.mov"}
    ) is True
    assert content_records._publish_ref_looks_like_video(
        {"image_url": "https://x.example/a/b/c.mp4?sign=1"}
    ) is True
    assert content_records._publish_ref_looks_like_video(
        {"image_url": "https://x.example/a/b/c.jpg", "kind": "image"}
    ) is False

def test_generate_group_declares_nonlocal_publish_draft():
    """线上事故：generate_group 是嵌套函数，漏了 nonlocal 导致生成的 3 张图
    只留在 groups[0]，顶层 publish_draft 为空 → 发布时没有配图，退回用原始素材。"""
    source = Path(ip_content_studio.__file__).read_text(encoding="utf-8")
    assert "nonlocal workflow_publish_draft" in source
    assert "# 兜底：groups[0]" in source
