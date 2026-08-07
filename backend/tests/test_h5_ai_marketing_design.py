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


def test_ai_marketing_digital_human_exposes_duration_and_template_controls():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert 'workSegmentedHtml("workHiflyDurationMode"' in script
    assert 'workSegmentedHtml("workHiflyTemplateMode"' in script
    assert 'id="workHiflyTargetDuration"' in script
    assert 'id="workHiflyTemplateField"' in script
    assert 'id="workHiflyTemplateSummary"' in script
    assert 'openPersonalDigitalHumanTemplatePicker("work")' in script
    assert 'data-preview-work-dh-template' in script
    assert 'long_video: longVideo' in script
    assert 'video_duration: videoDuration' in script
    assert 'duration_seconds: videoDuration' in script
    assert 'use_template: useTemplate' in script
    assert 'if (useTemplate && !workValue("workHiflyTemplate"))' in script
    assert ".work-segmented" in css
    assert ".work-hifly-template-selected" in css


def test_ai_marketing_design_uses_online_image_studio_workflow():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    for field in (
        "workImagePrompt",
        "workImageReference",
        "workImageReferencePurpose",
        "workImageAspectRatio",
        "workImageModel",
        "workImageQuality",
        "workImageBackground",
    ):
        assert field in script

    assert 'taskKind: "client_workflow"' in script
    assert 'payload: { action: "image_studio_generate", params: collectImageStudioParams("workImage") }' in script
    assert 'payload: { action: "image_studio_generate", params: collectImageStudioParams("workflowParamImage") }' in script
    assert 'workflowAction: "image_studio_generate"' in script
    assert 'reference_image_urls: referenceUrl ? [referenceUrl] : []' in script
    assert 'reference_purposes: referenceUrl ? [workflowParamValue(`${prefix}ReferencePurpose`) || "auto"] : []' in script
    assert 'if (action === "image_studio_generate") return "image_composer_studio";' in script
    assert 'payload: { capability_id: "goal.image.pipeline", payload: { prompt } }' not in script


def test_bottom_create_button_opens_compact_creation_sheet():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert 'id="creationQuickModal"' in html
    assert 'id="creationQuickGrid"' in html
    assert "function openCreationQuickSheet()" in script
    assert 'if (key === "aiMarketingCreation") {\n        openCreationQuickSheet();' in script
    assert "grid.innerHTML = renderMarketingCreationEntries()" in script
    assert 'openAbilityView(key, AI_MARKETING_CREATION_ID);' in script
    assert ".creation-quick-modal" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "20260730-create-sheet-v1" in html


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

    assert "20260729-linkedin-result-v1" in html
    assert ".home-marketing-grid" in css
    assert ".marketing-creation-grid" in css
    assert ".marketing-category-mode" in css
    assert ".marketing-tool-mode" in css
    assert "aspect-ratio: 1400 / 682" in css
    assert "grid-auto-rows: max-content" in css
    assert "height: fit-content" in css


def test_ai_marketing_back_skips_the_intermediate_landing_page():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    branch = script.split('if (activeId === "abilityView") {', 1)[1].split('if (activeId === "departmentView") {', 1)[0]
    assert 'lookup && lookup.trail.length > 1' in branch
    assert 'openAbilityView(parent.key, AI_MARKETING_CREATION_ID)' in branch
    assert 'switchTab("office")' in branch


def test_h5_typography_is_regular_except_for_primary_titles():
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")
    voice_preview = (H5 / "voice-preview.html").read_text(encoding="utf-8")

    assert "body * {\n  font-weight: 400 !important;\n}" in css
    assert ".section-title > h2," in css
    assert "font-weight: 600 !important;" in css
    assert "body * { font-weight: 400 !important; }" in voice_preview


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


def test_upload_dialogs_use_the_shared_overflow_safe_layout():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    for modal_id in (
        "assetUploadModal",
        "assetAvatarModal",
        "assetVoiceModal",
        "personalUploadModal",
    ):
        assert f'id="{modal_id}"' in html
        assert f'class="employee-modal upload-dialog hidden"' in html

    assert css.count(".upload-dialog") >= 4

    assert "Upload dialogs share one stable layout" in css
    assert 'input[type="file"]::file-selector-button' in css
    assert ".employee-modal .work-dispatch-popover .employee-popover-title strong" in css
    assert 'id="personalMemoryGenerateModal"' in html
    assert "grid-template-rows: minmax(0, 1fr) auto" in css
    assert "overflow-x: hidden" in css
    assert "20260807-upload-dialog-v3" in html
    assert ".upload-dialog > .work-dispatch-popover" in css
    assert "max-width: 100vw" in css
    assert ":is(#assetUploadModal" not in css


def test_content_and_memory_previews_use_separate_detail_views():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert 'id="contentRecordDetailView"' in html
    assert 'id="contentRecordDetailTabs"' in html
    assert 'id="personalMemoryDetailView"' in html
    assert 'id="personalMemoryPreview"' in html
    assert 'id="personalMemorySourceTabs"' in html
    assert 'function openContentRecordDetail' in script
    assert 'function previewPersonalMemory' in script
    assert 'function setPersonalMemorySourceSection' in script
    assert 'activeId === "contentRecordDetailView"' in script
    assert 'activeId === "personalMemoryDetailView"' in script
