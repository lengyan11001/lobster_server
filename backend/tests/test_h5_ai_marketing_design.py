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


def test_ai_marketing_ability_pages_use_virtual_department_labels():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "function displayDepartmentForAbility(lookup)" in script
    assert "if (isMarketingCreationMode()) return AI_MARKETING_CREATION_DEPARTMENT;" in script
    assert "return abilityLeafNodes(marketingCreationEntryNodes());" in script
    assert "const displayDepartment = marketingMode ? AI_MARKETING_CREATION_DEPARTMENT : department;" in script
    assert "const labels = [displayDepartment.name, ...trail.map((item) => item.label || item.key)];" in script
    assert '$("abilityKicker").textContent = displayDepartment.name || "ABILITY";' in script
    assert "eachAbilityNode(marketingCreationEntryNodes(), department, []" in script


def test_ai_marketing_visible_nodes_are_aligned_with_online_entries():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    visible_block = script.split("const AI_MARKETING_VISIBLE_NODE_KEYS = new Set([", 1)[1].split("]);", 1)[0]

    for key in (
        "hifly.video.create_by_tts",
        "marketing_video_group",
        "comfly.seedance.tvc.pipeline",
        "local_bestseller",
        "image_composer_studio",
        "marketing_copy_group",
        "ip_content_daily",
        "wewrite.article.pipeline",
    ):
        assert f'"{key}"' in visible_block

    for hidden_key in (
        "goal.video.pipeline",
        "comfly.daihuo.pipeline",
        "viral_video_remix",
        "create.video.pipeline",
        "ppt.create",
        "comfly.ecommerce.detail_pipeline",
    ):
        assert f'"{hidden_key}"' not in visible_block

    assert "function marketingCreationVisibleNode(node)" in script
    assert "(node.children || []).map(marketingCreationVisibleNode).filter(Boolean)" in script
    assert "const visibleChildCount = childNodes.filter((child) => !isPublishCenterNode(child)).length;" in script
    assert 'abilityShell.classList.toggle("marketing-category-mode", marketingMode && visibleChildCount > 0);' in script


def test_h5_home_ai_marketing_entries_do_not_expose_removed_items():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    home_block = html.split('class="office-section marketing-creation-section"', 1)[1].split("</section>", 1)[0]
    quick_block = script.split("const WORK_QUICK_ITEMS = [", 1)[1].split("const DEPARTMENT_SKILL_TREE", 1)[0]

    for key in (
        "hifly.video.create_by_tts",
        "marketing_video_group",
        "image_composer_studio",
        "marketing_copy_group",
    ):
        assert f'data-marketing-ability="{key}"' in home_block

    for hidden_label in ("创意视频", "爆款TVC", "爆款复刻", "多段视频混剪", "PPT制作", "电商详情页"):
        assert hidden_label not in home_block

    for removed_key in ("creative_general", "ai_shop_diagnosis", "ai_product_selection"):
        assert f'key: "{removed_key}"' not in quick_block

    for hidden_key in ("comfly.daihuo.pipeline", "viral_video_remix", "publish_center"):
        item_block = quick_block.split(f'key: "{hidden_key}"', 1)[1].split("},", 1)[0]
        assert "hidden: true" in item_block


def test_public_homepage_uses_new_ai_structure_labels():
    homepage = (ROOT / "backend" / "app" / "api" / "homepage.py").read_text(encoding="utf-8")

    for legacy in ("市场部", "销售部", "客服部", "运营部"):
        assert legacy not in homepage

    for label in ("AI营销创作", "AI获客", "私域销管", "AI执行台"):
        assert label in homepage


def test_h5_visible_work_structure_uses_new_ai_categories():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    html = (H5 / "index.html").read_text(encoding="utf-8")
    tree_block = script.split("const DEPARTMENT_SKILL_TREE = [", 1)[1].split("const IP_DAILY_TASK_OPTIONS", 1)[0]
    quick_block = script.split("const TASK_DEPARTMENTS = ", 1)[1].split("const SCHEDULED_TASK_CAPABILITY_IDS", 1)[0]

    for label in ("AI营销创作", "AI获客", "私域销管", "AI海外平台"):
        assert label in tree_block

    for legacy in ('name: "市场部"', 'name: "销售部"', 'name: "运营部"', 'name: "客服部"'):
        assert legacy not in tree_block

    assert '"AI获客"' in quick_block
    assert '"私域销管"' in quick_block
    assert '"销售部"' not in quick_block
    assert "function departmentEntryNodes(department)" in script
    assert "entries.map(abilityCardHtml)" in script
    assert "这个类目暂时没有配置能力" in script
    assert "能力类目排行" in html
    assert "部门排行" not in html


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


