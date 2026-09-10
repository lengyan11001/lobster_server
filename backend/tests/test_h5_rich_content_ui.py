"""AI 调度助手的富内容显示层：不同回复类型分别渲染。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_h5_rich_renderer_and_styles_ship_together():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    css = (ROOT / "h5_static" / "rich-content.css").read_text(encoding="utf-8")
    page = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    for token in (
        "function renderRichText",
        "function richUrlKind",
        "function richMediaGroup",
        "function richLinkCard",
        "function richImageGrid",
        "richLightbox",
    ):
        assert token in script, token

    for token in (
        ".rich-media-grid",
        ".rich-media-block",
        ".rich-link-card",
        ".rich-file-row",
        ".rich-lightbox",
        ".rich-code",
    ):
        assert token in css, token

    assert "rich-content.css" in page


def test_h5_bubble_text_uses_rich_renderer():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = script.index("function renderBubbleText(bubble)")
    body = script[start : start + 320]

    assert "renderRichText(" in body
