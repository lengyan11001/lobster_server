from __future__ import annotations

import asyncio
import io
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from backend.app.api import h5_personal_settings
from backend.app.api.h5_personal_settings import _collect_sources
from backend.app.api.openclaw_memory_cloud import (
    _decode_text_payload,
    _extract_docx_text,
    _extract_pdf_text,
    _extract_pptx_text,
    _extract_xls_text,
)


ROOT = Path(__file__).resolve().parents[2]


def _zip_payload(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_docx_extracts_body_headers_and_notes() -> None:
    payload = _zip_payload(
        {
            "word/document.xml": '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:document>',
            "word/header1.xml": '<w:hdr xmlns:w="urn:w"><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>',
            "word/footnotes.xml": '<w:footnotes xmlns:w="urn:w"><w:p><w:r><w:t>脚注</w:t></w:r></w:p></w:footnotes>',
        }
    )

    text = _extract_docx_text(payload)

    assert "正文" in text
    assert "页眉" in text
    assert "脚注" in text


def test_pptx_uses_presentation_order_and_extracts_related_text() -> None:
    payload = _zip_payload(
        {
            "ppt/presentation.xml": """<p:presentation xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId10"/><p:sldId id="2" r:id="rId1"/></p:sldIdLst></p:presentation>""",
            "ppt/_rels/presentation.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide10.xml"/></Relationships>""",
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:p><a:t>最后一页</a:t></a:p></p:sld>',
            "ppt/slides/slide10.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:p><a:t>第一页正文</a:t></a:p></p:sld>',
            "ppt/slides/_rels/slide10.xml.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/></Relationships>""",
            "ppt/notesSlides/notesSlide1.xml": '<p:notes xmlns:p="urn:p" xmlns:a="urn:a"><a:p><a:t>讲者备注</a:t></a:p></p:notes>',
            "ppt/charts/chart1.xml": '<c:chart xmlns:c="urn:c" xmlns:a="urn:a"><a:p><a:t>销售趋势</a:t></a:p></c:chart>',
        }
    )

    text = _extract_pptx_text(payload)

    assert text.index("第一页正文") < text.index("最后一页")
    assert "## 第 1 页" in text
    assert "讲者备注" in text
    assert "销售趋势" in text


def test_pdf_and_legacy_xls_use_declared_runtime_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "PDF 内容"

    class FakeReader:
        pages = [FakePage()]

    class FakeSheet:
        name = "客户"
        nrows = 1
        ncols = 2

        @staticmethod
        def cell_value(row: int, column: int) -> str:
            return ("姓名", "张三")[column]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda _stream: FakeReader()))
    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        SimpleNamespace(open_workbook=lambda **_kwargs: SimpleNamespace(sheets=lambda: [FakeSheet()])),
    )

    assert "PDF 内容" in _extract_pdf_text(b"pdf")
    assert "张三" in _extract_xls_text(b"xls")


def test_legacy_doc_is_not_advertised_as_supported() -> None:
    with pytest.raises(HTTPException) as exc:
        _decode_text_payload(b"legacy", "legacy.doc")

    assert "Word(docx)" in str(exc.value.detail)
    assert ".doc 暂不支持" not in str(exc.value.detail)


def test_multi_file_collection_keeps_successful_files() -> None:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    files = [
        UploadFile(file=io.BytesIO(b"legacy"), filename="bad.doc"),
        UploadFile(file=io.BytesIO("有效资料".encode()), filename="good.txt"),
    ]
    results: list[dict[str, str]] = []

    source_text, visual_blocks, source_images = asyncio.run(
        _collect_sources(
            request,
            "test-installation",
            files,
            "",
            "",
            db=None,  # type: ignore[arg-type]
            stt_user=None,  # type: ignore[arg-type]
            file_results=results,
        )
    )

    assert "有效资料" in source_text
    assert not visual_blocks
    assert not source_images
    assert results == [
        {"filename": "bad.doc", "status": "skipped", "error": ANY},
        {"filename": "good.txt", "status": "processed", "error": ""},
    ]


def test_audio_collection_retains_reusable_audio_source(monkeypatch: pytest.MonkeyPatch) -> None:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    upload = UploadFile(file=io.BytesIO(b"audio"), filename="访谈.mp3")
    retained: list[dict[str, object]] = []
    monkeypatch.setattr(
        h5_personal_settings,
        "_transcribe_audio_attachment",
        lambda **_kwargs: ("客户希望下周交付", "https://example.test/retained.wav"),
    )

    source_text, _visual_blocks, _source_images = asyncio.run(
        _collect_sources(
            request,
            "test-installation",
            [upload],
            "",
            "",
            db=None,  # type: ignore[arg-type]
            stt_user=None,  # type: ignore[arg-type]
            audio_sources=retained,
        )
    )

    assert "客户希望下周交付" in source_text
    assert retained == [{
        "filename": "访谈.mp3",
        "source_url": "https://example.test/retained.wav",
        "file_size": 5,
    }]


def test_runtime_and_h5_formats_match_actual_support() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    preflight = (ROOT / "scripts" / "deploy_preflight.py").read_text(encoding="utf-8")
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert "pypdf" in requirements
    assert "xlrd" in requirements
    assert "import pypdf" in preflight
    assert "import xlrd" in preflight
    for input_id in ("personalMemoryFiles", "personalCustomReferenceFile"):
        match = re.search(rf'<input id="{input_id}"[^>]*accept="([^"]+)"', html)
        assert match is not None
        accepted = {item.strip() for item in match.group(1).split(",")}
        assert ".pptx" in accepted
        assert ".docx" in accepted
        assert ".doc" not in accepted
        assert ".ppt" not in accepted
