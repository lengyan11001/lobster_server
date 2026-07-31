from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
H5 = ROOT / "h5_static"


def test_content_library_cards_separate_preview_from_action_menu():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert 'class="asset-library-card designer-media-card content-action-card"' in script
    assert 'class="content-card-preview"' in script
    assert 'class="content-document-preview"' in script
    assert 'class="task-action-menu content-action-menu' in script
    assert ".content-action-card.task-menu-open" in styles
    assert ".content-action-menu .task-action-list" in styles


def test_content_actions_route_to_matching_workbench_with_prefill():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'add("generate_image", "生成图片")' in script
    assert 'add("generate_video", "生成视频")' in script
    assert 'add("generate_avatar", "生成数字人")' in script
    assert 'add("publish", "发布")' in script
    assert 'openContentActionAbility("image_composer_studio", "workImagePrompt"' in script
    assert 'openContentActionAbility("goal.video.pipeline", "abilityVideoPrompt"' in script
    assert 'openContentActionAbility("hifly.video.create_by_tts", "workHiflyScript"' in script
    assert 'openContentActionAbility("publish_center", "workPublishMaterial"' in script
    assert 'state.assetAvatarPrefillFile = file' in script


def test_work_record_results_reuse_content_actions():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "function runMediaContentActionMenu(entry, row, index)" in script
    assert 'contentActionMenuHtml(actionItem)' in script
    assert '<h4>内容操作</h4>' in script
    assert 'class="run-media-item content-action-host"' in script


def test_content_action_assets_are_cache_versioned():
    html = (H5 / "index.html").read_text(encoding="utf-8")

    assert html.count("20260731-content-actions-v1") == 2
