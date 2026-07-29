from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_ai_marketing_home_and_group_navigation_are_present():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    for key in (
        "hifly.video.create_by_tts",
        "marketing_video_group",
        "image_composer_studio",
        "marketing_copy_group",
    ):
        assert f'data-marketing-ability="{key}"' in html
        assert f'"{key}"' in script

    assert "renderMarketingCreationEntries()" in script
    assert 'openAbilityView(parent.key, AI_MARKETING_CREATION_ID)' in script


def test_ai_marketing_cover_assets_are_bundled_and_mapped():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    covers = (
        "marketing-cover-creative-video.png",
        "marketing-cover-local-bestseller.png",
        "marketing-cover-tvc.png",
        "marketing-cover-storyboard.png",
        "marketing-cover-remix.png",
        "marketing-cover-ip-daily.png",
        "marketing-cover-wechat-article.png",
    )

    for name in covers:
        path = H5 / name
        assert path.is_file()
        assert path.stat().st_size > 100_000
        assert f'/h5-static/{name}' in script


def test_ai_marketing_design_styles_are_versioned():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert "20260729-designer-pages-v1" in html
    assert ".home-marketing-grid" in css
    assert ".marketing-creation-grid" in css
    assert ".marketing-category-mode" in css
    assert ".marketing-tool-mode" in css
    assert "aspect-ratio: 1400 / 682" in css


def test_home_marketing_uses_the_designer_background_asset():
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")
    background = H5 / "designer-ai-marketing-bg.png"

    assert background.is_file()
    assert background.stat().st_size > 1_000_000
    assert 'url("/h5-static/designer-ai-marketing-bg.png")' in css


def test_work_history_uses_twenty_item_infinite_loading():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="workListLoadState"' in html
    assert "workListPageSize: 20" in script
    assert "function loadMoreWorkList()" in script
    assert "function setupWorkListInfiniteScroll()" in script
    assert "new IntersectionObserver" in script
