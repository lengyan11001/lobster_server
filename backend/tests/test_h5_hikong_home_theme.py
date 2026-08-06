from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_hikong_home_uses_douyin_palette_without_global_brand_overrides():
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")
    html = (H5 / "index.html").read_text(encoding="utf-8")

    marker = "/* Hikong (OEM 0400) homepage: align with the Douyin lead-generation palette. */"
    theme = css.split(marker, 1)[1]

    assert 'html[data-brand="hikong"] #officeView' in theme
    assert theme.count('html[data-brand="hikong"] #officeView') >= 20
    assert "#0b1a3a" in theme
    assert "#1a3fa3" in theme
    assert "#13b7d8" in theme
    assert 'html[data-brand="hikong"] body' not in theme
    assert 'html[data-brand="hikong"] .shell' not in theme
    assert "20260806-hikong-douyin-home-v1" in html