def test_h5_goal_video_exposes_duration_and_submits_it():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    html = (H5 / "index.html").read_text(encoding="utf-8")

    assert 'function goalVideoDurationSelectHtml(id)' in script
    assert 'optionHtml(String(n), `${n} 秒`)' in script
    assert 'goalVideoDurationSelectHtml("workflowParamVideoDuration")' in script
    assert 'goalVideoDurationSelectHtml("abilityVideoDuration")' in script
    assert 'goalVideoDurationSelectHtml("taskVideoDuration")' in script
    assert 'durationId: "workflowParamVideoDuration"' in script
    assert 'durationId: "abilityVideoDuration"' in script
    assert 'durationId: "taskVideoDuration"' in script
    assert "payload.duration = duration;" in script
    assert "payload.duration_seconds = duration;" in script
    assert 'setFieldValue("abilityVideoDuration", inner.duration || inner.duration_seconds || 10)' in script
    assert 'setFieldValue("workflowParamVideoDuration", inner.duration || inner.duration_seconds || 10)' in script
    assert "20260809-goal-video-duration-v1" in html


def test_h5_storyboard_video_uses_online_workbench_payload_contract():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    payload_builder = script.split("function seedancePayloadFromFields", 1)[1].split("function seedanceUiModelFromPayload", 1)[0]

    for prefix in ("workflowParamSeedance", "workSeedance", "taskSeedance"):
        assert f'seedanceFieldsHtml("{prefix}"' in script
        assert f'seedancePayloadFromFields("{prefix}", {{ requireAsset: false }})' in script
        assert f'bindSeedanceControls("{prefix}")' in script

    for field in (
        "InputMode",
        "ReferencePurpose",
        "Model",
        "Duration",
        "Aspect",
        "VisualTone",
        "Rhythm",
        "NeedMerge",
        "NeedAudio",
    ):
        assert f"${{prefix}}{field}" in script

    for required_payload_key in (
        "total_duration_seconds",
        "segment_count",
        "segment_duration_seconds",
        "workflow_mode",
        "merge_clips",
        "auto_save",
        "image_model_fallback",
        "video_model",
        "video_channel",
        "video_fallbacks",
        "aspect_ratio",
        "visual_tone",
        "rhythm",
        "reference_purposes",
        "generate_audio",
        "watermark",
        "task_text",
    ):
        assert required_payload_key in payload_builder

    assert 'return { model: "grok-imagine-video-1.5-preview", channel: "openmind" };' in script
    assert 'return { model: "veo3.1", channel: "yunwu" };' in script
    assert 'seedanceIsYunwuVeoModel(model) ? 8 : 10' in script
    assert 'image_model_fallback: "gpt-image-2-yunwu"' in payload_builder
    assert 'video_fallbacks: seedanceIsYunwuVeoModel(model) ? [{ channel: "comfly", model: "veo3.1-fast" }] : []' in payload_builder
    assert 'workflow_mode: useDirectVideo ? "direct_video" : "storyboard"' in payload_builder
    assert 'seedanceNormalizedDurationForModel(model, requestedDuration)' in payload_builder


def test_h5_scheduled_capabilities_default_to_online_commands():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")

    assert "const SERVER_SIDE_SCHEDULED_TASK_KINDS = new Set([" in script
    assert "function scheduledTaskKindForAbility(abilityKey)" in script
    assert "if (meta.serverTask) return String(meta.taskKind || key).trim();" in script
    assert 'return "capability";' in script
    assert "function scheduledTaskPayloadForAbility(abilityKey, capPayload)" in script
    assert "return { capability_id: key, payload: capPayload || {} };" in script
    assert "function buildCapabilityTaskPlan(options = {})" in script
    assert "payload: scheduledTaskPayloadForAbility(capabilityId, options.payload || {})" in script
    assert 'const taskKind = String((plan && (plan.taskKind || plan.task_kind)) || "client_workflow").trim() || "client_workflow";' in script
    assert "const serverSide = isServerSideScheduledKind(taskKind) || !!(plan && plan.serverSide);" in script
    assert 'task_kind: taskKind' in script
    assert "const taskPayload = scheduledTaskPayloadForAbility(state.taskAbility, capPayload)" in script
    assert 'payload: taskPayload' in script
    assert 'installation_ids: serverSide ? [] : [installationId]' in script
    assert "return submitScheduledClientTask(plan, { schedule_type: \"once\" });\n    }\n\n    async function submitWorkDispatch" in script
    assert "return submitOnceClientTask(buildCapabilityTaskPlan({" in script
    assert "payload: payload || {}," in script
    assert "payload: { capability_id" not in script
    assert 'taskKind: "capability"' not in script
    assert 'task_kind: "capability"' not in script
    assert 'state.taskAbility !== "ip_content_daily" && !selectedInstallationId' not in script
    assert 'state.taskAbility === "ip_content_daily" ? capPayload' not in script
    assert 'task_kind: isIpDaily ? "ip_content_daily" : "capability"' not in script
    assert 'plan.taskKind === "ip_content_daily"' not in script
    assert 'task.task_kind === "ip_content_daily"' not in script


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
