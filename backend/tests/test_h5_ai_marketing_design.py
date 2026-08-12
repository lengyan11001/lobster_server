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
    assert "const referenceValues = assetPickerSelectedValues(`${prefix}Reference`).slice(0, 8);" in script
    assert "reference_image_urls: referenceUrls" in script
    assert "reference_asset_ids: referenceAssetIds" in script
    assert "reference_purposes: referenceValues.map(() => referencePurpose)" in script
    assert 'if (action === "image_studio_generate") return "image_composer_studio";' in script
    assert 'payload: { capability_id: "goal.image.pipeline", payload: { prompt } }' not in script


def test_ai_marketing_advanced_settings_keep_defaults_and_persona_visible():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")

    assert 'return `<details class="task-advanced-settings field full">' in script
    assert 'hint = "已按 Online 默认值填充"' in script
    assert ".task-advanced-settings > summary" in css
    assert ".task-advanced-grid" in css

    image_fields = script.split("function imageStudioFieldsHtml", 1)[1].split("function collectImageStudioParams", 1)[0]
    assert image_fields.index('taskFieldHtml("比例"') < image_fields.index("taskAdvancedFieldsHtml(")
    assert image_fields.index('taskFieldHtml("模型"') > image_fields.index("taskAdvancedFieldsHtml(")

    local_fields = script.split("function localBestsellerFieldsHtml", 1)[1].split("function localBestsellerParamsFromFields", 1)[0]
    for visible_label in ("画面风格", "人物照片", "参考视频", "姓名", "短视频昵称", "身份人设", "行业/产品", "当前城市", "目标客户"):
        assert local_fields.index(f'taskFieldHtml("{visible_label}"') < local_fields.index("taskAdvancedFieldsHtml(")
    for advanced_label in ("图片模型", "图片质量", "视频模型"):
        assert local_fields.index(f'taskFieldHtml("{advanced_label}"') > local_fields.index("taskAdvancedFieldsHtml(")

    article_fields = script.split("function articleFieldsHtml", 1)[1].split("function articlePayloadFromFields", 1)[0]
    assert article_fields.index('taskFieldHtml("公众号主题"') < article_fields.index("taskAdvancedFieldsHtml(")
    assert article_fields.index('taskFieldHtml("目标读者"') < article_fields.index("taskAdvancedFieldsHtml(")
    for advanced_label in ("写作风格", "排版主题", "配图比例", "配图数量", "图片处理"):
        assert article_fields.index(f'taskFieldHtml("{advanced_label}"') > article_fields.index("taskAdvancedFieldsHtml(")

    storyboard_fields = script.split("function seedanceFieldsHtml", 1)[1].split("function syncSeedanceDurationOptions", 1)[0]
    for visible_label in ("输入方式", "参考图片", "视频需求", "视频时长", "画面比例"):
        assert storyboard_fields.index(f'taskFieldHtml("{visible_label}"') < storyboard_fields.index("taskAdvancedFieldsHtml(")
    for advanced_label in ("生成模型", "参考图用途", "视觉基调", "镜头节奏", "结果处理"):
        assert storyboard_fields.index(f'taskFieldHtml("{advanced_label}"') > storyboard_fields.index("taskAdvancedFieldsHtml(")

    workflow_digital_human = script.split("function workflowDigitalHumanFieldsHtml", 1)[1].split("function taskDigitalHumanFieldsHtml", 1)[0]
    task_digital_human = script.split("function taskDigitalHumanFieldsHtml", 1)[1].split("function localBestsellerFieldsHtml", 1)[0]
    for fields in (workflow_digital_human, task_digital_human):
        assert fields.index('taskFieldHtml("驱动方式"') < fields.index("taskAdvancedFieldsHtml(")
        assert fields.index('taskFieldHtml("视频时长"') < fields.index("taskAdvancedFieldsHtml(")
        assert fields.index('taskFieldHtml("语速"') > fields.index("taskAdvancedFieldsHtml(")
        assert fields.index('taskFieldHtml("模板处理"') > fields.index("taskAdvancedFieldsHtml(")

    for prefix in ("workflowParamIp", "abilityIp", "taskIp"):
        assert f'ipDailyAdvancedFieldsHtml("{prefix}")' in script
    assert 'workInputHtml(`${prefix}IndustryCount`, "number", "5"' in script
    assert 'workInputHtml(`${prefix}IpCount`, "number", "5"' in script
    assert 'workInputHtml(`${prefix}MomentsCount`, "number", "20"' in script
    assert 'workflowParamNumber(`${prefix}ImageCount`, 3, 1, 5)' in script
    assert 'workflowParamNumber("workflowParamIpIndustryCount", 5, 1, 5)' in script
    assert 'abilityNumber("abilityIpIndustryCount", 5, 1, 5)' in script
    assert 'workflowParamValue("workflowParamHiflyRate") || "1"' in script
    assert 'workflowParamValue(`${prefix}ImageModel`) || "gpt-image-2"' in script


def test_live_executor_uses_compact_modal_configuration_flow():
    html = (H5 / "index.html").read_text(encoding="utf-8")
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-app.css").read_text(encoding="utf-8")
    view = html.split('id="liveExecutorView"', 1)[1].split('id="recorderView"', 1)[0]
    modal = html.split('id="liveExecutorConfigModal"', 1)[1].split('id="assetVoiceModal"', 1)[0]

    assert 'id="liveExecutorVoiceOpenBtn"' in view
    assert 'live-executor-action-grid' in view
    assert 'id="liveExecutorImageFile"' not in view
    assert 'id="liveExecutorPrompt"' not in view
    assert 'id="liveExecutorImageFile"' in modal
    assert 'data-camera-target="liveExecutorImageFile"' in modal
    assert 'id="liveExecutorPrompt"' in modal
    assert 'id="liveExecutorConfigSubmitBtn"' in modal
    assert "function openLiveExecutorConfig" in script
    assert "function submitLiveExecutorConfig" in script
    assert "openLiveExecutorConfig(key)" in script
    assert "handleLiveExecutorAction(action, { fromConfig: true })" in script
    assert ".live-executor-config-panel" in css
    assert ".live-executor-action-grid" in css


