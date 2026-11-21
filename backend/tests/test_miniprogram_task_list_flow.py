from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "miniprogram" / "pages"


def _text(*parts: str) -> str:
    return (PAGES.joinpath(*parts)).read_text(encoding="utf-8")


def test_generation_pages_start_on_task_list_and_use_shared_backoff_poller():
    cases = [
        ("digital", "digital.js", "digital.wxml"),
        ("image-generate", "image-generate.js", "image-generate.wxml"),
        ("moments", "moments.js", "moments.wxml"),
        ("topic-recommend", "topic-recommend.js", "topic-recommend.wxml"),
    ]
    for folder, js_name, wxml_name in cases:
        js = _text(folder, js_name)
        wxml = _text(folder, wxml_name)
        assert 'viewMode: "list"' in js, folder
        assert "openCreate" in js and "backToList" in js, folder
        assert 'require("../../utils/task-poll")' in js, folder
        assert "createPoller(" in js, folder
        assert "viewMode === 'list'" in wxml, folder
        assert "openCreate" in wxml, folder


def test_article_list_turns_new_generation_into_a_processing_row():
    js = _text("article", "history.js")
    assert 'const api = require("../../utils/api")' in js
    assert 'status: "processing"' in js
    assert 'status: "completed"' in js
    assert "createPoller(" in js
    assert "modalOpen: false" in js
