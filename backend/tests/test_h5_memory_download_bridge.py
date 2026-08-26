from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h5_memory_download_prefers_native_app_bridge():
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'window.LobsterAndroid.saveTextFile(filename, "text/markdown", text)' in script
    assert "const text = await resp.text();" in script
    assert "return downloadPersonalTextFile(fallbackName" in script
    assert "URL.createObjectURL(blob)" in script


def test_h5_memory_download_script_cache_key_is_current():
    index = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")

    assert "/h5-static/h5-app.js?v=20260826-memory-download-bridge-v1" in index