def test_local_bestseller_defaults_to_persona_and_clears_it_for_other_styles():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")
    fields = script.split("function localBestsellerFieldsHtml", 1)[1].split("function localBestsellerParamsFromFields", 1)[0]
    controls = script.split("function bindLocalBestsellerPersonaControls", 1)[1].split("function localBestsellerFieldsHtml", 1)[0]

    assert 'optionHtml("", "使用个人 IP 人设")' in fields
    assert 'api("/api/ip-content/personal-default", { cache: "no-store" })' in script
    assert 'bindLocalBestsellerPersonaControls("workLocal")' in script
    assert 'bindLocalBestsellerPersonaControls("workflowParamLocal")' in script
    assert 'if (style.dataset.localPersonaActive !== "0") clearLocalBestsellerPersonaFields(prefix);' in controls
    assert 'style.dataset.localPersonaActive = "0";' in controls
    assert 'profile_source: usePersona ? "persona" : "custom"' in script
    assert 'const style = profileSource === "persona" ? "" : (profile.style || "");' in script
    assert script.index('setFieldValue(`${prefix}Style`, style);') < script.index('setAssetPickerPayloadValue(`${prefix}Photo`')
    assert ".local-persona-status" in css
    assert ".field.local-persona-locked" in css


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


def test_h5_storyboard_checkbox_options_are_compact_and_scoped():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-designer-v2.css").read_text(encoding="utf-8")
    fields_block = script.split("function seedanceFieldsHtml", 1)[1].split("function syncSeedanceDurationOptions", 1)[0]

    assert "function workCheckboxGroupHtml" in script
    assert 'taskFieldHtml("结果处理", workCheckboxGroupHtml([' in fields_block
    assert "workCheckboxHtml(`${prefix}NeedMerge`" not in fields_block
    assert "#abilityView .marketing-tool-mode .ability-workbench-fields .task-checkbox-grid" in css
    assert '#abilityView .marketing-tool-mode .ability-workbench-fields .task-checkbox input[type="checkbox"]' in css
    assert "max-width: 18px !important;" in css
    assert "-webkit-appearance: checkbox;" in css


def test_h5_storyboard_run_detail_text_is_not_reused_as_prompt():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    run_rows_block = script.split("function runParameterRows", 1)[1].split("function runDetailActionsHtml", 1)[0]
    seedance_payload_block = script.split("function seedancePayloadFromFields", 1)[1].split("function seedanceUiModelFromPayload", 1)[0]
    seedance_refill_block = script.split("function setSeedanceFieldsFromPayload", 1)[1].split("function taskTextareaHtml", 1)[0]

    assert "function looksLikeRunDetailText" in script
    assert "function safeTaskInputText" in script
    assert 'capabilityId === "comfly.seedance.tvc.pipeline"' in run_rows_block
    assert 'add("视频要求", safeTaskInputText(inner.task_text, inner.prompt, params.prompt));' in run_rows_block
    assert 'add("商品要求", inner.task_text);' not in run_rows_block
    assert "looksLikeRunDetailText(rawPrompt)" in seedance_payload_block
    assert "视频要求里不能使用执行详情或失败结果" in seedance_payload_block
    assert "safeTaskInputText(inner.task_text, inner.prompt, fallbackPrompt)" in seedance_refill_block


def test_h5_run_detail_prioritizes_results_and_collapses_details():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    css = (H5 / "h5-app.css").read_text(encoding="utf-8")
    task_detail_block = script.split("function taskDetailHtml", 1)[1].split("async function openRunDetail", 1)[0]
    technical_block = task_detail_block.split("const technicalHtml", 1)[1].split("function douyinLeadActionLabel", 1)[0]

    assert "function runDetailActionSectionHtml" in script
    assert "actionSections.unshift(runDetailActionsHtml(run));" in task_detail_block
    assert "const actionHtml = runDetailActionSectionHtml(actionSections);" in task_detail_block
    assert "if (actionHtml) primarySections.push(actionHtml);" in task_detail_block
    assert "查看执行配置与参数" in technical_block
    assert "runDetailActionsHtml(run)" not in technical_block
    assert "<details class=\"task-detail-section task-detail-result-details task-detail-secondary\">" in task_detail_block
    assert ".task-detail-action-primary" in css
    assert ".task-detail-secondary summary" in css


def test_h5_run_refill_maps_online_digital_human_action_to_workbench():
    script = (H5 / "h5-app.js").read_text(encoding="utf-8")
    ability_key_block = script.split("function abilityKeyFromClientAction", 1)[1].split("function clientActionName", 1)[0]
    run_rows_block = script.split("function runParameterRows", 1)[1].split("function runDetailActionsHtml", 1)[0]
    refill_block = script.split("function abilityKeyFromRun", 1)[1].split("function setFieldValue", 1)[0]

    assert 'shanjian_digital_human_video: "hifly.video.create_by_tts"' in ability_key_block
    assert 'if (payload.action) add("执行动作", clientActionName(payload.action));' in run_rows_block
    assert "const mapped = abilityKeyFromClientAction(action);" in refill_block
    assert "if (mapped) return findAbilityKeyBy" in refill_block


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
