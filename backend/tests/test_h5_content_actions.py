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
    assert 'if ($("assetAvatarVersion")) $("assetAvatarVersion").value = "v1"' in script
    assert 'if ($("assetAvatarSourceType")) $("assetAvatarSourceType").value = mediaType' in script
    assert 'selectAssetPickerRow("abilityVideoAsset", {' in script
    assert 'asset_origin: "generated"' in script
    assert "ensureContentImageInAssetPicker" not in script
    assert 'renderAssetPickerControl("abilityVideoAsset")' in script
    assert 'setFieldValue("workImagePrompt", creativePrompt)' in script
    assert 'setFieldValue("abilityVideoPrompt", creativePrompt)' in script
    assert 'setFieldValue("workHiflyScript", script)' in script
    assert 'setFieldValue("workPublishDescription", text)' in script
    assert 'setFieldValue("workPublishTags", tags)' in script
    assert 'if (String(source.mediaType || "").trim().toLowerCase() === "image")' in script
    assert 'return String(source.url || source.assetId || "").trim()' in script


def test_content_action_fields_keep_prompt_copy_and_script_separate():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "function contentActionTextValue(...values)" in script
    assert "function contentActionCreativePromptValue(...values)" in script
    assert "mediaReferencePattern.test(text)" in script
    assert "mediaFilenamePattern.test(text)" in script
    assert "const explicitCreativePrompt = contentActionCreativePromptValue(" in script
    assert "item.image_prompt," in script
    assert "item.video_prompt," in script
    assert "item.original_prompt," in script
    assert "script: contentActionTextValue(" in script
    assert "tags: contentActionTextValue(item.tags, item.hashtags, meta.tags, meta.hashtags)" in script
    assert 'if (mediaType === "video") add("generate_avatar", "生成数字人")' in script
    assert 'await openContentMediaAsAvatar(item)' in script
    assert '&& !source.url)' in script
    assert "function contentActionMediaUrl(value)" in script
    assert 'return apiUrl(raw.startsWith("/") ? raw : `/${raw}`)' in script


def test_work_record_results_reuse_content_actions():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "function runMediaContentActionMenu(entry, row, index)" in script
    assert 'contentActionMenuHtml(actionItem)' in script
    assert '<h4>内容操作</h4>' in script
    assert 'class="run-media-item content-action-host"' in script
    assert "creativePrompt: contentActionTextValue(" in script
    assert "entry.image_prompt," in script
    assert "entry.video_prompt," in script
    assert "const explicitScript = contentActionTextValue(entry.script, entry.voiceover_script)" in script
    assert "script: explicitScript || defaults.script" in script
    assert "const seen = new Map()" in script
    assert "if (explicitCreativePrompt) existing.creativePrompt = explicitCreativePrompt" in script
    assert "&& !source.url)" in script


def test_content_action_assets_are_cache_versioned():
    html = (H5 / "index.html").read_text(encoding="utf-8")

    assert html.count("20260731-content-actions-v6") == 2
    assert html.count("20260731-content-picker-v1") == 3


def test_document_cards_only_use_real_images_and_render_article_images():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert "function contentRecordImageUrls(asset)" in script
    assert "function contentRecordArticleBodyHtml(content, imageUrls = [])" in script
    assert 'contentRecordImageUrls(asset)[0] || ""' in script
    assert 'class="content-document-cover-empty' in script
    assert "designerFallbackMedia({ ...(asset || {}), origin: \"generated\"" not in script
    assert ".content-record-inline-image img" in styles
    assert "top: 50%;" in styles
    assert "data-content-document-cover" in script
    assert 'image.matches("[data-content-document-cover], [data-asset-picker-library-image]")' in script


def test_asset_picker_uses_library_modal_instead_of_native_dropdown():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    base_styles = (H5 / "h5-app.css").read_text(encoding="utf-8")
    designer_styles = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert 'id="assetPickerLibraryModal"' in html
    assert 'data-asset-picker-source="user_upload"' in html
    assert 'data-asset-picker-source="generated"' in html
    assert 'data-asset-picker-open="${escapeHtml(id)}"' in script
    assert "data-asset-select" not in script
    assert "function openAssetPickerModal(id)" in script
    assert "function loadAssetPickerModalRows()" in script
    assert 'origin: source' in script
    assert "state.assetPickerSelections[id] = item" in script
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in base_styles
    assert ".asset-picker-library-grid" in designer_styles
    assert ".asset-picker-library-card.selected" in designer_styles


def test_media_paths_are_not_used_as_creative_prompts():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "contentActionCreativePromptValue(item.prompt, meta.prompt)" in script
    assert "source.creativePrompt = contentActionCreativePromptValue(" in script
    assert 'const creativePrompt = contentActionCreativePromptValue(item.creativePrompt)' in script
    assert '|| (textBased ? contentActionTextValue(text, title) : "")' in script
