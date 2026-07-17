    const $ = (id) => document.getElementById(id);
    const state = {
      token: localStorage.getItem("lobster_h5_token") || "",
      mode: "direct",
      user: null,
      streams: new Map(),
      pollers: new Map(),
      uploads: [],
      devices: [],
      selectedInstallationId: localStorage.getItem("lobster_h5_selected_installation_id") || "",
      tasks: [],
      runs: [],
      taskListOffset: 0,
      taskListHasNext: false,
      taskEditId: "",
      runListOffset: 0,
      runListHasNext: false,
      runDetailBackTab: "runList",
      taskDetailBackTab: "taskList",
      taskListBackTab: "profile",
      taskListBackTarget: null,
      workListBackTab: "profile",
      workListBackTarget: null,
      personalSettingsBackTab: "profile",
      agentManageBackTab: "profile",
      workListScope: { type: "all", label: "全部记录" },
      workListScopeOptions: [],
      historyItems: [],
      socialLeadJobs: [],
      linkedinJobs: [],
      wechatTranscriptJobs: [],
      companyName: localStorage.getItem("lobster_h5_company_name") || "我的AI公司",
      officeDeviceFilter: "all",
      departmentSelectedDate: "",
      workflowSelectedDate: "",
      officePage: 1,
      officePageSize: 4,
      taskAbility: "goal.video.pipeline",
      taskPanelOpen: false,
      hiflyLoaded: false,
      hiflyLoading: false,
      avatarRows: [],
      voiceRows: [],
      candidateGroups: [],
      publishAccounts: [],
      publishAccountsLoaded: false,
      publishAccountsLoading: false,
      publishRunDraft: null,
      publishRunSubmitting: false,
      userUploadAssetCache: {},
      userUploadAssetLoading: {},
      assetLibrarySection: "uploads",
      assetLibraryOrigin: "user_upload",
      assetLibraryPage: { user_upload: 1, generated: 1 },
      assetLibraryPageSize: 10,
      assetLibraryRows: { user_upload: [], generated: [] },
      assetLibraryTotals: { user_upload: 0, generated: 0 },
      assetLibraryLoading: false,
      assetLibraryAvatarPage: 1,
      assetLibraryVoicePage: 1,
      assetLibraryAvatarRows: [],
      assetLibraryVoiceRows: [],
      assetLibraryAvatarTotal: 0,
      assetLibraryVoiceTotal: 0,
      assetLibraryDigitalLoading: false,
      contentRecordMediaType: "",
      contentRecordPage: 1,
      contentRecordPageSize: 10,
      contentRecordRows: [],
      contentRecordTotal: 0,
      contentRecordLoading: false,
      workflowTemplates: [],
      workflowTemplatesLoaded: false,
      workflowTemplatesLoading: false,
      workflowCanGrant: false,
      workflowNodesDraft: [],
      workflowEditingTemplateId: "",
      workflowActive: null,
      workflowSubUsers: [],
      workflowSubUserTotal: 0,
      workflowSubUserOffset: 0,
      workflowSubUserLimit: 10,
      workflowSubUserQuery: "",
      workflowGrantTemplateId: "",
      workflowGrantSelectedUserIds: {},
      workflowSubmitting: false,
      workflowParamNodeId: "",
      agentUsers: [],
      agentUsersTotal: 0,
      agentUsersOffset: 0,
      agentUsersLimit: 10,
      agentUsersQuery: "",
      agentSelectedUserId: "",
      agentResources: { workflow_templates: [], ip_templates: [], memory_docs: [] },
      agentGrantWorkflow: {},
      agentGrantIpTemplates: {},
      agentGrantMemories: {},
      agentPendingIpTemplateId: "",
      agentLoading: false,
      tikhubRecords: [],
      tikhubRecordDetails: {},
      leadCenterDomain: "public",
      leadCenterPlatform: "all",
      leadCenterRows: [],
      leadCenterLoading: false,
      ipTemplates: [],
      ipTemplatesLoaded: false,
      ipTemplatesLoading: false,
      personalSettingsLoaded: false,
      personalSettingsLoading: false,
      personalSettingsTab: "keywords",
      personalSurveyIndex: 0,
      personalKeywords: [],
      personalCompetitors: [],
      personalMemoryDocs: [],
      personalTemplates: [],
      personalEditingTemplateId: "",
      personalDefault: null,
      personalUploadFiles: [],
      personalCustomReferenceFile: null,
      personalGeneratedDocuments: {},
      personalGeneratedDocOrder: [],
      personalSelectedKeywords: {},
      personalSelectedCompetitors: {},
      personalSelectedMemories: {},
      personalMemoryUseProfile: true,
      personalMemorySourceKeywords: {},
      personalMemorySourceCompetitors: {},
      personalMemorySourceDocs: {},
      personalMemorySourceFiles: {},
      taskSkillPackages: [],
      taskAllowedCapabilityIds: [],
      taskSkillsLoaded: false,
      taskSkillsLoading: false,
      taskSkillsError: "",
      workDispatchKey: "",
      workDispatchSubmitting: false,
      abilityWorkSubmitting: false,
      douyinStatus: null,
      douyinTaskAction: "search_collect",
      voiceRecording: false,
      voiceDraft: "",
      voiceTimer: null,
      voiceStatus: "idle",
      voicePartial: "",
      voiceIntent: null,
      voiceActions: [],
      voiceExpanded: false,
      lastViewBeforeMessages: "",
      voiceWs: null,
      voiceAudioContext: null,
      voiceMediaStream: null,
      voiceProcessor: null,
      voiceSourceNode: null,
      voiceSeq: 0,
      officeVoiceHoldActive: false,
      currentDepartmentId: "",
      currentAbilityKey: "",
      abilityTrail: [],
      chatContext: null,
    };
    const SHOW_INTERNAL_STEPS = false;
    const IMAGE_TO_VIDEO_PROMPT = "用这个图片，提示词：";
    const TASK_CAPABILITIES = {
      "goal.video.pipeline": {
        label: "创意成片",
        description: "根据记忆或自定义提示词生成文案，可从备选素材组随机取图，或先 AI 生成首帧再生成视频。",
        packageId: "goal_video_pipeline_skill",
        department: "市场部",
      },
      "goal.image.pipeline": {
        label: "文案+创意图片",
        description: "根据记忆或自定义提示词生成文案和创意图片，生成图片后结束。",
        packageId: "goal_video_pipeline_skill",
        department: "市场部",
      },
      "hifly.video.create_by_tts": {
        label: "必火数字人",
        description: "选择本机已有数字人模板和声音，生成数字人口播视频。",
        packageId: "hifly_digital_human_skill",
        department: "市场部",
      },
      "ip_content_daily": {
        label: "IP日更文案",
        description: "服务器定时同步关键词和同行数据，生成行业口播、专业IP口播和朋友圈文案。",
        packageId: "ip_content_daily_skill",
        department: "市场部",
        serverTask: true,
      },
      "comfly.daihuo.pipeline": {
        label: "爆款TVC",
        description: "用素材或公网图生成多分镜成片，适合商品宣传和广告视频。",
        packageId: "comfly_veo_skill",
        department: "市场部",
      },
      "comfly.seedance.tvc.pipeline": {
        label: "创意分镜头视频",
        description: "按 10 秒一段生成连续分镜和视频，并合成为完整成片。",
        packageId: "comfly_seedance_tvc_skill",
        department: "市场部",
      },
      "create.video.pipeline": {
        label: "速推视频制作",
        description: "根据创意要求规划分镜，生成首帧并制作视频。",
        packageId: "create_video_pipeline_skill",
        department: "市场部",
      },
      "wewrite.article.pipeline": {
        label: "微信公众号",
        description: "输入主题，生成公众号文章、配图并推送到草稿箱。",
        packageId: "wewrite_official_account_skill",
        department: "市场部",
      },
      "ppt.create": {
        label: "PPT",
        description: "输入主题或大纲，生成可下载的 PPT 演示文稿。",
        packageId: "create_ppt_skill",
        department: "运营部",
      },
      "comfly.ecommerce.detail_pipeline": {
        label: "电商详情页",
        description: "用商品主图生成电商详情页长图并自动入库。",
        packageId: "comfly_ecommerce_detail_skill",
        department: "运营部",
      },
      "douyin_leads": {
        label: "抖音获客",
        description: "采集客户、评论互动、私信触达和同行监控。",
        featureKey: "douyin_leads_access",
        department: "销售部",
        routeTab: "douyinLeadsSchedule",
      },
    };
    const TASK_DEPARTMENTS = ["市场部", "销售部", "客服部", "运营部"];
    const SCHEDULED_TASK_CAPABILITY_IDS = [
      "goal.image.pipeline",
      "ip_content_daily",
      "goal.video.pipeline",
      "hifly.video.create_by_tts",
    ];
    const WORK_QUICK_ITEMS = [
      {
        key: "creative_general",
        label: "帮我创作",
        department: "市场部",
        mark: "创",
        prompt: "帮我写一版电商详情页文案、短视频脚本和发布标题。",
        always: true,
      },
      {
        key: "image_composer_studio",
        label: "创作图片",
        department: "市场部",
        mark: "图",
        dispatchKind: "capability",
        capabilityId: "goal.image.pipeline",
        packageId: "goal_video_pipeline_skill",
      },
      {
        key: "comfly.daihuo.pipeline",
        capabilityId: "comfly.daihuo.pipeline",
        packageId: "comfly_veo_skill",
        label: "爆款TVC",
        department: "市场部",
        mark: "▶",
        prompt: "用爆款tvc帮我生成一个视频。",
      },
      {
        key: "comfly.seedance.tvc.pipeline",
        capabilityId: "comfly.seedance.tvc.pipeline",
        packageId: "comfly_seedance_tvc_skill",
        label: "创意分镜头视频",
        department: "市场部",
        mark: "▶",
        dispatchKind: "capability",
      },
      {
        key: "local_bestseller",
        label: "同城爆款",
        department: "市场部",
        mark: "城",
        dispatchKind: "client_workflow",
        workflowAction: "local_bestseller_plan",
        always: true,
      },
      {
        key: "viral_video_remix",
        label: "爆款复刻",
        department: "市场部",
        mark: "R",
        dispatchKind: "client_workflow",
        workflowAction: "viral_video_remix_start",
        always: true,
      },
      {
        key: "hifly.video.create_by_tts",
        capabilityId: "hifly.video.create_by_tts",
        packageId: "hifly_digital_human_skill",
        label: "数字人",
        department: "市场部",
        mark: "H",
        dispatchKind: "capability",
      },
      {
        key: "douyin_leads",
        featureKey: "douyin_leads_access",
        label: "抖音获客",
        department: "销售部",
        mark: "获",
        dispatchKind: "douyin_leads",
        highlight: true,
      },
      {
        key: "wecom_reply",
        packageId: "wecom_reply",
        label: "企业微信客服",
        department: "客服部",
        mark: "微",
        dispatchKind: "client_workflow",
        workflowAction: "wecom_poll_reply",
      },
      {
        key: "publish_center",
        label: "发布中心入库",
        department: "运营部",
        mark: "发",
        dispatchKind: "client_workflow",
        workflowAction: "publish_content",
        always: true,
      },
      {
        key: "ai_shop_diagnosis",
        label: "AI店铺诊断（敬请期待）",
        department: "运营部",
        mark: "店",
        disabled: true,
        always: true,
      },
      {
        key: "ai_product_selection",
        label: "AI选品（敬请期待）",
        department: "运营部",
        mark: "品",
        disabled: true,
        always: true,
      },
    ];
    const DEPARTMENT_SKILL_TREE = [
      {
        id: "marketing",
        name: "市场部",
        alias: "流量部 / 内容部",
        mark: "市",
        description: "负责内容创作、视频生产、IP日更、公众号和发布矩阵。",
        children: [
          {
            key: "image_composer_studio",
            label: "创作图片",
            mark: "图",
            description: "根据文案或产品资料生成创意图片，适合海报、配图和素材生产。",
            capabilityId: "goal.image.pipeline",
            packageId: "goal_video_pipeline_skill",
            workQuickKey: "image_composer_studio",
          },
          {
            key: "marketing_video_group",
            label: "创作视频",
            mark: "视",
            description: "视频创作能力集合，进入后选择具体视频工作流。",
            children: [
              {
                key: "goal.video.pipeline",
                label: "创意视频",
                mark: "创",
                description: "从创意、素材或首帧生成完整视频。",
                capabilityId: "goal.video.pipeline",
                packageId: "goal_video_pipeline_skill",
              },
              {
                key: "local_bestseller",
                label: "同城爆款视频",
                mark: "城",
                description: "围绕同城热点和门店场景生成爆款视频方案。",
                workQuickKey: "local_bestseller",
                always: true,
              },
              {
                key: "hifly.video.create_by_tts",
                label: "数字人口播视频",
                mark: "数",
                description: "选择数字人和声音，生成口播视频。",
                capabilityId: "hifly.video.create_by_tts",
                packageId: "hifly_digital_human_skill",
                workQuickKey: "hifly.video.create_by_tts",
              },
              {
                key: "comfly.daihuo.pipeline",
                label: "爆款TVC",
                mark: "TVC",
                description: "使用素材或产品图生成广告短片。",
                capabilityId: "comfly.daihuo.pipeline",
                packageId: "comfly_veo_skill",
                workQuickKey: "comfly.daihuo.pipeline",
              },
              {
                key: "comfly.seedance.tvc.pipeline",
                label: "创意分镜头视频",
                mark: "分",
                description: "按连续分镜规划视频，并生成完整成片。",
                capabilityId: "comfly.seedance.tvc.pipeline",
                packageId: "comfly_seedance_tvc_skill",
                workQuickKey: "comfly.seedance.tvc.pipeline",
              },
              {
                key: "viral_video_remix",
                label: "爆款复刻",
                mark: "复",
                description: "基于爆款结构复刻视频脚本和执行方案。",
                workQuickKey: "viral_video_remix",
                always: true,
              },
            ],
          },
          {
            key: "ip_content_daily",
            label: "IP日更文案",
            mark: "IP",
            description: "生成短视频口播、朋友圈文案和配图提示词。",
            capabilityId: "ip_content_daily",
            packageId: "ip_content_daily_skill",
            serverTask: true,
          },
          {
            key: "wewrite.article.pipeline",
            label: "公众号文章",
            mark: "文",
            description: "根据主题生成公众号文章、配图和发布草稿。",
            capabilityId: "wewrite.article.pipeline",
            packageId: "wewrite_official_account_skill",
          },
          {
            key: "publish_center",
            label: "发布中心（矩阵系统）",
            mark: "发",
            description: "管理素材发布和账号矩阵任务。",
            workQuickKey: "publish_center",
            always: true,
          },
        ],
      },
      {
        id: "sales",
        name: "销售部",
        alias: "获客 / 转化",
        mark: "销",
        description: "负责线索采集、客户触达、销售材料和账号发布。",
        children: [
          {
            key: "douyin_leads",
            label: "抖音获客",
            mark: "抖",
            description: "采集客户线索、评论互动、私信触达和同行监控。",
            featureKey: "douyin_leads_access",
            routeTab: "douyinLeadsSchedule",
            workQuickKey: "douyin_leads",
          },
          {
            key: "wecom_reply",
            label: "企微自动回复",
            mark: "企",
            description: "根据企业微信会话自动理解并辅助回复。",
            packageId: "wecom_reply",
            workQuickKey: "wecom_reply",
          },
          {
            key: "personal_wechat",
            label: "个微（私聊+朋友圈评论区）",
            mark: "微",
            description: "围绕个人微信私聊和朋友圈评论区做客户跟进。",
            comingSoon: true,
          },
          {
            key: "sales_publish_center",
            label: "发布中心（矩阵系统）",
            mark: "发",
            description: "把销售内容同步到账号矩阵。",
            routeTab: "home",
            workQuickKey: "publish_center",
            always: true,
          },
          {
            key: "ppt.create",
            label: "PPT生成",
            mark: "PPT",
            description: "生成销售提案、招商介绍和汇报 PPT。",
            capabilityId: "ppt.create",
            packageId: "create_ppt_skill",
          },
        ],
      },
      {
        id: "operations",
        name: "运营部",
        alias: "运营 / 商品 / 工具",
        mark: "运",
        description: "负责运营工具、详情页、视频号、3D模型和内容执行。",
        children: [
          {
            key: "ops_wecom_reply",
            label: "企微自动回复",
            mark: "企",
            description: "运营侧企业微信咨询自动回复。",
            packageId: "wecom_reply",
            workQuickKey: "wecom_reply",
          },
          {
            key: "ops_personal_wechat",
            label: "个微（私聊+朋友圈评论区）",
            mark: "微",
            description: "运营侧个人微信跟进和评论区处理。",
            comingSoon: true,
          },
          {
            key: "ops_digital_human",
            label: "数字人视频",
            mark: "数",
            description: "生成数字人口播视频。",
            capabilityId: "hifly.video.create_by_tts",
            packageId: "hifly_digital_human_skill",
            workQuickKey: "hifly.video.create_by_tts",
          },
          {
            key: "ops_publish_center",
            label: "发布中心（矩阵系统）",
            mark: "发",
            description: "运营内容发布和矩阵管理。",
            routeTab: "home",
            workQuickKey: "publish_center",
            always: true,
          },
          {
            key: "wechat_channels_transcript",
            label: "视频号文案提取",
            mark: "号",
            description: "提取视频号内容文案，便于复盘和改写。",
            packageId: "wechat_channels_transcript_skill",
          },
          {
            key: "comfly.ecommerce.detail_pipeline",
            label: "电商详情页",
            mark: "详",
            description: "根据商品图片和资料生成电商详情页。",
            capabilityId: "comfly.ecommerce.detail_pipeline",
            packageId: "comfly_ecommerce_detail_skill",
          },
          {
            key: "ai_3d_model",
            label: "高质量3D模型",
            mark: "3D",
            description: "根据图片或文本生成高质量 3D 模型。",
            packageId: "ai_3d_model_skill",
          },
          {
            key: "ops_ppt.create",
            label: "PPT生成",
            mark: "PPT",
            description: "生成运营方案、活动复盘和项目汇报 PPT。",
            capabilityId: "ppt.create",
            packageId: "create_ppt_skill",
          },
        ],
      },
      {
        id: "customer_service",
        name: "客服部",
        alias: "服务 / 回复",
        mark: "客",
        description: "负责企微、个微和常用客服知识库。",
        children: [
          {
            key: "cs_wecom_reply",
            label: "企微自动回复",
            mark: "企",
            description: "企业微信咨询自动回复。",
            packageId: "wecom_reply",
            workQuickKey: "wecom_reply",
          },
          {
            key: "cs_personal_wechat",
            label: "个微（私聊+朋友圈评论区）",
            mark: "微",
            description: "个人微信私聊和朋友圈评论区回复。",
            comingSoon: true,
          },
        ],
      },
      {
        id: "overseas",
        name: "海外部",
        alias: "海外线索",
        mark: "海",
        description: "负责海外平台线索采集和客户资料沉淀。",
        children: [
          {
            key: "linkedin_leads",
            label: "LinkedIn线索挖掘",
            mark: "in",
            description: "采集 LinkedIn 相关线索和账号资料。",
            packageId: "linkedin_leads",
          },
          {
            key: "reddit_leads",
            label: "Reddit线索采集",
            mark: "R",
            description: "采集社区帖子、评论并分析精准用户。",
            packageId: "reddit_leads",
          },
          {
            key: "x_leads",
            label: "X线索采集",
            mark: "X",
            description: "采集账号内容、评论和潜在线索。",
            packageId: "x_leads",
          },
          {
            key: "tiktok_leads",
            label: "TikTok线索采集",
            mark: "TT",
            description: "采集账号作品、视频评论和潜在线索。",
            packageId: "tiktok_leads",
          },
        ],
      },
    ];
    const IP_DAILY_TASK_OPTIONS = [
      { value: "industry_hot_oral", label: "行业热门口播" },
      { value: "professional_ip_oral", label: "专业 IP 口播" },
      { value: "moments_candidate", label: "朋友圈文案" },
    ];
    const SALES_WORKFLOW_PRESET = [
      ["06:00", "local_bestseller", "自动创作一条同城爆款视频"],
      ["07:00", "hifly.video.create_by_tts", "自动创作一条数字人口播视频"],
      ["08:00", "wecom_reply", "私信接管"],
      ["08:30", "douyin_leads", "自动养号"],
      ["09:00", "douyin_leads", "视频发布后采集互动线索"],
      ["09:30", "wecom_reply", "私信接管"],
      ["09:45", "wecom_reply", "自动加好友"],
      ["10:00", "ip_content_daily", "朋友圈发布文案准备"],
      ["10:30", "wecom_reply", "朋友圈点赞评论"],
      ["11:00", "wecom_reply", "私信接管"],
      ["11:30", "douyin_leads", "自动养号"],
      ["12:00", "douyin_leads", "关键词抓取精准客户"],
      ["12:30", "douyin_leads", "回复10个精准客户评论"],
      ["13:00", "douyin_leads", "评论上午发布的视频并@精准客户"],
      ["13:30", "douyin_leads", "关注精准客户并评论首条作品"],
      ["14:00", "wecom_reply", "私信接管"],
      ["14:30", "douyin_leads", "抖音私信10个精准客户"],
      ["15:00", "douyin_leads", "抖音私信引流接管"],
      ["15:30", "douyin_leads", "自动养号"],
      ["16:30", "wecom_reply", "自动加好友"],
      ["17:00", "wecom_reply", "私信接管"],
      ["17:30", "douyin_leads", "关键词抓取精准客户"],
      ["18:30", "douyin_leads", "回复10个精准客户评论"],
      ["19:00", "douyin_leads", "评论上午发布的视频并@精准客户"],
      ["19:30", "douyin_leads", "关注精准客户并评论首条作品"],
      ["20:00", "wecom_reply", "私信接管"],
      ["20:30", "douyin_leads", "抖音私信10个精准客户"],
      ["21:00", "wecom_reply", "朋友圈点赞评论"],
      ["21:30", "douyin_leads", "抖音私信引流接管"],
      ["22:30", "wecom_reply", "自动加好友"],
      ["23:00", "wecom_reply", "私信接管"],
    ];
    const SALES_WORKFLOW_NODE_OPTIONS = Array.from(new Map(SALES_WORKFLOW_PRESET.map((row) => {
      const key = `${row[1]}@@${row[2]}`;
      return [key, { key: row[1], label: row[2], note: row[2] }];
    })).values());

    const DOUYIN_TASK_ACTIONS = {
      search_collect: {
        label: "采集客户",
        description: "按行业关键词搜索抖音内容，先把视频和客户线索沉淀下来。",
      },
      comment_collect: {
        label: "视频评论",
        description: "围绕目标视频或关键词评论，顺手采集评论区里的潜在客户。",
      },
      interaction: {
        label: "私信获客",
        description: "对已经沉淀的客户池做评论跟进、私信触达和二次转化。",
      },
      tasks_from_search: {
        label: "同行监控",
        description: "按关键词持续盯同行内容，把可跟进对象自动整理成任务。",
      },
    };

    function toast(text) {
      const el = $("toast");
      el.textContent = text;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 2600);
    }

    function closeTaskSuccessDialog() {
      const modal = $("taskSuccessDialog");
      if (modal) modal.classList.add("hidden");
    }

    function openPersonalTemplateSettings() {
      state.personalSettingsTab = "template";
      switchTab("personalSettings");
      loadPersonalSettings();
    }

    function openPersonalTemplateHelpDialog() {
      const modal = $("personalTemplateHelpDialog");
      if (modal) modal.classList.remove("hidden");
    }

    function closePersonalTemplateHelpDialog() {
      const modal = $("personalTemplateHelpDialog");
      if (modal) modal.classList.add("hidden");
    }

    function closeAssetPreviewDialog() {
      const modal = $("assetPreviewDialog");
      if (modal) modal.classList.add("hidden");
    }

    function closeLeadDetailDialog() {
      const modal = $("leadDetailDialog");
      if (modal) modal.classList.add("hidden");
    }

    function normalizeViewTarget(target, defaultTab = "profile") {
      if (!target) return { tab: defaultTab };
      if (typeof target === "string") return { tab: target || defaultTab };
      if (typeof target === "object") return { ...target, tab: target.tab || defaultTab };
      return { tab: defaultTab };
    }

    function viewTargetFromCurrent(defaultTab = "profile") {
      const current = activeViewKey();
      if (!current || ["taskList", "taskDetail", "runDetail"].includes(current)) return { tab: defaultTab };
      const target = { tab: current };
      if (current === "department") {
        target.departmentId = state.currentDepartmentId || "";
      }
      if (current === "ability") {
        target.departmentId = state.currentDepartmentId || "";
        target.abilityKey = state.currentAbilityKey || "";
        target.abilityTrail = Array.isArray(state.abilityTrail) ? [...state.abilityTrail] : [];
      }
      return target;
    }

    function restoreViewTarget(target, defaultTab = "profile") {
      const next = normalizeViewTarget(target, defaultTab);
      if (next.tab === "department") {
        if (next.departmentId) state.currentDepartmentId = String(next.departmentId);
        state.currentAbilityKey = "";
        state.abilityTrail = [];
        if (departmentById(state.currentDepartmentId)) {
          switchTab("department");
        } else {
          switchTab(defaultTab);
        }
        return;
      }
      if (next.tab === "ability") {
        const lookup = abilityLookup(next.abilityKey || state.currentAbilityKey || "");
        if (lookup) {
          state.currentDepartmentId = lookup.department.id;
          state.currentAbilityKey = lookup.node.key;
          state.abilityTrail = lookup.trail.map((node) => node.key);
          switchTab("ability");
          return;
        }
        if (next.departmentId && departmentById(next.departmentId)) {
          openDepartmentView(next.departmentId);
          return;
        }
      }
      switchTab(next.tab || defaultTab);
    }

    function openWorkHistory(scope = null, backTarget = null) {
      closeTaskSuccessDialog();
      const nextScope = scope || scopeFromActiveView();
      const options = workScopeOptions(nextScope);
      const target = backTarget ? normalizeViewTarget(backTarget) : viewTargetFromCurrent("profile");
      state.workListBackTarget = target;
      state.workListBackTab = target.tab || "profile";
      setWorkListScope(nextScope, options);
      switchTab("workList");
    }

    function backTargetFromCurrent(defaultTab = "profile") {
      return viewTargetFromCurrent(defaultTab);
    }

    function openScheduleManager(backTarget = null) {
      closeTaskSuccessDialog();
      const target = backTarget ? normalizeViewTarget(backTarget) : backTargetFromCurrent("profile");
      state.taskListBackTarget = target;
      state.taskListBackTab = target.tab || "profile";
      switchTab("taskList");
    }

    function showTaskSuccessDialog(detail) {
      const modal = $("taskSuccessDialog");
      if (!modal) {
        toast("下发任务成功");
        return;
      }
      const text = $("taskSuccessText");
      if (text) text.textContent = detail || "任务已进入工作历史。";
      modal.classList.remove("hidden");
    }

    const UA = navigator.userAgent || "";
    const IS_WECHAT = /MicroMessenger/i.test(UA);
    const IS_IOS = /iPad|iPhone|iPod/i.test(UA) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

    async function copyText(text) {
      const value = String(text || "");
      if (!value) return false;
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(value);
          return true;
        } catch {}
      }
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      area.style.top = "0";
      document.body.appendChild(area);
      area.focus();
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      document.body.removeChild(area);
      return ok;
    }

    async function copyMediaLink(url) {
      const ok = await copyText(url);
      toast(ok ? "链接已复制，请在外部浏览器打开后保存" : "复制失败，请长按链接复制后用外部浏览器打开");
    }

    function onIosDownloadClick() {
      if (IS_IOS && !IS_WECHAT) toast("已开始下载，可在 Safari 下载项或文件 App 的下载目录查看");
    }

    function installIosWebclip() {
      if (IS_WECHAT) {
        toast("请先用 Safari 打开本页，再下载描述文件");
        return;
      }
      if (!IS_IOS) {
        toast("这个入口用于 iPhone/iPad 添加桌面快捷方式");
      } else {
        toast("下载后请到系统设置里安装描述文件");
      }
      window.location.href = "/install/ios-webclip.mobileconfig";
    }

    function authHeaders(extra = {}) {
      return state.token ? { Authorization: `Bearer ${state.token}`, ...extra } : { ...extra };
    }

    const H5_API_BASE = (() => {
      try {
        const queryBase = new URLSearchParams(window.location.search || "").get("api_base");
        if (queryBase) return String(queryBase).replace(/\/$/, "");
      } catch {}
      const host = String((window.location && window.location.hostname) || "").toLowerCase();
      const port = String((window.location && window.location.port) || "").trim();
      if (host === "h5.bhzn.top" || host.startsWith("h5.")) {
        return String((window.location && window.location.origin) || "").replace(/\/$/, "");
      }
      if ((host === "127.0.0.1" || host === "localhost") && port === "8000") return "http://127.0.0.1:8002";
      return String((window.location && window.location.origin) || "").replace(/\/$/, "");
    })();

    function apiUrl(path) {
      const raw = String(path || "").trim();
      if (!raw) return H5_API_BASE;
      if (/^https?:\/\//i.test(raw)) return raw;
      if (raw.startsWith("/")) return `${H5_API_BASE}${raw}`;
      return `${H5_API_BASE}/${raw}`;
    }

    function api(path, options = {}) {
      const headers = { ...(options.headers || {}), ...authHeaders() };
      if (options.json) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(options.json);
      }
      return fetch(apiUrl(path), { ...options, headers }).then(async (resp) => {
        const text = await resp.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
        if (!resp.ok) throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
        return data;
      });
    }

    function escapeHtml(text) {
      return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function cssEscape(value) {
      const text = String(value || "");
      if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(text);
      return text.replace(/["\\\]\[]/g, "\\$&");
    }

    function firstChar(text) {
      const s = String(text || "AI").trim();
      return (s[0] || "A").toUpperCase();
    }

    function fmtTime(value) {
      if (!value) return "-";
      const d = parseDate(value);
      if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
      return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }

    function datetimeLocalValue(value) {
      if (!value) return "";
      const d = parseDate(value);
      if (Number.isNaN(d.getTime())) return "";
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function statusText(status) {
      return {
        pending: "待执行",
        processing: "执行中",
        completed: "完成",
        failed: "失败",
        cancelled: "已取消",
        active: "启用",
        paused: "暂停",
      }[status] || status || "-";
    }

    function capabilityName(capabilityId) {
      const specialName = {
        social_leads: "社媒线索采集",
        linkedin_mining: "LinkedIn线索采集",
        wechat_channels_transcript: "视频号文案提取",
      }[capabilityId];
      if (specialName) return specialName;
      return {
        "goal.video.pipeline": "创意成片",
        "goal.image.pipeline": "文案+创意图片",
        "hifly.video.create_by_tts": "必火数字人",
        "ip_content_daily": "IP日更文案",
        "douyin_leads": "抖音获客",
        "comfly.daihuo.pipeline": "爆款TVC",
        "comfly.seedance.tvc.pipeline": "创意分镜头视频",
        "create.video.pipeline": "速推视频制作",
        "wewrite.article.pipeline": "微信公众号",
        "ppt.create": "PPT",
        "comfly.ecommerce.detail_pipeline": "电商详情页",
        "client_workflow": "客户端工作流",
      }[capabilityId] || capabilityId || "定时任务";
    }

    function taskCapabilityId(row) {
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      if (row && row.task_kind === "ip_content_daily") return "ip_content_daily";
      if (row && row.task_kind === "douyin_leads") return "douyin_leads";
      if (row && ["social_leads", "linkedin_mining", "wechat_channels_transcript"].includes(row.task_kind)) return row.task_kind;
      return String(payload.capability_id || "");
    }

    function cleanKey(value) {
      return String(value || "").trim();
    }

    function addKey(set, value) {
      const key = cleanKey(value);
      if (key) set.add(key);
    }

    function quickKeysFor(key) {
      const quick = workQuickItemByKey(key);
      const keys = new Set();
      if (!quick) return keys;
      addKey(keys, quick.key);
      addKey(keys, quick.capabilityId);
      addKey(keys, quick.packageId);
      addKey(keys, quick.workflowAction);
      addKey(keys, quick.dispatchKind);
      return keys;
    }

    function collectAbilityKeys(node, includeChildren = true) {
      const keys = new Set();
      const visit = (item) => {
        if (!item) return;
        addKey(keys, item.key);
        addKey(keys, item.capabilityId);
        addKey(keys, item.packageId);
        addKey(keys, item.workQuickKey);
        addKey(keys, item.workflowAction);
        quickKeysFor(item.workQuickKey).forEach((key) => keys.add(key));
        if (item.key === "reddit_leads") keys.add("reddit");
        if (item.key === "x_leads") keys.add("x");
        if (item.key === "tiktok_leads") keys.add("tiktok");
        if (includeChildren && Array.isArray(item.children)) item.children.forEach(visit);
      };
      visit(node);
      return keys;
    }

    function h5ContextFromPayload(payload) {
      const data = payload && typeof payload === "object" ? payload : {};
      const ctx = data.h5_context && typeof data.h5_context === "object" ? data.h5_context : {};
      return ctx;
    }

    function rowMatchKeys(row) {
      const keys = new Set();
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      const inner = payload.payload && typeof payload.payload === "object" ? payload.payload : {};
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      const ctx = h5ContextFromPayload(payload);
      addKey(keys, row && row.task_kind);
      addKey(keys, row && row.title);
      addKey(keys, taskCapabilityId(row));
      addKey(keys, payload.capability_id);
      addKey(keys, payload.action);
      addKey(keys, payload.workflow_action);
      addKey(keys, payload.platform);
      addKey(keys, params.action);
      addKey(keys, inner.action);
      addKey(keys, ctx.department_id);
      addKey(keys, ctx.ability_key);
      addKey(keys, ctx.capability_id);
      const platform = cleanKey(payload.platform || params.platform);
      if (row && row.task_kind === "social_leads" && platform) addKey(keys, `${platform}_leads`);
      if (row && row.task_kind === "linkedin_mining") addKey(keys, "linkedin_leads");
      if (row && row.task_kind === "wechat_channels_transcript") addKey(keys, "wechat_channels_transcript");
      if (row && row.task_kind === "client_workflow") {
        const action = cleanKey(payload.action || params.action);
        if (action.startsWith("local_bestseller_")) addKey(keys, "local_bestseller");
        if (action === "viral_video_remix_start") addKey(keys, "viral_video_remix");
        if (action === "wecom_poll_reply") addKey(keys, "wecom_reply");
        if (action === "publish_content") addKey(keys, "publish_center");
      }
      return keys;
    }

    function departmentScope(department) {
      if (!department) return { type: "all", label: "全部记录" };
      return { type: "department", departmentId: department.id, label: department.name || "当前部门" };
    }

    function abilityScope(lookup, trailIndex = -1) {
      if (!lookup || !lookup.department || !Array.isArray(lookup.trail) || !lookup.trail.length) {
        return { type: "all", label: "全部记录" };
      }
      const idx = trailIndex >= 0 ? Math.min(trailIndex, lookup.trail.length - 1) : lookup.trail.length - 1;
      const node = lookup.trail[idx];
      const includeChildren = !!(node && node.children && node.children.length);
      return {
        type: "ability",
        departmentId: lookup.department.id,
        abilityKey: node && node.key,
        label: node && node.label || "当前能力",
        includeChildren,
      };
    }

    function scopeId(scope) {
      const item = scope || {};
      return [item.type || "all", item.departmentId || "", item.abilityKey || "", item.includeChildren ? "tree" : "one"].join(":");
    }

    function scopeFromActiveView() {
      const active = activeViewKey();
      if (active === "department") return departmentScope(departmentById(state.currentDepartmentId));
      if (active === "ability") return abilityScope(activeAbilityLookup());
      return { type: "all", label: "全部记录" };
    }

    function workScopeOptions(scope) {
      const options = [{ type: "all", label: "全部记录" }];
      const active = activeViewKey();
      let department = null;
      let lookup = null;
      if (active === "department") department = departmentById(state.currentDepartmentId);
      if (active === "ability") {
        lookup = activeAbilityLookup();
        department = lookup && lookup.department;
      }
      if (!department && scope && scope.departmentId) department = departmentById(scope.departmentId);
      if (department) options.push(departmentScope(department));
      if (lookup && Array.isArray(lookup.trail)) {
        lookup.trail.forEach((node, idx) => {
          const next = abilityScope(lookup, idx);
          if (!options.some((item) => scopeId(item) === scopeId(next))) options.push(next);
        });
      } else if (scope && scope.abilityKey) {
        const scopedLookup = abilityLookup(scope.abilityKey);
        if (scopedLookup) {
          scopedLookup.trail.forEach((node, idx) => {
            const next = abilityScope(scopedLookup, idx);
            if (!options.some((item) => scopeId(item) === scopeId(next))) options.push(next);
          });
        }
      }
      return options;
    }

    function recordMatchesWorkScope(row, scope) {
      const item = scope || { type: "all" };
      if (!row || item.type === "all") return true;
      const payload = row.payload && typeof row.payload === "object" ? row.payload : {};
      const ctx = h5ContextFromPayload(payload);
      if (item.type === "department") {
        if (ctx.department_id && ctx.department_id === item.departmentId) return true;
        const department = departmentById(item.departmentId);
        const keys = departmentNodeKeys(department);
        const rowKeys = rowMatchKeys(row);
        return Array.from(rowKeys).some((key) => keys.has(key));
      }
      if (item.type === "ability") {
        if (ctx.ability_key && ctx.ability_key === item.abilityKey) return true;
        const lookup = abilityLookup(item.abilityKey);
        if (!lookup) return false;
        const keys = collectAbilityKeys(lookup.node, item.includeChildren !== false);
        const rowKeys = rowMatchKeys(row);
        return Array.from(rowKeys).some((key) => keys.has(key));
      }
      return true;
    }

    function renderWorkScopeBar() {
      const box = $("workScopeChips");
      if (!box) return;
      const options = state.workListScopeOptions && state.workListScopeOptions.length
        ? state.workListScopeOptions
        : workScopeOptions(state.workListScope);
      const active = scopeId(state.workListScope || { type: "all" });
      box.innerHTML = options.map((item) => `<button type="button" class="${scopeId(item) === active ? "active" : ""}" data-work-scope="${escapeHtml(scopeId(item))}">${escapeHtml(item.label || "全部记录")}</button>`).join("");
    }

    function setWorkListScope(scope, options) {
      const next = scope || { type: "all", label: "全部记录" };
      state.workListScope = next;
      state.workListScopeOptions = options && options.length ? options : workScopeOptions(next);
      renderWorkScopeBar();
      renderWorkList();
    }

    function attachH5ContextToPayload(payload, context) {
      const ctx = context && context.department_id ? context : null;
      if (!ctx || !payload || typeof payload !== "object") return payload || {};
      return { ...payload, h5_context: { ...ctx, capability_id: payload.capability_id || "" } };
    }

    function taskActionMenuHtml(task, options = {}) {
      if (!task || !task.id) return "";
      const id = escapeHtml(task.id);
      const nextStatus = task.status === "paused" ? "active" : "paused";
      const statusLabel = task.status === "paused" ? "启用任务" : "暂停任务";
      const detailItem = options.includeDetail
        ? `<button type="button" data-open-task-detail="${id}">查看详情</button>`
        : "";
      const runItem = options.includeRunNow === false
        ? ""
        : `<button type="button" data-run-task-now="${id}">立即执行</button>`;
      const recentItem = task.last_run_id
        ? `<button type="button" data-open-run-detail="${escapeHtml(task.last_run_id)}">最近结果</button>`
        : "";
      return `<details class="task-action-menu">
        <summary>操作</summary>
        <div class="task-action-list">
          ${detailItem}
          ${runItem}
          ${recentItem}
          <button type="button" data-edit-task="${id}">编辑任务</button>
          <button type="button" data-task-status="${id}" data-next-status="${nextStatus}">${statusLabel}</button>
          <button type="button" class="danger-text" data-delete-task="${id}">删除任务</button>
        </div>
      </details>`;
    }

    function parseDate(value) {
      if (!value) return null;
      let text = String(value || "").trim();
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text) && !/[zZ]|[+-]\d{2}:\d{2}$/.test(text)) {
        text += "Z";
      }
      const d = new Date(text);
      return Number.isNaN(d.getTime()) ? null : d;
    }

    function timeLabel(value) {
      const d = parseDate(value);
      if (!d) return "--:--";
      return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    }

    function shortId(value) {
      const s = String(value || "");
      if (!s) return "";
      return s.length > 10 ? `${s.slice(0, 6)}...${s.slice(-4)}` : s;
    }

    function compactNumber(value, digits = 0) {
      const n = Number(value || 0);
      if (!Number.isFinite(n)) return "0";
      if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1).replace(/\.0$/, "")}万`;
      return n.toFixed(digits).replace(/\.0+$/, "");
    }

    function numericValue(value) {
      const n = Number(value || 0);
      return Number.isFinite(n) ? n : 0;
    }

    function isActiveRun(row) {
      const status = String((row && row.status) || "").toLowerCase();
      return status === "pending" || status === "processing" || status === "running";
    }

    function isRunningStatus(status) {
      const s = String(status || "").toLowerCase();
      return s === "pending" || s === "processing" || s === "running" || s === "queued" || s === "waiting";
    }

    function isActiveMessageStatus(status) {
      const s = String(status || "").toLowerCase();
      return s === "pending" || s === "processing";
    }

    function itemTimeMs(...values) {
      for (const value of values) {
        const d = parseDate(value);
        if (d) return d.getTime();
      }
      return 0;
    }

    function runInstallationId(row) {
      return String((row && (row.claimed_by_installation_id || row.installation_id)) || "").trim();
    }

    function isServerSideRun(row) {
      return String((row && row.task_kind) || "").trim() === "ip_content_daily"
        || !!(row && row.server_side)
        || !!(row && row.progress && row.progress.server_side);
    }

    function activeRunForDevice(device) {
      const id = String((device && device.installation_id) || "").trim();
      const activeRuns = state.runs.filter(isActiveRun);
      if (!activeRuns.length) return null;
      const matched = activeRuns.find((row) => runInstallationId(row) && runInstallationId(row) === id);
      if (matched) return matched;
      if (state.devices.filter((d) => d.online).length === 1) {
        return activeRuns.find((row) => !runInstallationId(row)) || null;
      }
      return null;
    }

    function onlineDeviceIndex(device) {
      const id = String((device && device.installation_id) || "").trim();
      return state.devices.filter((d) => d.online).findIndex((d) => String(d.installation_id || "").trim() === id);
    }

    function unassignedActiveRunForDevice(device) {
      const online = state.devices.filter((d) => d.online);
      const index = onlineDeviceIndex(device);
      if (index < 0) return null;
      const rows = state.runs.filter((row) => isActiveRun(row) && !runInstallationId(row));
      return rows[index % Math.max(online.length, 1)] || null;
    }

    function activeMessageForDevice(device) {
      const id = String((device && device.installation_id) || "").trim();
      const rows = state.historyItems.filter((row) => {
        const msg = row && row.message ? row.message : row;
        const status = String((msg && msg.status) || "").toLowerCase();
        return status === "pending" || status === "processing";
      });
      if (!rows.length) return null;
      return rows.find((row) => {
        const msg = row && row.message ? row.message : row;
        return String(msg.claimed_by_installation_id || msg.installation_id || "").trim() === id;
      }) || unassignedActiveMessageForDevice(device);
    }

    function unassignedActiveMessageForDevice(device) {
      const online = state.devices.filter((d) => d.online);
      const index = onlineDeviceIndex(device);
      if (index < 0) return null;
      const rows = state.historyItems.filter((row) => {
        const msg = row && row.message ? row.message : row;
        const status = String((msg && msg.status) || "").toLowerCase();
        const owner = String(msg && (msg.claimed_by_installation_id || msg.installation_id) || "").trim();
        return (status === "pending" || status === "processing") && !owner;
      });
      return rows[index % Math.max(online.length, 1)] || null;
    }

    function employeeName(device, index) {
      return String((device && device.display_name) || "").trim() || "实习员工";
    }

    function stableHash(value) {
      const s = String(value || "");
      let h = 0;
      for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
      return Math.abs(h);
    }

    function employeeGender(device, index) {
      const seed = `${device && device.installation_id ? device.installation_id : ""}:${index}`;
      return stableHash(seed) % 2 === 0 ? "male" : "female";
    }

    function employeeAsset(device, index, mode) {
      const gender = employeeGender(device, index);
      const status = mode === "working" ? "working" : (mode === "offline" ? "offline" : "idle");
      return `/h5-static/h5-employee-${gender}-${status}.png`;
    }

    function idleBubbleForDevice(device, index) {
      const lines = ["等我一声，我马上开工", "今天也在认真待命", "我可以帮你做内容和任务"];
      const raw = `${device && device.installation_id ? device.installation_id : ""}:${index}`;
      let sum = 0;
      for (const ch of raw) sum += ch.charCodeAt(0);
      return lines[sum % lines.length];
    }

    function updateCompanySign() {
      const el = $("companySignText");
      if (el) el.textContent = state.companyName || "我的AI公司";
    }

    function employeeLayoutMetrics(total) {
      const mobile = window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
      const cols = mobile ? 2 : Math.min(4, Math.max(2, Math.ceil(Math.sqrt(Math.max(total, 1)))));
      const rowGap = mobile ? 192 : 190;
      const top = mobile ? 194 : 214;
      const rows = Math.max(1, Math.ceil(Math.max(total, 1) / cols));
      return { cols, rowGap, top, minHeight: Math.max(mobile ? 620 : 560, top + rows * rowGap + 90) };
    }

    function employeePositionStyle(index, total = 1) {
      const metrics = employeeLayoutMetrics(total);
      const col = index % metrics.cols;
      const row = Math.floor(index / metrics.cols);
      const jitter = (stableHash(`${index}:jitter`) % 11) - 5;
      const x = ((col + 0.5) / metrics.cols) * 100;
      const y = metrics.top + row * metrics.rowGap + jitter;
      const delay = -((index % 7) * 0.45).toFixed(2);
      return `--x:${x.toFixed(2)}%;--y:${y}px;--delay:${delay}s;`;
    }

    function deviceByInstallationId(installationId) {
      const id = String(installationId || "").trim();
      return state.devices.find((device) => String(device.installation_id || "").trim() === id) || null;
    }

    function messageRow(entry) {
      return entry && entry.message ? entry.message : entry;
    }

    function messageEvents(entry) {
      return entry && Array.isArray(entry.events) ? entry.events : [];
    }

    function progressTextFromEvent(ev) {
      if (!ev) return "";
      const p = ev.payload || {};
      if (ev.type === "queued") return "已进入云端队列";
      if (ev.type === "claimed") return "本地设备已领取";
      if (ev.type === "thinking") return p.text || "正在思考";
      if (ev.type === "tool_start") return p.name ? `开始调用：${p.name}` : "开始调用能力";
      if (ev.type === "tool_end") return p.name ? `${p.name} 已完成` : "能力调用完成";
      if (ev.type === "progress") return p.text || p.message || "处理中";
      if (ev.type === "publish_pending") return "等待发布";
      if (ev.type === "publish_claimed") return "开始发布";
      if (ev.type === "publish_result") return "发布完成";
      if (ev.type === "final") return "处理完成";
      if (ev.type === "error") return p.error || p.detail || "处理失败";
      return "";
    }

    function officeOutcomeStats() {
      const runs = Array.isArray(state.runs) ? state.runs : [];
      const messages = Array.isArray(state.historyItems) ? state.historyItems : [];
      let spent = 0;
      let harvest = 0;
      runs.forEach((row) => {
        const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
        spent += numericValue(
          row && (row.credits_used || row.credits_charged || row.price || row.cost_credits)
          || payload.credits_used || payload.credits_charged || payload.credits_final || payload.price || payload.cost_credits
        );
        harvest += collectMediaUrls(payload).length;
      });
      messages.forEach((entry) => {
        const msg = messageRow(entry) || {};
        spent += numericValue(msg.credits_used || msg.credits_charged || msg.price || msg.cost_credits);
        messageEvents(entry).forEach((ev) => {
          if (ev && (ev.type === "final" || ev.type === "publish_result")) harvest += collectMediaUrls(ev.payload || {}).length;
        });
      });
      return { spent, harvest };
    }

    function updateBossOfficeStats(onlineCount = 0, workingCount = 0) {
      const stats = officeOutcomeStats();
      if ($("bossCreditsSpent")) $("bossCreditsSpent").textContent = compactNumber(stats.spent, stats.spent > 0 && stats.spent < 10 ? 1 : 0);
      if ($("bossHarvestCount")) $("bossHarvestCount").textContent = compactNumber(stats.harvest);
      if ($("bossName")) $("bossName").textContent = "让AI员工24小时为我工作";
      const line = workingCount > 0
        ? `${workingCount} 位员工正在干活，老板盯着交付进度。`
        : (onlineCount > 0 ? `${onlineCount} 位员工已到岗，等待安排。` : "员工还没到岗，办公室先亮着灯。");
      if ($("bossOfficeLine")) $("bossOfficeLine").textContent = line;
    }

    function latestProgressTextForMessage(entry) {
      const events = messageEvents(entry);
      for (let i = events.length - 1; i >= 0; i -= 1) {
        const text = progressTextFromEvent(events[i]);
        if (text) return text;
      }
      const msg = messageRow(entry);
      if (msg && msg.status === "pending") return "任务提交中，等待本地设备领取";
      if (msg && msg.status === "processing") return "生成中，正在处理手机会话";
      return "";
    }

    function employeeSnapshot(device, index = 0) {
      const run = device && device.online ? (activeRunForDevice(device) || unassignedActiveRunForDevice(device)) : null;
      const msgEntry = device && device.online && !run ? activeMessageForDevice(device) : null;
      const msg = messageRow(msgEntry);
      const mode = !device || !device.online ? "offline" : (run || msg ? "working" : "idle");
      const label = mode === "offline" ? "下班了" : mode === "working" ? "工作中" : "发呆中";
      const detail = run
        ? (run.error || run.result_text || runProgressText(run) || run.content || "正在执行任务")
        : msg
          ? (msg.content || msg.reply_text || "正在处理手机会话消息")
          : mode === "idle"
            ? "在线待命，当前没有领取任务。"
            : `最近在线：${fmtTime(device && device.last_seen_at)}`;
      const title = run ? (run.title || capabilityName(taskCapabilityId(run))) : (msg ? "手机会话消息" : (mode === "idle" ? "等待新工作" : "已下班"));
      const started = run ? (run.started_at || run.claimed_at || run.created_at) : (msg ? (msg.claimed_at || msg.created_at) : (device && device.last_seen_at));
      const bubble = mode === "working"
        ? (run
          ? runProgressText(run)
          : (latestProgressTextForMessage(msgEntry) || "生成中，正在处理手机会话"))
        : (mode === "idle" ? idleBubbleForDevice(device, index) : "");
      const steps = [];
      steps.push(["状态", label]);
      if (mode === "working") {
        steps.push(["当前", title]);
        steps.push(["进展", detail]);
        steps.push(["时间", fmtTime(started)]);
      } else if (mode === "idle") {
        steps.push(["当前", "没有进行中的任务"]);
        steps.push(["待命", "可以从安排工作或手机会话继续下发工作"]);
      } else {
        steps.push(["当前", "设备不在线"]);
        steps.push(["最近", fmtTime(device && device.last_seen_at)]);
      }
      return { mode, label, title, detail, started, steps, run, msg, msgEntry, bubble };
    }

    function deviceSnapshot(device) {
      const index = state.devices.indexOf(device);
      return employeeSnapshot(device, index >= 0 ? index : 0);
    }

    function deviceSortRank(device) {
      const snapshot = deviceSnapshot(device);
      if (snapshot.mode === "working") return 0;
      if (snapshot.mode === "idle") return 1;
      return 2;
    }

    function sortDevicesForOffice(devices) {
      return (devices || []).slice().sort((a, b) => {
        const ar = deviceSortRank(a);
        const br = deviceSortRank(b);
        if (ar !== br) return ar - br;
        return itemTimeMs(b && b.last_seen_at) - itemTimeMs(a && a.last_seen_at);
      });
    }

    function filteredOfficeDevices(devices) {
      const filter = state.officeDeviceFilter || "all";
      const sorted = sortDevicesForOffice(devices);
      if (filter === "all") return sorted;
      return sorted.filter((device) => deviceSnapshot(device).mode === filter);
    }

    function officeFilterLabel(filter) {
      return {
        working: "工作中的员工",
        idle: "空闲待命员工",
        offline: "下班离线员工",
      }[filter] || "";
    }

    function setOfficeDeviceFilter(filter) {
      const next = ["working", "idle", "offline"].includes(filter) ? filter : "all";
      state.officeDeviceFilter = state.officeDeviceFilter === next ? "all" : next;
      state.officePage = 1;
      renderOfficeEmployees();
    }

    function openEmployeeModal(installationId) {
      const device = deviceByInstallationId(installationId);
      if (!device) return;
      const snapshot = employeeSnapshot(device, state.devices.indexOf(device));
      const modal = $("employeeModal");
      modal.dataset.mode = snapshot.mode;
      modal.dataset.employeeId = device.installation_id || "";
      $("employeeModalAvatar").textContent = firstChar(employeeName(device, 0));
      $("employeeModalTitle").textContent = employeeName(device, state.devices.indexOf(device));
      $("employeeModalSub").textContent = device.installation_id ? `设备 ${shortId(device.installation_id)}` : "未绑定设备 ID";
      $("employeeModalBadge").textContent = snapshot.label;
      $("employeeTaskSteps").innerHTML = snapshot.steps.map(([label, value]) => `<div class="employee-task-step"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "-")}</strong></div>`).join("");
      $("employeeWorkbenchBtn").disabled = snapshot.mode === "offline";
      modal.classList.remove("hidden");
    }

    function closeEmployeeModal() {
      const modal = $("employeeModal");
      modal.classList.add("hidden");
      modal.dataset.employeeId = "";
    }

    function departmentById(id) {
      const wanted = String(id || "").trim();
      return DEPARTMENT_SKILL_TREE.find((dept) => dept.id === wanted) || DEPARTMENT_SKILL_TREE[0];
    }

    function eachAbilityNode(nodes, department, trail, visit) {
      (nodes || []).forEach((node) => {
        const nextTrail = [...trail, node];
        visit(node, department, nextTrail);
        if (node.children && node.children.length) eachAbilityNode(node.children, department, nextTrail, visit);
      });
    }

    function abilityLookup(key) {
      const wanted = String(key || "").trim();
      let found = null;
      DEPARTMENT_SKILL_TREE.forEach((department) => {
        if (found) return;
        eachAbilityNode(department.children, department, [], (node, dept, trail) => {
          if (!found && String(node.key || "") === wanted) found = { node, department: dept, trail };
        });
      });
      return found;
    }

    function activeAbilityLookup() {
      return abilityLookup(state.currentAbilityKey || "");
    }

    function abilityChildCount(node) {
      return (node && Array.isArray(node.children)) ? node.children.length : 0;
    }

    function isPublishCenterNode(node) {
      if (!node) return false;
      const keys = [
        node.key,
        node.workQuickKey,
        node.workflowAction,
        node.routeTab,
      ].map((value) => String(value || "").trim());
      return keys.includes("publish_center") || keys.includes("publish_content") || keys.some((key) => key.endsWith("_publish_center"));
    }

    function abilityLeafNodes(nodes) {
      const leaves = [];
      const visit = (node) => {
        if (!node) return;
        if (isPublishCenterNode(node)) return;
        if (Array.isArray(node.children) && node.children.length) {
          node.children.forEach(visit);
          return;
        }
        leaves.push(node);
      };
      (nodes || []).forEach(visit);
      return leaves;
    }

    function departmentLeafNodes(department) {
      return abilityLeafNodes((department && department.children) || []);
    }

    function officeDepartments() {
      return DEPARTMENT_SKILL_TREE;
    }

    function departmentAvailableLeafCount(department) {
      return departmentLeafNodes(department).filter((node) => node && !node.comingSoon).length;
    }

    function workflowOptionValue(lookup) {
      if (lookup && lookup.optionId != null) return `sales@@${lookup.optionId}`;
      return `${lookup.department.id}@@${lookup.node.key || ""}`;
    }

    function workflowSalesNodeLookups() {
      return SALES_WORKFLOW_NODE_OPTIONS.map((item, index) => {
        const lookup = abilityLookup(item.key);
        if (!lookup || !lookup.node || lookup.node.comingSoon || isPublishCenterNode(lookup.node)) return null;
        return {
          ...lookup,
          optionId: index,
          optionLabel: item.label,
          defaultNote: item.note,
        };
      }).filter(Boolean);
    }

    function workflowLeafLookups() {
      return workflowSalesNodeLookups();
    }

    function workflowLookupFromValue(value) {
      const raw = String(value || "");
      if (raw.startsWith("sales@@")) {
        const index = Number(raw.split("@@")[1]);
        return workflowSalesNodeLookups().find((item) => Number(item.optionId) === index) || null;
      }
      const [departmentId, key] = raw.split("@@");
      return workflowLeafLookups().find((item) => item.department.id === departmentId && String(item.node.key || "") === key) || null;
    }

    function workflowAbilityOptionsHtml() {
      const options = workflowLeafLookups().map((lookup) => {
        const disabled = abilityIsActionable(lookup.node) ? "" : " disabled";
        const label = lookup.optionLabel || lookup.node.label || lookup.node.key;
        return `<option value="${escapeHtml(workflowOptionValue(lookup))}"${disabled}>${escapeHtml(label)}</option>`;
      }).join("");
      return `<optgroup label="销售员工">${options}</optgroup>`;
    }

    function workflowPrompt(note, node) {
      return String(note || "").trim() || `执行${(node && (node.label || node.key)) || "任务"}`;
    }

    function workflowParamValue(id) {
      const el = $(id);
      return ((el && el.value) || "").trim();
    }

    function workflowParamChecked(id) {
      const el = $(id);
      return !!(el && el.checked);
    }

    function workflowParamNumber(id, fallback, min, max) {
      return workNumber(workflowParamValue(id), fallback, min, max);
    }

    function workflowParamSplitList(id) {
      return splitTextareaList(workflowParamValue(id));
    }

    function workflowIpDailyTaskOptionsHtml() {
      return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;">${IP_DAILY_TASK_OPTIONS.map((item) => `
        <label class="task-checkbox" style="min-height:38px;padding:0 10px;border:1px solid rgba(15,23,42,.08);border-radius:10px;background:rgba(255,255,255,.72);">
          <input type="checkbox" data-workflow-ip-daily-task="${escapeHtml(item.value)}" checked>
          <span>${escapeHtml(item.label)}</span>
        </label>
      `).join("")}</div>`;
    }

    function selectedWorkflowIpDailyTasks() {
      return Array.from(document.querySelectorAll("[data-workflow-ip-daily-task]"))
        .filter((el) => el.checked)
        .map((el) => String(el.getAttribute("data-workflow-ip-daily-task") || "").trim())
        .filter(Boolean);
    }

    function workflowSocialFieldsHtml(platform) {
      const sourceLabel = platform === "reddit" ? "社区" : "来源关键词";
      const sourcePlaceholder = platform === "reddit" ? "例如：Entrepreneur、marketing、SaaS" : "例如：AI agent、marketing automation、lead generation";
      return taskFieldHtml("任务名称", workInputHtml("workflowParamLeadTitle", "text", `${socialPlatformLabel(platform)}线索采集`))
        + taskFieldHtml("精准用户方向", taskTextareaHtml("workflowParamLeadKeywords", "要筛选的精准用户方向"), true)
        + taskFieldHtml("采集方式", taskSelectHtml("workflowParamLeadMode", optionHtml("source", sourceLabel) + optionHtml("account", "账号")))
        + taskFieldHtml(sourceLabel, taskTextareaHtml("workflowParamLeadSources", sourcePlaceholder), true)
        + taskFieldHtml("账号", taskTextareaHtml("workflowParamLeadAccounts", platform === "reddit" ? "例如：u/example 或 example" : "例如：@example 或主页链接"), true)
        + taskFieldHtml("采集上限", workInputHtml("workflowParamLeadMaxItems", "number", "100", 'min="1" max="100"'));
    }

    function workflowLinkedinFieldsHtml() {
      return taskFieldHtml("任务名称", workInputHtml("workflowParamLinkedinTitle", "text", "LinkedIn线索挖掘"))
        + taskFieldHtml("目标画像", taskTextareaHtml("workflowParamLinkedinTarget", "例如：跨境电商老板、AI工具采购负责人、营销负责人"), true)
        + taskFieldHtml("个人主页", taskTextareaHtml("workflowParamLinkedinProfiles", "LinkedIn个人主页链接，可多行"), true)
        + taskFieldHtml("公司主页", taskTextareaHtml("workflowParamLinkedinCompanies", "LinkedIn公司主页链接，可多行"), true)
        + taskFieldHtml("关键词", taskTextareaHtml("workflowParamLinkedinKeywords", "例如：AI marketing、automation、lead generation"), true)
        + taskFieldHtml("话题标签", taskTextareaHtml("workflowParamLinkedinHashtags", "例如：ai、marketing、startup"), true)
        + taskFieldHtml("人数上限", workInputHtml("workflowParamLinkedinMaxPeople", "number", "30", 'min="5" max="80"'));
    }

    function workflowWechatTranscriptFieldsHtml() {
      return taskFieldHtml("视频号账号 / 链接 / 关键词", taskTextareaHtml("workflowParamWechatQuery", "填写视频号账号、sph开头ID、视频详情链接，或搜索关键词"), true)
        + taskFieldHtml("拉取页数", workInputHtml("workflowParamWechatPages", "number", "1", 'min="1" max="20"'))
        + taskFieldHtml("最多转写视频数", workInputHtml("workflowParamWechatLimit", "number", "10", 'min="1" max="50"'));
    }

    function workflowCapabilityFieldsHtml(capabilityId) {
      const id = String(capabilityId || "").trim();
      if (id === "ip_content_daily") {
        return taskFieldHtml("模板", ipTemplateSelectControl("workflowParamIpTemplate"))
          + taskFieldHtml("生成内容", workflowIpDailyTaskOptionsHtml(), true)
          + taskFieldHtml("执行前同步", `<label class="task-checkbox"><input id="workflowParamIpSyncBefore" type="checkbox" checked>每次执行前同步新数据</label>`, true)
          + taskFieldHtml("补充要求", taskTextareaHtml("workflowParamIpRequirement", "可选"), true);
      }
      if (id === "goal.image.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("workflowParamImageTitle", "text", "创作图片"))
          + taskFieldHtml("图片需求", taskTextareaHtml("workflowParamImagePrompt", "图片生成要求"), true);
      }
      if (id === "goal.video.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("workflowParamVideoTitle", "text", "创意视频"))
          + taskFieldHtml("生成模式", taskSelectHtml("workflowParamVideoMode", optionHtml("single_asset", "单个素材生成视频") + optionHtml("memory_image", "根据记忆先生成图片") + optionHtml("asset_group", "素材分组轮换生成")))
          + `<div class="field full" id="workflowParamVideoAssetField"><label>素材图片</label>${assetPickerControlHtml("workflowParamVideoAsset", { mediaType: "image", output: "url", uploadText: "上传图片", selectText: "选择已上传图片" })}</div>`
          + `<div class="field full hidden" id="workflowParamVideoMemoryField"><label>记忆文件</label>${videoMemorySelectControl("workflowParamVideoMemoryDocs")}</div>`
          + `<div class="field hidden" id="workflowParamVideoCandidateGroupField"><label>素材分组</label>${taskSelectHtml("workflowParamVideoCandidateGroup", optionHtml("", "不选择"))}</div>`
          + taskFieldHtml("补充提示词", taskTextareaHtml("workflowParamVideoPrompt", "可选"), true);
      }
      if (id === "hifly.video.create_by_tts") {
        return taskFieldHtml("数字人", taskSelectHtml("workflowParamAvatar", optionHtml("", "加载中...")))
          + taskFieldHtml("声音", taskSelectHtml("workflowParamVoice", optionHtml("", "加载中...")))
          + taskFieldHtml("任务名称", workInputHtml("workflowParamHiflyTitle", "text", "数字人口播"))
          + taskFieldHtml("口播文案", taskTextareaHtml("workflowParamHiflyScript", "填写完整口播文案"), true);
      }
      if (id === "comfly.daihuo.pipeline") {
        return taskFieldHtml("参考图片", assetPickerControlHtml("workflowParamComflyAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }), true)
          + taskFieldHtml("视频要求", taskTextareaHtml("workflowParamComflyText", "视频生成要求"), true)
          + taskFieldHtml("分镜数量", workInputHtml("workflowParamComflyStoryboardCount", "number", "5", 'min="1" max="8"'))
          + taskFieldHtml("自动入库", workCheckboxHtml("workflowParamComflyAutoSave", "完成后保存到素材库", true));
      }
      if (id === "comfly.seedance.tvc.pipeline") {
        return taskFieldHtml("参考图片", assetPickerControlHtml("workflowParamSeedanceAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }), true)
          + taskFieldHtml("视频要求", taskTextareaHtml("workflowParamSeedanceText", "连续分镜和视频要求"), true)
          + taskFieldHtml("总时长", taskSelectHtml("workflowParamSeedanceDuration", [10,20,30,40,50,60].map((n) => optionHtml(String(n), `${n} 秒`)).join("")))
          + taskFieldHtml("画幅", taskSelectHtml("workflowParamSeedanceAspect", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏")));
      }
      if (id === "create.video.pipeline") {
        return taskFieldHtml("视频主题", taskTextareaHtml("workflowParamCreateVideoPrompt", "视频主题和要求"), true)
          + taskFieldHtml("时长秒数", workInputHtml("workflowParamCreateVideoDuration", "number", "8", 'min="3" max="60"'))
          + taskFieldHtml("分镜数量", workInputHtml("workflowParamCreateVideoSceneCount", "number", "1", 'min="1" max="6"'))
          + taskFieldHtml("画幅", taskSelectHtml("workflowParamCreateVideoAspect", optionHtml("16:9", "16:9 横屏") + optionHtml("9:16", "9:16 竖屏") + optionHtml("1:1", "1:1 方图")));
      }
      if (id === "wewrite.article.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("workflowParamArticleTitle", "text", "公众号文章"))
          + taskFieldHtml("公众号主题", taskTextareaHtml("workflowParamArticleIdea", "文章主题、受众、核心观点"), true)
          + taskFieldHtml("文章风格", workInputHtml("workflowParamArticleStyle", "text", "", 'placeholder="例如：专业、有案例、适合老板阅读"'))
          + taskFieldHtml("配图数量", workInputHtml("workflowParamArticleImageCount", "number", "3", 'min="0" max="6"'))
          + taskFieldHtml("自动配图", workCheckboxHtml("workflowParamArticleIncludeImages", "生成 16:9 横屏配图并插入", true), true);
      }
      if (id === "ppt.create") {
        return taskFieldHtml("任务名称", workInputHtml("workflowParamPptTitle", "text", "PPT生成"))
          + taskFieldHtml("PPT主题", taskTextareaHtml("workflowParamPptTopic", "PPT主题、用途、受众"), true)
          + taskFieldHtml("页数", workInputHtml("workflowParamPptSlideCount", "number", "10", 'min="1" max="80"'))
          + taskFieldHtml("风格要求", workInputHtml("workflowParamPptInstructions", "text", "", 'placeholder="例如：科技感、适合招商、案例更具体"'))
          + taskFieldHtml("生成模式", taskSelectHtml("workflowParamPptMode", optionHtml("ai", "AI视觉页") + optionHtml("outline", "结构化大纲")));
      }
      if (id === "comfly.ecommerce.detail_pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("workflowParamEcommerceTitle", "text", "电商详情页"))
          + taskFieldHtml("商品主图", assetPickerControlHtml("workflowParamEcommerceAsset", { mediaType: "image", output: "url", uploadText: "上传主图" }), true)
          + taskFieldHtml("详情页要求", taskTextareaHtml("workflowParamEcommerceText", "突出材质、卖点、使用场景和购买理由"), true)
          + taskFieldHtml("页面数量", workInputHtml("workflowParamEcommercePageCount", "number", "12", 'min="1" max="20"'))
          + taskFieldHtml("自动入库", workCheckboxHtml("workflowParamEcommerceAutoSave", "完成后保存到素材库", true));
      }
      return taskFieldHtml("任务名称", workInputHtml("workflowParamGenericTitle", "text", capabilityName(id) || "能力任务"))
        + taskFieldHtml("任务要求", taskTextareaHtml("workflowParamGenericPrompt", "填写要执行的任务参数和要求"), true);
    }

    function workflowQuickFieldsHtml(item) {
      const key = String(item && item.key || "");
      if (key === "image_composer_studio") return workflowCapabilityFieldsHtml("goal.image.pipeline");
      if (key === "comfly.seedance.tvc.pipeline") return workflowCapabilityFieldsHtml("comfly.seedance.tvc.pipeline");
      if (key === "comfly.daihuo.pipeline") return workflowCapabilityFieldsHtml("comfly.daihuo.pipeline");
      if (key === "hifly.video.create_by_tts") return workflowCapabilityFieldsHtml("hifly.video.create_by_tts");
      if (key === "douyin_leads") {
        return taskFieldHtml("采集关键词", taskTextareaHtml("workflowParamDouyinKeyword", "例如：深圳装修、口腔种植、母婴门店"), true)
          + taskFieldHtml("地区", workInputHtml("workflowParamDouyinRegions", "text", "全国", 'placeholder="全国，或用逗号分隔多个城市"'))
          + taskFieldHtml("搜索数量", workInputHtml("workflowParamDouyinMaxResults", "number", "50", 'min="10" max="100"'))
          + taskFieldHtml("搜索方式", taskSelectHtml("workflowParamDouyinMode", optionHtml("script", "浏览器脚本") + optionHtml("api", "接口模式")));
      }
      if (key === "local_bestseller") {
        return taskFieldHtml("生成方式", taskSelectHtml("workflowParamLocalMode", optionHtml("plan", "先生成 30 天内容方案") + optionHtml("scene_batch", "直接批量生成场景图")))
          + taskFieldHtml("天数", workInputHtml("workflowParamLocalDays", "number", "30", 'min="1" max="30"'))
          + taskFieldHtml("姓名", workInputHtml("workflowParamLocalName", "text", "", 'placeholder="真实姓名，可选"'))
          + taskFieldHtml("短视频昵称", workInputHtml("workflowParamLocalNickname", "text", "", 'placeholder="不填则使用姓名或“我”"'))
          + taskFieldHtml("性别", taskSelectHtml("workflowParamLocalGender", optionHtml("female", "女") + optionHtml("male", "男")))
          + taskFieldHtml("人设身份", workInputHtml("workflowParamLocalIdentity", "text", "女老板"))
          + taskFieldHtml("行业/赛道", workInputHtml("workflowParamLocalIndustry", "text", "大健康"))
          + taskFieldHtml("城市", workInputHtml("workflowParamLocalCity", "text", "深圳"))
          + taskFieldHtml("省份", workInputHtml("workflowParamLocalProvince", "text", "广东"))
          + taskFieldHtml("人物照片", assetPickerControlHtml("workflowParamLocalPhoto", { mediaType: "image", output: "url", uploadText: "从相册上传" }), true);
      }
      if (key === "viral_video_remix") {
        return taskFieldHtml("参考视频", assetPickerControlHtml("workflowParamViralVideoUrl", { mediaType: "video", output: "url", accept: "video/*", uploadText: "上传视频" }), true)
          + taskFieldHtml("人物参考图", assetPickerControlHtml("workflowParamViralCharacterUrl", { mediaType: "image", output: "url", uploadText: "上传人物图" }))
          + taskFieldHtml("产品参考图", assetPickerControlHtml("workflowParamViralProductUrl", { mediaType: "image", output: "url", uploadText: "上传产品图" }))
          + taskFieldHtml("复刻要求", taskTextareaHtml("workflowParamViralPrompt", "复刻要求"), true)
          + taskFieldHtml("分段时长", taskSelectHtml("workflowParamViralDuration", optionHtml("10", "10 秒/段") + optionHtml("5", "5 秒/段")))
          + taskFieldHtml("画幅", taskSelectHtml("workflowParamViralRatio", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏") + optionHtml("1:1", "1:1 方图")))
          + taskFieldHtml("音频", workCheckboxHtml("workflowParamViralGenerateAudio", "生成音频", true));
      }
      if (key === "wecom_reply") {
        return taskFieldHtml("执行动作", taskSelectHtml("workflowParamWecomAction", optionHtml("poll_reply", "拉取待处理消息并自动回复一次")))
          + taskFieldHtml("备注", taskTextareaHtml("workflowParamWecomNote", "可选"), true);
      }
      return workflowCapabilityFieldsHtml(item && (item.capabilityId || item.key));
    }

    function workflowFieldsHtmlForNode(node) {
      if (!node) return "";
      const platform = socialPlatformFromAbilityKey(node.key);
      if (platform) return workflowSocialFieldsHtml(platform);
      if (node.key === "linkedin_leads") return workflowLinkedinFieldsHtml();
      if (node.key === "wechat_channels_transcript") return workflowWechatTranscriptFieldsHtml();
      if (node.workQuickKey) return workflowQuickFieldsHtml(workQuickItemByKey(node.workQuickKey) || node);
      if (node.capabilityId || node.serverTask) return workflowCapabilityFieldsHtml(node.capabilityId || node.key);
      if (node.routeTab) return `<div class="quick-empty">这个节点是页面入口，不能加入定时工作流。</div>`;
      return workflowCapabilityFieldsHtml(node.key);
    }

    function bindWorkflowGoalVideoModeControls() {
      const mode = workflowParamValue("workflowParamVideoMode") || "single_asset";
      $("workflowParamVideoAssetField")?.classList.toggle("hidden", mode !== "single_asset");
      $("workflowParamVideoMemoryField")?.classList.toggle("hidden", mode !== "memory_image");
      $("workflowParamVideoCandidateGroupField")?.classList.toggle("hidden", mode !== "asset_group");
      const sel = $("workflowParamVideoMode");
      if (sel && !sel.dataset.workflowVideoModeBound) {
        sel.dataset.workflowVideoModeBound = "1";
        sel.addEventListener("change", bindWorkflowGoalVideoModeControls);
      }
    }

    function initWorkflowParamControls(node) {
      const modal = $("workflowParamModal");
      if (!modal) return;
      initAssetPickerControls(modal);
      bindWorkflowGoalVideoModeControls();
      if ($("workflowParamVideoCandidateGroup")) {
        fillCandidateGroupSelect();
        loadCandidateGroups();
      }
      if ($("workflowParamVideoMemoryDocs")) {
        fillVideoMemorySelects();
        loadVideoMemoryDocsForSelect();
      }
      if ($("workflowParamIpTemplate")) loadIpTemplates(true);
      if ($("workflowParamAvatar") || $("workflowParamVoice")) {
        renderWorkHiflyOptions();
        loadHiflyLibraries();
      }
    }

    function workflowLookupForNode(node) {
      const value = `${node && node.department_id || ""}@@${node && node.ability_key || ""}`;
      return workflowLookupFromValue(value) || workflowLeafLookups().find((item) => String(item.node.key || "") === String(node && node.ability_key || "")) || null;
    }

    function collectWorkflowSocialLeadsPayload(platform) {
      const keywords = workflowParamSplitList("workflowParamLeadKeywords");
      if (!keywords.length) throw new Error("请填写精准用户方向");
      const mode = workflowParamValue("workflowParamLeadMode") || "source";
      const accounts = workflowParamSplitList("workflowParamLeadAccounts");
      const sources = workflowParamSplitList("workflowParamLeadSources");
      const payload = {
        platform,
        title: workflowParamValue("workflowParamLeadTitle") || `${socialPlatformLabel(platform)}线索采集`,
        keywords,
        max_items: workflowParamNumber("workflowParamLeadMaxItems", 100, 1, 100),
        include_comments: true,
        include_account_posts: true,
        auto_run: true,
      };
      if (mode === "account") {
        if (!accounts.length) throw new Error("请填写账号");
        payload.accounts = accounts;
      } else if (platform === "reddit") {
        if (!sources.length) throw new Error("请填写社区");
        payload.communities = sources;
      } else {
        if (!sources.length) throw new Error("请填写来源关键词");
        payload.source_keywords = sources;
      }
      return payload;
    }

    function collectWorkflowLinkedinPayload(node) {
      const payload = {
        title: workflowParamValue("workflowParamLinkedinTitle") || (node && node.label) || "LinkedIn线索采集",
        target_profile: workflowParamValue("workflowParamLinkedinTarget"),
        seed_profile_urls: workflowParamSplitList("workflowParamLinkedinProfiles"),
        seed_company_urls: workflowParamSplitList("workflowParamLinkedinCompanies"),
        keywords: workflowParamSplitList("workflowParamLinkedinKeywords"),
        hashtags: workflowParamSplitList("workflowParamLinkedinHashtags"),
        max_people: workflowParamNumber("workflowParamLinkedinMaxPeople", 30, 5, 80),
        auto_run: true,
      };
      if (!payload.seed_profile_urls.length && !payload.seed_company_urls.length && !payload.keywords.length && !payload.hashtags.length) {
        throw new Error("请至少填写个人主页、公司主页、关键词或话题");
      }
      return payload;
    }

    function collectWorkflowWechatPayload(node) {
      const query = workflowParamValue("workflowParamWechatQuery");
      if (!query) throw new Error("请填写视频号账号、链接或关键词");
      return {
        title: (node && node.label) || "视频号文案提取",
        query,
        max_pages: workflowParamNumber("workflowParamWechatPages", 1, 1, 20),
        limit: workflowParamNumber("workflowParamWechatLimit", 10, 1, 50),
        page_size: 20,
      };
    }

    function collectWorkflowCapabilityPlan(node) {
      const capabilityId = String((node && (node.capabilityId || node.key)) || "").trim();
      if (capabilityId === "ip_content_daily") {
        const templateId = parseInt(workflowParamValue("workflowParamIpTemplate") || "0", 10);
        if (!templateId || Number.isNaN(templateId)) throw new Error("请选择 IP日更服务器模板");
        const tasks = selectedWorkflowIpDailyTasks();
        if (!tasks.length) throw new Error("请选择至少一种生成内容");
        const extra = workflowParamValue("workflowParamIpRequirement");
        return {
          title: node.label || "IP日更文案",
          task_kind: "ip_content_daily",
          content: "H5 工作流：IP日更文案",
          payload: {
            template_id: templateId,
            tasks,
            sync_before: workflowParamChecked("workflowParamIpSyncBefore"),
            requirements: extra ? { common: extra, oral: extra, moments: extra, image: extra } : {},
            industry_count: 5,
            ip_count: 5,
            moments_count: 20,
          },
        };
      }
      if (capabilityId === "goal.image.pipeline") {
        const prompt = workflowParamValue("workflowParamImagePrompt");
        if (!prompt) throw new Error("请填写图片需求");
        return {
          title: workflowParamValue("workflowParamImageTitle") || node.label || "创作图片",
          task_kind: "capability",
          content: "H5 工作流：创作图片",
          payload: { capability_id: "goal.image.pipeline", payload: { prompt } },
        };
      }
      if (capabilityId === "goal.video.pipeline") {
        const payload = collectGoalVideoPayloadFromFields({
          modeId: "workflowParamVideoMode",
          assetId: "workflowParamVideoAsset",
          memoryId: "workflowParamVideoMemoryDocs",
          groupId: "workflowParamVideoCandidateGroup",
          promptId: "workflowParamVideoPrompt",
        });
        return {
          title: workflowParamValue("workflowParamVideoTitle") || node.label || "创意视频",
          task_kind: "capability",
          content: "H5 工作流：创意视频",
          payload: { capability_id: "goal.video.pipeline", payload },
        };
      }
      if (capabilityId === "hifly.video.create_by_tts") {
        const avatar = workflowParamValue("workflowParamAvatar");
        const voice = workflowParamValue("workflowParamVoice");
        const script = workflowParamValue("workflowParamHiflyScript");
        if (!avatar) throw new Error("请选择数字人");
        if (!voice) throw new Error("请选择声音");
        if (!script) throw new Error("请填写口播文案");
        return {
          title: workflowParamValue("workflowParamHiflyTitle") || node.label || "数字人口播",
          task_kind: "capability",
          content: "H5 工作流：数字人口播",
          payload: { capability_id: "hifly.video.create_by_tts", payload: { avatar, voice, script, prompt: script } },
        };
      }
      if (capabilityId === "comfly.daihuo.pipeline") {
        const asset = assetOrImagePayload(workflowParamValue("workflowParamComflyAsset"), "参考图片");
        return {
          title: node.label || "爆款TVC",
          task_kind: "capability",
          content: "H5 工作流：爆款TVC",
          payload: {
            capability_id: "comfly.daihuo.pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: workflowParamValue("workflowParamComflyText"),
              storyboard_count: workflowParamNumber("workflowParamComflyStoryboardCount", 5, 1, 8),
              auto_save: workflowParamChecked("workflowParamComflyAutoSave"),
            },
          },
        };
      }
      if (capabilityId === "comfly.seedance.tvc.pipeline") {
        const asset = assetOrImagePayload(workflowParamValue("workflowParamSeedanceAsset"), "参考图片");
        return {
          title: node.label || "创意分镜头视频",
          task_kind: "capability",
          content: "H5 工作流：创意分镜头视频",
          payload: {
            capability_id: "comfly.seedance.tvc.pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: workflowParamValue("workflowParamSeedanceText"),
              total_duration_seconds: workflowParamNumber("workflowParamSeedanceDuration", 20, 5, 120),
              aspect_ratio: workflowParamValue("workflowParamSeedanceAspect") || "9:16",
              auto_save: true,
            },
          },
        };
      }
      if (capabilityId === "create.video.pipeline") {
        const prompt = workflowParamValue("workflowParamCreateVideoPrompt");
        if (!prompt) throw new Error("请填写视频主题");
        return {
          title: node.label || "速推视频制作",
          task_kind: "capability",
          content: "H5 工作流：速推视频制作",
          payload: {
            capability_id: "create.video.pipeline",
            payload: {
              action: "start_pipeline",
              prompt,
              duration: workflowParamNumber("workflowParamCreateVideoDuration", 8, 3, 60),
              scene_count: workflowParamNumber("workflowParamCreateVideoSceneCount", 1, 1, 6),
              aspect_ratio: workflowParamValue("workflowParamCreateVideoAspect") || "16:9",
            },
          },
        };
      }
      if (capabilityId === "wewrite.article.pipeline") {
        const idea = workflowParamValue("workflowParamArticleIdea");
        if (!idea) throw new Error("请填写公众号主题");
        return {
          title: workflowParamValue("workflowParamArticleTitle") || node.label || "公众号文章",
          task_kind: "capability",
          content: "H5 工作流：公众号文章",
          payload: {
            capability_id: "wewrite.article.pipeline",
            payload: {
              idea,
              style: workflowParamValue("workflowParamArticleStyle"),
              include_images: workflowParamChecked("workflowParamArticleIncludeImages"),
              image_count: workflowParamNumber("workflowParamArticleImageCount", 3, 0, 6),
              image_aspect_ratio: "16:9",
            },
          },
        };
      }
      if (capabilityId === "ppt.create") {
        const topic = workflowParamValue("workflowParamPptTopic");
        if (!topic) throw new Error("请填写 PPT 主题");
        return {
          title: workflowParamValue("workflowParamPptTitle") || node.label || "PPT生成",
          task_kind: "capability",
          content: "H5 工作流：PPT生成",
          payload: {
            capability_id: "ppt.create",
            payload: {
              mode: workflowParamValue("workflowParamPptMode") || "ai",
              topic,
              slide_count: workflowParamNumber("workflowParamPptSlideCount", 10, 1, 80),
              instructions: workflowParamValue("workflowParamPptInstructions"),
              language: "zh-CN",
            },
          },
        };
      }
      if (capabilityId === "comfly.ecommerce.detail_pipeline") {
        const asset = assetOrImagePayload(workflowParamValue("workflowParamEcommerceAsset"), "商品主图");
        return {
          title: workflowParamValue("workflowParamEcommerceTitle") || node.label || "电商详情页",
          task_kind: "capability",
          content: "H5 工作流：电商详情页",
          payload: {
            capability_id: "comfly.ecommerce.detail_pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: workflowParamValue("workflowParamEcommerceText"),
              page_count: workflowParamNumber("workflowParamEcommercePageCount", 12, 1, 20),
              auto_save: workflowParamChecked("workflowParamEcommerceAutoSave"),
            },
          },
        };
      }
      const prompt = workflowParamValue("workflowParamGenericPrompt");
      if (!prompt) throw new Error("请填写任务要求");
      return {
        title: workflowParamValue("workflowParamGenericTitle") || node.label || capabilityName(capabilityId) || "能力任务",
        task_kind: "capability",
        content: `H5 工作流：${node.label || capabilityId}`,
        payload: { capability_id: capabilityId, payload: { prompt, task_text: prompt } },
      };
    }

    function collectWorkflowQuickPlan(quick) {
      const key = String(quick && quick.key || "");
      if (key === "image_composer_studio") return collectWorkflowCapabilityPlan({ ...quick, key: "goal.image.pipeline", capabilityId: "goal.image.pipeline", label: quick.label || "创作图片" });
      if (key === "comfly.seedance.tvc.pipeline") return collectWorkflowCapabilityPlan({ ...quick, capabilityId: "comfly.seedance.tvc.pipeline", label: quick.label || "创意分镜头视频" });
      if (key === "comfly.daihuo.pipeline") return collectWorkflowCapabilityPlan({ ...quick, capabilityId: "comfly.daihuo.pipeline", label: quick.label || "爆款TVC" });
      if (key === "hifly.video.create_by_tts") return collectWorkflowCapabilityPlan({ ...quick, capabilityId: "hifly.video.create_by_tts", label: quick.label || "数字人口播" });
      if (key === "douyin_leads") {
        const keyword = workflowParamValue("workflowParamDouyinKeyword");
        if (!keyword) throw new Error("请填写采集关键词");
        const regions = workSplitList(workflowParamValue("workflowParamDouyinRegions"));
        return {
          title: `抖音获客 - ${keyword.slice(0, 24)}`,
          task_kind: "douyin_leads",
          content: "H5 工作流：抖音获客",
          payload: {
            action: "search_collect",
            params: {
              keyword,
              max_results: workflowParamNumber("workflowParamDouyinMaxResults", 50, 10, 100),
              regions: regions.length ? regions : ["全国"],
              mode: workflowParamValue("workflowParamDouyinMode") || "script",
            },
          },
        };
      }
      if (key === "local_bestseller") {
        const photo = workflowParamValue("workflowParamLocalPhoto");
        const profile = {
          name: workflowParamValue("workflowParamLocalName"),
          nickname: workflowParamValue("workflowParamLocalNickname"),
          gender: workflowParamValue("workflowParamLocalGender") || "female",
          identity: workflowParamValue("workflowParamLocalIdentity") || "女老板",
          industry: workflowParamValue("workflowParamLocalIndustry") || "大健康",
          city: workflowParamValue("workflowParamLocalCity") || "深圳",
          province: workflowParamValue("workflowParamLocalProvince") || "广东",
        };
        if (/^https?:\/\//i.test(photo)) profile.photo_url = photo;
        else if (photo) profile.photo_asset_id = photo;
        const mode = workflowParamValue("workflowParamLocalMode") || "plan";
        return {
          title: `同城爆款 - ${profile.city || "本地"}`,
          task_kind: "client_workflow",
          content: "H5 工作流：同城爆款",
          payload: {
            action: mode === "scene_batch" ? "local_bestseller_scene_batch" : "local_bestseller_plan",
            params: { profile, days: workflowParamNumber("workflowParamLocalDays", 30, 1, 30) },
          },
        };
      }
      if (key === "viral_video_remix") {
        const originalVideoUrl = workflowParamValue("workflowParamViralVideoUrl");
        const characterImageUrl = workflowParamValue("workflowParamViralCharacterUrl");
        const productImageUrl = workflowParamValue("workflowParamViralProductUrl");
        if (!/^https?:\/\//i.test(originalVideoUrl)) throw new Error("请上传或选择参考视频");
        if (!characterImageUrl && !productImageUrl) throw new Error("请至少上传或选择人物图、产品图其中一个");
        return {
          title: "爆款复刻",
          task_kind: "client_workflow",
          content: "H5 工作流：爆款复刻",
          payload: {
            action: "viral_video_remix_start",
            params: {
              original_video_url: originalVideoUrl,
              character_image_url: characterImageUrl,
              product_image_url: productImageUrl,
              prompt: workflowParamValue("workflowParamViralPrompt"),
              duration: workflowParamNumber("workflowParamViralDuration", 10, 5, 10),
              ratio: workflowParamValue("workflowParamViralRatio") || "9:16",
              generate_audio: workflowParamChecked("workflowParamViralGenerateAudio"),
              billing_confirmed: true,
            },
          },
        };
      }
      if (key === "wecom_reply") {
        return {
          title: "企业微信客服 - 拉取回复",
          task_kind: "client_workflow",
          content: "H5 工作流：企业微信客服",
          payload: { action: "wecom_poll_reply", params: { note: workflowParamValue("workflowParamWecomNote") } },
        };
      }
      return collectWorkflowCapabilityPlan(quick || {});
    }

    function workflowPlanFromParamFields(lookup, note) {
      const node = lookup && lookup.node;
      if (!node) throw new Error("未找到任务节点");
      const platform = socialPlatformFromAbilityKey(node.key);
      if (platform) {
        const payload = collectWorkflowSocialLeadsPayload(platform);
        return { title: payload.title || `${socialPlatformLabel(platform)}线索采集`, task_kind: "social_leads", content: `H5 工作流：${socialPlatformLabel(platform)}线索采集`, payload };
      }
      if (node.key === "linkedin_leads") {
        const payload = collectWorkflowLinkedinPayload(node);
        return { title: payload.title || "LinkedIn线索采集", task_kind: "linkedin_mining", content: "H5 工作流：LinkedIn线索采集", payload };
      }
      if (node.key === "wechat_channels_transcript") {
        const payload = collectWorkflowWechatPayload(node);
        return { title: payload.title || "视频号文案提取", task_kind: "wechat_channels_transcript", content: "H5 工作流：视频号文案提取", payload };
      }
      if (node.workQuickKey) return collectWorkflowQuickPlan(workQuickItemByKey(node.workQuickKey) || node);
      if (node.capabilityId || node.serverTask) return collectWorkflowCapabilityPlan(node);
      return workflowPlanForLookup(lookup, note);
    }

    function workflowPlanForLookup(lookup, note) {
      const node = lookup && lookup.node;
      if (!node) throw new Error("请选择任务节点");
      const prompt = workflowPrompt(note, node);
      const platform = socialPlatformFromAbilityKey(node.key);
      if (platform) {
        const payload = {
          platform,
          title: `${socialPlatformLabel(platform)}线索采集`,
          keywords: [prompt],
          max_items: 100,
          include_comments: true,
          include_account_posts: true,
          auto_run: true,
        };
        if (platform === "reddit") payload.communities = [prompt];
        else payload.source_keywords = [prompt];
        return { title: payload.title, task_kind: "social_leads", content: `H5 工作流：${payload.title}`, payload };
      }
      if (node.key === "linkedin_leads") {
        const payload = { title: "LinkedIn线索采集", keywords: [prompt], max_people: 30, auto_run: true };
        return { title: payload.title, task_kind: "linkedin_mining", content: "H5 工作流：LinkedIn线索采集", payload };
      }
      if (node.key === "wechat_channels_transcript") {
        const payload = { title: "视频号文案提取", query: prompt, max_pages: 1, limit: 10, page_size: 20 };
        return { title: payload.title, task_kind: "wechat_channels_transcript", content: "H5 工作流：视频号文案提取", payload };
      }
      if (node.key === "douyin_leads" || node.workQuickKey === "douyin_leads") {
        return {
          title: `抖音获客 - ${prompt.slice(0, 24)}`,
          task_kind: "douyin_leads",
          content: "H5 工作流：抖音获客",
          payload: { action: "search_collect", params: { keyword: prompt, max_results: 50, regions: ["全国"], mode: "script" } },
        };
      }
      const capabilityId = String(node.capabilityId || node.key || "").trim();
      if (capabilityId === "ip_content_daily") {
        const tpl = (state.ipTemplates || [])[0];
        if (!tpl || !tpl.id) throw new Error("IP日更节点需要先保存一个IP日更模板");
        return {
          title: "IP日更文案",
          task_kind: "ip_content_daily",
          content: "H5 工作流：IP日更文案",
          payload: {
            template_id: tpl.id,
            tasks: ["oral", "moments", "image"],
            sync_before: true,
            requirements: { common: prompt, oral: prompt, moments: prompt, image: prompt },
            industry_count: 5,
            ip_count: 5,
            moments_count: 20,
          },
        };
      }
      if (node.workQuickKey) {
        const quick = workQuickItemByKey(node.workQuickKey) || {};
        const action = quick.workflowAction || node.workflowAction || "";
        if (action) {
          const params = { note: prompt, prompt };
          if (action === "local_bestseller_plan") {
            params.profile = { identity: "老板", industry: prompt, city: "本地", province: "" };
            params.days = 30;
          }
          if (action === "viral_video_remix_start") {
            params.billing_confirmed = true;
            params.ratio = "9:16";
          }
          return {
            title: node.label || quick.label || "客户端工作流",
            task_kind: "client_workflow",
            content: `H5 工作流：${node.label || quick.label || "客户端工作流"}`,
            payload: { action, params },
          };
        }
      }
      if (capabilityId) {
        let payload = { prompt, task_text: prompt };
        if (capabilityId === "goal.video.pipeline") payload = { source_mode: "ai_image", prompt, task_text: prompt };
        if (capabilityId === "hifly.video.create_by_tts") payload = { script: prompt, prompt };
        if (capabilityId === "comfly.daihuo.pipeline" || capabilityId === "comfly.seedance.tvc.pipeline") {
          payload = { action: "start_pipeline", task_text: prompt, prompt, auto_save: true };
        }
        if (capabilityId === "wewrite.article.pipeline") payload = { idea: prompt, style: "", include_images: true, image_count: 3, image_aspect_ratio: "16:9" };
        if (capabilityId === "ppt.create") payload = { mode: "ai", topic: prompt, slide_count: 10, instructions: "", language: "zh-CN" };
        if (capabilityId === "comfly.ecommerce.detail_pipeline") payload = { action: "start_pipeline", task_text: prompt, page_count: 12, auto_save: true };
        return {
          title: node.label || capabilityName(capabilityId),
          task_kind: "capability",
          content: `H5 工作流：${node.label || capabilityId}`,
          payload: { capability_id: capabilityId, payload },
        };
      }
      throw new Error("这个节点暂不支持加入工作流");
    }

    function workflowNodePayloadFromInput() {
      const lookup = workflowLookupFromValue($("workflowNodeAbility") ? $("workflowNodeAbility").value : "");
      if (!lookup) throw new Error("请选择任务节点");
      const time = ($("workflowNodeTime") && $("workflowNodeTime").value || "").trim();
      if (!/^\d{2}:\d{2}$/.test(time)) throw new Error("请选择执行时间");
      const note = (($("workflowNodeNote") && $("workflowNodeNote").value) || lookup.defaultNote || "").trim();
      const plan = workflowPlanForLookup(lookup, note);
      return {
        id: `wf_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        time,
        ability_key: lookup.node.key || "",
        ability_label: lookup.optionLabel || lookup.node.label || lookup.node.key || "",
        department_id: lookup.department.id,
        department_name: lookup.department.name || "",
        note,
        param_configured: false,
        plan,
      };
    }

    function openWorkflowNodeModal() {
      const modal = $("workflowNodeModal");
      if (!modal) return;
      if ($("workflowNodeTime") && !$("workflowNodeTime").value) $("workflowNodeTime").value = "09:00";
      renderWorkflowAbilitySelect();
      modal.classList.remove("hidden");
      const first = $("workflowNodeTime") || modal.querySelector("input, textarea, select");
      if (first && typeof first.focus === "function") setTimeout(() => first.focus(), 80);
    }

    function closeWorkflowNodeModal() {
      const modal = $("workflowNodeModal");
      if (modal) modal.classList.add("hidden");
    }

    function addWorkflowNodeFromInput() {
      state.workflowNodesDraft.push(workflowNodePayloadFromInput());
      state.workflowNodesDraft.sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      if ($("workflowNodeNote")) $("workflowNodeNote").value = "";
      renderWorkflow();
      closeWorkflowNodeModal();
    }

    function buildSalesWorkflowPresetNodes() {
      const nodes = [];
      SALES_WORKFLOW_PRESET.forEach(([time, key, note], index) => {
        const lookup = abilityLookup(key);
        if (!lookup || !lookup.node || lookup.node.comingSoon || isPublishCenterNode(lookup.node)) return;
        try {
          const plan = workflowPlanForLookup(lookup, note);
          nodes.push({
            id: `sales_${String(time).replace(":", "")}_${index}`,
            time,
            ability_key: lookup.node.key || "",
            ability_label: note || lookup.node.label || lookup.node.key || "",
            department_id: lookup.department.id,
            department_name: lookup.department.name || "",
            note,
            param_configured: false,
            plan,
          });
        } catch (_err) {
          // Some nodes need user-owned templates before they can be scheduled.
        }
      });
      return nodes.sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
    }

    function prepareSalesWorkflowDraft() {
      state.workflowEditingTemplateId = "";
      state.workflowNodesDraft = buildSalesWorkflowPresetNodes();
      state.workflowParamNodeId = "";
      if ($("workflowTemplateName")) $("workflowTemplateName").value = "销售24小时员工";
      if ($("workflowNodeTime")) $("workflowNodeTime").value = "09:00";
      if ($("workflowNodeNote")) $("workflowNodeNote").value = "";
    }

    function resetWorkflowDraft() {
      state.workflowEditingTemplateId = "";
      state.workflowNodesDraft = [];
      state.workflowParamNodeId = "";
      if ($("workflowTemplateName")) $("workflowTemplateName").value = "";
      if ($("workflowNodeTime")) $("workflowNodeTime").value = "09:00";
      if ($("workflowNodeNote")) $("workflowNodeNote").value = "";
      renderWorkflow();
    }

    function refillWorkflowParamFields(node, lookup) {
      const plan = node && node.plan && typeof node.plan === "object" ? node.plan : {};
      const payload = plan.payload && typeof plan.payload === "object" ? plan.payload : {};
      const inner = payload.payload && typeof payload.payload === "object" ? payload.payload : payload;
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      const nodeInfo = lookup && lookup.node || {};
      const capabilityId = String(payload.capability_id || nodeInfo.capabilityId || nodeInfo.key || "").trim();
      const platform = socialPlatformFromAbilityKey(nodeInfo.key);
      if (platform) {
        setFieldValue("workflowParamLeadTitle", payload.title || plan.title || `${socialPlatformLabel(platform)}线索采集`);
        setTextareaList("workflowParamLeadKeywords", payload.keywords || (node.note ? [node.note] : []));
        setFieldValue("workflowParamLeadMode", payload.accounts && payload.accounts.length ? "account" : "source");
        setTextareaList("workflowParamLeadSources", payload.communities || payload.source_keywords || []);
        setTextareaList("workflowParamLeadAccounts", payload.accounts || []);
        setFieldValue("workflowParamLeadMaxItems", payload.max_items || 100);
        return;
      }
      if (nodeInfo.key === "linkedin_leads") {
        setFieldValue("workflowParamLinkedinTitle", payload.title || plan.title || "LinkedIn线索采集");
        setFieldValue("workflowParamLinkedinTarget", payload.target_profile || node.note || "");
        setTextareaList("workflowParamLinkedinProfiles", payload.seed_profile_urls || []);
        setTextareaList("workflowParamLinkedinCompanies", payload.seed_company_urls || []);
        setTextareaList("workflowParamLinkedinKeywords", payload.keywords || []);
        setTextareaList("workflowParamLinkedinHashtags", payload.hashtags || []);
        setFieldValue("workflowParamLinkedinMaxPeople", payload.max_people || 30);
        return;
      }
      if (nodeInfo.key === "wechat_channels_transcript") {
        setFieldValue("workflowParamWechatQuery", payload.query || payload.username || node.note || "");
        setFieldValue("workflowParamWechatPages", payload.max_pages || 1);
        setFieldValue("workflowParamWechatLimit", payload.limit || 10);
        return;
      }
      if (payload.action === "search_collect" || nodeInfo.workQuickKey === "douyin_leads") {
        setFieldValue("workflowParamDouyinKeyword", params.keyword || params.query || node.note || "");
        setFieldValue("workflowParamDouyinRegions", valueLabel(params.regions || params.region_list || params.area_list || ["全国"]));
        setFieldValue("workflowParamDouyinMaxResults", params.max_results || 50);
        setFieldValue("workflowParamDouyinMode", params.mode || "script");
        return;
      }
      if (payload.action === "local_bestseller_plan" || payload.action === "local_bestseller_scene_batch" || nodeInfo.workQuickKey === "local_bestseller") {
        const profile = params.profile && typeof params.profile === "object" ? params.profile : {};
        setFieldValue("workflowParamLocalMode", payload.action === "local_bestseller_scene_batch" ? "scene_batch" : "plan");
        setFieldValue("workflowParamLocalDays", params.days || 30);
        setFieldValue("workflowParamLocalName", profile.name || "");
        setFieldValue("workflowParamLocalNickname", profile.nickname || "");
        setFieldValue("workflowParamLocalGender", profile.gender || "female");
        setFieldValue("workflowParamLocalIdentity", profile.identity || "女老板");
        setFieldValue("workflowParamLocalIndustry", profile.industry || node.note || "大健康");
        setFieldValue("workflowParamLocalCity", profile.city || "深圳");
        setFieldValue("workflowParamLocalProvince", profile.province || "广东");
        setFieldValue("workflowParamLocalPhoto", profile.photo_asset_id || profile.photo_url || "");
        return;
      }
      if (payload.action === "viral_video_remix_start" || nodeInfo.workQuickKey === "viral_video_remix") {
        setFieldValue("workflowParamViralVideoUrl", params.original_video_url || "");
        setFieldValue("workflowParamViralCharacterUrl", params.character_image_url || "");
        setFieldValue("workflowParamViralProductUrl", params.product_image_url || "");
        setFieldValue("workflowParamViralPrompt", params.prompt || node.note || "");
        setFieldValue("workflowParamViralDuration", params.duration || 10);
        setFieldValue("workflowParamViralRatio", params.ratio || "9:16");
        setFieldValue("workflowParamViralGenerateAudio", params.generate_audio !== false);
        return;
      }
      if (payload.action === "wecom_poll_reply" || nodeInfo.workQuickKey === "wecom_reply") {
        setFieldValue("workflowParamWecomNote", params.note || node.note || "");
        return;
      }
      if (capabilityId === "ip_content_daily") {
        const setTemplate = () => setFieldValue("workflowParamIpTemplate", payload.template_id || "");
        if (state.ipTemplatesLoaded) setTemplate();
        else loadIpTemplates(true).then(setTemplate).catch(() => {});
        const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
        document.querySelectorAll("[data-workflow-ip-daily-task]").forEach((el) => {
          el.checked = !tasks.length || tasks.includes(el.getAttribute("data-workflow-ip-daily-task"));
        });
        setFieldValue("workflowParamIpSyncBefore", payload.sync_before !== false);
        const req = payload.requirements && typeof payload.requirements === "object" ? payload.requirements : {};
        setFieldValue("workflowParamIpRequirement", req.common || req.moments || req.oral || req.image || node.note || "");
        return;
      }
      if (capabilityId === "goal.image.pipeline") {
        setFieldValue("workflowParamImageTitle", plan.title || nodeInfo.label || "创作图片");
        setFieldValue("workflowParamImagePrompt", inner.prompt || inner.task_text || node.note || "");
        return;
      }
      if (capabilityId === "goal.video.pipeline") {
        setFieldValue("workflowParamVideoTitle", plan.title || nodeInfo.label || "创意视频");
        setFieldValue("workflowParamVideoPrompt", inner.prompt || inner.task_text || node.note || "");
        setFieldValue("workflowParamVideoMode", goalVideoModeFromPayload(inner));
        setFieldValue("workflowParamVideoAsset", firstGoalVideoReference(inner));
        setFieldValue("workflowParamVideoCandidateGroup", inner.candidate_group || "");
        bindWorkflowGoalVideoModeControls();
        loadVideoMemoryDocsForSelect().then(() => setMultiSelectValues("workflowParamVideoMemoryDocs", inner.memory_doc_ids || [])).catch(() => {});
        return;
      }
      if (capabilityId === "hifly.video.create_by_tts") {
        setFieldValue("workflowParamAvatar", inner.avatar || "");
        setFieldValue("workflowParamVoice", inner.voice || "");
        setFieldValue("workflowParamHiflyTitle", plan.title || nodeInfo.label || "数字人口播");
        setFieldValue("workflowParamHiflyScript", inner.script || inner.prompt || node.note || "");
        return;
      }
      if (capabilityId === "comfly.daihuo.pipeline") {
        setFieldValue("workflowParamComflyAsset", inner.asset_id || inner.image_url || "");
        setFieldValue("workflowParamComflyText", inner.task_text || inner.prompt || node.note || "");
        setFieldValue("workflowParamComflyStoryboardCount", inner.storyboard_count || 5);
        setFieldValue("workflowParamComflyAutoSave", inner.auto_save !== false);
        return;
      }
      if (capabilityId === "comfly.seedance.tvc.pipeline") {
        setFieldValue("workflowParamSeedanceAsset", inner.asset_id || inner.image_url || "");
        setFieldValue("workflowParamSeedanceText", inner.task_text || inner.prompt || node.note || "");
        setFieldValue("workflowParamSeedanceDuration", inner.total_duration_seconds || 20);
        setFieldValue("workflowParamSeedanceAspect", inner.aspect_ratio || "9:16");
        return;
      }
      if (capabilityId === "create.video.pipeline") {
        setFieldValue("workflowParamCreateVideoPrompt", inner.prompt || inner.task_text || node.note || "");
        setFieldValue("workflowParamCreateVideoDuration", inner.duration || 8);
        setFieldValue("workflowParamCreateVideoSceneCount", inner.scene_count || 1);
        setFieldValue("workflowParamCreateVideoAspect", inner.aspect_ratio || "16:9");
        return;
      }
      if (capabilityId === "wewrite.article.pipeline") {
        setFieldValue("workflowParamArticleTitle", plan.title || nodeInfo.label || "公众号文章");
        setFieldValue("workflowParamArticleIdea", inner.idea || node.note || "");
        setFieldValue("workflowParamArticleStyle", inner.style || "");
        setFieldValue("workflowParamArticleImageCount", inner.image_count || 3);
        setFieldValue("workflowParamArticleIncludeImages", inner.include_images !== false);
        return;
      }
      if (capabilityId === "ppt.create") {
        setFieldValue("workflowParamPptTitle", plan.title || nodeInfo.label || "PPT生成");
        setFieldValue("workflowParamPptTopic", inner.topic || node.note || "");
        setFieldValue("workflowParamPptSlideCount", inner.slide_count || 10);
        setFieldValue("workflowParamPptInstructions", inner.instructions || "");
        setFieldValue("workflowParamPptMode", inner.mode || "ai");
        return;
      }
      if (capabilityId === "comfly.ecommerce.detail_pipeline") {
        setFieldValue("workflowParamEcommerceTitle", plan.title || nodeInfo.label || "电商详情页");
        setFieldValue("workflowParamEcommerceAsset", inner.asset_id || inner.image_url || "");
        setFieldValue("workflowParamEcommerceText", inner.task_text || inner.prompt || node.note || "");
        setFieldValue("workflowParamEcommercePageCount", inner.page_count || 12);
        setFieldValue("workflowParamEcommerceAutoSave", inner.auto_save !== false);
        return;
      }
      setFieldValue("workflowParamGenericTitle", plan.title || nodeInfo.label || "能力任务");
      setFieldValue("workflowParamGenericPrompt", inner.prompt || inner.task_text || node.note || "");
    }

    function openWorkflowParamModal(nodeId) {
      const node = (state.workflowNodesDraft || []).find((item) => String(item.id || "") === String(nodeId || ""));
      const lookup = workflowLookupForNode(node);
      if (!node || !lookup) {
        toast("未找到节点");
        return;
      }
      state.workflowParamNodeId = String(node.id || "");
      const modal = $("workflowParamModal");
      if (!modal) return;
      $("workflowParamTitle").textContent = node.ability_label || "节点设置";
      $("workflowParamSubTitle").textContent = `${node.department_name || ""}${node.time ? ` · ${node.time}` : ""}`;
      $("workflowParamTime").value = node.time || "09:00";
      $("workflowParamNote").value = node.note || "";
      $("workflowParamFields").innerHTML = workflowFieldsHtmlForNode(lookup.node);
      modal.classList.remove("hidden");
      initWorkflowParamControls(lookup.node);
      refillWorkflowParamFields(node, lookup);
      const first = modal.querySelector("input, textarea, select");
      if (first && typeof first.focus === "function") setTimeout(() => first.focus(), 80);
    }

    function closeWorkflowParamModal() {
      const modal = $("workflowParamModal");
      if (modal) modal.classList.add("hidden");
      state.workflowParamNodeId = "";
      if ($("workflowParamFields")) $("workflowParamFields").innerHTML = "";
    }

    function saveWorkflowParamNode() {
      const nodeId = String(state.workflowParamNodeId || "");
      const idx = (state.workflowNodesDraft || []).findIndex((item) => String(item.id || "") === nodeId);
      if (idx < 0) throw new Error("未找到节点");
      const current = state.workflowNodesDraft[idx];
      const lookup = workflowLookupForNode(current);
      if (!lookup) throw new Error("未找到任务节点");
      const time = workflowParamValue("workflowParamTime");
      if (!/^\d{2}:\d{2}$/.test(time)) throw new Error("请选择执行时间");
      const note = workflowParamValue("workflowParamNote");
      const plan = workflowPlanFromParamFields(lookup, note);
      state.workflowNodesDraft[idx] = {
        ...current,
        time,
        note,
        param_configured: true,
        plan,
      };
      state.workflowNodesDraft.sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      closeWorkflowParamModal();
      renderWorkflow();
      toast("节点参数已保存");
    }

    function workflowDemoPlan(node) {
      const lookup = workflowLookupForNode(node);
      if (!node || !lookup) throw new Error("未找到节点");
      const raw = node.plan && typeof node.plan === "object" ? node.plan : workflowPlanForLookup(lookup, node.note || "");
      return {
        title: `演示-${raw.title || node.ability_label || "员工节点"}`,
        taskKind: raw.taskKind || raw.task_kind || "client_workflow",
        content: raw.content || `H5 员工节点演示：${node.ability_label || "任务节点"}`,
        payload: raw.payload || {},
        serverSide: raw.serverSide,
        h5Context: contextFromAbility(lookup),
      };
    }

    async function demoWorkflowNode(nodeId, btn) {
      const node = (state.workflowNodesDraft || []).find((item) => String(item.id || "") === String(nodeId || ""));
      if (!node) {
        toast("未找到节点");
        return;
      }
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "演示中";
      }
      try {
        await submitScheduledClientTask(workflowDemoPlan(node), { schedule_type: "once" });
        renderWorkflow();
        showTaskSuccessDialog("演示任务已下发，可在工作历史查看效果。");
      } catch (err) {
        toast(err.message || "演示失败");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "演示";
        }
      }
    }

    async function demoWorkflowTemplateNode(key, btn) {
      const [tplId, nodeId] = String(key || "").split("@@");
      const tpl = workflowTemplateById(tplId || "");
      const nodes = (Array.isArray(tpl && tpl.nodes) ? tpl.nodes : []).slice().sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      const node = nodes.find((item) => String(item.id || "") === String(nodeId || "")) || nodes[Number(nodeId)];
      if (!node) {
        toast("未找到节点");
        return;
      }
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "演示中";
      }
      try {
        await submitScheduledClientTask(workflowDemoPlan(node), { schedule_type: "once" });
        showTaskSuccessDialog("演示任务已下发，可在工作历史查看效果。");
      } catch (err) {
        toast(err.message || "演示失败");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "演示";
        }
      }
    }

    function renderWorkflowDeviceSelect() {
      const sel = $("workflowDeviceSelect");
      if (!sel) return;
      const current = state.selectedInstallationId || "";
      const rows = state.devices || [];
      sel.innerHTML = rows.length
        ? rows.map((device, idx) => optionHtml(device.installation_id || "", `${employeeName(device, idx)}${device.online ? "" : "（离线）"}`)).join("")
        : optionHtml("", "暂无设备");
      if (current && rows.some((device) => String(device.installation_id || "") === current)) sel.value = current;
      else sel.value = currentInstallationId();
    }

    function renderWorkflowAbilitySelect() {
      const sel = $("workflowNodeAbility");
      if (!sel) return;
      const current = sel.value;
      sel.innerHTML = workflowAbilityOptionsHtml() || optionHtml("", "暂无可用节点");
      if (current && Array.from(sel.options).some((opt) => opt.value === current && !opt.disabled)) sel.value = current;
    }

    function renderWorkflowTimeline() {
      const box = $("workflowTimeline");
      if (!box) return;
      const nodes = (state.workflowNodesDraft || []).slice().sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      if (!nodes.length) {
        box.innerHTML = `<div class="workflow-empty">还没有节点</div>`;
        return;
      }
      const selectedKey = workflowSelectedDateKey();
      const tasks = workflowTasksForDate(selectedKey);
      const runs = workflowRunsForDate(selectedKey);
      box.innerHTML = nodes.map((node, index) => `
        <div class="workflow-node-card" data-workflow-edit-node="${escapeHtml(node.id || "")}">
          <div class="workflow-node-time">${escapeHtml(node.time || "--:--")}</div>
          <div class="workflow-node-main">
            <strong>${escapeHtml(node.ability_label || "任务节点")}</strong>
            <span>${escapeHtml(node.department_name || "")}${node.note ? ` · ${escapeHtml(node.note)}` : ""}</span>
          </div>
          <div class="workflow-node-actions">
            ${workflowStatusPillHtml(workflowStatusInfo(node, workflowTaskForNode(node, tasks), workflowLatestRunForNode(node, runs), selectedKey))}
            <button class="ghost" type="button" data-workflow-demo-node="${escapeHtml(node.id || "")}">演示</button>
            <button class="ghost danger-text" type="button" data-workflow-remove-node="${escapeHtml(node.id || "")}">删除</button>
          </div>
        </div>
      `).join("");
    }

    function renderWorkflowTemplates() {
      const list = $("workflowTemplateList");
      if (!list) return;
      if (state.workflowTemplatesLoading) {
        list.innerHTML = `<div class="hint">加载中...</div>`;
        return;
      }
      const rows = state.workflowTemplates || [];
      if (!rows.length) {
        list.innerHTML = `<div class="hint">暂无模板</div>`;
        return;
      }
      list.innerHTML = rows.map((tpl) => {
        const own = tpl.source === "own";
        const nodeCount = Array.isArray(tpl.nodes) ? tpl.nodes.length : 0;
        const grantBtn = own && state.workflowCanGrant ? `<button type="button" data-workflow-grant="${tpl.id}">授权</button>` : "";
        const deleteBtn = own ? `<button class="ghost" type="button" data-workflow-delete="${tpl.id}">删除</button>` : "";
        return `<div class="workflow-template-item">
          <div>
            <strong>${escapeHtml(tpl.name || "工作流模板")}</strong>
            <span>${escapeHtml(tpl.source === "granted" ? `来自 ${tpl.owner_name || "代理商"}` : `${nodeCount} 个节点`)}</span>
          </div>
          <div class="workflow-template-actions">
            <button type="button" data-workflow-load="${tpl.id}">${own ? "编辑" : "套用"}</button>
            <button type="button" data-workflow-activate-template="${tpl.id}">启用</button>
            ${grantBtn}
            ${deleteBtn}
          </div>
        </div>`;
      }).join("");
    }

    function workflowTemplateRows() {
      return Array.isArray(state.workflowTemplates) ? state.workflowTemplates : [];
    }

    function workflowTemplateById(id) {
      const sid = String(id || "");
      return workflowTemplateRows().find((tpl) => String(tpl && tpl.id || "") === sid) || null;
    }

    function workflowTemplateCanEdit(tpl) {
      return !!tpl && tpl.source === "own";
    }

    function workflowTemplateNodeCount(tpl) {
      return Array.isArray(tpl && tpl.nodes) ? tpl.nodes.length : 0;
    }

    function workflowTemplateSourceText(tpl) {
      if (!tpl) return "";
      return tpl.source === "granted" ? `代理商下发${tpl.owner_name ? ` · ${tpl.owner_name}` : ""}` : "自己创建";
    }

    function workflowTemplateInitial(tpl) {
      const name = String(tpl && tpl.name || "员").trim();
      return (name.slice(0, 1) || "员").toUpperCase();
    }

    function workflowTemplateNodeListHtml(tpl) {
      const nodes = (Array.isArray(tpl && tpl.nodes) ? tpl.nodes : []).slice().sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      if (!nodes.length) return `<div class="custom-employee-empty">暂无任务节点</div>`;
      return `<div class="custom-employee-node-list">${nodes.map((node, index) => {
        const plan = node.plan && typeof node.plan === "object" ? node.plan : {};
        const nodeKey = `${tpl.id || ""}@@${node.id || index}`;
        return `<div class="custom-employee-node">
          <span>${escapeHtml(node.time || "--:--")}</span>
          <strong>${escapeHtml(node.ability_label || plan.title || "任务节点")}</strong>
          <button class="ghost" type="button" data-custom-employee-demo-node="${escapeHtml(nodeKey)}">演示</button>
          ${node.note ? `<em>${escapeHtml(node.note)}</em>` : ""}
        </div>`;
      }).join("")}</div>`;
    }

    function customEmployeeCardHtml(tpl, options = {}) {
      const compact = !!options.compact;
      const nodeCount = workflowTemplateNodeCount(tpl);
      const own = workflowTemplateCanEdit(tpl);
      return `<button class="custom-employee-card${compact ? " compact" : ""}" type="button" data-custom-employee-detail="${escapeHtml(tpl.id || "")}">
        <span class="custom-employee-avatar">${escapeHtml(workflowTemplateInitial(tpl))}</span>
        <span class="custom-employee-main">
          <strong>${escapeHtml(tpl.name || "自定义员工")}</strong>
          <em>${escapeHtml(nodeCount ? `${nodeCount} 个节点` : "暂无节点")}</em>
        </span>
        <b>${own ? "我的" : "授权"}</b>
      </button>`;
    }

    function renderCustomEmployees() {
      const strip = $("customEmployeeStrip");
      if (!strip) return;
      const rows = workflowTemplateRows();
      if ($("customEmployeeTotal")) $("customEmployeeTotal").textContent = `(${rows.length})`;
      if (state.workflowTemplatesLoading) {
        strip.innerHTML = `<div class="custom-employee-empty">加载中...</div>`;
        return;
      }
      if (!rows.length) {
        strip.innerHTML = `<div class="custom-employee-empty">暂无自定义员工</div>`;
        return;
      }
      strip.innerHTML = rows.slice(0, 3).map((tpl) => customEmployeeCardHtml(tpl, { compact: true })).join("");
    }

    function closeCustomEmployeeDialog() {
      $("customEmployeeDialog")?.classList.add("hidden");
    }

    function openCustomEmployeeList() {
      const modal = $("customEmployeeDialog");
      const body = $("customEmployeeDialogBody");
      const title = $("customEmployeeDialogTitle");
      if (!modal || !body) return;
      if (title) title.textContent = "自定义员工";
      const rows = workflowTemplateRows();
      body.innerHTML = rows.length
        ? `<div class="custom-employee-list">${rows.map((tpl) => customEmployeeCardHtml(tpl)).join("")}</div>`
        : `<div class="custom-employee-empty">暂无自定义员工</div>`;
      modal.classList.remove("hidden");
    }

    function openCustomEmployeeDetail(id) {
      const tpl = workflowTemplateById(id);
      const modal = $("customEmployeeDialog");
      const body = $("customEmployeeDialogBody");
      const title = $("customEmployeeDialogTitle");
      if (!modal || !body || !tpl) return;
      const own = workflowTemplateCanEdit(tpl);
      if (title) title.textContent = tpl.name || "自定义员工";
      body.innerHTML = `<div class="custom-employee-detail">
        <div class="custom-employee-detail-head">
          <span class="custom-employee-avatar large">${escapeHtml(workflowTemplateInitial(tpl))}</span>
          <div>
            <strong>${escapeHtml(tpl.name || "自定义员工")}</strong>
            <em>${escapeHtml(workflowTemplateSourceText(tpl))} · ${escapeHtml(workflowTemplateNodeCount(tpl) + " 个节点")}</em>
          </div>
        </div>
        ${workflowTemplateNodeListHtml(tpl)}
        <div class="custom-employee-actions">
          <button class="ghost" type="button" data-custom-employee-list>返回列表</button>
          ${own ? `<button class="ghost danger-text" type="button" data-custom-employee-delete="${escapeHtml(tpl.id || "")}">删除</button>` : ""}
          ${own ? `<button type="button" data-custom-employee-edit="${escapeHtml(tpl.id || "")}">编辑</button>` : ""}
          <button type="button" data-custom-employee-activate="${escapeHtml(tpl.id || "")}">启用</button>
        </div>
      </div>`;
      modal.classList.remove("hidden");
    }

    async function deleteWorkflowTemplateById(id) {
      const tpl = workflowTemplateById(id);
      if (!tpl || !workflowTemplateCanEdit(tpl)) throw new Error("只能删除自己创建的模板");
      if (!confirm(`删除自定义员工「${tpl.name || "未命名"}」？`)) return;
      await api(`/api/h5-workflows/templates/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (String(state.workflowEditingTemplateId) === String(id)) {
        state.workflowEditingTemplateId = "";
        state.workflowNodesDraft = [];
        if ($("workflowTemplateName")) $("workflowTemplateName").value = "";
      }
      state.workflowTemplatesLoaded = false;
      await loadWorkflowTemplates(true);
      toast("模板已删除");
      openCustomEmployeeList();
    }

    function renderWorkflowGrantPanel() {
      const panel = $("workflowGrantPanel");
      const list = $("workflowSubUserList");
      if (!panel || !list) return;
      const templateId = String(state.workflowGrantTemplateId || "");
      panel.classList.toggle("hidden", !templateId);
      if (!templateId) return;
      const rows = state.workflowSubUsers || [];
      const checkedMap = state.workflowGrantSelectedUserIds || {};
      list.innerHTML = rows.length
        ? rows.map((user) => `<label class="workflow-sub-user"><input type="checkbox" value="${escapeHtml(user.id)}" ${checkedMap[String(user.id)] ? "checked" : ""}><span>${escapeHtml(user.email || `用户${user.id}`)}</span></label>`).join("")
        : `<div class="hint">暂无下级用户</div>`;
      const pageText = $("workflowGrantPageText");
      if (pageText) {
        const start = state.workflowSubUserTotal ? state.workflowSubUserOffset + 1 : 0;
        const end = Math.min(state.workflowSubUserOffset + rows.length, state.workflowSubUserTotal);
        pageText.textContent = `${start}-${end} / ${state.workflowSubUserTotal || 0}`;
      }
      const prev = $("workflowGrantPrevBtn");
      const next = $("workflowGrantNextBtn");
      if (prev) prev.disabled = state.workflowSubUserOffset <= 0;
      if (next) next.disabled = state.workflowSubUserOffset + state.workflowSubUserLimit >= state.workflowSubUserTotal;
      list.querySelectorAll("input[type='checkbox']").forEach((input) => {
        input.onchange = () => { state.workflowGrantSelectedUserIds[String(input.value || "")] = !!input.checked; };
      });
    }

    function renderWorkflow() {
      renderWorkflowDeviceSelect();
      renderWorkflowAbilitySelect();
      const active = state.workflowActive;
      if ($("workflowActiveText")) {
        $("workflowActiveText").textContent = active ? `已启用：${active.template_name || "工作流模板"}` : "未启用工作流";
      }
      if ($("workflowActivateBtn")) $("workflowActivateBtn").disabled = !!state.workflowSubmitting;
      if ($("workflowStopBtn")) $("workflowStopBtn").disabled = !active || !!state.workflowSubmitting;
      renderWorkflowDayBoard();
      renderWorkflowTimeline();
      renderWorkflowTemplates();
      renderWorkflowGrantPanel();
    }

    function applyWorkflowTemplate(tpl) {
      if (!tpl) return;
      state.workflowEditingTemplateId = tpl.source === "own" ? String(tpl.id || "") : "";
      state.workflowNodesDraft = Array.isArray(tpl.nodes) ? JSON.parse(JSON.stringify(tpl.nodes)) : [];
      if ($("workflowTemplateName")) $("workflowTemplateName").value = tpl.name || "";
      renderWorkflow();
    }

    async function loadWorkflowTemplates(force = false) {
      if (!force && state.workflowTemplatesLoaded) {
        renderWorkflowTemplates();
        return;
      }
      state.workflowTemplatesLoading = true;
      renderWorkflowTemplates();
      try {
        const data = await api("/api/h5-workflows/templates");
        state.workflowTemplates = Array.isArray(data.templates) ? data.templates : [];
        state.workflowCanGrant = !!data.can_grant;
        state.workflowTemplatesLoaded = true;
      } finally {
        state.workflowTemplatesLoading = false;
        renderWorkflow();
        renderCustomEmployees();
      }
    }

    async function loadWorkflowActive() {
      const iid = currentInstallationId();
      if (!iid) {
        state.workflowActive = null;
        renderWorkflow();
        return;
      }
      const data = await api(`/api/h5-workflows/active?installation_id=${encodeURIComponent(iid)}`);
      state.workflowActive = data.activation || null;
      renderWorkflow();
    }

    async function loadWorkflowSubUsers(reset = false) {
      if (reset) state.workflowSubUserOffset = 0;
      const params = new URLSearchParams({
        q: state.workflowSubUserQuery || "",
        limit: String(state.workflowSubUserLimit || 10),
        offset: String(state.workflowSubUserOffset || 0),
      });
      const data = await api(`/api/h5-agent/sub-users?${params.toString()}`);
      state.workflowSubUsers = Array.isArray(data.items) ? data.items : [];
      state.workflowSubUserTotal = Number(data.total || 0);
      renderWorkflowGrantPanel();
    }

    async function saveWorkflowTemplate() {
      const name = ($("workflowTemplateName") && $("workflowTemplateName").value || "").trim();
      if (!name) {
        $("workflowTemplateName")?.focus();
        throw new Error("请先给员工模板取一个名字");
      }
      if (!state.workflowNodesDraft.length) throw new Error("请至少添加一个节点");
      const id = String(state.workflowEditingTemplateId || "");
      const data = await api(id ? `/api/h5-workflows/templates/${encodeURIComponent(id)}` : "/api/h5-workflows/templates", {
        method: id ? "PATCH" : "POST",
        json: { name, nodes: state.workflowNodesDraft },
      });
      state.workflowEditingTemplateId = String((data.template && data.template.id) || id || "");
      state.workflowTemplatesLoaded = false;
      await loadWorkflowTemplates(true);
      toast("模板已保存");
    }

    async function activateWorkflowTemplate(templateId = "") {
      let id = String(templateId || state.workflowEditingTemplateId || "");
      if (!id) {
        await saveWorkflowTemplate();
        id = String(state.workflowEditingTemplateId || "");
      }
      if (!id) throw new Error("请先保存模板");
      const iid = currentInstallationId();
      if (!iid) throw new Error("请选择设备");
      state.workflowSubmitting = true;
      renderWorkflow();
      try {
        const data = await api("/api/h5-workflows/activate", {
          method: "POST",
          json: { template_id: Number(id), installation_id: iid, timezone_offset_minutes: timezoneOffsetMinutes() },
        });
        state.workflowActive = data.activation || null;
        await Promise.all([loadTasks({ reset: true }), loadWorkflowActive()]);
        toast("工作流已启用");
      } finally {
        state.workflowSubmitting = false;
        renderWorkflow();
      }
    }

    async function stopWorkflowActive() {
      const active = state.workflowActive;
      if (!active || !active.id) return;
      state.workflowSubmitting = true;
      renderWorkflow();
      try {
        await api(`/api/h5-workflows/activations/${encodeURIComponent(active.id)}/stop`, { method: "POST", json: {} });
        state.workflowActive = null;
        await Promise.all([loadTasks({ reset: true }), loadWorkflowActive()]);
        toast("工作流已停用");
      } finally {
        state.workflowSubmitting = false;
        renderWorkflow();
      }
    }

    async function saveWorkflowGrant() {
      const templateId = String(state.workflowGrantTemplateId || "");
      if (!templateId) return;
      const ids = Object.keys(state.workflowGrantSelectedUserIds || {}).filter((id) => state.workflowGrantSelectedUserIds[id]).map((id) => Number(id)).filter(Boolean);
      await api(`/api/h5-workflows/templates/${encodeURIComponent(templateId)}/grants`, { method: "POST", json: { target_user_ids: ids } });
      state.workflowGrantTemplateId = "";
      state.workflowTemplatesLoaded = false;
      await loadWorkflowTemplates(true);
      toast("授权已保存");
    }

    function canManageAgent() {
      const user = state.user || {};
      return !!(user.is_agent || Number(user.agent_level || 0) > 0 || cleanKey(user.role) === "agent");
    }

    function syncAgentManageEntry() {
      const btn = $("profileAgentManageEntry");
      if (!btn) return;
      const allowed = canManageAgent();
      btn.classList.toggle("agent-locked", !allowed);
      btn.setAttribute("aria-disabled", allowed ? "false" : "true");
      const meta = $("profileAgentManageMeta");
      if (meta) {
        meta.textContent = allowed
          ? "查看下级用户，给下级授权员工定制、模板和记忆资料"
          : "仅代理商账号可进入";
      }
    }

    function agentResourceLabel(row, fallback) {
      return String((row && (row.name || row.title || row.filename || row.email)) || fallback || "").trim();
    }

    function agentResourceMeta(row, kind) {
      if (!row) return "";
      if (kind === "workflow") {
        const count = Array.isArray(row.nodes) ? row.nodes.length : 0;
        return `${count} 个节点`;
      }
      if (kind === "ip") {
        const k = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
        const c = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
        return `关键词 ${k} · 同行 ${c}`;
      }
      return row.source === "agent" ? "代理商记忆" : (row.filename || "");
    }

    function agentGrantMap(kind) {
      if (kind === "workflow") return state.agentGrantWorkflow;
      if (kind === "ip") return state.agentGrantIpTemplates;
      return state.agentGrantMemories;
    }

    function renderAgentResourceList(targetId, rows, kind) {
      const box = $(targetId);
      if (!box) return;
      const map = agentGrantMap(kind);
      if (!rows.length) {
        box.innerHTML = `<div class="personal-empty">暂无</div>`;
        return;
      }
      box.innerHTML = rows.map((row) => {
        const id = kind === "memory" ? personalDocId(row) : String(row.id || "");
        const title = agentResourceLabel(row, kind === "memory" ? "记忆文档" : "模板");
        const meta = agentResourceMeta(row, kind);
        return `<label class="agent-resource-row">
          <input type="checkbox" data-agent-grant="${escapeHtml(kind)}" value="${escapeHtml(id)}"${map[String(id)] ? " checked" : ""}>
          <span class="agent-resource-main">
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(meta)}</span>
          </span>
        </label>`;
      }).join("");
      box.querySelectorAll("[data-agent-grant]").forEach((input) => {
        input.onchange = () => { agentGrantMap(input.dataset.agentGrant || "")[String(input.value || "")] = !!input.checked; };
      });
    }

    function renderAgentUsers() {
      const list = $("agentUserList");
      if (!list) return;
      const rows = state.agentUsers || [];
      if (state.agentLoading && !rows.length) {
        list.innerHTML = `<div class="personal-empty">加载中</div>`;
      } else if (!rows.length) {
        list.innerHTML = `<div class="personal-empty">暂无下级</div>`;
      } else {
        list.innerHTML = rows.map((user) => {
          const id = String(user.id || "");
          return `<label class="agent-user-row${id === String(state.agentSelectedUserId || "") ? " active" : ""}">
            <input type="radio" name="agentUserPick" value="${escapeHtml(id)}"${id === String(state.agentSelectedUserId || "") ? " checked" : ""}>
            <span class="agent-user-main">
              <strong>${escapeHtml(user.email || `用户${id}`)}</strong>
              <span>ID ${escapeHtml(id)} · 积分 ${escapeHtml(String(user.credits ?? ""))}</span>
            </span>
          </label>`;
        }).join("");
      }
      list.querySelectorAll("input[name='agentUserPick']").forEach((input) => {
        input.onchange = () => selectAgentUser(input.value).catch((err) => toast(err.message || "读取授权失败"));
      });
      const pageText = $("agentUserPageText");
      if (pageText) {
        const start = state.agentUsersTotal ? state.agentUsersOffset + 1 : 0;
        const end = Math.min(state.agentUsersOffset + rows.length, state.agentUsersTotal);
        pageText.textContent = `${start}-${end} / ${state.agentUsersTotal || 0}`;
      }
      if ($("agentUserPrevBtn")) $("agentUserPrevBtn").disabled = state.agentUsersOffset <= 0;
      if ($("agentUserNextBtn")) $("agentUserNextBtn").disabled = state.agentUsersOffset + state.agentUsersLimit >= state.agentUsersTotal;
    }

    function renderAgentManage() {
      syncAgentManageEntry();
      renderAgentUsers();
      const selected = (state.agentUsers || []).find((user) => String(user.id || "") === String(state.agentSelectedUserId || ""));
      if ($("agentSelectedUserText")) $("agentSelectedUserText").textContent = selected ? (selected.email || `用户${selected.id}`) : "请选择下级";
      if ($("agentSaveGrantBtn")) $("agentSaveGrantBtn").disabled = !state.agentSelectedUserId || state.agentLoading;
      const res = state.agentResources || {};
      if ($("agentSummaryUsers")) $("agentSummaryUsers").textContent = String(state.agentUsersTotal || 0);
      if ($("agentSummaryWorkflows")) $("agentSummaryWorkflows").textContent = String((Array.isArray(res.workflow_templates) ? res.workflow_templates : []).length);
      if ($("agentSummaryTemplates")) $("agentSummaryTemplates").textContent = String((Array.isArray(res.ip_templates) ? res.ip_templates : []).length);
      if ($("agentSummaryMemories")) $("agentSummaryMemories").textContent = String((Array.isArray(res.memory_docs) ? res.memory_docs : []).length);
      renderAgentResourceList("agentWorkflowGrantList", Array.isArray(res.workflow_templates) ? res.workflow_templates : [], "workflow");
      renderAgentResourceList("agentIpTemplateGrantList", Array.isArray(res.ip_templates) ? res.ip_templates : [], "ip");
      renderAgentResourceList("agentMemoryGrantList", Array.isArray(res.memory_docs) ? res.memory_docs : [], "memory");
    }

    async function loadAgentResources() {
      if (!canManageAgent()) return;
      const data = await api("/api/h5-agent/resources");
      state.agentResources = {
        workflow_templates: Array.isArray(data.workflow_templates) ? data.workflow_templates : [],
        ip_templates: Array.isArray(data.ip_templates) ? data.ip_templates : [],
        memory_docs: Array.isArray(data.memory_docs) ? data.memory_docs : [],
      };
      renderAgentManage();
    }

    async function loadAgentUsers(reset = false) {
      if (!canManageAgent()) return;
      if (reset) state.agentUsersOffset = 0;
      state.agentLoading = true;
      renderAgentManage();
      try {
        const params = new URLSearchParams({
          q: state.agentUsersQuery || "",
          limit: String(state.agentUsersLimit || 10),
          offset: String(state.agentUsersOffset || 0),
        });
        const data = await api(`/api/h5-agent/sub-users?${params.toString()}`);
        state.agentUsers = Array.isArray(data.items) ? data.items : [];
        state.agentUsersTotal = Number(data.total || 0);
        if (!state.agentSelectedUserId && state.agentUsers.length) {
          state.agentSelectedUserId = String(state.agentUsers[0].id || "");
          await loadAgentUserGrants(state.agentSelectedUserId);
        }
      } finally {
        state.agentLoading = false;
        renderAgentManage();
      }
    }

    async function loadAgentUserGrants(userId) {
      if (!userId) return;
      const data = await api(`/api/h5-agent/sub-users/${encodeURIComponent(userId)}/grants`);
      state.agentGrantWorkflow = {};
      state.agentGrantIpTemplates = {};
      state.agentGrantMemories = {};
      (data.workflow_template_ids || []).forEach((id) => { state.agentGrantWorkflow[String(id)] = true; });
      (data.ip_template_ids || []).forEach((id) => { state.agentGrantIpTemplates[String(id)] = true; });
      (data.memory_doc_ids || []).forEach((id) => { state.agentGrantMemories[String(id)] = true; });
      if (state.agentPendingIpTemplateId) {
        state.agentGrantIpTemplates[String(state.agentPendingIpTemplateId)] = true;
      }
      renderAgentManage();
    }

    async function selectAgentUser(userId) {
      state.agentSelectedUserId = String(userId || "");
      await loadAgentUserGrants(state.agentSelectedUserId);
    }

    async function openAgentManage(options = {}) {
      if (!canManageAgent()) {
        toast("仅代理商可用");
        return;
      }
      state.agentManageBackTab = options.backTab || activeViewKey() || "profile";
      if (options.ipTemplateId) state.agentPendingIpTemplateId = String(options.ipTemplateId);
      switchTab("agentManage");
      await Promise.all([loadAgentResources(), loadAgentUsers(!state.agentUsers.length)]);
      if (state.agentSelectedUserId) await loadAgentUserGrants(state.agentSelectedUserId);
    }

    async function saveAgentGrants(btn) {
      const userId = String(state.agentSelectedUserId || "");
      if (!userId) throw new Error("请选择下级");
      personalSetBusy(btn, true, "保存中...");
      try {
        const payload = {
          workflow_template_ids: Object.keys(state.agentGrantWorkflow || {}).filter((id) => state.agentGrantWorkflow[id]).map(Number).filter(Boolean),
          ip_template_ids: Object.keys(state.agentGrantIpTemplates || {}).filter((id) => state.agentGrantIpTemplates[id]).map(Number).filter(Boolean),
          memory_doc_ids: Object.keys(state.agentGrantMemories || {}).filter((id) => state.agentGrantMemories[id]),
        };
        await api(`/api/h5-agent/sub-users/${encodeURIComponent(userId)}/grants`, { method: "POST", json: payload });
        state.agentPendingIpTemplateId = "";
        state.workflowTemplatesLoaded = false;
        state.ipTemplatesLoaded = false;
        toast("授权已保存");
      } finally {
        personalSetBusy(btn, false);
      }
    }

    function abilityIsActionable(node) {
      if (!node || node.comingSoon) return false;
      if (node.children && node.children.length) return true;
      if (node.always || node.routeTab) return true;
      if (node.featureKey) return !!(state.user && state.user.features && state.user.features[node.featureKey]);
      if (node.workQuickKey) {
        const quick = workQuickItemByKey(node.workQuickKey);
        if (quick && workQuickItemVisible(quick) && !quick.disabled) return true;
      }
      if (!state.taskSkillsLoaded) return false;
      const allowedCaps = new Set((state.taskAllowedCapabilityIds || []).map((id) => String(id || "").trim()).filter(Boolean));
      if (node.capabilityId && allowedCaps.has(String(node.capabilityId))) return true;
      if (node.packageId && packageVisible(node.packageId)) return true;
      return false;
    }

    function abilityStatusText(node) {
      const count = abilityChildCount(node);
      if (count) return `${count}项`;
      if (node && node.comingSoon) return "敬请期待";
      if (abilityIsActionable(node)) return "可用";
      if (!state.taskSkillsLoaded) return "权限加载中";
      return "未开通";
    }

    function abilityCardHtml(node) {
      const count = abilityChildCount(node);
      const actionable = abilityIsActionable(node);
      const disabled = node.comingSoon ? " disabled" : "";
      const chipClass = count ? "department-skill-chip child" : "department-skill-chip";
      return `<button class="department-skill-card${disabled}" type="button" data-ability-key="${escapeHtml(node.key || "")}" ${node.comingSoon ? 'aria-disabled="true"' : ""}>
        <div class="department-skill-top">
          <h3 class="department-skill-title">${escapeHtml(node.label || node.key || "能力")}</h3>
          <span class="${chipClass}">${escapeHtml(node.mark || firstChar(node.label || node.key))}</span>
        </div>
        <div class="department-skill-foot">
          <span>${count ? "继续选择" : (actionable ? "可进入" : "需开通")}</span>
          <strong>${escapeHtml(abilityStatusText(node))}</strong>
        </div>
      </button>`;
    }

    function contextFromDepartment(department) {
      return {
        source: "h5_department",
        department_id: department.id,
        department: department.name,
        ability_key: "",
        ability: "",
        path: department.name,
        description: department.description || "",
      };
    }

    function contextFromAbility(lookup) {
      const node = lookup && lookup.node;
      const department = lookup && lookup.department;
      const labels = [department && department.name, ...(lookup && lookup.trail ? lookup.trail.map((item) => item.label) : [])].filter(Boolean);
      return {
        source: "h5_ability",
        department_id: department && department.id,
        department: department && department.name,
        ability_key: node && node.key,
        ability: node && node.label,
        path: labels.join(" / "),
        description: node && node.description || "",
      };
    }

    function renderChatContextBar() {
      const bar = $("chatContextBar");
      if (!bar) return;
      const ctx = state.chatContext;
      bar.classList.toggle("hidden", !ctx);
      if (!ctx) return;
      $("chatContextTitle").textContent = `本次对话来源：${ctx.ability || ctx.department || "能力"}`;
      $("chatContextPath").textContent = ctx.path || ctx.department || "";
    }

    function setChatContext(context) {
      state.chatContext = context && context.department ? context : null;
      renderChatContextBar();
    }

    function activeViewKey() {
      const activeView = document.querySelector(".view.active");
      return activeView ? String(activeView.id || "").replace(/View$/, "") : "office";
    }

    function openContextChat(context) {
      const from = activeViewKey();
      if (from && from !== "messages") state.lastViewBeforeMessages = from;
      setChatContext(context);
      switchTab("messages");
    }

    function openDepartmentView(departmentId) {
      state.currentDepartmentId = String(departmentId || "");
      state.currentAbilityKey = "";
      state.abilityTrail = [];
      switchTab("department");
    }

    function renderDepartmentView() {
      const department = departmentById(state.currentDepartmentId);
      if (!department) return;
      state.currentDepartmentId = department.id;
      $("departmentKicker").textContent = department.alias || "DEPARTMENT";
      $("departmentTitle").textContent = department.name || "职能中心";
      if ($("pageTitle")) $("pageTitle").textContent = department.name || "职能中心";
      if ($("pageSubtitle")) $("pageSubtitle").textContent = "";
      $("departmentBreadcrumb").innerHTML = "";
      renderDepartmentDayBoard();
      const leaves = departmentLeafNodes(department);
      $("departmentSkillGrid").innerHTML = leaves.map(abilityCardHtml).join("") || `<div class="quick-empty">这个部门暂时没有配置能力。</div>`;
    }

    function openAbilityView(key) {
      const lookup = abilityLookup(key);
      if (!lookup) return;
      state.currentDepartmentId = lookup.department.id;
      state.currentAbilityKey = lookup.node.key;
      state.abilityTrail = lookup.trail.map((node) => node.key);
      switchTab("ability");
    }

    function hideAbilityWorkbench() {
      const box = $("abilityWorkbench");
      if (box) box.classList.add("hidden");
      if ($("abilityWorkbenchFields")) $("abilityWorkbenchFields").innerHTML = "";
      const btn = $("abilityWorkbenchSubmit");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "下发任务";
      }
      state.abilityWorkSubmitting = false;
    }

    function abilityValue(id) {
      const el = $(id);
      return ((el && el.value) || "").trim();
    }

    function abilityNumber(id, fallback, min, max) {
      return workNumber(abilityValue(id), fallback, min, max);
    }

    function splitTextareaList(value) {
      return String(value || "")
        .split(/[\n,，;；、]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function abilityIpDailyTaskOptionsHtml() {
      return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;">${IP_DAILY_TASK_OPTIONS.map((item) => `
        <label class="task-checkbox" style="min-height:38px;padding:0 10px;border:1px solid rgba(15,23,42,.08);border-radius:10px;background:rgba(255,255,255,.72);">
          <input type="checkbox" data-ability-ip-daily-task="${escapeHtml(item.value)}" checked>
          <span>${escapeHtml(item.label)}</span>
        </label>
      `).join("")}</div>`;
    }

    function selectedAbilityIpDailyTasks() {
      return Array.from(document.querySelectorAll("[data-ability-ip-daily-task]"))
        .filter((el) => el.checked)
        .map((el) => String(el.getAttribute("data-ability-ip-daily-task") || "").trim())
        .filter(Boolean);
    }

    function socialPlatformFromAbilityKey(key) {
      const raw = String(key || "").trim();
      if (raw === "reddit_leads") return "reddit";
      if (raw === "x_leads") return "x";
      if (raw === "tiktok_leads") return "tiktok";
      return "";
    }

    function socialPlatformLabel(platform) {
      return ({ reddit: "Reddit", x: "X", tiktok: "TikTok" }[String(platform || "")] || platform || "平台");
    }

    function abilitySocialFieldsHtml(platform) {
      const sourceLabel = platform === "reddit" ? "社区" : "来源关键词";
      const sourcePlaceholder = platform === "reddit" ? "例如：Entrepreneur、marketing、SaaS" : "例如：AI agent、marketing automation、lead generation";
      return taskFieldHtml("任务名称", workInputHtml("abilityLeadTitle", "text", `${socialPlatformLabel(platform)}线索采集`))
        + taskFieldHtml("精准用户方向", taskTextareaHtml("abilityLeadKeywords", "这里填写要分析筛选的用户方向，例如：需要AI获客、正在找自动化工具、询问营销方案"), true)
        + taskFieldHtml("采集方式", taskSelectHtml("abilityLeadMode", optionHtml("source", sourceLabel) + optionHtml("account", "账号")))
        + taskFieldHtml(`${sourceLabel}（可多行）`, taskTextareaHtml("abilityLeadSources", sourcePlaceholder), true)
        + taskFieldHtml("账号（可多行）", taskTextareaHtml("abilityLeadAccounts", platform === "reddit" ? "例如：u/example 或 example" : "例如：@example 或主页链接"), true)
        + taskFieldHtml("采集上限", workInputHtml("abilityLeadMaxItems", "number", "100", 'min="1" max="100"'));
    }

    function abilityLinkedinFieldsHtml() {
      return taskFieldHtml("任务名称", workInputHtml("abilityLinkedinTitle", "text", "LinkedIn线索挖掘"))
        + taskFieldHtml("目标画像", taskTextareaHtml("abilityLinkedinTarget", "例如：跨境电商老板、AI工具采购负责人、营销负责人"), true)
        + taskFieldHtml("个人主页（可多行）", taskTextareaHtml("abilityLinkedinProfiles", "LinkedIn个人主页链接，可不填"), true)
        + taskFieldHtml("公司主页（可多行）", taskTextareaHtml("abilityLinkedinCompanies", "LinkedIn公司主页链接，可不填"), true)
        + taskFieldHtml("关键词（可多行）", taskTextareaHtml("abilityLinkedinKeywords", "例如：AI marketing、automation、lead generation"), true)
        + taskFieldHtml("话题标签（可多行）", taskTextareaHtml("abilityLinkedinHashtags", "例如：ai、marketing、startup"), true)
        + taskFieldHtml("人数上限", workInputHtml("abilityLinkedinMaxPeople", "number", "30", 'min="5" max="80"'));
    }

    function abilityWechatTranscriptFieldsHtml() {
      return taskFieldHtml("视频号账号 / 链接 / 关键词", taskTextareaHtml("abilityWechatQuery", "填写视频号账号、sph开头ID、视频详情链接，或搜索关键词"), true)
        + taskFieldHtml("拉取页数", workInputHtml("abilityWechatPages", "number", "1", 'min="1" max="20"'))
        + taskFieldHtml("最多转写视频数", workInputHtml("abilityWechatLimit", "number", "10", 'min="1" max="50"'));
    }

    function abilityCapabilityFieldsHtml(capabilityId) {
      const id = String(capabilityId || "").trim();
      if (id === "ip_content_daily") {
        return taskFieldHtml("模板", ipTemplateSelectControl("abilityIpTemplate"))
          + taskFieldHtml("生成内容", abilityIpDailyTaskOptionsHtml(), true)
          + taskFieldHtml("执行前同步", `<label class="task-checkbox"><input id="abilityIpSyncBefore" type="checkbox" checked>每次执行前同步新数据</label>`, true)
          + taskFieldHtml("补充要求", taskTextareaHtml("abilityIpRequirement", "可选"), true);
      }
      if (id === "goal.video.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("abilityVideoTitle", "text", "创意视频"))
          + taskFieldHtml("生成模式", taskSelectHtml("abilityVideoMode", optionHtml("single_asset", "单个素材生成视频") + optionHtml("memory_image", "根据记忆先生成图片") + optionHtml("asset_group", "素材分组轮换生成")))
          + `<div class="field full" id="abilityVideoAssetField"><label>素材图片</label>${assetPickerControlHtml("abilityVideoAsset", { mediaType: "image", output: "url", uploadText: "上传图片", selectText: "选择已上传图片" })}</div>`
          + `<div class="field full hidden" id="abilityVideoMemoryField"><label>记忆文件</label>${videoMemorySelectControl("abilityVideoMemoryDocs")}</div>`
          + `<div class="field hidden" id="abilityVideoCandidateGroupField"><label>素材分组</label>${taskSelectHtml("abilityVideoCandidateGroup", optionHtml("", "不选择"))}</div>`
          + taskFieldHtml("补充提示词", taskTextareaHtml("abilityVideoPrompt", "可选"), true);
      }
      if (id === "wewrite.article.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("abilityArticleTitle", "text", "公众号文章"))
          + taskFieldHtml("公众号主题", taskTextareaHtml("abilityArticleIdea", "填写文章主题、受众、核心观点"), true)
          + taskFieldHtml("文章风格", workInputHtml("abilityArticleStyle", "text", "", 'placeholder="例如：专业、有案例、适合老板阅读"'))
          + taskFieldHtml("配图数量", workInputHtml("abilityArticleImageCount", "number", "3", 'min="0" max="6"'))
          + taskFieldHtml("自动配图", workCheckboxHtml("abilityArticleIncludeImages", "生成 16:9 横屏配图并插入", true), true);
      }
      if (id === "ppt.create") {
        return taskFieldHtml("任务名称", workInputHtml("abilityPptTitle", "text", "PPT生成"))
          + taskFieldHtml("PPT主题", taskTextareaHtml("abilityPptTopic", "填写PPT主题、用途、受众"), true)
          + taskFieldHtml("页数", workInputHtml("abilityPptSlideCount", "number", "10", 'min="1" max="80"'))
          + taskFieldHtml("风格要求", workInputHtml("abilityPptInstructions", "text", "", 'placeholder="例如：科技感、适合招商、案例更具体"'))
          + taskFieldHtml("生成模式", taskSelectHtml("abilityPptMode", optionHtml("ai", "AI视觉页") + optionHtml("outline", "结构化大纲")));
      }
      if (id === "comfly.ecommerce.detail_pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("abilityEcommerceTitle", "text", "电商详情页"))
          + taskFieldHtml("商品主图", assetPickerControlHtml("abilityEcommerceAsset", { mediaType: "image", output: "url", uploadText: "上传主图" }), true)
          + taskFieldHtml("详情页要求", taskTextareaHtml("abilityEcommerceText", "突出材质、卖点、使用场景和购买理由"), true)
          + taskFieldHtml("页面数量", workInputHtml("abilityEcommercePageCount", "number", "12", 'min="1" max="20"'))
          + taskFieldHtml("自动入库", workCheckboxHtml("abilityEcommerceAutoSave", "完成后保存到素材库", true));
      }
      return taskFieldHtml("任务名称", workInputHtml("abilityGenericTitle", "text", capabilityName(id) || "能力任务"))
        + taskFieldHtml("任务要求", taskTextareaHtml("abilityGenericPrompt", "填写要执行的任务参数和要求"), true);
    }

    function renderAbilityWorkbench(lookup) {
      const box = $("abilityWorkbench");
      if (!box || !lookup) return;
      const { node } = lookup;
      const fields = $("abilityWorkbenchFields");
      const title = $("abilityWorkbenchTitle");
      const badge = $("abilityWorkbenchBadge");
      const submit = $("abilityWorkbenchSubmit");
      if (!node || (node.children && node.children.length) || node.comingSoon) {
        hideAbilityWorkbench();
        return;
      }
      let html = "";
      let submitText = "下发任务";
      let badgeText = "独立任务";
      if (node.workQuickKey) {
        const quick = workQuickItemByKey(node.workQuickKey);
        if (quick && workQuickItemVisible(quick) && !quick.disabled) {
          html = workDispatchFieldsHtml(quick);
          if (quick.key === "hifly.video.create_by_tts") {
            setTimeout(() => {
              renderWorkHiflyOptions();
              loadHiflyLibraries();
            }, 0);
          }
        }
      } else if (node.capabilityId || node.serverTask) {
        html = abilityCapabilityFieldsHtml(node.capabilityId || node.key);
        if ((node.capabilityId || node.key) === "ip_content_daily") {
          setTimeout(() => loadIpTemplates(true), 0);
        }
        if ((node.capabilityId || node.key) === "goal.video.pipeline") {
          setTimeout(() => {
            bindGoalVideoModeControls("ability");
            fillCandidateGroupSelect();
            loadCandidateGroups();
            fillVideoMemorySelects();
            loadVideoMemoryDocsForSelect();
          }, 0);
        }
      } else if (socialPlatformFromAbilityKey(node.key)) {
        const platform = socialPlatformFromAbilityKey(node.key);
        html = abilitySocialFieldsHtml(platform);
        submitText = "创建采集任务";
        badgeText = "服务器采集";
      } else if (node.key === "linkedin_leads") {
        html = abilityLinkedinFieldsHtml();
        submitText = "创建采集任务";
        badgeText = "服务器采集";
      } else if (node.key === "wechat_channels_transcript") {
        html = abilityWechatTranscriptFieldsHtml();
        submitText = "创建提取任务";
        badgeText = "服务器转写";
      } else if (node.routeTab) {
        html = `<div class="field full"><button type="submit" id="abilityRouteOpenBtn">${node.routeTab === "profile" ? "打开IP人设定位" : "打开页面"}</button></div>`;
        submitText = node.routeTab === "profile" ? "打开IP人设定位" : "打开页面";
        badgeText = "配置入口";
      }
      if (!html) {
        hideAbilityWorkbench();
        return;
      }
      if (!node.routeTab) html += abilityScheduleFieldsHtml();
      box.classList.remove("hidden");
      if (title) title.textContent = `${node.label || "能力"}工作台`;
      if (badge) badge.textContent = badgeText;
      if (fields) fields.innerHTML = html;
      initAssetPickerControls(box);
      if (submit) {
        submit.disabled = false;
        submit.textContent = submitText;
      }
      setTimeout(updateAbilityScheduleFields, 0);
    }

    function renderAbilityView() {
      const lookup = activeAbilityLookup();
      if (!lookup) {
        openDepartmentView(state.currentDepartmentId || (DEPARTMENT_SKILL_TREE[0] && DEPARTMENT_SKILL_TREE[0].id));
        return;
      }
      const { node, department, trail } = lookup;
      const labels = [department.name, ...trail.map((item) => item.label || item.key)];
      $("abilityKicker").textContent = department.name || "ABILITY";
      $("abilityTitle").textContent = node.label || "能力";
      if ($("pageTitle")) $("pageTitle").textContent = node.label || "能力";
      if ($("pageSubtitle")) $("pageSubtitle").textContent = "";
      $("abilityBreadcrumb").innerHTML = `<span>首页</span>${labels.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}`;
      $("abilityChildren").innerHTML = (node.children || []).filter((child) => !isPublishCenterNode(child)).map(abilityCardHtml).join("");
      const routeBtn = $("abilityRouteBtn");
      const dispatchBtn = $("abilityDispatchBtn");
      if (routeBtn) {
        routeBtn.classList.add("hidden");
        routeBtn.textContent = node.routeTab === "profile" ? "打开设置" : "打开工作台";
      }
      if (dispatchBtn) dispatchBtn.classList.add("hidden");
      renderAbilityWorkbench(lookup);
    }

    function chatContextMarker() {
      const ctx = state.chatContext;
      if (!ctx) return "";
      return [
        "",
        "【H5来源上下文】",
        `部门：${ctx.department || ""}`,
        `能力路径：${ctx.path || ""}`,
        ctx.ability_key ? `能力标记：${ctx.ability_key}` : "",
        ctx.description ? `相关描述：${ctx.description}` : "",
        "处理要求：请优先只参考这个部门下的技能和相关描述理解用户意图。",
      ].filter(Boolean).join("\n");
    }

    async function renameActiveEmployee() {
      const modal = $("employeeModal");
      const installationId = String((modal && modal.dataset.employeeId) || "").trim();
      const device = deviceByInstallationId(installationId);
      if (!installationId || !device) return;
      const next = prompt("给这个 AI 员工起个名字", employeeName(device, 0));
      if (next === null) return;
      const displayName = next.trim().slice(0, 24);
      try {
        const data = await api(`/api/h5-chat/devices/${encodeURIComponent(installationId)}/display-name`, {
          method: "PATCH",
          json: { display_name: displayName },
        });
        const updated = data.device || {};
        state.devices = state.devices.map((row) => (
          String(row.installation_id || "") === installationId
            ? { ...row, display_name: updated.display_name || displayName || "" }
            : row
        ));
        renderOfficeEmployees();
        openEmployeeModal(installationId);
        toast(displayName ? "员工名字已更新" : "已恢复默认名字");
      } catch (err) {
        toast(err.message || "改名失败");
      }
    }

    function departmentNodeKeys(department) {
      const keys = new Set();
      eachAbilityNode((department && department.children) || [], department, [], (node) => {
        [node.key, node.capabilityId, node.packageId, node.workQuickKey].forEach((value) => {
          const text = String(value || "").trim();
          if (text) keys.add(text);
        });
      });
      return keys;
    }

    function departmentRun(department) {
      const keys = departmentNodeKeys(department);
      return (state.runs || []).filter(isActiveRun).find((row) => {
        const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
        const candidates = [
          row && row.task_kind,
          row && row.title,
          taskCapabilityId(row),
          payload.capability_id,
          payload.workflow_action,
          payload.action,
        ].map((value) => String(value || "").trim()).filter(Boolean);
        return candidates.some((value) => keys.has(value) || value.includes(department.name || ""));
      }) || null;
    }

    function departmentBubbleFor(department, index) {
      const run = departmentRun(department);
      if (run) {
        const title = run.title || capabilityName(taskCapabilityId(run) || run.task_kind) || "任务";
        const progress = runProgressText(run) || "执行中";
        return `工作中：${title} · ${progress}`.slice(0, 34);
      }
      const minuteSlot = Math.floor(Date.now() / 60000);
      if (minuteSlot % 4 !== 0) return "";
      if (index !== minuteSlot % Math.max(DEPARTMENT_SKILL_TREE.length, 1)) return "";
      const lines = ["等你安排新任务", "整理可用能力", "待命中"];
      return lines[minuteSlot % lines.length];
    }

    function localDateKey(value = new Date()) {
      const d = value instanceof Date ? value : parseDate(value);
      if (!d || Number.isNaN(d.getTime())) return "";
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${day}`;
    }

    function todayDateKey() {
      return localDateKey(new Date());
    }

    function parseDateKey(key) {
      const parts = String(key || "").split("-").map((item) => Number(item));
      if (parts.length !== 3 || parts.some((item) => !Number.isFinite(item))) return new Date();
      return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function addDateDays(date, days) {
      const d = new Date(date.getTime());
      d.setDate(d.getDate() + Number(days || 0));
      return d;
    }

    function departmentSelectedDateKey() {
      if (!state.departmentSelectedDate) state.departmentSelectedDate = todayDateKey();
      return state.departmentSelectedDate;
    }

    function workDateKey(row) {
      return localDateKey(row && (row.finished_at || row.updated_at || row.started_at || row.claimed_at || row.created_at));
    }

    function runsForDate(dateKey) {
      const key = dateKey || todayDateKey();
      return (state.runs || []).filter((row) => workDateKey(row) === key);
    }

    function departmentRunsForDate(department, dateKey) {
      const rows = runsForDate(dateKey || departmentSelectedDateKey());
      const scope = departmentScope(department);
      return rows.filter((row) => recordMatchesWorkScope(row, scope));
    }

    function runSucceeded(row) {
      const s = String((row && row.status) || "").toLowerCase();
      return ["completed", "success", "done"].includes(s);
    }

    function runFailed(row) {
      const s = String((row && row.status) || "").toLowerCase();
      return ["failed", "error", "cancelled", "canceled"].includes(s);
    }

    function runStats(rows) {
      const list = rows || [];
      const running = list.filter(isActiveRun).length;
      const failed = list.filter(runFailed).length;
      const completed = list.filter(runSucceeded).length;
      return { total: list.length, running, failed, completed };
    }

    function dayShortLabel(date) {
      const today = todayDateKey();
      const key = localDateKey(date);
      if (key === today) return "今天";
      const names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
      return names[date.getDay()] || "";
    }

    function taskDailyTimes(task) {
      const config = task && task.schedule_config && typeof task.schedule_config === "object" ? task.schedule_config : {};
      return Array.isArray(task && task.daily_times) ? task.daily_times
        : (Array.isArray(config.daily_times) ? config.daily_times : []);
    }

    function taskScheduledOnDate(task, dateKey) {
      if (!task) return false;
      const status = String(task.status || "").toLowerCase();
      if (status === "deleted" || status === "cancelled" || status === "canceled") return false;
      const type = String(task.schedule_type || "").toLowerCase();
      if (type === "daily_times") {
        const createdKey = localDateKey(task.created_at || task.updated_at || new Date());
        return !createdKey || dateKey >= createdKey;
      }
      if (type === "interval") {
        return localDateKey(task.next_run_at || task.start_at || task.created_at) === dateKey;
      }
      if (type === "once" && status === "completed" && taskHasRun(task, state.runs || [])) return false;
      return localDateKey(task.next_run_at || task.start_at || task.created_at) === dateKey;
    }

    function departmentTasksForDate(department, dateKey) {
      const scope = departmentScope(department);
      return (state.tasks || []).filter((task) => recordMatchesWorkScope(task, scope) && taskScheduledOnDate(task, dateKey || departmentSelectedDateKey()));
    }

    function dayBoardEmpty(text) {
      return `<div class="department-day-empty">${escapeHtml(text || "暂无")}</div>`;
    }

    function departmentRunItemHtml(row) {
      return `<button class="department-day-item" type="button" data-open-run-detail="${escapeHtml(row && row.id || "")}">
        <span>${escapeHtml(timeLabel(row && (row.finished_at || row.updated_at || row.started_at || row.created_at)))}</span>
        <strong>${escapeHtml(row && (row.title || "任务") || "任务")}</strong>
        <em>${escapeHtml(statusText(row && row.status))}</em>
      </button>`;
    }

    function departmentTaskItemHtml(task) {
      const daily = taskDailyTimes(task);
      const timeText = daily.length ? daily.join("、") : timeLabel(task && (task.next_run_at || task.start_at || task.created_at));
      return `<div class="department-day-item">
        <span>${escapeHtml(timeText || "--:--")}</span>
        <strong>${escapeHtml(task && (task.title || capabilityName(taskCapabilityId(task))) || "任务安排")}</strong>
        <em>${escapeHtml(statusText(task && task.status))}</em>
      </div>`;
    }

    function renderDepartmentDayBoard() {
      const department = departmentById(state.currentDepartmentId);
      const daysBox = $("departmentCalendarDays");
      if (!department || !daysBox) return;
      const selected = parseDateKey(departmentSelectedDateKey());
      const start = addDateDays(selected, -((selected.getDay() + 6) % 7));
      const today = todayDateKey();
      const selectedKey = departmentSelectedDateKey();
      daysBox.innerHTML = Array.from({ length: 7 }, (_item, idx) => {
        const d = addDateDays(start, idx);
        const key = localDateKey(d);
        return `<button class="department-calendar-day${key === selectedKey ? " active" : ""}${key === today ? " today" : ""}" type="button" data-department-date="${escapeHtml(key)}">
          <span>${escapeHtml(dayShortLabel(d))}</span>
          <strong>${escapeHtml(String(d.getDate()))}</strong>
        </button>`;
      }).join("");
      const runs = departmentRunsForDate(department, selectedKey)
        .sort((a, b) => itemTimeMs(b.finished_at, b.updated_at, b.created_at) - itemTimeMs(a.finished_at, a.updated_at, a.created_at));
      const tasks = departmentTasksForDate(department, selectedKey)
        .sort((a, b) => String(a.next_run_at || a.created_at || "").localeCompare(String(b.next_run_at || b.created_at || "")));
      const stats = runStats(runs);
      const summary = $("departmentDaySummary");
      if (summary) {
        summary.innerHTML = `<span>${escapeHtml(selectedKey === today ? "今天" : selectedKey)}</span>
          <strong>${escapeHtml(department.name || "部门")} · ${stats.total} 个执行 · ${tasks.length} 个安排</strong>
          <em>完成 ${stats.completed} · 执行中 ${stats.running} · 失败 ${stats.failed}</em>`;
      }
      const runBox = $("departmentDayRuns");
      if (runBox) runBox.innerHTML = runs.length ? runs.slice(0, 6).map(departmentRunItemHtml).join("") : dayBoardEmpty("当天暂无执行");
      const taskBox = $("departmentDaySchedules");
      if (taskBox) taskBox.innerHTML = tasks.length ? tasks.slice(0, 6).map(departmentTaskItemHtml).join("") : dayBoardEmpty("当天暂无安排");
    }

    function workflowSelectedDateKey() {
      if (!state.workflowSelectedDate) state.workflowSelectedDate = todayDateKey();
      return state.workflowSelectedDate;
    }

    function workflowActiveTaskIds() {
      return new Set(((state.workflowActive && state.workflowActive.scheduled_task_ids) || []).map((id) => String(id || "")).filter(Boolean));
    }

    function workflowRecordMatchesCurrent(row) {
      if (!row) return false;
      const activeIds = workflowActiveTaskIds();
      if (activeIds.size) {
        const rowTaskId = String((row.task_id !== undefined && row.task_id !== null) ? row.task_id : row.id || "");
        return !!rowTaskId && activeIds.has(rowTaskId);
      }
      const payload = row.payload && typeof row.payload === "object" ? row.payload : {};
      const ctx = h5ContextFromPayload(payload);
      const activeTemplateId = String((state.workflowActive && state.workflowActive.template_id) || "");
      if (activeTemplateId && String(ctx.workflow_template_id || "") === activeTemplateId) return true;
      return String(row.created_by_role || "") === "workflow" || !!(ctx.workflow_template_id || ctx.workflow_node_id);
    }

    function workflowRunsForDate(dateKey) {
      const key = dateKey || workflowSelectedDateKey();
      return (state.runs || []).filter((row) => workflowRecordMatchesCurrent(row) && workDateKey(row) === key);
    }

    function workflowTasksForDate(dateKey) {
      const key = dateKey || workflowSelectedDateKey();
      return (state.tasks || []).filter((task) => workflowRecordMatchesCurrent(task) && taskScheduledOnDate(task, key));
    }

    function workflowTemplateNodesForSchedule() {
      const active = state.workflowActive;
      if (active && Array.isArray(active.template_nodes) && active.template_nodes.length) {
        return active.template_nodes.slice().sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      }
      const activeTpl = active && active.template_id ? workflowTemplateById(active.template_id) : null;
      const nodes = activeTpl && Array.isArray(activeTpl.nodes) && activeTpl.nodes.length
        ? activeTpl.nodes
        : (state.workflowNodesDraft || []);
      return nodes.slice().sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
    }

    function workflowTaskForNode(node, tasks) {
      const nodeId = String((node && node.id) || "");
      const nodeKey = String((node && node.ability_key) || "");
      const nodeTime = String((node && node.time) || "");
      return (tasks || []).find((task) => {
        const payload = task && task.payload && typeof task.payload === "object" ? task.payload : {};
        const ctx = h5ContextFromPayload(payload);
        if (nodeId && String(ctx.workflow_node_id || "") === nodeId) return true;
        if (nodeKey && String(ctx.ability_key || "") === nodeKey && taskDailyTimes(task).includes(nodeTime)) return true;
        return false;
      }) || null;
    }

    function workflowLatestRunForNode(node, runs) {
      const nodeId = String((node && node.id) || "");
      const nodeKey = String((node && node.ability_key) || "");
      const nodeTime = String((node && node.time) || "");
      const matched = (runs || []).filter((run) => {
        return workflowRunMatchesNode(run, { id: nodeId, ability_key: nodeKey, time: nodeTime });
      });
      return matched.sort((a, b) => itemTimeMs(b.finished_at, b.updated_at, b.created_at) - itemTimeMs(a.finished_at, a.updated_at, a.created_at))[0] || null;
    }

    function workflowRunMatchesNode(run, node) {
      const nodeId = String((node && node.id) || "");
      const nodeKey = String((node && node.ability_key) || "");
      const nodeTime = String((node && node.time) || "");
      const payload = run && run.payload && typeof run.payload === "object" ? run.payload : {};
      const ctx = h5ContextFromPayload(payload);
      if (nodeId && String(ctx.workflow_node_id || "") === nodeId) return true;
      if (nodeKey && String(ctx.ability_key || "") === nodeKey && String(ctx.workflow_node_time || "") === nodeTime) return true;
      return false;
    }

    function workflowNodeDueAt(node, dateKey) {
      const time = String((node && node.time) || "").trim();
      if (!dateKey || !/^\d{2}:\d{2}$/.test(time)) return null;
      const [year, month, day] = String(dateKey).split("-").map((n) => Number(n));
      const [hour, minute] = time.split(":").map((n) => Number(n));
      const d = new Date(year, month - 1, day, hour, minute, 0);
      return Number.isNaN(d.getTime()) ? null : d;
    }

    function workflowStatusInfo(item, activeTask, run, dateKey) {
      if (run && isActiveRun(run)) return { label: "执行中", kind: "running", runId: run.id || "" };
      if (run && runFailed(run)) return { label: "失败", kind: "failed", runId: run.id || "" };
      if (run && runSucceeded(run)) return { label: "完成", kind: "completed", runId: run.id || "" };
      const status = String((activeTask && activeTask.status) || "").toLowerCase();
      if (status === "paused") return { label: "暂停", kind: "paused", taskId: activeTask.id || "" };
      if (status === "cancelled" || status === "canceled") return { label: "已取消", kind: "failed", taskId: activeTask.id || "" };
      const dueAt = workflowNodeDueAt(item, dateKey);
      if (state.workflowActive && dueAt && dueAt.getTime() < Date.now()) {
        return { label: "过期", kind: "overdue", taskId: activeTask && activeTask.id || "" };
      }
      if (activeTask) return { label: statusText(activeTask.status) || "待执行", kind: "pending", taskId: activeTask.id || "" };
      return state.workflowActive ? { label: "待执行", kind: "pending" } : { label: "未启用", kind: "idle" };
    }

    function workflowStatusPillHtml(info) {
      const cls = `workflow-status-pill ${info && info.kind ? `is-${info.kind}` : "is-idle"}`;
      if (info && info.runId) {
        return `<button class="${escapeHtml(cls)}" type="button" data-open-run-detail="${escapeHtml(info.runId)}">${escapeHtml(info.label || "-")}</button>`;
      }
      if (info && info.taskId) {
        return `<button class="${escapeHtml(cls)}" type="button" data-open-task-detail="${escapeHtml(info.taskId)}">${escapeHtml(info.label || "-")}</button>`;
      }
      return `<span class="${escapeHtml(cls)}">${escapeHtml(info && info.label || "-")}</span>`;
    }

    function renderWorkflowDayBoard() {
      const daysBox = $("workflowCalendarDays");
      if (!daysBox) return;
      const selected = parseDateKey(workflowSelectedDateKey());
      const start = addDateDays(selected, -((selected.getDay() + 6) % 7));
      const today = todayDateKey();
      const selectedKey = workflowSelectedDateKey();
      daysBox.innerHTML = Array.from({ length: 7 }, (_item, idx) => {
        const d = addDateDays(start, idx);
        const key = localDateKey(d);
        return `<button class="workflow-calendar-day${key === selectedKey ? " active" : ""}${key === today ? " today" : ""}" type="button" data-workflow-date="${escapeHtml(key)}">
          <span>${escapeHtml(dayShortLabel(d))}</span>
          <strong>${escapeHtml(String(d.getDate()))}</strong>
        </button>`;
      }).join("");
    }

    function secretaryAbilityCount(department) {
      let count = 0;
      eachAbilityNode((department && department.children) || [], department, [], (node) => {
        if (!node || node.comingSoon) return;
        if (!node.children || !node.children.length) count += 1;
      });
      return count;
    }

    function secretaryRunCredits(row) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      return numericValue(
        row && (row.credits_used || row.credits_charged || row.price || row.cost_credits)
        || payload.credits_used || payload.credits_charged || payload.credits_final || payload.price || payload.cost_credits
      );
    }

    function secretaryJobRowsForDepartment(department) {
      const scope = departmentScope(department);
      return [
        ...(state.socialLeadJobs || []).filter((job) => workbenchJobMatchesScope(job, "social", scope)),
        ...(state.linkedinJobs || []).filter((job) => workbenchJobMatchesScope(job, "linkedin", scope)),
        ...(state.wechatTranscriptJobs || []).filter((job) => workbenchJobMatchesScope(job, "wechat", scope)),
      ];
    }

    function secretaryTimeMs(value) {
      const d = parseDate(value);
      return d ? d.getTime() : 0;
    }

    function secretaryRowTime(row) {
      return itemTimeMs(row && row.finished_at, row && row.completed_at, row && row.updated_at, row && row.created_at);
    }

    function secretaryLocalDayStart(offsetDays = 0) {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      d.setDate(d.getDate() + offsetDays);
      return d.getTime();
    }

    function secretaryIsCompleted(status) {
      const s = String(status || "").toLowerCase();
      return s === "completed" || s === "success" || s === "done";
    }

    function secretaryTrendArrow(value) {
      if (value > 0) return `+${value}%`;
      if (value < 0) return `${value}%`;
      return "0%";
    }

    function secretaryTrendClass(value) {
      if (value > 0) return "up";
      if (value < 0) return "down";
      return "flat";
    }

    function secretarySparkline(values) {
      const nums = Array.isArray(values) ? values : [];
      const max = Math.max(1, ...nums);
      return nums.map((value, index) => {
        const h = Math.max(8, Math.round((Number(value || 0) / max) * 42));
        return `<i style="height:${h}px;--bar-delay:${(index * .07).toFixed(2)}s"></i>`;
      }).join("");
    }

    function secretaryDepartmentStats(department) {
      const scope = departmentScope(department);
      const runs = (state.runs || []).filter((row) => recordMatchesWorkScope(row, scope));
      const tasks = (state.tasks || []).filter((row) => recordMatchesWorkScope(row, scope));
      const jobs = secretaryJobRowsForDepartment(department);
      const now = Date.now();
      const todayStart = secretaryLocalDayStart(0);
      const yesterdayStart = secretaryLocalDayStart(-1);
      const weekStart = secretaryLocalDayStart(-6);
      const prevWeekStart = secretaryLocalDayStart(-13);
      const activeRuns = runs.filter(isActiveRun).length;
      const activeTasks = tasks.filter((row) => isRunningStatus(row && row.status)).length;
      const activeJobs = jobs.filter((job) => isRunningStatus(job && job.status)).length;
      const failedRuns = runs.filter((row) => String(row && row.status || "").toLowerCase() === "failed").length;
      const failedJobs = jobs.filter((job) => String(job && job.status || "").toLowerCase() === "failed").length;
      const assets = runs.reduce((sum, row) => sum + collectRunMediaEntries(row).length, 0);
      const credits = runs.reduce((sum, row) => sum + secretaryRunCredits(row), 0);
      const completedRuns = runs.filter((row) => secretaryIsCompleted(row && row.status));
      const completedJobs = jobs.filter((job) => secretaryIsCompleted(job && job.status));
      const todayRuns = completedRuns.filter((row) => secretaryRowTime(row) >= todayStart);
      const todayJobs = completedJobs.filter((job) => secretaryRowTime(job) >= todayStart);
      const yesterdayRuns = completedRuns.filter((row) => {
        const ms = secretaryRowTime(row);
        return ms >= yesterdayStart && ms < todayStart;
      });
      const yesterdayJobs = completedJobs.filter((job) => {
        const ms = secretaryRowTime(job);
        return ms >= yesterdayStart && ms < todayStart;
      });
      const weekRows = [...completedRuns, ...completedJobs].filter((row) => secretaryRowTime(row) >= weekStart);
      const prevWeekRows = [...completedRuns, ...completedJobs].filter((row) => {
        const ms = secretaryRowTime(row);
        return ms >= prevWeekStart && ms < weekStart;
      });
      const trend = prevWeekRows.length ? Math.round(((weekRows.length - prevWeekRows.length) / prevWeekRows.length) * 100) : (weekRows.length ? 100 : 0);
      const daily = Array.from({ length: 7 }, (_item, index) => {
        const start = secretaryLocalDayStart(index - 6);
        const end = secretaryLocalDayStart(index - 5);
        return [...completedRuns, ...completedJobs].filter((row) => {
          const ms = secretaryRowTime(row);
          return ms >= start && ms < end;
        }).length;
      });
      const stuckRuns = runs.filter((row) => isActiveRun(row) && now - itemTimeMs(row && row.started_at, row && row.claimed_at, row && row.created_at) > 30 * 60 * 1000).length;
      const todayAssets = todayRuns.reduce((sum, row) => sum + collectRunMediaEntries(row).length, 0);
      const lastMs = Math.max(
        0,
        ...runs.map((row) => itemTimeMs(row && row.updated_at, row && row.finished_at, row && row.created_at)),
        ...tasks.map((row) => itemTimeMs(row && row.next_run_at, row && row.updated_at, row && row.created_at)),
        ...jobs.map((job) => itemTimeMs(job && job.updated_at, job && job.completed_at, job && job.created_at))
      );
      return {
        department,
        abilityCount: secretaryAbilityCount(department),
        totalRuns: runs.length + jobs.length,
        active: activeRuns + activeTasks + activeJobs,
        failed: failedRuns + failedJobs,
        stuck: stuckRuns,
        risk: failedRuns + failedJobs + stuckRuns,
        scheduled: tasks.filter((row) => String(row && row.status || "").toLowerCase() !== "deleted").length,
        assets,
        todayAssets,
        credits,
        todayDelivered: todayRuns.length + todayJobs.length,
        yesterdayDelivered: yesterdayRuns.length + yesterdayJobs.length,
        weekDelivered: weekRows.length,
        prevWeekDelivered: prevWeekRows.length,
        trend,
        daily,
        score: (todayRuns.length + todayJobs.length) * 6 + weekRows.length * 2 + assets + (activeRuns + activeTasks + activeJobs) * 2 - (failedRuns + failedJobs + stuckRuns) * 5,
        lastMs,
      };
    }

    function secretaryAllStats() {
      return DEPARTMENT_SKILL_TREE.map(secretaryDepartmentStats);
    }

    function secretaryMetricCard(label, value, tone) {
      return `<div class="secretary-metric ${escapeHtml(tone || "")}">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(String(value))}</strong>
      </div>`;
    }

    function secretaryPulseClass(stat) {
      if (stat.risk > 0) return " alert";
      if (stat.active > 0) return " active";
      return "";
    }

    function renderSecretaryView() {
      const metrics = $("secretaryMetrics");
      const focus = $("secretaryFocus");
      const map = $("secretaryMap");
      const trendPanel = $("secretaryTrendPanel");
      const grid = $("secretaryDepartmentGrid");
      if (!metrics || !focus || !map || !trendPanel || !grid) return;
      const stats = secretaryAllStats();
      const totals = stats.reduce((acc, item) => {
        acc.active += item.active;
        acc.failed += item.failed;
        acc.stuck += item.stuck;
        acc.risk += item.risk;
        acc.scheduled += item.scheduled;
        acc.assets += item.assets;
        acc.todayAssets += item.todayAssets;
        acc.credits += item.credits;
        acc.runs += item.totalRuns;
        acc.abilities += item.abilityCount;
        acc.todayDelivered += item.todayDelivered;
        acc.yesterdayDelivered += item.yesterdayDelivered;
        acc.weekDelivered += item.weekDelivered;
        acc.prevWeekDelivered += item.prevWeekDelivered;
        item.daily.forEach((value, idx) => { acc.daily[idx] += value; });
        return acc;
      }, { active: 0, failed: 0, stuck: 0, risk: 0, scheduled: 0, assets: 0, todayAssets: 0, credits: 0, runs: 0, abilities: 0, todayDelivered: 0, yesterdayDelivered: 0, weekDelivered: 0, prevWeekDelivered: 0, daily: [0, 0, 0, 0, 0, 0, 0] });
      const totalTrend = totals.prevWeekDelivered ? Math.round(((totals.weekDelivered - totals.prevWeekDelivered) / totals.prevWeekDelivered) * 100) : (totals.weekDelivered ? 100 : 0);
      const topDept = stats.slice().sort((a, b) => b.score - a.score)[0] || stats[0];
      const riskDept = stats.slice().sort((a, b) => b.risk - a.risk)[0] || stats[0];
      const momentumDept = stats.slice().sort((a, b) => b.trend - a.trend)[0] || stats[0];
      metrics.innerHTML = [
        secretaryMetricCard("今日交付", compactNumber(totals.todayDelivered), "green"),
        secretaryMetricCard("进行中", compactNumber(totals.active), "blue"),
        secretaryMetricCard("风险", compactNumber(totals.risk), totals.risk ? "red" : "cyan"),
        secretaryMetricCard("今日素材", compactNumber(totals.todayAssets), "orange"),
      ].join("");
      focus.innerHTML = [
        `<div class="secretary-focus-card primary"><span>最活跃部门</span><strong>${escapeHtml(topDept ? topDept.department.name : "-")}</strong><em>${escapeHtml(topDept ? `${compactNumber(topDept.todayDelivered)} 个今日交付` : "")}</em></div>`,
        `<div class="secretary-focus-card ${riskDept && riskDept.risk ? "danger" : ""}"><span>需要盯</span><strong>${escapeHtml(riskDept && riskDept.risk ? riskDept.department.name : "暂无风险")}</strong><em>${escapeHtml(riskDept && riskDept.risk ? `${riskDept.risk} 个风险点` : "全部正常")}</em></div>`,
        `<div class="secretary-focus-card"><span>7日趋势</span><strong class="${escapeHtml(secretaryTrendClass(totalTrend))}">${escapeHtml(secretaryTrendArrow(totalTrend))}</strong><em>${escapeHtml(momentumDept ? `${momentumDept.department.name} ${secretaryTrendArrow(momentumDept.trend)}` : "")}</em></div>`,
      ].join("");
      const positions = [
        ["18%", "28%"],
        ["75%", "24%"],
        ["22%", "72%"],
        ["78%", "70%"],
      ];
      map.innerHTML = `<div class="secretary-map-core">
        <div class="secretary-core-ring"></div>
        <img class="secretary-core-img" src="/h5-static/h5-secretary-badge.png?v=20260706-secretary-fullbody-2" alt="" loading="lazy">
        <strong>${escapeHtml(compactNumber(totals.todayDelivered))}</strong>
        <span>今日交付</span>
      </div>${stats.map((stat, index) => {
        const pos = positions[index % positions.length];
        return `<button class="secretary-map-node${secretaryPulseClass(stat)}" type="button" data-secretary-dept="${escapeHtml(stat.department.id)}" style="--sx:${pos[0]};--sy:${pos[1]};--delay:${(index * .24).toFixed(2)}s">
          <span>${escapeHtml(stat.department.name)}</span>
          <strong>${escapeHtml(compactNumber(stat.todayDelivered))}</strong>
        </button>`;
      }).join("")}`;
      trendPanel.innerHTML = `<div class="secretary-trend-copy">
        <strong>7日交付趋势</strong>
        <span class="${escapeHtml(secretaryTrendClass(totalTrend))}">${escapeHtml(secretaryTrendArrow(totalTrend))}</span>
      </div>
      <div class="secretary-sparkline">${secretarySparkline(totals.daily)}</div>`;
      grid.innerHTML = stats.slice().sort((a, b) => b.score - a.score).map((stat, index) => {
        const last = stat.lastMs ? fmtTime(new Date(stat.lastMs).toISOString()) : "暂无";
        return `<button class="secretary-dept-card${secretaryPulseClass(stat)}" type="button" data-secretary-dept="${escapeHtml(stat.department.id)}" style="--accent:${index % 4}">
          <div class="secretary-dept-head">
            <span>${escapeHtml(stat.department.mark || firstChar(stat.department.name))}</span>
            <strong>${escapeHtml(stat.department.name)}</strong>
            <em class="${escapeHtml(secretaryTrendClass(stat.trend))}">${escapeHtml(secretaryTrendArrow(stat.trend))}</em>
          </div>
          <div class="secretary-dept-radar">
            <i style="--value:${Math.min(100, Math.max(8, stat.todayDelivered * 18 + stat.active * 14 + stat.weekDelivered * 4 - stat.risk * 10))}%"></i>
          </div>
          <div class="secretary-dept-data">
            <b>${escapeHtml(compactNumber(stat.todayDelivered))}<em>今日</em></b>
            <b>${escapeHtml(compactNumber(stat.active))}<em>进行中</em></b>
            <b>${escapeHtml(compactNumber(stat.risk))}<em>风险</em></b>
          </div>
          <div class="secretary-dept-sparkline">${secretarySparkline(stat.daily)}</div>
          <div class="secretary-dept-foot">
            <span>${escapeHtml(stat.risk ? `${stat.failed} 异常 / ${stat.stuck} 卡住` : `最近 ${last}`)}</span>
            <span>${escapeHtml(compactNumber(stat.assets))} 素材</span>
          </div>
        </button>`;
      }).join("");
    }

    function secretaryRoleCardHtml() {
      return `<button class="department-role-card secretary-role-card" type="button" data-secretary-role="1" aria-label="秘书中枢">
        <img class="secretary-role-img" src="/h5-static/h5-secretary-role.png?v=20260706-secretary-fullbody-2" alt="" loading="lazy">
        <div class="department-role-meta">
          <div class="department-role-name">秘书</div>
          <div class="department-role-count">工作态势</div>
        </div>
      </button>`;
    }

    function renderOfficeRecentTasks() {
      const box = $("officeRecentTasks");
      if (!box) return;
      const runRows = (state.runs || []).map((row) => ({
        kind: "run",
        id: row && row.id,
        title: row && (row.title || "任务"),
        status: row && row.status,
        time: row && (row.updated_at || row.finished_at || row.created_at),
        sortTime: row && (row.updated_at || row.finished_at || row.created_at),
        text: isActiveRun(row) ? (runProgressText(row) || "执行中") : (row && (row.error || row.result_text || "")),
        active: isActiveRun(row),
      }));
      const rows = runRows
        .filter((row) => row.id)
        .sort((a, b) => itemTimeMs(b.sortTime, b.time) - itemTimeMs(a.sortTime, a.time))
        .slice(0, 5);
      if (!rows.length) {
        box.innerHTML = `<div class="office-recent-empty">暂无执行记录</div>`;
        return;
      }
      box.innerHTML = rows.map((row) => {
        const attr = row.kind === "task" ? `data-open-task-detail="${escapeHtml(row.id || "")}"` : `data-open-run-detail="${escapeHtml(row.id || "")}"`;
        return `<button class="office-recent-item${row.active ? " active" : ""}" type="button" ${attr}>
          <span class="office-recent-dot"></span>
          <span class="office-recent-main">
            <strong>${escapeHtml(row.title || "任务")}</strong>
            <em>${escapeHtml(fmtTime(row.time))}</em>
          </span>
          <span class="office-recent-status">${escapeHtml(statusText(row.status))}</span>
          ${row.text ? `<small>${escapeHtml(String(row.text).slice(0, 46))}</small>` : ""}
        </button>`;
      }).join("");
    }

    function renderOfficeEmployees() {
      const floor = $("employeeFloor");
      if (!floor) return;
      const devices = state.devices || [];
      const snapshots = devices.map((device) => ({ device, snapshot: deviceSnapshot(device) }));
      const workingCount = snapshots.filter((row) => row.snapshot.mode === "working").length;
      const idleCount = snapshots.filter((row) => row.snapshot.mode === "idle").length;
      const offlineCount = snapshots.filter((row) => row.snapshot.mode === "offline").length;
      const onlineCount = workingCount + idleCount;
      const roles = [
        { id: "sales", name: "销售", status: "待命", target: "salesWorkflow" },
        { id: "customer_service", departmentId: "customer_service", name: "客服", status: "待命" },
        { id: "overseas", departmentId: "overseas", name: "海外员工", status: "待命" },
        { id: "hr", departmentId: "operations", name: "HR", status: "待命" },
      ];
      const runningCount = (state.runs || []).filter(isActiveRun).length;
      if ($("officeDeviceCount")) $("officeDeviceCount").textContent = String(devices.length);
      if ($("officeEmployeeCount")) $("officeEmployeeCount").textContent = String(roles.length);
      if ($("officeEmployeeTotal")) $("officeEmployeeTotal").textContent = `(${roles.length})`;
      if ($("officeRunningCount")) $("officeRunningCount").textContent = String(runningCount);
      if ($("officeTotalCount")) $("officeTotalCount").textContent = String(devices.length);
      if ($("officeOnlineCount")) $("officeOnlineCount").textContent = String(onlineCount);
      if ($("officeWorkingCount")) $("officeWorkingCount").textContent = String(workingCount);
      if ($("officeIdleCount")) $("officeIdleCount").textContent = String(idleCount);
      if ($("officeOfflineCount")) $("officeOfflineCount").textContent = String(offlineCount);
      updateBossOfficeStats(onlineCount, workingCount);
      floor.style.minHeight = "";
      floor.innerHTML = roles.map((role, index) => {
        const img = employeeAsset({ installation_id: role.id }, index, "idle");
        const hue = ["rgba(19,168,115,.2)", "rgba(36,92,255,.18)", "rgba(240,139,45,.2)", "rgba(19,183,216,.18)"][index % 4];
        const targetAttr = role.target ? ` data-home-target="${escapeHtml(role.target)}"` : "";
        const departmentAttr = !role.target && role.departmentId ? ` data-role-department="${escapeHtml(role.departmentId)}"` : "";
        return `<button class="office-employee-card" type="button"${targetAttr}${departmentAttr} style="--employee-glow:${escapeHtml(hue)}" aria-label="${escapeHtml(role.name)}">
          <img src="${escapeHtml(img)}" alt="" loading="lazy">
          <span class="office-employee-info">
            <strong>${escapeHtml(role.name || "员工")}</strong>
            <em>${escapeHtml(role.status || "")}</em>
          </span>
        </button>`;
      }).join("");
      renderOfficeRecentTasks();
      renderCustomEmployees();
    }

    function assetOriginLabel(origin) {
      return origin === "user_upload" ? "用户上传" : "内容记录";
    }

    function renderAssetLibraryTabs() {
      document.querySelectorAll("[data-asset-section]").forEach((btn) => {
        const active = btn.dataset.assetSection === (state.assetLibrarySection || "uploads");
        btn.classList.toggle("active", active);
      });
      if (activeViewKey() === "assetLibrary" && $("pageTitle")) $("pageTitle").textContent = "素材库";
      if ($("assetUserUploadTotal")) $("assetUserUploadTotal").textContent = compactNumber(state.assetLibraryTotals.user_upload || 0);
      if ($("assetAvatarTotal")) $("assetAvatarTotal").textContent = compactNumber(state.assetLibraryAvatarTotal || 0);
      if ($("assetVoiceTotal")) $("assetVoiceTotal").textContent = compactNumber(state.assetLibraryVoiceTotal || 0);
      if ($("assetLibraryAddBtn")) {
        const labelMap = { uploads: "上传", avatars: "添加形象", voices: "添加声音" };
        $("assetLibraryAddBtn").textContent = labelMap[state.assetLibrarySection || "uploads"] || "添加";
      }
    }

    function assetPreviewHtml(asset) {
      const url = String((asset && asset.source_url) || "").trim();
      const type = String((asset && asset.media_type) || mediaTypeFromUrl(url) || "").toLowerCase();
      if (!url) return `<div class="asset-library-thumb asset-library-thumb-empty">${escapeHtml(type || "素材")}</div>`;
      const src = mediaProxyUrl(url, "inline", filenameFromUrl(url, asset && asset.filename || "asset"));
      if (type === "video" || /\.(mp4|mov|webm)(\?|$)/i.test(url)) {
        return `<video class="asset-library-thumb" src="${escapeHtml(src)}" muted playsinline preload="metadata"></video>`;
      }
      if (type === "image" || /\.(png|jpe?g|gif|webp|bmp)(\?|$)/i.test(url)) {
        return `<img class="asset-library-thumb" src="${escapeHtml(src)}" alt="" loading="lazy">`;
      }
      return `<div class="asset-library-thumb asset-library-thumb-empty">${escapeHtml(type || "文件")}</div>`;
    }

    function assetTitle(asset) {
      const raw = asset && (asset.title || asset.name || asset.tags || asset.prompt || asset.filename || asset.asset_id);
      const title = valueLabel(raw);
      return title || "素材";
    }

    function assetCardHtml(asset) {
      const title = assetTitle(asset);
      const id = String((asset && asset.asset_id) || "");
      return `<button class="asset-library-card" type="button" data-asset-preview-id="${escapeHtml(id)}">
        ${assetPreviewHtml(asset)}
        <div class="asset-library-card-main">
          <strong>${escapeHtml(title || "素材")}</strong>
          <span>${escapeHtml((asset && asset.media_type) || "file")}</span>
          <em>${escapeHtml(fmtTime(asset && asset.created_at))}</em>
        </div>
      </button>`;
    }

    function hiflyStatusClass(row) {
      const status = String((row && row.status) || "").toLowerCase();
      return status === "failed" ? " failed" : "";
    }

    function hiflyAvatarCardHtml(row) {
      const id = String((row && row.id) || "");
      const title = String((row && row.title) || "未命名形象");
      const img = String((row && (row.image_url || row.cover_url || row.detail_url)) || "").trim();
      const thumb = img
        ? `<img class="asset-library-thumb" src="${escapeHtml(mediaProxyUrl(img, "inline", filenameFromUrl(img, "avatar")))}" alt="" loading="lazy">`
        : `<div class="asset-library-thumb asset-library-thumb-empty">形象</div>`;
      return `<button class="asset-library-card" type="button" data-hifly-asset-kind="avatar" data-hifly-asset-id="${escapeHtml(id)}">
        ${thumb}
        <div class="asset-library-card-main">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(row && row.source_type || "image")}</span>
          <em class="asset-status${hiflyStatusClass(row)}">${escapeHtml(row && row.status_text || row && row.status || "处理中")}</em>
        </div>
      </button>`;
    }

    function hiflyVoiceCardHtml(row) {
      const id = String((row && row.id) || "");
      const title = String((row && row.title) || "未命名声音");
      const provider = String((row && row.provider) || "");
      return `<button class="asset-library-card" type="button" data-hifly-asset-kind="voice" data-hifly-asset-id="${escapeHtml(id)}">
        <div class="asset-audio-thumb">声</div>
        <div class="asset-library-card-main">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(provider || "voice")}</span>
          <em class="asset-status${hiflyStatusClass(row)}">${escapeHtml(row && row.status_text || row && row.status || "处理中")}</em>
        </div>
      </button>`;
    }

    function assetPreviewLargeHtml(asset) {
      const url = String((asset && asset.source_url) || "").trim();
      const type = String((asset && asset.media_type) || mediaTypeFromUrl(url) || "").toLowerCase();
      if (!url) return `<div class="asset-preview-large asset-preview-large-empty">暂无可预览文件</div>`;
      const src = mediaProxyUrl(url, "inline", filenameFromUrl(url, asset && asset.filename || "asset"));
      if (type === "video" || /\.(mp4|mov|webm)(\?|$)/i.test(url)) {
        return `<video class="asset-preview-large" src="${escapeHtml(src)}" controls playsinline preload="metadata"></video>`;
      }
      if (type === "image" || /\.(png|jpe?g|gif|webp|bmp)(\?|$)/i.test(url)) {
        return `<img class="asset-preview-large" src="${escapeHtml(src)}" alt="">`;
      }
      return `<div class="asset-preview-large asset-preview-large-empty">${escapeHtml(type || "文件")}</div>`;
    }

    function findAssetInLibrary(assetId) {
      const id = String(assetId || "");
      const rows = [].concat(state.assetLibraryRows.user_upload || [], state.assetLibraryRows.generated || [], state.contentRecordRows || []);
      return rows.find((row) => String(row && row.asset_id || "") === id) || null;
    }

    function findHiflyAsset(kind, id) {
      const rows = kind === "voice" ? state.assetLibraryVoiceRows : state.assetLibraryAvatarRows;
      return (rows || []).find((row) => String(row && row.id || "") === String(id || "")) || null;
    }

    function openAssetPreview(assetId) {
      const asset = findAssetInLibrary(assetId);
      if (!asset) return;
      const modal = $("assetPreviewDialog");
      const body = $("assetPreviewBody");
      const title = $("assetPreviewTitle");
      if (!modal || !body) return;
      if (title) title.textContent = assetTitle(asset);
      const url = String(asset.source_url || "").trim();
      const actions = url ? mediaActionHtml(url, "下载素材", asset.filename || asset.asset_id || "asset") : "";
      body.innerHTML = `
        ${assetPreviewLargeHtml(asset)}
        <div class="asset-preview-meta">
          <div><span>来源</span><strong>${escapeHtml(assetOriginLabel(asset.asset_origin || state.assetLibraryOrigin))}</strong></div>
          <div><span>类型</span><strong>${escapeHtml(asset.media_type || "file")}</strong></div>
          <div><span>时间</span><strong>${escapeHtml(fmtTime(asset.created_at))}</strong></div>
          <div><span>ID</span><strong>${escapeHtml(asset.asset_id || "-")}</strong></div>
        </div>
        ${asset.prompt ? `<div class="asset-preview-text">${escapeHtml(asset.prompt)}</div>` : ""}
        ${actions}`;
      modal.classList.remove("hidden");
    }

    function openHiflyAssetPreview(kind, id) {
      const row = findHiflyAsset(kind, id);
      if (!row) return;
      const modal = $("assetPreviewDialog");
      const body = $("assetPreviewBody");
      const title = $("assetPreviewTitle");
      if (!modal || !body) return;
      if (title) title.textContent = row.title || (kind === "voice" ? "声音分身" : "形象分身");
      const isVoice = kind === "voice";
      const mediaUrl = String((isVoice ? row.demo_url : (row.image_url || row.cover_url || row.detail_url)) || "").trim();
      let preview = `<div class="asset-preview-large asset-preview-large-empty">${isVoice ? "暂无试听" : "暂无预览"}</div>`;
      if (mediaUrl) {
        const src = mediaProxyUrl(mediaUrl, "inline", filenameFromUrl(mediaUrl, isVoice ? "voice.mp3" : "avatar"));
        preview = isVoice
          ? `<audio class="asset-preview-audio" src="${escapeHtml(src)}" controls></audio>`
          : `<img class="asset-preview-large" src="${escapeHtml(src)}" alt="">`;
      }
      body.innerHTML = `
        ${preview}
        <div class="asset-preview-meta">
          <div><span>类型</span><strong>${isVoice ? "声音分身" : "形象分身"}</strong></div>
          <div><span>状态</span><strong>${escapeHtml(row.status_text || row.status || "-")}</strong></div>
          <div><span>时间</span><strong>${escapeHtml(fmtTime(row.created_at))}</strong></div>
          <div><span>ID</span><strong>${escapeHtml(String(row.avatar || row.voice || row.task_id || row.id || "-"))}</strong></div>
        </div>
        ${row.message ? `<div class="asset-preview-text">${escapeHtml(row.message)}</div>` : ""}
        <div class="work-dispatch-actions">
          <button class="ghost danger-text" type="button" data-delete-hifly-asset="${escapeHtml(kind)}" data-delete-hifly-id="${escapeHtml(String(row.id || ""))}">删除</button>
        </div>`;
      modal.classList.remove("hidden");
    }

    function renderAssetLibrary() {
      const list = $("assetLibraryList");
      if (!list) return;
      renderAssetLibraryTabs();
      const section = state.assetLibrarySection || "uploads";
      const pageSize = Number(state.assetLibraryPageSize || 10);
      let page = 1;
      let total = 0;
      let rows = [];
      let loading = false;
      if (section === "avatars") {
        page = Math.max(1, Number(state.assetLibraryAvatarPage || 1));
        total = Number(state.assetLibraryAvatarTotal || 0);
        rows = state.assetLibraryAvatarRows || [];
        loading = !!state.assetLibraryDigitalLoading;
      } else if (section === "voices") {
        page = Math.max(1, Number(state.assetLibraryVoicePage || 1));
        total = Number(state.assetLibraryVoiceTotal || 0);
        rows = state.assetLibraryVoiceRows || [];
        loading = !!state.assetLibraryDigitalLoading;
      } else {
        const origin = state.assetLibraryOrigin || "user_upload";
        page = Math.max(1, Number((state.assetLibraryPage || {})[origin]) || 1);
        total = Number((state.assetLibraryTotals || {})[origin] || 0);
        rows = (state.assetLibraryRows && state.assetLibraryRows[origin]) || [];
        loading = !!state.assetLibraryLoading;
      }
      const pageCount = Math.max(1, Math.ceil(total / pageSize));
      list.classList.toggle("loading", loading);
      if (loading) {
        list.innerHTML = `<div class="asset-library-empty">加载中...</div>`;
      } else if (!rows.length) {
        list.innerHTML = `<div class="asset-library-empty">暂无素材</div>`;
      } else if (section === "avatars") {
        list.innerHTML = rows.map(hiflyAvatarCardHtml).join("");
      } else if (section === "voices") {
        list.innerHTML = rows.map(hiflyVoiceCardHtml).join("");
      } else {
        list.innerHTML = rows.map(assetCardHtml).join("");
      }
      if ($("assetLibraryPageText")) $("assetLibraryPageText").textContent = `${page} / ${pageCount}`;
      if ($("assetLibraryPrevBtn")) $("assetLibraryPrevBtn").disabled = page <= 1 || loading;
      if ($("assetLibraryNextBtn")) $("assetLibraryNextBtn").disabled = page >= pageCount || loading;
    }

    async function loadAssetLibrary(origin = state.assetLibraryOrigin) {
      if (!state.token) return;
      const cleanOrigin = origin === "generated" ? "generated" : "user_upload";
      const pageSize = Number(state.assetLibraryPageSize || 10);
      const page = Math.max(1, Number((state.assetLibraryPage || {})[cleanOrigin]) || 1);
      const offset = (page - 1) * pageSize;
      state.assetLibraryLoading = true;
      renderAssetLibrary();
      try {
        const data = await api(`/api/assets?origin=${encodeURIComponent(cleanOrigin)}&limit=${pageSize}&offset=${offset}`);
        state.assetLibraryRows[cleanOrigin] = Array.isArray(data.assets) ? data.assets : [];
        state.assetLibraryTotals[cleanOrigin] = Number(data.total || 0);
      } catch (err) {
        state.assetLibraryRows[cleanOrigin] = [];
        toast(err.message || "素材加载失败");
      } finally {
        state.assetLibraryLoading = false;
        renderAssetLibrary();
      }
    }

    async function loadAssetLibraryAvatars() {
      if (!state.token) return;
      const pageSize = Number(state.assetLibraryPageSize || 10);
      const page = Math.max(1, Number(state.assetLibraryAvatarPage || 1));
      state.assetLibraryDigitalLoading = true;
      renderAssetLibrary();
      try {
        const data = await api(`/api/hifly/my/avatar/list?page=${page}&size=${pageSize}`);
        state.assetLibraryAvatarRows = Array.isArray(data.items) ? data.items : [];
        state.assetLibraryAvatarTotal = Number(data.total || 0);
        state.hiflyLoaded = false;
      } catch (err) {
        state.assetLibraryAvatarRows = [];
        toast(err.message || "形象分身加载失败");
      } finally {
        state.assetLibraryDigitalLoading = false;
        renderAssetLibrary();
      }
    }

    async function loadAssetLibraryVoices() {
      if (!state.token) return;
      const pageSize = Number(state.assetLibraryPageSize || 10);
      const page = Math.max(1, Number(state.assetLibraryVoicePage || 1));
      state.assetLibraryDigitalLoading = true;
      renderAssetLibrary();
      try {
        const data = await api(`/api/hifly/my/voice/list?page=${page}&size=${pageSize}`);
        state.assetLibraryVoiceRows = Array.isArray(data.items) ? data.items : [];
        state.assetLibraryVoiceTotal = Number(data.total || 0);
        state.hiflyLoaded = false;
      } catch (err) {
        state.assetLibraryVoiceRows = [];
        toast(err.message || "声音分身加载失败");
      } finally {
        state.assetLibraryDigitalLoading = false;
        renderAssetLibrary();
      }
    }

    async function refreshAssetLibrary() {
      if ((state.assetLibrarySection || "uploads") === "avatars") return loadAssetLibraryAvatars();
      if ((state.assetLibrarySection || "uploads") === "voices") return loadAssetLibraryVoices();
      state.assetLibraryOrigin = "user_upload";
      return loadAssetLibrary("user_upload");
    }

    function renderContentRecordTabs() {
      document.querySelectorAll("[data-content-record-media]").forEach((btn) => {
        btn.classList.toggle("active", String(btn.dataset.contentRecordMedia || "") === String(state.contentRecordMediaType || ""));
      });
    }

    function renderContentRecords() {
      const list = $("contentRecordList");
      if (!list) return;
      renderContentRecordTabs();
      const page = Math.max(1, Number(state.contentRecordPage || 1));
      const total = Number(state.contentRecordTotal || 0);
      const pageSize = Number(state.contentRecordPageSize || 10);
      const pageCount = Math.max(1, Math.ceil(total / pageSize));
      const rows = state.contentRecordRows || [];
      list.classList.toggle("loading", !!state.contentRecordLoading);
      if (state.contentRecordLoading) {
        list.innerHTML = `<div class="asset-library-empty">加载中...</div>`;
      } else if (!rows.length) {
        list.innerHTML = `<div class="asset-library-empty">暂无内容记录</div>`;
      } else {
        list.innerHTML = rows.map(assetCardHtml).join("");
      }
      if ($("contentRecordPageText")) $("contentRecordPageText").textContent = `${page} / ${pageCount}`;
      if ($("contentRecordPrevBtn")) $("contentRecordPrevBtn").disabled = page <= 1 || state.contentRecordLoading;
      if ($("contentRecordNextBtn")) $("contentRecordNextBtn").disabled = page >= pageCount || state.contentRecordLoading;
    }

    async function loadContentRecords() {
      if (!state.token) return;
      const pageSize = Number(state.contentRecordPageSize || 10);
      const page = Math.max(1, Number(state.contentRecordPage || 1));
      const offset = (page - 1) * pageSize;
      const params = new URLSearchParams({ origin: "generated", limit: String(pageSize), offset: String(offset) });
      if (state.contentRecordMediaType) params.set("media_type", state.contentRecordMediaType);
      state.contentRecordLoading = true;
      renderContentRecords();
      try {
        const data = await api(`/api/assets?${params.toString()}`);
        state.contentRecordRows = Array.isArray(data.assets) ? data.assets : [];
        state.contentRecordTotal = Number(data.total || 0);
        state.assetLibraryRows.generated = state.contentRecordRows;
        state.assetLibraryTotals.generated = state.contentRecordTotal;
      } catch (err) {
        state.contentRecordRows = [];
        toast(err.message || "内容记录加载失败");
      } finally {
        state.contentRecordLoading = false;
        renderContentRecords();
      }
    }

    async function uploadAssetLibraryFiles(btn) {
      const input = $("assetLibraryUploadInput");
      const files = input && input.files ? Array.from(input.files).filter(Boolean) : [];
      if (!files.length) return;
      const status = $("assetLibraryUploadStatus");
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "上传中...";
      }
      if (status) status.textContent = `0/${files.length}`;
      try {
        for (let i = 0; i < files.length; i += 1) {
          const fd = new FormData();
          fd.append("file", files[i], files[i].name || "upload");
          fd.append("split_video", "true");
          const resp = await fetch(apiUrl("/api/assets/upload"), { method: "POST", headers: authHeaders(), body: fd });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error(data.detail || data.message || `上传失败：HTTP ${resp.status}`);
          const rows = Array.isArray(data.assets) && data.assets.length ? data.assets : [data];
          rows.forEach((row) => addUserUploadAssetToCache({ ...row, asset_origin: "user_upload" }));
          if (status) status.textContent = `${i + 1}/${files.length}`;
        }
        if (input) input.value = "";
        state.assetLibraryPage.user_upload = 1;
        await refreshAssetLibrary();
        toast("上传完成");
        closeAssetUploadModal();
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "上传图片/视频";
        }
      }
    }

    function closeAssetUploadModal() {
      $("assetUploadModal")?.classList.add("hidden");
      if ($("assetLibraryUploadStatus")) $("assetLibraryUploadStatus").textContent = "";
    }

    function closeAssetAvatarModal() {
      $("assetAvatarModal")?.classList.add("hidden");
      if ($("assetAvatarStatus")) $("assetAvatarStatus").textContent = "";
    }

    function closeAssetVoiceModal() {
      $("assetVoiceModal")?.classList.add("hidden");
      if ($("assetVoiceStatus")) $("assetVoiceStatus").textContent = "";
    }

    function openAssetLibraryAddModal() {
      const section = state.assetLibrarySection || "uploads";
      if (section === "avatars") {
        if ($("assetAvatarForm")) $("assetAvatarForm").reset();
        if ($("assetAvatarModel")) $("assetAvatarModel").value = "2";
        syncAssetAvatarFileAccept();
        $("assetAvatarModal")?.classList.remove("hidden");
        setTimeout(() => $("assetAvatarName")?.focus(), 80);
        return;
      }
      if (section === "voices") {
        if ($("assetVoiceForm")) $("assetVoiceForm").reset();
        $("assetVoiceModal")?.classList.remove("hidden");
        setTimeout(() => $("assetVoiceName")?.focus(), 80);
        return;
      }
      if ($("assetUploadForm")) $("assetUploadForm").reset();
      $("assetUploadModal")?.classList.remove("hidden");
    }

    function syncAssetAvatarFileAccept() {
      const type = (($("assetAvatarSourceType") && $("assetAvatarSourceType").value) || "image").trim();
      if ($("assetAvatarFile")) $("assetAvatarFile").accept = type === "video" ? "video/*,.mp4,.mov" : "image/*,.jpg,.jpeg,.png";
      if ($("assetAvatarModel")) $("assetAvatarModel").closest(".field")?.classList.toggle("hidden", type === "video");
    }

    async function submitAssetAvatarForm(evt) {
      evt.preventDefault();
      const file = $("assetAvatarFile") && $("assetAvatarFile").files ? $("assetAvatarFile").files[0] : null;
      if (!file) return toast("请选择素材文件");
      const title = (($("assetAvatarName") && $("assetAvatarName").value) || file.name || "未命名形象").trim();
      const sourceType = (($("assetAvatarSourceType") && $("assetAvatarSourceType").value) || "image").trim();
      const fd = new FormData();
      fd.append("title", title);
      fd.append("file", file, file.name || "avatar");
      if (sourceType !== "video") fd.append("model", (($("assetAvatarModel") && $("assetAvatarModel").value) || "2"));
      const btn = $("assetAvatarSubmit");
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      if ($("assetAvatarStatus")) $("assetAvatarStatus").textContent = "正在提交克隆任务";
      try {
        const endpoint = sourceType === "video" ? "/api/hifly/my/avatar/create-by-video-upload" : "/api/hifly/my/avatar/create-by-image-upload";
        const resp = await fetch(apiUrl(endpoint), { method: "POST", headers: authHeaders(), body: fd });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || data.message || `提交失败：HTTP ${resp.status}`);
        state.assetLibraryAvatarPage = 1;
        closeAssetAvatarModal();
        await loadAssetLibraryAvatars();
        toast("形象克隆任务已提交");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "开始克隆";
        }
      }
    }

    async function submitAssetVoiceForm(evt) {
      evt.preventDefault();
      const file = $("assetVoiceFile") && $("assetVoiceFile").files ? $("assetVoiceFile").files[0] : null;
      if (!file) return toast("请选择声音文件");
      const title = (($("assetVoiceName") && $("assetVoiceName").value) || file.name || "未命名声音").trim();
      const fd = new FormData();
      fd.append("title", title);
      fd.append("file", file, file.name || "voice.mp3");
      const btn = $("assetVoiceSubmit");
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      if ($("assetVoiceStatus")) $("assetVoiceStatus").textContent = "正在提交克隆任务";
      try {
        const resp = await fetch(apiUrl("/api/hifly/my/voice/create-upload"), { method: "POST", headers: authHeaders(), body: fd });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || data.message || `提交失败：HTTP ${resp.status}`);
        state.assetLibraryVoicePage = 1;
        closeAssetVoiceModal();
        await loadAssetLibraryVoices();
        toast("声音克隆任务已提交");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "开始克隆";
        }
      }
    }

    async function deleteHiflyAsset(kind, id) {
      const cleanKind = kind === "voice" ? "voice" : "avatar";
      if (!id) return;
      if (!confirm(`删除这个${cleanKind === "voice" ? "声音" : "形象"}分身？`)) return;
      await api(`/api/hifly/my/${cleanKind}/${encodeURIComponent(id)}`, { method: "DELETE" });
      closeAssetPreviewDialog();
      if (cleanKind === "voice") await loadAssetLibraryVoices();
      else await loadAssetLibraryAvatars();
      state.hiflyLoaded = false;
      toast("已删除");
    }

    function leadPlatformLabel(platform) {
      const raw = cleanKey(platform);
      return {
        redbook: "小红书",
        xiaohongshu: "小红书",
        douyin: "抖音",
        kuaishou: "快手",
        wechat_channels: "视频号",
        wechat: "视频号",
        reddit: "Reddit",
        x: "X",
        tiktok: "TikTok",
        linkedin: "LinkedIn",
      }[raw] || socialPlatformLabel(raw || platform || "all");
    }

    function leadCountFromPayload(payload) {
      const data = payload && typeof payload === "object" ? payload : {};
      const summary = data.lead_summary && typeof data.lead_summary === "object" ? data.lead_summary : {};
      const intent = data.intent_analysis && typeof data.intent_analysis === "object" ? data.intent_analysis : {};
      const candidates = Array.isArray(data.candidates) ? data.candidates : [];
      return Number(summary.candidate_count || summary.total || intent.candidate_count || candidates.length || 0);
    }

    function leadCountFromJob(job) {
      const sourceSummary = job && job.source_summary && typeof job.source_summary === "object" ? job.source_summary : {};
      const sources = Array.isArray(job && job.source_items) ? job.source_items : [];
      return Number(leadCountFromPayload(job && job.result_payload) || sourceSummary.total || sources.length || 0);
    }

    function douyinRunLeadCount(run) {
      const payload = run && run.result_payload && typeof run.result_payload === "object" ? run.result_payload : {};
      const raw = [
        payload.lead_count,
        payload.customer_count,
        payload.total_customers,
        payload.total,
        Array.isArray(payload.leads) ? payload.leads.length : 0,
        Array.isArray(payload.customers) ? payload.customers.length : 0,
      ].map((value) => Number(value || 0)).find((value) => value > 0);
      return Number(raw || 0);
    }

    function leadDomainForRun(run) {
      const payload = run && run.payload && typeof run.payload === "object" ? run.payload : {};
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      const action = cleanKey(payload.action || params.action || payload.workflow_action || "");
      return /(private|message|friend|group|comment)/.test(action) ? "private" : "public";
    }

    function leadSourceLabel(type) {
      return {
        social: "线索采集",
        tikhub: "平台资料",
        douyin: "获客任务",
        linkedin: "线索采集",
      }[type] || "线索记录";
    }

    function leadRecords() {
      const social = (state.socialLeadJobs || []).map((job) => ({
        type: "social",
        domain: "public",
        platform: cleanKey(job.platform || (job.request_payload || {}).platform || "all"),
        source: leadSourceLabel("social"),
        title: job.title || `${leadPlatformLabel(job.platform || (job.request_payload || {}).platform)}线索采集`,
        count: leadCountFromJob(job),
        status: jobStatusText(job.status),
        time: job.completed_at || job.updated_at || job.created_at,
        raw: job,
      }));
      const tikhub = (state.tikhubRecords || []).map((row) => ({
        type: "tikhub",
        domain: "public",
        platform: cleanKey(row.platform || "douyin"),
        source: leadSourceLabel("tikhub"),
        title: `${leadPlatformLabel(row.platform)}资料采集`,
        count: Number(row.result_count || 0),
        status: row.success ? "完成" : (row.status || "失败"),
        time: row.created_at || row.updated_at,
        raw: row,
      }));
      const douyinRuns = (state.runs || [])
        .filter((row) => String(row && row.task_kind || "") === "douyin_leads")
        .map((row) => ({
          type: "douyin",
          domain: leadDomainForRun(row),
          platform: "douyin",
          source: leadSourceLabel("douyin"),
          title: row.title || "抖音获客",
          count: douyinRunLeadCount(row),
          status: statusText(row.status),
          time: row.finished_at || row.updated_at || row.created_at,
          raw: row,
        }));
      const linkedin = (state.linkedinJobs || []).map((job) => ({
        type: "linkedin",
        domain: "public",
        platform: "linkedin",
        source: leadSourceLabel("linkedin"),
        title: job.title || "LinkedIn线索挖掘",
        count: leadCountFromPayload(job.result_payload),
        status: jobStatusText(job.status),
        time: job.completed_at || job.updated_at || job.created_at,
        raw: job,
      }));
      return [...douyinRuns, ...social, ...linkedin, ...tikhub]
        .sort((a, b) => itemTimeMs(b.time) - itemTimeMs(a.time));
    }

    function leadPayloads(record) {
      const raw = record && record.raw && typeof record.raw === "object" ? record.raw : {};
      return [
        raw.result_payload,
        raw.result_snapshot,
        raw.response_payload,
        raw.response,
        raw.data,
        raw.payload,
        raw,
      ].filter((item) => item && typeof item === "object");
    }

    function leadArraysFromObject(obj) {
      const out = [];
      if (!obj || typeof obj !== "object") return out;
      ["leads", "customers", "candidates", "items", "source_items", "accounts", "users", "comments", "posts", "videos", "works"].forEach((key) => {
        if (Array.isArray(obj[key])) out.push(obj[key]);
      });
      ["lead_summary", "intent_analysis", "result", "results", "detail", "details"].forEach((key) => {
        if (obj[key] && typeof obj[key] === "object") out.push(...leadArraysFromObject(obj[key]));
      });
      return out;
    }

    function leadDetailItems(record) {
      const seen = new Set();
      const items = [];
      leadPayloads(record).forEach((payload) => {
        leadArraysFromObject(payload).forEach((rows) => {
          rows.forEach((item) => {
            if (!item || typeof item !== "object") return;
            const key = String(item.id || item.user_id || item.uid || item.sec_uid || item.url || item.username || item.nickname || JSON.stringify(item).slice(0, 140));
            if (seen.has(key)) return;
            seen.add(key);
            items.push(item);
          });
        });
      });
      return items;
    }

    function leadTextValue(item, keys) {
      for (const key of keys) {
        const value = item && item[key];
        if (value == null || value === "") continue;
        if (Array.isArray(value)) {
          const text = value.map((entry) => typeof entry === "object" ? leadTextValue(entry, ["text", "content", "title", "name", "keyword"]) : String(entry || "")).filter(Boolean).join("、");
          if (text) return text;
        } else if (typeof value === "object") {
          const text = leadTextValue(value, ["text", "content", "title", "name", "nickname", "username", "desc", "summary"]);
          if (text) return text;
        } else {
          const text = String(value || "").trim();
          if (text) return text;
        }
      }
      return "";
    }

    function leadEvidenceTexts(item) {
      const raw = [];
      ["evidence", "evidences", "source_evidence", "proofs", "matched_keywords", "keywords"].forEach((key) => {
        const value = item && item[key];
        if (Array.isArray(value)) raw.push(...value);
        else if (value) raw.push(value);
      });
      const reason = leadTextValue(item, ["reason", "match_reason", "analysis", "intent_reason", "summary"]);
      if (reason) raw.unshift(reason);
      return raw.map((entry) => {
        if (entry == null) return "";
        if (typeof entry === "object") return leadTextValue(entry, ["text", "content", "title", "comment", "keyword", "reason", "url"]);
        return String(entry || "").trim();
      }).filter(Boolean).slice(0, 4);
    }

    function leadItemHtml(item, index) {
      const title = leadTextValue(item, ["nickname", "display_name", "username", "name", "author_name", "author", "title", "id", "user_id", "uid"]) || `客资 ${index + 1}`;
      const body = leadTextValue(item, ["bio", "description", "desc", "summary", "text", "content", "comment", "caption", "post_text"]);
      const account = leadTextValue(item, ["handle", "unique_id", "sec_uid", "account", "profile_url", "url"]);
      const score = leadTextValue(item, ["score", "intent_score", "lead_score", "confidence"]);
      const evidence = leadEvidenceTexts(item);
      return `<article class="lead-detail-item">
        <div class="lead-detail-item-head">
          <strong>${escapeHtml(title)}</strong>
          ${score ? `<span>${escapeHtml(score)}分</span>` : ""}
        </div>
        ${account ? `<div class="lead-detail-meta">${escapeHtml(account)}</div>` : ""}
        ${body ? `<p>${escapeHtml(body)}</p>` : ""}
        ${evidence.length ? `<div class="lead-evidence">${evidence.map((text) => `<em>${escapeHtml(text)}</em>`).join("")}</div>` : ""}
      </article>`;
    }

    function leadDetailSummaryHtml(record, itemCount = 0) {
      return `<div class="lead-detail-summary">
        <div><span>平台</span><strong>${escapeHtml(leadPlatformLabel(record.platform))}</strong></div>
        <div><span>数量</span><strong>${escapeHtml(compactNumber(record.count || itemCount || 0))}</strong></div>
        <div><span>状态</span><strong>${escapeHtml(record.status || "-")}</strong></div>
        <div><span>时间</span><strong>${escapeHtml(fmtTime(record.time))}</strong></div>
      </div>`;
    }

    function leadDetailLoadingHtml(record) {
      return `${leadDetailSummaryHtml(record)}
        <div class="lead-detail-list">
          ${Array.from({ length: 4 }).map(() => `<div class="lead-detail-item lead-detail-skeleton">
            <i></i><b></b><span></span>
          </div>`).join("")}
        </div>`;
    }

    function leadDetailContentHtml(record, items) {
      return `
        ${leadDetailSummaryHtml(record, items.length)}
        <div class="lead-detail-list">
          ${items.length ? items.slice(0, 40).map(leadItemHtml).join("") : `<div class="lead-empty lead-detail-empty"><span>这条记录没有保存可展开的客资明细。</span></div>`}
        </div>`;
    }

    async function hydrateLeadDetailRecord(record) {
      if (!record || record.type !== "tikhub") return record;
      const queryId = record.raw && record.raw.query_id;
      if (!queryId) return record;
      if (state.tikhubRecordDetails[queryId]) {
        record.raw = { ...record.raw, ...state.tikhubRecordDetails[queryId], summary_only: false };
        return record;
      }
      const detail = await api(`/api/ip-content/tikhub/records/${encodeURIComponent(queryId)}`);
      state.tikhubRecordDetails[queryId] = detail || {};
      record.raw = { ...record.raw, ...(detail || {}), summary_only: false };
      record.count = Number((detail && detail.result_count) || record.count || 0);
      return record;
    }

    async function openLeadDetail(index) {
      const record = (state.leadCenterRows || [])[Number(index)];
      if (!record) return;
      const modal = $("leadDetailDialog");
      const body = $("leadDetailBody");
      const title = $("leadDetailTitle");
      if (!modal || !body) return;
      modal.classList.remove("hidden");
      if (title) title.textContent = record.title || "客资明细";
      body.innerHTML = leadDetailLoadingHtml(record);
      try {
        const hydrated = await hydrateLeadDetailRecord(record);
        const items = leadDetailItems(hydrated);
        body.innerHTML = leadDetailContentHtml(hydrated, items);
      } catch (err) {
        body.innerHTML = `${leadDetailSummaryHtml(record)}
          <div class="lead-empty lead-detail-empty"><span>${escapeHtml((err && err.message) || "客资明细加载失败")}</span></div>`;
      }
    }

    function renderLeadCenter() {
      const list = $("leadCenterList");
      if (!list) return;
      const all = leadRecords();
      const publicTotal = all.filter((row) => row.domain === "public").reduce((sum, row) => sum + Number(row.count || 0), 0);
      const privateTotal = all.filter((row) => row.domain === "private").reduce((sum, row) => sum + Number(row.count || 0), 0);
      const friendCount = all.filter((row) => row.domain === "private").reduce((sum, row) => sum + Number(row.count || 0), 0);
      if ($("leadPublicTotal")) $("leadPublicTotal").textContent = compactNumber(publicTotal);
      if ($("leadPrivateTotal")) $("leadPrivateTotal").textContent = compactNumber(privateTotal);
      if ($("leadTotalCustomers")) $("leadTotalCustomers").textContent = compactNumber(publicTotal + privateTotal);
      if ($("leadClueCount")) $("leadClueCount").textContent = compactNumber(all.reduce((sum, row) => sum + Number(row.count || 0), 0));
      if ($("leadFriendCount")) $("leadFriendCount").textContent = compactNumber(friendCount);
      if ($("leadPullGroupCount")) $("leadPullGroupCount").textContent = "0";
      document.querySelectorAll("[data-lead-domain]").forEach((btn) => btn.classList.toggle("active", btn.dataset.leadDomain === state.leadCenterDomain));
      document.querySelectorAll("[data-lead-platform]").forEach((btn) => btn.classList.toggle("active", btn.dataset.leadPlatform === state.leadCenterPlatform));
      const platform = state.leadCenterPlatform || "all";
      const rows = all.filter((row) => row.domain === state.leadCenterDomain)
        .filter((row) => platform === "all" || cleanKey(row.platform) === platform || (platform === "redbook" && cleanKey(row.platform) === "xiaohongshu"));
      state.leadCenterRows = rows.slice(0, 10);
      if (state.leadCenterLoading) {
        list.innerHTML = Array.from({ length: 6 }).map(() => `<div class="lead-card lead-card-skeleton" aria-hidden="true">
          <i></i><b></b><span></span>
        </div>`).join("");
        return;
      }
      if (!rows.length) {
        list.innerHTML = `<div class="lead-empty"><div class="lead-empty-box"></div><span>暂无客资线索</span></div>`;
        return;
      }
      list.innerHTML = state.leadCenterRows.map((row, index) => `<button class="lead-card" type="button" data-lead-detail-index="${index}">
        <div class="lead-card-main">
          <strong>${escapeHtml(row.title || "线索记录")}</strong>
          <span>${escapeHtml(leadPlatformLabel(row.platform))} · ${escapeHtml(fmtTime(row.time))}</span>
        </div>
        <div class="lead-card-foot">
          <em>${escapeHtml(compactNumber(row.count || 0))}</em>
          <b>${escapeHtml(row.status || "")}</b>
        </div>
      </button>`).join("");
    }

    async function loadLeadCenterData() {
      if (!state.token) return;
      state.leadCenterLoading = true;
      renderLeadCenter();
      try {
        await Promise.allSettled([
          loadRuns({ reset: true, limit: 10 }),
          loadWorkbenchJobs({ limit: 10 }),
          api("/api/ip-content/tikhub/records?limit=10&offset=0").then((data) => {
            state.tikhubRecords = Array.isArray(data.items) ? data.items : [];
          }),
        ]);
      } finally {
        state.leadCenterLoading = false;
        renderLeadCenter();
      }
    }

    function collectPlatforms(row) {
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      const result = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const draft = publishDraftFromPayload ? publishDraftFromPayload({ ...result, run_id: row && row.id }) : null;
      const raw = [
        payload.platform,
        payload.platform_name,
        payload.publish_platform,
        result.platform,
        result.platform_name,
        draft && (draft.platform_name || draft.platform),
      ].filter(Boolean);
      return raw.map((v) => String(v).trim()).filter(Boolean);
    }

    function workSortTime(item) {
      const d = parseDate(item && (item.sortTime || item.time || item.createdTime || item.updatedTime));
      return d ? d.getTime() : 0;
    }

    function workDisplayTime(item) {
      if (item && item.type === "future") return item.time || item.createdTime || item.updatedTime;
      return item.time || item.createdTime || item.updatedTime;
    }

    function messageWorkBadge(row) {
      const status = String((row && row.status) || "").toLowerCase();
      if (status === "pending") return "任务提交中";
      if (status === "processing") return "生成中";
      return statusText(row && row.status);
    }

    function taskHasRun(task, runs) {
      const id = String(task && task.id || "");
      if (!id) return false;
      return (runs || []).some((row) => String(row && row.task_id || "") === id);
    }

    function taskIsFutureWork(task, runs) {
      const status = String((task && task.status) || "").toLowerCase();
      if (status === "cancelled") return false;
      if (status === "completed" && String(task && task.schedule_type || "").toLowerCase() === "once" && taskHasRun(task, runs)) return false;
      return true;
    }

    function taskShouldShowInWorkList(task, runs) {
      if (!task) return false;
      if (taskIsFutureWork(task, runs)) return true;
      return !taskHasRun(task, runs);
    }

    function taskScheduleLabel(task) {
      const type = String((task && task.schedule_type) || "").toLowerCase();
      const config = task && task.schedule_config && typeof task.schedule_config === "object" ? task.schedule_config : {};
      const dailyTimes = Array.isArray(task && task.daily_times) ? task.daily_times
        : (Array.isArray(config.daily_times) ? config.daily_times : []);
      if (type === "daily_times") return dailyTimes.length ? `每天 ${dailyTimes.join("、")}` : "每天定时";
      if (type === "interval") {
        const seconds = Number((task && task.interval_seconds) || config.interval_seconds || 0);
        const minutes = Math.max(1, Math.round(seconds / 60) || 1);
        return `每 ${minutes} 分钟`;
      }
      if (task && task.next_run_at) return `下次 ${fmtTime(task.next_run_at)}`;
      return "一次性";
    }

    function jobStatusText(status) {
      const s = String(status || "").toLowerCase();
      return {
        queued: "排队中",
        pending: "待执行",
        running: "执行中",
        processing: "执行中",
        completed: "完成",
        failed: "失败",
        cancelled: "已取消",
      }[s] || statusText(s);
    }

    function workbenchJobItem(job, kind) {
      if (!job) return null;
      const req = job.request_payload && typeof job.request_payload === "object" ? job.request_payload : {};
      const platform = String(job.platform || req.platform || "").trim();
      const titlePrefix = kind === "linkedin"
        ? "LinkedIn线索挖掘"
        : (kind === "wechat" ? "视频号文案提取" : `${socialPlatformLabel(platform)}线索采集`);
      const status = String(job.status || "").toLowerCase();
      const progress = Number(job.progress || 0);
      const stage = job.stage || job.current_step || "";
      const progressText = Number.isFinite(progress) && progress > 0 ? `进度 ${Math.min(100, Math.max(0, progress))}%` : "";
      const meta = String(job.error || stage || progressText || "工作台任务").slice(0, 90);
      return {
        type: status === "failed" ? "failed" : (isActiveMessageStatus(status) ? "current" : "done"),
        time: job.completed_at || job.updated_at || job.created_at,
        sortTime: job.completed_at || job.updated_at || job.created_at,
        createdTime: job.created_at,
        title: job.title || titlePrefix,
        badge: jobStatusText(status),
        meta,
        actionTaskId: "",
      };
    }

    function workbenchJobMatchesScope(job, kind, scope) {
      const item = scope || { type: "all" };
      if (!job || item.type === "all") return true;
      const req = job.request_payload && typeof job.request_payload === "object" ? job.request_payload : {};
      const keys = new Set();
      if (kind === "linkedin") addKey(keys, "linkedin_leads");
      if (kind === "wechat") addKey(keys, "wechat_channels_transcript");
      if (kind === "social") {
        const platform = cleanKey(job.platform || req.platform);
        addKey(keys, "social_leads");
        addKey(keys, platform);
        if (platform) addKey(keys, `${platform}_leads`);
      }
      addKey(keys, job.title);
      addKey(keys, req.platform);
      const fake = { task_kind: kind === "linkedin" ? "linkedin_mining" : (kind === "wechat" ? "wechat_channels_transcript" : "social_leads"), payload: req, title: job.title };
      rowMatchKeys(fake).forEach((key) => keys.add(key));
      if (item.type === "department") {
        const department = departmentById(item.departmentId);
        const departmentKeys = departmentNodeKeys(department);
        return Array.from(keys).some((key) => departmentKeys.has(key));
      }
      if (item.type === "ability") {
        const lookup = abilityLookup(item.abilityKey);
        if (!lookup) return false;
        const abilityKeys = collectAbilityKeys(lookup.node, item.includeChildren !== false);
        return Array.from(keys).some((key) => abilityKeys.has(key));
      }
      return true;
    }

    function messageMatchesWorkScope(entry, scope) {
      const item = scope || { type: "all" };
      if (item.type === "all") return true;
      const msg = entry && entry.message ? entry.message : entry;
      const content = String((msg && msg.content) || "").trim();
      if (!content) return false;
      if (item.type === "department") {
        const dept = departmentById(item.departmentId);
        return !!dept && (content.includes(`部门：${dept.name}`) || content.includes(`department_id:${dept.id}`));
      }
      if (item.type === "ability") {
        const lookup = abilityLookup(item.abilityKey);
        const label = lookup && lookup.node && lookup.node.label;
        return content.includes(`能力标记：${item.abilityKey}`) || (label && content.includes(label));
      }
      return true;
    }

    function runWorkMeta(row) {
      if (isServerSideRun(row)) {
        const progress = row && row.progress && (row.progress.text || row.progress.message || row.progress.status);
        return progress || `${capabilityName(taskCapabilityId(row) || row.task_kind)} · 服务器执行`;
      }
      const owner = runInstallationId(row);
      return `${capabilityName(taskCapabilityId(row) || row.task_kind)} · ${owner ? "员工 " + shortId(owner) : "等待员工领取"}`;
    }

    function mergeRuns(rows) {
      if (!Array.isArray(rows) || !rows.length) return;
      const byId = new Map((state.runs || []).map((row) => [String(row.id || ""), row]));
      rows.forEach((row) => {
        if (!row || !row.id) return;
        byId.set(String(row.id), { ...(byId.get(String(row.id)) || {}), ...row });
      });
      state.runs = Array.from(byId.values()).sort((a, b) => itemTimeMs(b.updated_at, b.created_at) - itemTimeMs(a.updated_at, a.created_at));
    }

    function removeRun(runId) {
      const id = String(runId || "");
      if (!id) return;
      state.runs = (state.runs || []).filter((row) => String(row.id || "") !== id);
    }

    function addOptimisticRun(body, title, isIpDaily) {
      const now = new Date().toISOString();
      const run = {
        id: `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
        task_id: "",
        title: title || capabilityName(state.taskAbility),
        task_kind: isIpDaily ? "ip_content_daily" : "capability",
        payload: body && body.payload ? body.payload : {},
        status: "processing",
        progress: { server_side: !!isIpDaily, text: isIpDaily ? "服务器任务已提交" : "任务已提交，等待员工领取" },
        server_side: !!isIpDaily,
        created_at: now,
        updated_at: now,
      };
      mergeRuns([run]);
      renderOfficeEmployees();
      renderWorkList();
      return run.id;
    }

    function runProgressText(row) {
      if (!row) return "";
      if (isServerSideRun(row)) {
        const progress = row.progress && (row.progress.text || row.progress.message || row.progress.phase || row.progress.status);
        if (progress) return progress;
        if (String(row.status || "").toLowerCase() === "pending") return "服务器任务已排队";
        if (String(row.status || "").toLowerCase() === "processing") return "服务器执行中";
        return row.title || "服务器任务";
      }
      return (row.progress && (row.progress.text || row.progress.message || row.progress.phase || row.progress.status))
        || (row.status === "pending" ? "任务提交中，等待员工领取" : row.title)
        || "正在执行任务";
    }

    function renderWorkList() {
      const timeline = $("workTimeline");
      if (!timeline) return;
      renderWorkScopeBar();
      const scope = state.workListScope || { type: "all", label: "全部记录" };
      const tasks = (state.tasks || []).filter((row) => recordMatchesWorkScope(row, scope));
      const runs = (state.runs || []).filter((row) => recordMatchesWorkScope(row, scope));
      const platforms = new Set();
      [...tasks, ...runs].forEach((row) => collectPlatforms(row).forEach((p) => platforms.add(p)));
      const scopedSocialJobs = (state.socialLeadJobs || []).filter((job) => workbenchJobMatchesScope(job, "social", scope));
      const scopedLinkedinJobs = (state.linkedinJobs || []).filter((job) => workbenchJobMatchesScope(job, "linkedin", scope));
      const scopedWechatJobs = (state.wechatTranscriptJobs || []).filter((job) => workbenchJobMatchesScope(job, "wechat", scope));
      scopedSocialJobs.forEach((job) => {
        const platform = String((job && (job.platform || (job.request_payload || {}).platform)) || "").trim();
        if (platform) platforms.add(socialPlatformLabel(platform));
      });
      if (scopedLinkedinJobs.length) platforms.add("LinkedIn");
      if (scopedWechatJobs.length) platforms.add("视频号");
      const current = runs.filter(isActiveRun).slice(0, 8).map((row) => ({
        type: "current",
        runId: row.id || "",
        time: row.started_at || row.claimed_at || row.created_at,
        sortTime: row.updated_at || row.started_at || row.claimed_at || row.created_at,
        createdTime: row.created_at,
        title: row.title || "正在执行的任务",
        badge: statusText(row.status),
        meta: runWorkMeta(row),
        actionTaskId: "",
      }));
      const done = runs.filter((row) => !isActiveRun(row)).slice(0, 18).map((row) => ({
        type: String(row.status).toLowerCase() === "failed" ? "failed" : "done",
        runId: row.id || "",
        time: row.finished_at || row.updated_at || row.created_at,
        sortTime: row.finished_at || row.updated_at || row.created_at,
        createdTime: row.created_at,
        title: row.title || "已执行任务",
        badge: statusText(row.status),
        meta: (row.error || row.result_text || (row.progress && (row.progress.text || row.progress.message)) || capabilityName(taskCapabilityId(row) || row.task_kind) || "已记录").slice(0, 90),
        actionTaskId: "",
      }));
      const messages = (state.historyItems || []).filter((entry) => messageMatchesWorkScope(entry, scope)).slice(-18).map((entry) => {
        const row = entry && entry.message ? entry.message : entry;
        const status = String((row && row.status) || "").toLowerCase();
        const progress = latestProgressTextForMessage(entry);
        return {
          type: status === "failed" ? "failed" : (isActiveMessageStatus(status) ? "current" : "done"),
          time: row.finished_at || row.updated_at || row.created_at,
          sortTime: row.finished_at || row.updated_at || row.created_at,
          createdTime: row.created_at,
          title: status === "pending" ? "任务提交中" : (status === "processing" ? "生成中" : "手机会话"),
          badge: messageWorkBadge(row),
          meta: String(row.error || progress || row.reply_text || row.content || "会话消息").slice(0, 90),
          actionTaskId: "",
        };
      });
      const workbenchJobs = [
        ...scopedSocialJobs.map((job) => workbenchJobItem(job, "social")),
        ...scopedLinkedinJobs.map((job) => workbenchJobItem(job, "linkedin")),
        ...scopedWechatJobs.map((job) => workbenchJobItem(job, "wechat")),
      ].filter(Boolean);
      const scheduled = tasks
        .filter((row) => taskShouldShowInWorkList(row, runs))
        .slice(0, 80)
        .map((row) => {
          const active = taskIsFutureWork(row, runs);
          return {
            type: active ? "future" : "scheduled",
            time: row.next_run_at || row.updated_at || row.created_at,
            sortTime: row.next_run_at || row.updated_at || row.created_at,
            createdTime: row.created_at,
            title: row.title || capabilityName(taskCapabilityId(row) || row.task_kind),
            badge: row.next_run_at ? "待执行" : statusText(row.status),
            meta: `${capabilityName(taskCapabilityId(row) || row.task_kind)} / ${taskScheduleLabel(row)}`,
            actionTaskId: active ? (row.id || "") : "",
          };
        });
      const items = [...current, ...scheduled, ...workbenchJobs, ...done, ...messages]
        .filter((item) => item.title)
        .sort((a, b) => workSortTime(b) - workSortTime(a))
        .slice(0, 80);
      if ($("workListSubtitle")) $("workListSubtitle").textContent = `查询：${(scope && scope.label) || "全部记录"} · ${items.length} 条`;
      if (!items.length) {
        timeline.innerHTML = `<div class="office-empty">当前查询条件下暂无工作记录。</div>`;
        return;
      }
      timeline.innerHTML = items.map((item) => `<div class="work-node">
        <div class="work-time"><strong>${escapeHtml(timeLabel(workDisplayTime(item)))}</strong><span>${escapeHtml(fmtTime(workDisplayTime(item)))}</span></div>
        <div class="work-card ${escapeHtml(item.type)}" ${item.runId ? `data-open-run-detail="${escapeHtml(item.runId)}"` : ""}>
          <div class="work-card-top"><div class="work-card-title">${escapeHtml(item.title)}</div><span class="work-badge">${escapeHtml(item.badge)}</span></div>
          <div class="work-meta">${escapeHtml(item.meta || "")}</div>
          ${item.actionTaskId ? `<div class="work-card-actions"><button type="button" data-run-task-now="${escapeHtml(item.actionTaskId)}">立即执行</button></div>` : ""}
        </div>
      </div>`).join("");
    }

    function ipTaskLabel(task) {
      return {
        industry_hot_oral: "行业热门口播",
        professional_ip_oral: "专业 IP 口播",
        moments_candidate: "朋友圈文案",
      }[String(task || "")] || task || "文案";
    }

    function workflowAction(row) {
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      return String(payload.action || "").trim();
    }

    function workflowParams(row) {
      const payload = row && row.payload && typeof row.payload === "object" ? row.payload : {};
      return payload.params && typeof payload.params === "object" ? payload.params : {};
    }

    function activeMomentImageRun(sourceRunId) {
      const source = String(sourceRunId || "").trim();
      if (!source) return null;
      const rows = (state.runs || []).filter((row) => {
        if (!row || row.task_kind !== "client_workflow") return false;
        if (workflowAction(row) !== "ip_moments_generate_images") return false;
        if (String(workflowParams(row).source_run_id || "").trim() !== source) return false;
        return isRunningStatus(row.status);
      });
      rows.sort((a, b) => itemTimeMs(b.created_at, b.updated_at) - itemTimeMs(a.created_at, a.updated_at));
      return rows[0] || null;
    }

    function latestMomentImageRun(sourceRunId) {
      const source = String(sourceRunId || "").trim();
      if (!source) return null;
      const rows = (state.runs || []).filter((row) => {
        if (!row || row.task_kind !== "client_workflow") return false;
        if (workflowAction(row) !== "ip_moments_generate_images") return false;
        return String(workflowParams(row).source_run_id || "").trim() === source;
      });
      rows.sort((a, b) => itemTimeMs(b.created_at, b.updated_at) - itemTimeMs(a.created_at, a.updated_at));
      return rows[0] || null;
    }

    function recordImages(rec) {
      if (!rec || typeof rec !== "object") return [];
      if (Array.isArray(rec.images) && rec.images.length) return rec.images;
      const meta = rec.meta && typeof rec.meta === "object" ? rec.meta : {};
      if (Array.isArray(meta.images) && meta.images.length) return meta.images;
      if (rec.image_url) return [{ image_url: rec.image_url, image_asset_id: rec.image_asset_id || "", image_prompt: rec.image_prompt || "", index: 1 }];
      return [];
    }

    function recordImagePrompts(rec) {
      const prompts = [];
      const add = (value) => {
        const text = String(value || "").trim();
        if (text && !prompts.includes(text)) prompts.push(text);
      };
      if (rec && Array.isArray(rec.image_prompts)) rec.image_prompts.forEach(add);
      const meta = rec && rec.meta && typeof rec.meta === "object" ? rec.meta : {};
      if (Array.isArray(meta.image_prompts)) meta.image_prompts.forEach(add);
      add(rec && rec.image_prompt);
      return prompts.slice(0, 3);
    }

    function momentImageTiles(images) {
      const rows = Array.isArray(images) ? images.filter((img) => img && (img.image_url || img.url)) : [];
      if (!rows.length) return "";
      return `<div class="moment-image-grid">${rows.map((img, idx) => {
        const url = img.image_url || img.url || "";
        const safeUrl = escapeHtml(mediaProxyUrl(url, "inline", filenameFromUrl(url, `moment-${idx + 1}.png`)));
        return `<div class="moment-image-tile">
          <a href="${safeUrl}" target="_blank" rel="noopener noreferrer"><img src="${safeUrl}" alt="朋友圈图片 ${escapeHtml(idx + 1)}"></a>
          <a href="${safeUrl}" target="_blank" rel="noopener noreferrer">打开图片</a>
        </div>`;
      }).join("")}</div>`;
    }

    function momentRecordHtml(rec, idx, imageBusy) {
      const recordId = String(rec.record_id || "").trim();
      const title = String(rec.title || `朋友圈文案 ${idx + 1}`).trim();
      const body = String(rec.body || rec.content || "").trim();
      const prompts = recordImagePrompts(rec);
      const images = recordImages(rec);
      const disabled = recordId ? "" : " disabled";
      const memoryDocIds = Array.isArray(rec.memory_doc_ids) ? rec.memory_doc_ids.map((id) => String(id || "").trim()).filter(Boolean).join(",") : "";
      return `<div class="moment-item" data-moment-record="${escapeHtml(recordId)}" data-moment-memory-doc-ids="${escapeHtml(memoryDocIds)}">
        <div class="moment-summary-row">
          <label class="summary-check"><input type="checkbox" data-draft-copy-select="1" data-draft-task="moments_candidate" data-moment-select="${escapeHtml(recordId)}"${disabled}>选择</label>
          <button class="moment-summary" type="button" data-toggle-moment="${escapeHtml(recordId || idx)}">
            <span>${escapeHtml(idx + 1)}. ${escapeHtml(title)}</span>
            <small>${images.length ? `已生成 ${images.length} 张图` : "展开"}</small>
          </button>
        </div>
        <div class="moment-detail">
          <div class="moment-select-row">
            ${recordId ? (imageBusy ? "<span>图片生成中，暂不可重复下发。</span>" : "<span>已选择后可复制，也可生成图片。</span>") : "<span>缺少 record_id，无法回写图片</span>"}
          </div>
          <div class="task-detail-moments-copy">${escapeHtml(body || "暂无正文")}</div>
          <div class="task-detail-prompts">
            ${prompts.length ? prompts.map((p, pIdx) => `<label><input type="checkbox" data-moment-prompt="${escapeHtml(recordId)}" value="${escapeHtml(pIdx)}" checked>配图 ${escapeHtml(pIdx + 1)}：${escapeHtml(p)}</label>`).join("") : "<span>暂无配图提示词，将根据正文生成。</span>"}
          </div>
          <div class="moment-image-status" data-moment-status="${escapeHtml(recordId)}">${images.length ? `已生成 ${images.length} 张图片` : ""}</div>
          <div data-moment-images="${escapeHtml(recordId)}">${momentImageTiles(images)}</div>
        </div>
      </div>`;
    }

    function copyRecordHtml(rec, idx, task) {
      const title = String(rec.title || `${ipTaskLabel(task)} ${idx + 1}`).trim();
      const body = String(rec.body || rec.content || "").trim();
      const id = String(rec.record_id || rec.id || `${task || "copy"}-${idx}`).trim();
      return `<div class="copy-item" data-copy-record="${escapeHtml(id)}">
        <div class="copy-summary-row">
          <label class="summary-check"><input type="checkbox" data-draft-copy-select="1" data-draft-task="${escapeHtml(task || "")}">选择</label>
          <button class="copy-summary" type="button" data-toggle-copy-record="${escapeHtml(id)}">
            <span>${escapeHtml(idx + 1)}. ${escapeHtml(title)}</span>
            <small>展开</small>
          </button>
        </div>
        <div class="copy-detail">
          <div class="task-detail-moments-copy">${escapeHtml(body || "暂无正文")}</div>
        </div>
      </div>`;
    }

    function valueLabel(value) {
      if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean).join("、");
      if (typeof value === "boolean") return value ? "是" : "否";
      return String(value == null ? "" : value).trim();
    }

    function runTaskInputPayload(run) {
      return run && run.payload && typeof run.payload === "object" ? run.payload : {};
    }

    function runInnerPayload(run) {
      const payload = runTaskInputPayload(run);
      return payload.payload && typeof payload.payload === "object" ? payload.payload : payload;
    }

    function runParameterRows(run) {
      const payload = runTaskInputPayload(run);
      const inner = runInnerPayload(run);
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      const rows = [];
      const add = (label, value) => {
        const text = valueLabel(value);
        if (text) rows.push([label, text]);
      };
      add("任务名称", run && run.title);
      if (payload.action) add("执行动作", payload.action);
      if (payload.platform) add("平台", socialPlatformLabel(payload.platform));
      if (payload.capability_id) add("能力", capabilityName(payload.capability_id));
      const sourceTask = (state.tasks || []).find((task) => String(task.id) === String(run && run.task_id)) || null;
      add("执行方式", taskScheduleLabel(sourceTask || { schedule_type: "once", schedule_config: payload.schedule_config || {}, next_run_at: "" }));
      add("模板ID", payload.template_id);
      add("生成内容", Array.isArray(payload.tasks) ? payload.tasks.map(ipTaskLabel) : "");
      add("执行前同步", payload.sync_before);
      const req = payload.requirements && typeof payload.requirements === "object" ? payload.requirements : {};
      add("补充要求", req.common || req.moments || req.oral || req.image);
      add("关键词/方向", payload.keywords || params.keyword || params.query || inner.prompt || inner.task_text);
      add("账号", payload.accounts || params.accounts);
      add("社区/来源", payload.communities || payload.source_keywords || params.source_keywords);
      add("采集上限", payload.max_items || params.max_results);
      add("目标画像", payload.target_profile);
      add("个人主页", payload.seed_profile_urls);
      add("公司主页", payload.seed_company_urls);
      add("话题标签", payload.hashtags);
      add("视频号查询", payload.query || payload.username);
      add("拉取页数", payload.max_pages);
      add("最多数量", payload.limit || payload.max_people);
      add("素材/图片", firstGoalVideoReference(inner) || inner.asset_id || inner.image_url || params.asset_id || params.image_url || params.original_video_url);
      add("视频要求", inner.task_text || inner.prompt || params.prompt);
      add("生成模式", goalVideoModeFromPayload(inner));
      add("首帧来源", inner.source_mode);
      add("记忆文件", inner.memory_doc_ids);
      add("备选素材组", inner.candidate_group);
      add("数字人", inner.avatar || params.avatar);
      add("声音", inner.voice || params.voice);
      add("口播文案", inner.script || params.script);
      add("公众号主题", inner.idea);
      add("文章风格", inner.style);
      add("PPT主题", inner.topic);
      add("页数", inner.slide_count || inner.page_count);
      add("商品要求", inner.task_text);
      add("发布账号", params.account_nickname || params.account || params.account_id);
      add("标题", params.title || inner.title);
      add("正文/描述", params.description || inner.description);
      add("标签", params.tags);
      add("地区", params.regions || params.region_list || params.area_list);
      add("评论内容", params.comment_text || params.reply_text || params.comment_content);
      add("私信内容", params.message || params.dm_text || params.private_message);
      return rows;
    }

    function runDetailActionsHtml(run) {
      if (!run || !run.id) return "";
      return `<div class="run-detail-actions">
        <button type="button" data-refill-run="${escapeHtml(run.id)}">重新执行</button>
      </div>`;
    }

    function taskDetailHtml(run) {
      const payload = run && run.result_payload && typeof run.result_payload === "object" ? run.result_payload : {};
      const sections = [];
      function renderTaskDetailSection(title, rows) {
        const normalizedRows = Array.isArray(rows)
          ? rows.filter((row) => Array.isArray(row) && String(row[1] == null ? "" : row[1]).trim() !== "")
          : [];
        if (!normalizedRows.length) return "";
        return `<div class="task-detail-section"><h4>${escapeHtml(title)}</h4>${normalizedRows.map(([label, value]) => `<div class="task-detail-record"><strong>${escapeHtml(label)}</strong><pre>${escapeHtml(String(value))}</pre></div>`).join("")}</div>`;
      }
      function renderTaskDetailRecord(title, lines) {
        const normalizedLines = (Array.isArray(lines) ? lines : [lines]).map((line) => String(line == null ? "" : line).trim()).filter(Boolean);
        if (!title && !normalizedLines.length) return "";
        return `<div class="task-detail-record"><strong>${escapeHtml(title || "结果")}</strong>${normalizedLines.map((line) => `<pre>${escapeHtml(line)}</pre>`).join("")}</div>`;
      }
      const inputRows = runParameterRows(run);
      sections.push(`<div class="task-detail-section"><h4>创建参数</h4>${inputRows.length ? inputRows.map(([label, value]) => `<div class="task-detail-record"><strong>${escapeHtml(label)}</strong><pre>${escapeHtml(String(value))}</pre></div>`).join("") : "<div class=\"hint\">暂无可展示参数。</div>"}${runDetailActionsHtml(run)}</div>`);
      function douyinLeadActionLabel(action) {
        return ((DOUYIN_TASK_ACTIONS[action] || {}).label || action || "抖音获客");
      }
      function renderDouyinLeadSummary(data) {
        const action = String(data.action || "").trim();
        const stats = data.stats && typeof data.stats === "object" ? data.stats : {};
        const finalStatus = data.final_status && typeof data.final_status === "object" ? data.final_status : {};
        const finalState = finalStatus.state && typeof finalStatus.state === "object" ? finalStatus.state : {};
        const rows = action ? [["执行动作", douyinLeadActionLabel(action)]] : [];
        if (action === "search_collect") {
          rows.push(
            ["执行模式", data.search_mode === "script" ? "脚本模式" : (data.search_mode || "")],
            ["执行账号", data.account_id || ""],
            ["搜索视频", data.search_videos_total != null ? `${compactNumber(data.search_videos_total)} 个` : (data.total != null ? `${compactNumber(data.total)} 个` : "")],
            ["采集客户", stats.comments_collected != null ? `${compactNumber(stats.comments_collected)} 人` : ""],
            ["精准客户", stats.high_intent_users != null ? `${compactNumber(stats.high_intent_users)} 人` : ""],
          );
        } else if (action === "tasks_from_search") {
          rows.push(
            ["本次同步", data.selected_total != null ? `${compactNumber(data.selected_total)} 条` : ""],
            ["任务池累计", data.total != null ? `${compactNumber(data.total)} 条` : ""],
          );
        } else if (action === "comment_collect") {
          rows.push(
            ["任务总数", stats.tasks_total != null ? compactNumber(stats.tasks_total) : ""],
            ["已完成任务", stats.tasks_completed != null ? compactNumber(stats.tasks_completed) : ""],
            ["评论采集数", stats.comments_collected != null ? compactNumber(stats.comments_collected) : ""],
            ["高意向客户", stats.high_intent_users != null ? compactNumber(stats.high_intent_users) : ""],
          );
        } else if (action === "interaction") {
          rows.push(
            ["目标客户", stats.users_total != null ? compactNumber(stats.users_total) : ""],
            ["已处理", finalState.processed != null ? compactNumber(finalState.processed) : ""],
            ["成功人数", stats.users_success != null ? compactNumber(stats.users_success) : ""],
            ["失败人数", stats.users_failed != null ? compactNumber(stats.users_failed) : ""],
          );
        } else {
          rows.push(
            ["结果数量", data.total != null ? compactNumber(data.total) : ""],
            ["任务总数", stats.tasks_total != null ? compactNumber(stats.tasks_total) : ""],
            ["用户总数", stats.users_total != null ? compactNumber(stats.users_total) : ""],
          );
        }
        return renderTaskDetailSection("任务概览", rows);
      }
      function renderDouyinLeadPreview(data) {
        const action = String(data.action || "").trim();
        const stats = data.stats && typeof data.stats === "object" ? data.stats : {};
        const rows = [];
        if (action === "search_collect") {
          const items = Array.isArray(data.items) ? data.items : [];
          const selectedVideo = data.selected_video && typeof data.selected_video === "object" ? data.selected_video : {};
          const selectedLines = [
            selectedVideo.author ? `作者：${selectedVideo.author}` : "",
            selectedVideo.url ? `视频链接：${selectedVideo.url}` : "",
            stats.comments_collected != null ? `采集客户：${compactNumber(stats.comments_collected)} 人` : "",
            stats.high_intent_users != null ? `精准客户：${compactNumber(stats.high_intent_users)} 人` : "",
          ].filter(Boolean);
          if (selectedLines.length || selectedVideo.title) {
            rows.push(renderTaskDetailRecord(`首个采集视频${selectedVideo.title ? ` · ${selectedVideo.title}` : ""}`, selectedLines));
          }
          items.slice(0, 6).forEach((row, idx) => {
            const item = row && typeof row === "object" ? row : {};
            const title = String(item.title || item.desc || item.aweme_id || `结果 ${idx + 1}`).trim();
            const meta = [
              item.author_name || item.author ? `作者：${item.author_name || item.author}` : "",
              item.likes != null ? `点赞：${compactNumber(item.likes)}` : "",
              item.comments != null ? `评论：${compactNumber(item.comments)}` : "",
              item.publish_time ? `发布时间：${item.publish_time}` : "",
            ].filter(Boolean).join(" · ");
            rows.push(renderTaskDetailRecord(`${idx + 1}. ${title || `结果 ${idx + 1}`}`, meta));
          });
          if (!rows.length) return "";
          return `<div class="task-detail-section"><h4>搜索结果与采集结果</h4>${rows.join("")}</div>`;
        }
        if (action === "comment_collect") {
          const tasks = Array.isArray(data.tasks) ? data.tasks : [];
          tasks.slice(0, 6).forEach((row, idx) => {
            const item = row && typeof row === "object" ? row : {};
            const title = String(item.title || item.url || item.id || `任务 ${idx + 1}`).trim();
            const meta = [
              item.status ? `状态：${item.status}` : "",
              item.comment_count != null ? `评论：${compactNumber(item.comment_count)}` : "",
              item.high_intent_count != null ? `高意向：${compactNumber(item.high_intent_count)}` : "",
              item.author ? `作者：${item.author}` : "",
            ].filter(Boolean).join(" · ");
            rows.push(renderTaskDetailRecord(`${idx + 1}. ${title || `任务 ${idx + 1}`}`, meta));
          });
          if (!rows.length) return "";
          return `<div class="task-detail-section"><h4>评论任务预览</h4>${rows.join("")}</div>`;
        }
        if (action === "interaction") {
          const users = Array.isArray(data.users) ? data.users : [];
          users.slice(0, 6).forEach((row, idx) => {
            const item = row && typeof row === "object" ? row : {};
            const title = String(item.nickname || item.name || item.user_name || item.uid || `客户 ${idx + 1}`).trim();
            const meta = [
              item.interaction_status ? `状态：${item.interaction_status}` : "",
              item.interaction_account_id ? `执行账号：${item.interaction_account_id}` : "",
              item.uid ? `用户ID：${item.uid}` : "",
              item.sec_uid ? `SecUID：${item.sec_uid}` : "",
            ].filter(Boolean).join(" · ");
            rows.push(renderTaskDetailRecord(`${idx + 1}. ${title || `客户 ${idx + 1}`}`, meta));
          });
          if (!rows.length) return "";
          return `<div class="task-detail-section"><h4>私信结果预览</h4>${rows.join("")}</div>`;
        }
        if (action === "tasks_from_search") {
          const rawResult = data.raw_result && typeof data.raw_result === "object" ? data.raw_result : {};
          const lines = [
            rawResult.msg || "",
            data.selected_total != null ? `本次同步 ${compactNumber(data.selected_total)} 条` : "",
            data.total != null ? `任务池当前累计 ${compactNumber(data.total)} 条` : "",
          ].filter(Boolean);
          if (!lines.length) return "";
          return `<div class="task-detail-section"><h4>同步结果</h4>${renderTaskDetailRecord("任务池写入", lines)}</div>`;
        }
        const genericRows = Array.isArray(data.items) ? data.items
          : Array.isArray(data.tasks) ? data.tasks
          : Array.isArray(data.users) ? data.users
          : Array.isArray(data.rows) ? data.rows
          : [];
        if (!genericRows.length) return "";
        return `<div class="task-detail-section"><h4>结果预览</h4>${genericRows.slice(0, 6).map((row, idx) => {
          const item = row && typeof row === "object" ? row : {};
          const title = String(item.title || item.desc || item.content || item.nickname || item.name || item.aweme_id || `结果 ${idx + 1}`).trim();
          const sub = String(item.author_name || item.author || item.user_name || item.account_name || item.uid || item.sec_uid || "").trim();
          return renderTaskDetailRecord(`${idx + 1}. ${title || `结果 ${idx + 1}`}`, sub);
        }).join("")}</div>`;
      }
      function readablePayloadRows(data) {
        const rows = [];
        const add = (label, value) => {
          const text = String(value == null ? "" : value).trim();
          if (text) rows.push([label, text]);
        };
        if (!data || typeof data !== "object") return rows;
        add("结果", data.message || data.msg || data.summary || data.result_text || data.text || "");
        add("标题", data.title || "");
        add("数量", data.count != null ? compactNumber(data.count) : "");
        add("总数", data.total != null ? compactNumber(data.total) : "");
        const refs = data.result_refs && typeof data.result_refs === "object" ? data.result_refs : {};
        const urls = Array.isArray(data.media_urls) ? data.media_urls : (Array.isArray(refs.urls) ? refs.urls : []);
        add("媒体文件", urls.length ? `${urls.length} 个` : "");
        return rows;
      }
      if (payload.ip_content_daily) {
        const groups = Array.isArray(payload.groups) ? payload.groups : [];
        const activeImageRun = activeMomentImageRun(run && run.id);
        const lastImageRun = latestMomentImageRun(run && run.id);
        const imageBusy = !!activeImageRun;
        const imageStateText = imageBusy
          ? `图片生成中：${statusText(activeImageRun.status)}，请等待客户端完成。`
          : (lastImageRun ? `最近图片任务：${statusText(lastImageRun.status)}${lastImageRun.result_text ? " · " + lastImageRun.result_text : ""}` : "先选择文案；每条最多使用 3 个配图提示词。");
        sections.push(`<div class="task-detail-section"><h4>IP日更文案</h4>${groups.map((group) => {
          const records = Array.isArray(group.records) ? group.records : [];
          const isMoments = String(group.task || "") === "moments_candidate";
          if (isMoments) {
            return `<div class="task-detail-record" data-moment-group="1">
              <strong>${escapeHtml(ipTaskLabel(group.task))} · ${records.length}条</strong>
              <div class="copy-toolbar">
                <button type="button" class="ghost" data-select-draft-group="moments_candidate">全选/取消</button>
                <button type="button" data-copy-selected-drafts="moments_candidate">复制选中文案</button>
              </div>
              <div class="moment-image-actions">
                <button type="button" data-generate-selected-moment-images="1"${imageBusy ? " disabled" : ""}>${imageBusy ? "图片生成中" : "生成选中图片"}</button>
                <span class="moment-image-status">${escapeHtml(imageStateText)}</span>
              </div>
              ${records.map((rec, idx) => momentRecordHtml(rec, idx, imageBusy)).join("")}
            </div>`;
          }
          return `<div class="task-detail-record" data-copy-group="${escapeHtml(group.task || "")}">
            <strong>${escapeHtml(ipTaskLabel(group.task))} · ${records.length}条</strong>
            <div class="copy-toolbar">
              <button type="button" class="ghost" data-select-draft-group="${escapeHtml(group.task || "")}">全选/取消</button>
              <button type="button" data-copy-selected-drafts="${escapeHtml(group.task || "")}">复制选中文案</button>
            </div>
            ${records.map((rec, idx) => copyRecordHtml(rec, idx, group.task)).join("")}
          </div>`;
        }).join("")}</div>`);
      }
      const text = run.error || run.result_text || (run.progress && (run.progress.text || run.progress.message)) || "";
      sections.push(`<div class="task-detail-section"><h4>结果 / 状态</h4><pre>${escapeHtml(text || statusText(run.status))}</pre></div>`);
      if (payload.task_kind === "douyin_leads" || run.task_kind === "douyin_leads") {
        const summaryHtml = renderDouyinLeadSummary(payload);
        if (summaryHtml) sections.push(summaryHtml);
        const previewHtml = renderDouyinLeadPreview(payload);
        if (previewHtml) sections.push(previewHtml);
      } else if (!payload.ip_content_daily) {
        const summaryRows = readablePayloadRows(payload);
        if (summaryRows.length) sections.push(renderTaskDetailSection("结果摘要", summaryRows));
        const media = renderRunMedia(collectRunMediaEntries(run), run);
        if (media) sections.push(`<div class="task-detail-section"><h4>媒体结果</h4>${media}</div>`);
        const publishActions = renderRunPublishActions(run);
        if (publishActions) sections.push(`<div class="task-detail-section"><h4>发布动作</h4>${publishActions}</div>`);
      }
      return sections.join("");
    }

    async function openRunDetail(runId, backTab = "") {
      if (!runId) return;
      const body = $("runPageBody");
      if (!body) return;
      const activeView = document.querySelector(".view.active");
      const activeId = activeView ? String(activeView.id || "") : "";
      state.runDetailBackTab = backTab || (activeId === "workListView" ? "workList" : (activeId === "taskDetailView" ? "taskDetail" : "runList"));
      state.currentRunDetailId = runId;
      const cachedRun = (state.runs || []).find((row) => String(row.id || "") === String(runId)) || null;
      $("runPageTitle").textContent = cachedRun ? (cachedRun.title || "执行详情") : "执行详情";
      $("runPageSubtitle").textContent = cachedRun
        ? `${statusText(cachedRun.status)} · ${fmtTime(cachedRun.created_at)}`
        : "正在读取结果";
      body.innerHTML = cachedRun ? taskDetailHtml(cachedRun) : `<div class="hint">加载中...</div>`;
      switchTab("runDetail");
      try {
        const data = await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}`);
        const run = data.run || {};
        mergeRuns([run]);
        $("runPageTitle").textContent = run.title || "执行详情";
        $("runPageSubtitle").textContent = `${statusText(run.status)} · ${fmtTime(run.created_at)}`;
        body.innerHTML = taskDetailHtml(run);
        if (run.task_kind === "ip_content_daily") {
          loadRuns({ reset: true }).then(() => {
            if (state.currentRunDetailId === runId) body.innerHTML = taskDetailHtml(run);
          }).catch(() => {});
        }
      } catch (err) {
        if (!cachedRun) {
          body.innerHTML = `<div class="hint">${escapeHtml(err.message || "详情加载失败")}</div>`;
        } else {
          $("runPageSubtitle").textContent = "详情刷新失败，已显示列表缓存";
          toast(err.message || "详情刷新失败");
        }
      }
    }

    function findAbilityKeyBy(match) {
      let found = "";
      DEPARTMENT_SKILL_TREE.forEach((department) => {
        if (found) return;
        eachAbilityNode(department.children || [], department, [], (node) => {
          if (!found && match(node, department)) found = node.key || "";
        });
      });
      return found;
    }

    function abilityKeyFromRun(run) {
      const payload = runTaskInputPayload(run);
      const ctx = h5ContextFromPayload(payload);
      if (ctx.ability_key && abilityLookup(ctx.ability_key)) return ctx.ability_key;
      const kind = String((run && run.task_kind) || "").trim();
      if (kind === "ip_content_daily") return "ip_content_daily";
      if (kind === "douyin_leads") return "douyin_leads";
      if (kind === "linkedin_mining") return "linkedin_leads";
      if (kind === "wechat_channels_transcript") return "wechat_channels_transcript";
      if (kind === "social_leads") {
        const platform = cleanKey(payload.platform);
        if (platform === "reddit") return "reddit_leads";
        if (platform === "x") return "x_leads";
        if (platform === "tiktok") return "tiktok_leads";
      }
      if (kind === "client_workflow") {
        const action = cleanKey(payload.action || (payload.params || {}).action);
        if (action.startsWith("local_bestseller_")) return findAbilityKeyBy((node) => node.workQuickKey === "local_bestseller") || "local_bestseller";
        if (action === "viral_video_remix_start") return findAbilityKeyBy((node) => node.workQuickKey === "viral_video_remix") || "viral_video_remix";
        if (action === "wecom_poll_reply") return findAbilityKeyBy((node) => node.workQuickKey === "wecom_reply") || "wecom_reply";
        if (action === "publish_content") return findAbilityKeyBy((node) => node.workQuickKey === "publish_center") || "publish_center";
      }
      const capabilityId = cleanKey(taskCapabilityId(run) || payload.capability_id);
      if (capabilityId) {
        return findAbilityKeyBy((node) => node.key === capabilityId || node.capabilityId === capabilityId || node.workQuickKey === capabilityId) || capabilityId;
      }
      return "";
    }

    function setFieldValue(id, value) {
      const el = $(id);
      if (!el) return;
      if (el.type === "checkbox") {
        el.checked = !!value;
      } else {
        el.value = value == null ? "" : String(value);
      }
      el.dispatchEvent(new Event("change", { bubbles: true }));
      if (document.querySelector(`[data-asset-picker="${cssEscape(id)}"]`)) renderAssetPickerControl(id);
    }

    function setTextareaList(id, values) {
      setFieldValue(id, Array.isArray(values) ? values.join("\n") : values || "");
    }

    function refillAbilityScheduleFromRun(run) {
      const task = (state.tasks || []).find((row) => String(row.id) === String(run && run.task_id)) || null;
      const payload = runTaskInputPayload(run);
      const cfg = task && task.schedule_config && typeof task.schedule_config === "object" ? task.schedule_config : (payload.schedule_config || {});
      const type = task ? (task.schedule_type || "once") : "once";
      setFieldValue("abilityScheduleType", type);
      if (task && task.interval_seconds) setFieldValue("abilityIntervalMinutes", Math.max(1, Math.round(Number(task.interval_seconds || 60) / 60)));
      setFieldValue("abilityStartAt", cfg.start_at || "");
      const list = $("abilityDailyTimesList");
      if (list) list.innerHTML = "";
      const times = Array.isArray(cfg.daily_times) ? cfg.daily_times : [];
      times.forEach((time) => addAbilityDailyTime(time));
      updateAbilityScheduleFields();
    }

    function refillAbilityFieldsFromRun(run) {
      const payload = runTaskInputPayload(run);
      const inner = runInnerPayload(run);
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      setFieldValue("abilityGenericTitle", run && run.title || "");
      setFieldValue("abilityGenericPrompt", inner.prompt || inner.task_text || "");
      setFieldValue("workImageTitle", run && run.title || "创作图片");
      setFieldValue("workImagePrompt", inner.prompt || inner.task_text || "");
      setFieldValue("abilityVideoTitle", run && run.title || "创意视频");
      setFieldValue("abilityVideoPrompt", inner.prompt || inner.task_text || "");
      setFieldValue("abilityVideoMode", goalVideoModeFromPayload(inner));
      setFieldValue("abilityVideoAsset", firstGoalVideoReference(inner));
      setFieldValue("abilityVideoCandidateGroup", inner.candidate_group || "");
      bindGoalVideoModeControls("ability");
      loadVideoMemoryDocsForSelect().then(() => setMultiSelectValues("abilityVideoMemoryDocs", inner.memory_doc_ids || [])).catch(() => {});
      setFieldValue("workSeedanceAsset", inner.asset_id || inner.image_url || "");
      setFieldValue("workSeedanceText", inner.task_text || inner.prompt || "");
      setFieldValue("workSeedanceDuration", inner.total_duration_seconds || "");
      setFieldValue("workSeedanceAspect", inner.aspect_ratio || "");
      setFieldValue("workComflyAsset", inner.asset_id || inner.image_url || "");
      setFieldValue("workComflyText", inner.task_text || inner.prompt || "");
      setFieldValue("workComflyStoryboardCount", inner.storyboard_count || "");
      setFieldValue("workComflyAutoSave", inner.auto_save !== false);
      setFieldValue("workAvatar", inner.avatar || "");
      setFieldValue("workVoice", inner.voice || "");
      setFieldValue("workHiflyTitle", run && run.title || "数字人口播");
      setFieldValue("workHiflyScript", inner.script || inner.prompt || "");
      setFieldValue("abilityIpTemplate", payload.template_id || "");
      document.querySelectorAll("[data-ability-ip-daily-task]").forEach((el) => {
        const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
        el.checked = !tasks.length || tasks.includes(el.getAttribute("data-ability-ip-daily-task"));
      });
      setFieldValue("abilityIpSyncBefore", payload.sync_before !== false);
      const req = payload.requirements && typeof payload.requirements === "object" ? payload.requirements : {};
      setFieldValue("abilityIpRequirement", req.common || req.moments || req.oral || req.image || "");
      setFieldValue("abilityArticleTitle", run && run.title || "公众号文章");
      setFieldValue("abilityArticleIdea", inner.idea || "");
      setFieldValue("abilityArticleStyle", inner.style || "");
      setFieldValue("abilityArticleImageCount", inner.image_count || "");
      setFieldValue("abilityArticleIncludeImages", inner.include_images !== false);
      setFieldValue("abilityPptTitle", run && run.title || "PPT生成");
      setFieldValue("abilityPptTopic", inner.topic || "");
      setFieldValue("abilityPptSlideCount", inner.slide_count || "");
      setFieldValue("abilityPptInstructions", inner.instructions || "");
      setFieldValue("abilityPptMode", inner.mode || "ai");
      setFieldValue("abilityEcommerceTitle", run && run.title || "电商详情页");
      setFieldValue("abilityEcommerceAsset", inner.asset_id || inner.image_url || "");
      setFieldValue("abilityEcommerceText", inner.task_text || inner.prompt || "");
      setFieldValue("abilityEcommercePageCount", inner.page_count || "");
      setFieldValue("abilityEcommerceAutoSave", inner.auto_save !== false);
      setFieldValue("abilityLeadTitle", payload.title || run && run.title || "");
      setTextareaList("abilityLeadKeywords", payload.keywords);
      setFieldValue("abilityLeadMode", payload.accounts && payload.accounts.length ? "account" : "source");
      setTextareaList("abilityLeadSources", payload.communities || payload.source_keywords);
      setTextareaList("abilityLeadAccounts", payload.accounts);
      setFieldValue("abilityLeadMaxItems", payload.max_items || "");
      setFieldValue("abilityLinkedinTitle", payload.title || run && run.title || "");
      setFieldValue("abilityLinkedinTarget", payload.target_profile || "");
      setTextareaList("abilityLinkedinProfiles", payload.seed_profile_urls);
      setTextareaList("abilityLinkedinCompanies", payload.seed_company_urls);
      setTextareaList("abilityLinkedinKeywords", payload.keywords);
      setTextareaList("abilityLinkedinHashtags", payload.hashtags);
      setFieldValue("abilityLinkedinMaxPeople", payload.max_people || "");
      setFieldValue("abilityWechatQuery", payload.query || payload.username || "");
      setFieldValue("abilityWechatPages", payload.max_pages || "");
      setFieldValue("abilityWechatLimit", payload.limit || "");
      setFieldValue("workDouyinKeyword", params.keyword || params.query || "");
      setFieldValue("workDouyinRegions", valueLabel(params.regions || params.region_list || params.area_list || ["全国"]));
      setFieldValue("workDouyinMaxResults", params.max_results || "");
      setFieldValue("workDouyinMode", params.mode || "script");
      setFieldValue("workLocalMode", payload.action === "local_bestseller_scene_batch" ? "scene_batch" : "plan");
      const profile = params.profile && typeof params.profile === "object" ? params.profile : {};
      setFieldValue("workLocalDays", params.days || "");
      setFieldValue("workLocalName", profile.name || "");
      setFieldValue("workLocalNickname", profile.nickname || "");
      setFieldValue("workLocalGender", profile.gender || "female");
      setFieldValue("workLocalIdentity", profile.identity || "");
      setFieldValue("workLocalIndustry", profile.industry || "");
      setFieldValue("workLocalCity", profile.city || "");
      setFieldValue("workLocalProvince", profile.province || "");
      setFieldValue("workLocalPhoto", profile.photo_asset_id || profile.photo_url || "");
      setFieldValue("workViralVideoUrl", params.original_video_url || "");
      setFieldValue("workViralCharacterUrl", params.character_image_url || "");
      setFieldValue("workViralProductUrl", params.product_image_url || "");
      setFieldValue("workViralPrompt", params.prompt || "");
      setFieldValue("workViralDuration", params.duration || "");
      setFieldValue("workViralRatio", params.ratio || "");
      setFieldValue("workViralGenerateAudio", params.generate_audio !== false);
      setFieldValue("workWecomNote", params.note || "");
      setFieldValue("workPublishMaterial", params.asset_id || params.url || params.source_url || params.material || "");
      setFieldValue("workPublishMediaType", params.media_type || "video");
      setFieldValue("workPublishAccount", params.account_nickname || params.account || "");
      setFieldValue("workPublishTitle", params.title || "");
      setFieldValue("workPublishDescription", params.description || "");
      setFieldValue("workPublishTags", params.tags || "");
      setFieldValue("workPublishAiCopy", params.ai_publish_copy !== false);
      refillAbilityScheduleFromRun(run);
    }

    async function refillRunToWorkbench(runId) {
      let run = (state.runs || []).find((row) => String(row.id || "") === String(runId)) || null;
      if (!run || !run.payload) {
        const data = await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}`);
        run = data.run || null;
        if (run) mergeRuns([run]);
      }
      if (!run) {
        toast("没有找到这条执行记录");
        return;
      }
      const abilityKey = abilityKeyFromRun(run);
      if (!abilityKey || !abilityLookup(abilityKey)) {
        toast("这条记录暂时无法定位到对应工作台");
        return;
      }
      openAbilityView(abilityKey);
      setTimeout(() => {
        refillAbilityFieldsFromRun(run);
        toast("已带入上次任务参数，可修改后重新下发");
      }, 120);
    }

    function setMomentStatus(recordId, text, isError = false) {
      const el = document.querySelector(`[data-moment-status="${cssEscape(String(recordId || ""))}"]`);
      if (!el) return;
      el.textContent = text || "";
      el.style.color = isError ? "var(--red)" : "var(--muted)";
    }

    function selectedMomentRecords() {
      return Array.from(document.querySelectorAll("[data-moment-select]:checked")).map((input) => {
        const recordId = String(input.dataset.momentSelect || "").trim();
        const item = input.closest("[data-moment-record]");
        const body = item ? (item.querySelector(".task-detail-moments-copy")?.textContent || "").trim() : "";
        const promptInputs = item ? Array.from(item.querySelectorAll(`[data-moment-prompt="${cssEscape(recordId)}"]:checked`)) : [];
        const prompts = promptInputs.map((el) => {
          const label = el.closest("label");
          const raw = label ? label.textContent || "" : "";
          return raw.replace(/^配图\s*\d+\s*[:：]\s*/, "").trim();
        }).filter(Boolean).slice(0, 3);
        const title = item ? (item.querySelector(".moment-summary span")?.textContent || "").replace(/^\d+\.\s*/, "").trim() : "";
        const memoryDocIds = item ? String(item.dataset.momentMemoryDocIds || "").split(",").map((id) => id.trim()).filter(Boolean) : [];
        return { recordId, body, prompts, title, memory_doc_ids: memoryDocIds };
      }).filter((row) => row.recordId);
    }

    function selectedDraftCopies(task) {
      const selector = `[data-draft-copy-select]:checked${task ? `[data-draft-task="${cssEscape(task)}"]` : ""}`;
      return Array.from(document.querySelectorAll(selector)).map((input) => {
        const item = input.closest(".copy-item, .moment-item");
        if (!item) return "";
        return (item.querySelector(".task-detail-moments-copy")?.textContent || "").trim();
      }).filter(Boolean);
    }

    async function copySelectedDrafts(task) {
      const rows = selectedDraftCopies(task);
      if (!rows.length) {
        toast("请先勾选要复制的文案");
        return;
      }
      const ok = await copyText(rows.join("\n\n---\n\n"));
      toast(ok ? `已复制 ${rows.length} 条文案` : "复制失败，请长按文案手动复制");
    }

    function toggleDraftGroupSelection(task) {
      const selector = `[data-draft-copy-select]${task ? `[data-draft-task="${cssEscape(task)}"]` : ""}`;
      const inputs = Array.from(document.querySelectorAll(selector));
      if (!inputs.length) return;
      const shouldCheck = inputs.some((input) => !input.checked);
      inputs.forEach((input) => { input.checked = shouldCheck; });
    }

    async function generateSelectedMomentImages(btn) {
      if (activeMomentImageRun(state.currentRunDetailId)) {
        toast("当前图片生成任务还在执行中，请完成后再生成");
        return;
      }
      const selected = selectedMomentRecords();
      if (!selected.length) {
        toast("请先选择要出图的朋友圈文案");
        return;
      }
      if (selected.length > 5) {
        toast("一次最多选择 5 条朋友圈文案");
        return;
      }
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "下发中...";
      }
      const batchId = `h5_moment_img_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
      const batchCreatedAt = new Date().toISOString();
      try {
        const records = selected.map((record) => {
          const prompts = (record.prompts && record.prompts.length ? record.prompts : [record.body || record.title || "朋友圈配图"]).slice(0, 3);
          setMomentStatus(record.recordId, "已下发到 online，等待客户端生成...");
          return { ...record, prompts, memory_doc_ids: record.memory_doc_ids || [] };
        });
        const created = await submitOnceClientTask({
            title: "朋友圈图片生成",
            taskKind: "client_workflow",
            content: `生成 ${records.length} 条朋友圈配图`,
            payload: {
              action: "ip_moments_generate_images",
              params: {
                source: "h5_scheduled_run_detail",
                source_run_id: state.currentRunDetailId || "",
                batch_id: batchId,
                batch_created_at: batchCreatedAt,
                records,
              },
            },
        });
        const run = created && Array.isArray(created.runs) && created.runs[0] && typeof created.runs[0] === "object" ? created.runs[0] : null;
        if (run && run.id && !(state.runs || []).some((row) => String(row.id || "") === String(run.id))) {
          state.runs = [run].concat(state.runs || []);
        }
        showTaskSuccessDialog("朋友圈图片生成任务已下发，可在工作历史查看进度。");
        if (state.currentRunDetailId) {
          setTimeout(() => openRunDetail(state.currentRunDetailId, state.runDetailBackTab || "runList"), 1200);
        }
      } catch (err) {
        toast(err.message || "朋友圈出图任务下发失败");
        selected.forEach((record) => setMomentStatus(record.recordId, err.message || "任务下发失败", true));
      } finally {
        if (btn) {
          const stillBusy = activeMomentImageRun(state.currentRunDetailId);
          btn.disabled = !!stillBusy;
          btn.textContent = stillBusy ? "图片生成中" : (oldText || "生成选中图片");
        }
      }
    }

    async function runTaskNow(taskId, btn) {
      if (!taskId) return;
      const oldText = btn ? btn.textContent : "";
      const task = (state.tasks || []).find((row) => String(row.id || "") === String(taskId)) || null;
      const optimisticRunId = addOptimisticRun(
        { payload: task && task.payload ? task.payload : {} },
        task ? task.title : "",
        task && task.task_kind === "ip_content_daily"
      );
      if (btn) {
        btn.disabled = true;
        btn.textContent = "下发中...";
      }
      try {
        const data = await api(`/api/scheduled-tasks/tasks/${encodeURIComponent(taskId)}/run-now`, { method: "POST", json: {} });
        removeRun(optimisticRunId);
        mergeRuns(data.runs || []);
        renderOfficeEmployees();
        renderWorkList();
        showTaskSuccessDialog("任务已下发执行，可在工作历史查看进度。");
        await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      } catch (err) {
        removeRun(optimisticRunId);
        renderOfficeEmployees();
        renderWorkList();
        toast(err.message || "下发失败");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "立即执行";
        }
      }
    }

    function ensureSelectedInstallationId() {
      const selected = String(state.selectedInstallationId || "").trim();
      if (selected && state.devices.some((d) => String(d.installation_id || "") === selected)) return selected;
      const previous = state.selectedInstallationId;
      const online = state.devices.find((d) => d.online && d.installation_id);
      const any = state.devices.find((d) => d.installation_id);
      const next = String(((online || any) || {}).installation_id || "");
      state.selectedInstallationId = next;
      if (next) localStorage.setItem("lobster_h5_selected_installation_id", next);
      else localStorage.removeItem("lobster_h5_selected_installation_id");
      if (previous !== next) {
        state.publishAccountsLoaded = false;
        state.publishAccounts = [];
      }
      return next;
    }

    function setSelectedInstallationId(value) {
      state.selectedInstallationId = String(value || "").trim();
      if (state.selectedInstallationId) localStorage.setItem("lobster_h5_selected_installation_id", state.selectedInstallationId);
      else localStorage.removeItem("lobster_h5_selected_installation_id");
      state.publishAccountsLoaded = false;
      state.publishAccounts = [];
      renderProfileDeviceSelect();
      fillPublishPlatformSelect();
      fillPublishRunPlatformSelect();
      loadPublishAccounts().catch((err) => toast(err.message || "发布账号加载失败"));
      if (activeViewKey() === "workflow") loadWorkflowActive().catch((err) => toast(err.message || "工作流状态加载失败"));
    }

    function selectedDevice() {
      const id = ensureSelectedInstallationId();
      return (state.devices || []).find((d) => String(d.installation_id || "") === id) || null;
    }

    function currentInstallationId() {
      return ensureSelectedInstallationId();
    }

    function deviceDisplayName(device) {
      if (!device) return "";
      return String(device.display_name || device.installation_id || "").trim();
    }

    function scrollMessagesToBottom() {
      const messages = $("messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
    }

    function focusMessageInput() {
      if (window.matchMedia && !window.matchMedia("(pointer: fine)").matches) return;
      const input = $("messageInput");
      if (!input) return;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }

    function autosizeMessageInput() {
      const input = $("messageInput");
      if (!input) return;
      input.style.height = "auto";
      const expanded = $("sendForm")?.classList.contains("expanded");
      const limit = expanded ? Math.min(window.innerHeight * 0.48, 360) : 148;
      const next = Math.min(input.scrollHeight, limit);
      input.style.height = `${Math.max(48, next)}px`;
    }

    function setComposerExpanded(expanded) {
      const form = $("sendForm");
      const btn = $("toggleComposerBtn");
      if (!form || !btn) return;
      form.classList.toggle("expanded", !!expanded);
      btn.textContent = expanded ? "收起" : "展开";
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      autosizeMessageInput();
      focusMessageInput();
      setTimeout(scrollMessagesToBottom, 60);
    }

    function syncFloatingScheduleButton(key) {
      const btn = $("floatingScheduleBtn");
      if (!btn) return;
      const hidden = !state.token || ["messages", "secretary", "taskList", "taskDetail", "runDetail"].includes(key);
      btn.classList.toggle("hidden", hidden);
    }

    function openHomeTarget(target, backTab = "office") {
      const key = String(target || "").trim();
      if (!key) return;
      if (key === "personalSettings") {
        state.personalSettingsBackTab = backTab || "office";
        state.personalSettingsTab = "profile";
        switchTab("personalSettings");
        return;
      }
      if (key === "assetLibrary") {
        state.assetLibrarySection = "uploads";
        state.assetLibraryOrigin = "user_upload";
        switchTab("assetLibrary");
        return;
      }
      if (key === "contentRecords") {
        switchTab("contentRecords");
        return;
      }
      if (key === "workflowNew") {
        resetWorkflowDraft();
        switchTab("workflow");
        return;
      }
      if (key === "salesWorkflow") {
        prepareSalesWorkflowDraft();
        switchTab("workflow");
        return;
      }
      switchTab(key);
    }

    function switchTab(tab) {
      const key = tab || "office";
      document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${key}View`));
      document.querySelectorAll("[data-tab-target]").forEach((btn) => btn.classList.toggle("active", btn.dataset.tabTarget === key));
      const titleMap = {
        office: ["必火AI员工", "我的AI员工办公室"],
        secretary: ["老板驾驶舱", "今日交付、趋势和风险"],
        home: ["安排工作", "远程任务、消息和执行记录"],
        workflow: ["员工定制", "24小时任务编排"],
        agentManage: ["代理商管理", ""],
        workList: ["工作列表", "已完成、当前和待执行的工作节点"],
        assetLibrary: ["素材库", ""],
        contentRecords: ["内容记录", ""],
        leadCenter: ["客资线索", ""],
        tutorial: ["教程", ""],
        messages: ["手机会话", "消息结果和素材预览"],
        voice: ["龙虾AI语音助手", ""],
        profile: ["个人中心", "账号和功能入口"],
        personalSettings: ["IP人设定位", "模板、关键词、同行账号和记忆文件"],
        taskList: ["定时任务", "默认展示 10 条，更多用翻页加载"],
        taskDetail: ["定时任务详情", "任务配置和最近执行入口"],
        runList: ["执行记录", "默认展示 10 条，点开查看具体内容"],
        runDetail: ["执行详情", "结果、文案和生成图片"],
        douyinLeads: ["抖音获客", "先看账号与机器状态，再安排采集、评论和私信任务"],
        douyinLeadsSchedule: ["安排抖音获客", "按当前在线设备给抖音账号下发具体获客工作"],
      };
      const currentDepartment = departmentById(state.currentDepartmentId);
      const currentAbility = activeAbilityLookup();
      titleMap.department = [currentDepartment && currentDepartment.name || "职能中心", ""];
      titleMap.ability = [currentAbility && currentAbility.node && currentAbility.node.label || "能力", ""];
      titleMap.home = ["定时任务", "按时间自动执行内容任务"];
      const nextTitle = titleMap[key] || titleMap.office;
      $("pageTitle").textContent = nextTitle[0];
      $("pageSubtitle").textContent = nextTitle[1];
      $("topBackBtn").classList.toggle("hidden", key === "office");
      $("topActions").classList.toggle("hidden", key !== "office" || !state.token);
      $("topbar").classList.toggle("subpage", key !== "office");
      $("topbar").classList.toggle("voice-page", key === "voice");
      syncFloatingScheduleButton(key);
      if (key === "office") {
        renderOfficeEmployees();
        loadWorkflowTemplates().catch(() => {});
      }
      if (key === "workflow") {
        renderWorkflow();
        Promise.all([
          refreshDeviceStatus().catch(() => {}),
          loadTaskSkills().catch(() => {}),
          loadIpTemplates().catch(() => {}),
          loadWorkflowTemplates().catch(() => {}),
          loadWorkflowActive().catch(() => {}),
          loadTasks({ reset: true, limit: 80 }).catch(() => {}),
          loadRuns({ reset: true, limit: 20, compact: true }).catch(() => {}),
        ]).then(renderWorkflow);
      }
      if (key === "personalSettings" && !state.personalSettingsBackTab) state.personalSettingsBackTab = "profile";
      if (key === "agentManage") {
        renderAgentManage();
        Promise.all([loadAgentResources().catch(() => {}), loadAgentUsers(!state.agentUsers.length).catch(() => {})]).then(renderAgentManage);
      }
      if (key === "assetLibrary") refreshAssetLibrary();
      if (key === "contentRecords") loadContentRecords();
      if (key === "leadCenter") loadLeadCenterData();
      if (key === "secretary") {
        renderSecretaryView();
        Promise.all([
          loadTasks({ reset: true, limit: 40 }),
          loadRuns({ reset: true, limit: 20, compact: true }),
          loadWorkbenchJobs({ limit: 80 }),
        ]).then(renderSecretaryView);
      }
      if (key === "department") {
        renderDepartmentView();
        Promise.all([
          loadTasks({ reset: true, limit: 40 }).catch(() => {}),
          loadRuns({ reset: true, limit: 20, compact: true }).catch(() => {}),
        ]).then(renderDepartmentDayBoard);
      }
      if (key === "ability") renderAbilityView();
      if (key !== "office") closeEmployeeModal();
      if (key === "personalSettings") loadPersonalSettings();
      if (key === "taskList") loadTasks({ reset: true });
      if (key === "runList") loadRuns({ reset: true });
      if (key === "workList") {
        renderWorkList();
        Promise.all([
          loadTasks({ reset: true, limit: 40 }),
          loadRuns({ reset: true, limit: 20, compact: true }),
          loadWorkbenchJobs({ limit: 80 }),
        ]).then(renderWorkList);
      }
      if (key === "home") {
        setTaskPanelOpen(true);
      }
      if (key === "douyinLeads") {
        loadDouyinStatus();
      }
      if (key === "douyinLeadsSchedule") {
        renderDouyinTaskActions();
        renderDouyinTaskDetailFields();
        updateDouyinScheduleFields();
      }
      if (key !== "messages") {
        if (key !== "voice" && state.lastViewBeforeMessages === "voice") {
          state.lastViewBeforeMessages = "";
        }
      }
      if (key === "messages") {
        renderChatContextBar();
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
        setTimeout(() => {
          autosizeMessageInput();
          scrollMessagesToBottom();
          focusMessageInput();
        }, 80);
      }
      if (key === "voice") setTimeout(() => {
        syncVoiceDraftDisplay();
      }, 40);
    }

    function setMessageTemplate(text) {
      const input = $("messageInput");
      if (!input) return;
      input.value = text || "";
      autosizeMessageInput();
      focusMessageInput();
      setTimeout(scrollMessagesToBottom, 80);
    }

    function voiceMockText() {
      return state.voiceDraft || "帮我分析一下第二季度的销售数据，并生成可视化报表。";
    }

    function currentVoiceText() {
      return String(state.voiceDraft || state.voicePartial || "").trim();
    }

    function resetVoiceIntent() {
      state.voiceIntent = null;
      state.voiceActions = [];
    }

    function voiceIntentLabel(intent) {
      const mapping = {
        image_generate: "生成图片",
        video_generate: "生成视频",
        creative_storyboard_video: "生成创意分镜",
        douyin_leads_task: "创建抖音获客任务",
        copywriting: "生成文案",
        schedule_task: "安排定时任务",
        chat_freeform: "发送到消息页",
        unknown: "继续补充参数",
      };
      return mapping[String(intent || "").trim()] || "继续补充参数";
    }

    function normalizeVoiceDouyinAction(action) {
      const raw = String(action || "").trim();
      if (!raw) return "search_collect";
      if (raw === "video_comment") return "comment_collect";
      if (raw === "direct_message") return "interaction";
      if (raw === "monitor_peer") return "tasks_from_search";
      return DOUYIN_TASK_ACTIONS[raw] ? raw : "search_collect";
    }

    function voiceSlotEntries(intentPayload) {
      const payload = intentPayload && typeof intentPayload === "object" ? intentPayload : {};
      const slots = payload.slots && typeof payload.slots === "object" ? payload.slots : {};
      const entries = [];
      if (slots.duration_seconds != null) entries.push(["时长", `${slots.duration_seconds} 秒`]);
      if (slots.aspect_ratio) entries.push(["比例", String(slots.aspect_ratio)]);
      if (slots.keyword) entries.push(["关键词", String(slots.keyword)]);
      if (slots.action) entries.push(["获客动作", (DOUYIN_TASK_ACTIONS[normalizeVoiceDouyinAction(slots.action)] || {}).label || String(slots.action)]);
      if (slots.comment_mode) entries.push(["评论模式", String(slots.comment_mode)]);
      if (slots.comment_text) entries.push(["评论内容", String(slots.comment_text)]);
      if (slots.dm_mode) entries.push(["私信模式", String(slots.dm_mode)]);
      if (slots.dm_text) entries.push(["私信内容", String(slots.dm_text)]);
      return entries.filter((item) => String(item[1] || "").trim());
    }

    function buildVoiceExecutionPlan() {
      const payload = state.voiceIntent && typeof state.voiceIntent === "object" ? state.voiceIntent : null;
      const draftText = String((payload && payload.draft_text) || state.voiceDraft || "").trim();
      if (!draftText) return null;
      const intent = String((payload && payload.intent) || "").trim();
      const slots = payload && payload.slots && typeof payload.slots === "object" ? payload.slots : {};
      const baseMissing = Array.isArray(payload && payload.missing_slots)
        ? payload.missing_slots.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      if (intent === "image_generate") {
        return {
          kind: "image_generate",
          direct: true,
          title: "直接创建图片任务",
          description: "确认后会按识别出的提示词直接下发图片生成。",
          content: draftText,
          payload: { prompt: draftText },
          missing: baseMissing,
        };
      }
      if (intent === "creative_storyboard_video") {
        return {
          kind: "creative_storyboard_video",
          direct: true,
          title: "直接创建创意分镜任务",
          description: "确认后会直接走创意分镜生成链路。",
          content: draftText,
          payload: {
            source_mode: "ai_image",
            candidate_group: "",
            prompt: draftText,
          },
          missing: baseMissing,
        };
      }
      if (intent === "video_generate") {
        return {
          kind: "video_generate",
          direct: true,
          title: "直接创建视频任务",
          description: "确认后会直接按当前语音内容下发视频生成任务。",
          content: draftText,
          payload: {
            source_mode: "ai_image",
            candidate_group: "",
            prompt: draftText,
          },
          missing: baseMissing,
        };
      }
      if (intent === "douyin_leads_task") {
        const action = normalizeVoiceDouyinAction(slots.action);
        const taskPayload = { action, params: {} };
        const requiredMissing = [];
        if (slots.keyword) {
          taskPayload.params.keyword = String(slots.keyword).trim();
          taskPayload.params.query = taskPayload.params.keyword;
          taskPayload.params.search_keyword = taskPayload.params.keyword;
        }
        if (action === "search_collect") {
          taskPayload.params.mode = "script";
          const regions = Array.isArray(slots.regions) && slots.regions.length
            ? slots.regions.map((item) => String(item || "").trim()).filter(Boolean)
            : ["全国"];
          taskPayload.params.regions = regions;
          taskPayload.params.region_list = regions;
          taskPayload.params.area_list = regions;
          taskPayload.params.region_mode = regions.includes("全国") ? "nationwide" : "custom";
          if (!taskPayload.params.keyword) requiredMissing.push("keyword");
        }
        if (action === "comment_collect") {
          const mode = String(slots.comment_mode || "fixed").trim();
          const text = String(slots.comment_text || "").trim();
          taskPayload.params.comment_mode = mode;
          taskPayload.params.content_mode = mode;
          taskPayload.params.comment_text = text;
          taskPayload.params.reply_text = text;
          taskPayload.params.comment_content = text;
          if (!text) requiredMissing.push("comment_text");
        }
        if (action === "interaction") {
          const mode = String(slots.dm_mode || "fixed").trim();
          const text = String(slots.dm_text || "").trim();
          taskPayload.params.dm_mode = mode;
          taskPayload.params.private_message_mode = mode;
          taskPayload.params.message = text;
          taskPayload.params.dm_text = text;
          taskPayload.params.private_message = text;
          if (!text) requiredMissing.push("dm_text");
        }
        if (action === "tasks_from_search" && !taskPayload.params.keyword) {
          requiredMissing.push("keyword");
        }
        const missing = Array.from(new Set(requiredMissing));
        return {
          kind: "douyin_leads_task",
          direct: missing.length === 0,
          title: missing.length ? "继续补充抖音获客参数" : "直接创建抖音获客任务",
          description: missing.length
            ? "还缺少少量参数，去安排工作页补充后就能下发。"
            : "确认后会直接下发到当前在线设备执行。",
          content: draftText,
          payload: taskPayload,
          missing: Array.from(new Set(missing)),
        };
      }
      return {
        kind: intent || "chat_freeform",
        direct: false,
        title: "继续补充参数",
        description: "这条内容更适合先进入消息页整理后再执行。",
        content: draftText,
        payload: null,
        missing: baseMissing,
      };
    }

    function renderVoiceIntentPanel() {
      const panel = $("voiceIntentPanel");
      const badge = $("voiceIntentBadge");
      const summary = $("voiceIntentSummary");
      const slotsHost = $("voiceIntentSlots");
      const missingHost = $("voiceIntentMissing");
      const primaryHost = $("voicePrimaryAction");
      const secondaryHost = $("voiceSecondaryActionsList");
      if (!panel || !badge || !summary || !slotsHost || !missingHost || !primaryHost || !secondaryHost) return;

      const payload = state.voiceIntent && typeof state.voiceIntent === "object" ? state.voiceIntent : null;
      const plan = buildVoiceExecutionPlan();
      if (!state.voiceDraft) {
        panel.classList.remove("show");
        badge.textContent = "待识别";
        summary.textContent = "识别完成后，这里会告诉你当前更像是哪一种任务。";
        slotsHost.classList.add("hidden");
        missingHost.classList.add("hidden");
        primaryHost.innerHTML = "";
        secondaryHost.innerHTML = "";
        return;
      }

      panel.classList.add("show");
      if (state.voiceStatus === "understanding" && !payload) {
        badge.textContent = "AI 理解中";
        summary.textContent = "正在分析这段语音对应的任务类型和参数，请稍等片刻。";
        slotsHost.innerHTML = "";
        slotsHost.classList.add("hidden");
        missingHost.innerHTML = "";
        missingHost.classList.add("hidden");
        primaryHost.innerHTML = `
          <button class="voice-action-card voice-secondary-card" type="button" disabled>
            <div class="voice-action-icon">析</div>
            <div class="voice-action-content">
              <strong>AI 正在理解任务</strong>
              <span>马上就会给出可直接执行或继续补充的建议动作</span>
            </div>
            <div class="voice-action-arrow">…</div>
          </button>
        `;
        secondaryHost.innerHTML = "";
        return;
      }
      const intent = String((payload && payload.intent) || "unknown").trim();
      const confidence = Number((payload && payload.confidence) || 0);
      badge.textContent = `${voiceIntentLabel(intent)}${confidence ? ` · ${Math.round(confidence * 100)}%` : ""}`;
      summary.textContent = plan ? plan.description : "识别到了语音内容，建议继续补充参数。";

      const slotRows = payload ? voiceSlotEntries(payload) : [];
      if (slotRows.length) {
        slotsHost.innerHTML = slotRows.map(([label, value]) => `
          <div class="voice-slot-row">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(String(value || ""))}</span>
          </div>
        `).join("");
        slotsHost.classList.remove("hidden");
      } else {
        slotsHost.innerHTML = "";
        slotsHost.classList.add("hidden");
      }

      const missing = plan && Array.isArray(plan.missing) ? plan.missing : [];
      if (false && missing.length) {
        missingHost.innerHTML = missing.map((item) => `
          <div class="voice-missing-row">
            <strong>待补充</strong>
            <span>${escapeHtml(String(item || ""))}</span>
          </div>
        `).join("");
        missingHost.classList.remove("hidden");
      } else {
        missingHost.innerHTML = "";
        missingHost.classList.add("hidden");
      }

      if (plan) {
        primaryHost.innerHTML = `
          <button class="voice-action-card voice-primary-card ${plan.direct ? "" : "warn"}" type="button" data-voice-plan-action="${plan.direct ? "execute" : "refine"}">
            <div class="voice-action-icon">${escapeHtml(plan.direct ? "执" : "补")}</div>
            <div class="voice-action-content">
              <strong>${escapeHtml(plan.title)}</strong>
              <span>${escapeHtml(plan.description)}</span>
            </div>
            <div class="voice-action-arrow">→</div>
          </button>
        `;
      } else {
        primaryHost.innerHTML = "";
      }

      const actions = Array.isArray(state.voiceActions) ? state.voiceActions : [];
      secondaryHost.innerHTML = actions.map((item, index) => {
        const label = escapeHtml(String(item && item.label || `动作 ${index + 1}`));
        const kind = String(item && item.kind || "");
        const desc = kind === "submit_message"
          ? "会下发到龙虾盒子中执行，处理完成后再把结果回给你"
          : "会在当前对话里继续整理和补充，适合先把内容说完整";
        return `
          <button class="voice-action-card voice-secondary-card" type="button" data-voice-action-index="${index}">
            <div class="voice-action-icon">${escapeHtml(label.slice(0, 1) || "动")}</div>
            <div class="voice-action-content">
              <strong>${label}</strong>
              <span>${escapeHtml(desc)}</span>
            </div>
            <div class="voice-action-arrow">→</div>
          </button>
        `;
      }).join("");
    }

    function renderVoiceActionCards() {
      renderVoiceIntentPanel();
    }

    function syncVoiceDraftDisplay() {
      const output = $("voiceTextOutput");
      const cards = $("voiceActionCards");
      const intentPanel = $("voiceIntentPanel");
      const shell = $("voiceShell");
      const meta = $("voiceResultMeta");
      const toggleBtn = $("voiceExpandBtn");
      const secondaryActions = document.querySelector(".voice-secondary-actions");
      if (!output || !cards || !shell || !intentPanel) return;
      const applyVoiceTextLayout = (rawText, mode) => {
        const text = String(rawText || "").trim();
        const longText = text.length >= 56 || text.includes("\n");
        const expanded = !!state.voiceExpanded;
        if (meta) meta.classList.toggle("show", longText && mode === "draft");
        output.classList.toggle("long-text", longText);
        output.classList.toggle("collapsed", longText && mode === "draft" && !expanded);
        if (toggleBtn) {
          toggleBtn.textContent = expanded ? "收起内容" : "展开全文";
        }
        return { text, longText };
      };
      if (state.voiceRecording) {
        shell.classList.add("is-recording");
        const previewText = String(state.voicePartial || "").trim();
        if (meta) meta.classList.remove("show");
        if (secondaryActions) secondaryActions.classList.remove("show");
        output.classList.remove("long-text", "collapsed");
        output.textContent = previewText || "正在聆听...";
        output.classList.toggle("active", !!previewText);
        cards.classList.remove("show");
        intentPanel.classList.remove("show");
        return;
      }
      shell.classList.remove("is-recording");
      if (state.voiceStatus === "recognizing") {
        const previewText = String(state.voicePartial || "").trim();
        if (meta) meta.classList.remove("show");
        if (secondaryActions) secondaryActions.classList.remove("show");
        output.classList.remove("long-text", "collapsed");
        output.textContent = previewText || "正在识别...";
        output.classList.toggle("active", !!previewText);
        cards.classList.remove("show");
        intentPanel.classList.remove("show");
        return;
      }
      if (state.voiceDraft) {
        const formatted = applyVoiceTextLayout(state.voiceDraft, "draft");
        output.textContent = formatted.longText ? state.voiceDraft : state.voiceDraft;
        output.classList.add("active");
        cards.classList.add("show");
        renderVoiceIntentPanel();
        if (secondaryActions) secondaryActions.classList.add("show");
      } else {
        if (meta) meta.classList.remove("show");
        if (secondaryActions) secondaryActions.classList.remove("show");
        output.classList.remove("long-text", "collapsed");
        output.textContent = "等待您的指令...";
        output.classList.remove("active");
        cards.classList.remove("show");
        intentPanel.classList.remove("show");
      }
    }

    function cleanupVoiceRuntime() {
      if (state.voiceProcessor) {
        try { state.voiceProcessor.disconnect(); } catch {}
        state.voiceProcessor.onaudioprocess = null;
        state.voiceProcessor = null;
      }
      if (state.voiceSourceNode) {
        try { state.voiceSourceNode.disconnect(); } catch {}
        state.voiceSourceNode = null;
      }
      if (state.voiceMediaStream) {
        try { state.voiceMediaStream.getTracks().forEach((track) => track.stop()); } catch {}
        state.voiceMediaStream = null;
      }
      if (state.voiceAudioContext) {
        try { state.voiceAudioContext.close(); } catch {}
        state.voiceAudioContext = null;
      }
      if (state.voiceWs) {
        try { state.voiceWs.close(); } catch {}
        state.voiceWs = null;
      }
    }

    function downsampleBuffer(buffer, inputRate, outputRate) {
      if (!buffer || outputRate >= inputRate) return buffer;
      const ratio = inputRate / outputRate;
      const newLength = Math.round(buffer.length / ratio);
      const result = new Float32Array(newLength);
      let offsetResult = 0;
      let offsetBuffer = 0;
      while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
        let accum = 0;
        let count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
          accum += buffer[i];
          count += 1;
        }
        result[offsetResult] = count ? accum / count : 0;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
      }
      return result;
    }

    function floatTo16BitPCM(floatBuffer) {
      const output = new Int16Array(floatBuffer.length);
      for (let i = 0; i < floatBuffer.length; i += 1) {
        const value = Math.max(-1, Math.min(1, floatBuffer[i]));
        output[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
      }
      return output;
    }

    function voiceWsUrl() {
      const base = H5_API_BASE.replace(/^http/i, "ws").replace(/^https/i, "wss").replace(/\/$/, "");
      const params = new URLSearchParams();
      params.set("token", state.token || "");
      const installationId = localStorage.getItem("installation_id") || "";
      if (installationId) params.set("installation_id", installationId);
      return `${base}/api/h5-chat/voice/session?${params.toString()}`;
    }

    async function openVoiceRealtimeSession() {
      if (!state.token) throw new Error("请先登录后再使用语音输入");
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("当前浏览器不支持麦克风录音");
      }
      cleanupVoiceRuntime();
      state.voiceDraft = "";
      state.voicePartial = "";
      state.voiceExpanded = false;
      resetVoiceIntent();

      const ws = new WebSocket(voiceWsUrl());
      ws.binaryType = "arraybuffer";
      await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (fn, value) => {
          if (settled) return;
          settled = true;
          fn(value);
        };
        ws.onopen = () => finish(resolve);
        ws.onerror = () => finish(reject, new Error("语音会话连接失败"));
        ws.onclose = () => {
          if (!settled) finish(reject, new Error("语音会话已关闭"));
        };
      });
      state.voiceWs = ws;
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data || "{}"));
          const type = String(payload.type || "");
          if (type === "listening") {
            state.voiceStatus = "recording";
            syncVoiceDraftDisplay();
            return;
          }
          if (type === "partial") {
            state.voiceStatus = "recognizing";
            state.voicePartial = String(payload.text || "").trim();
            syncVoiceDraftDisplay();
            return;
          }
          if (type === "final") {
            state.voiceStatus = "understanding";
            state.voicePartial = "";
            state.voiceDraft = String(payload.text || "").trim();
            state.voiceExpanded = false;
            resetVoiceIntent();
            syncVoiceDraftDisplay();
            return;
          }
          if (type === "intent") {
            state.voiceStatus = "resolved";
            state.voiceIntent = payload;
            state.voiceActions = Array.isArray(payload.actions) ? payload.actions : [];
            cleanupVoiceRuntime();
            renderVoiceActionCards();
            syncVoiceDraftDisplay();
            return;
          }
          if (type === "error") {
            state.voiceStatus = "error";
            cleanupVoiceRuntime();
            toast(String(payload.message || "语音识别失败"));
            syncVoiceDraftDisplay();
          }
        } catch (err) {
          console.warn("voice message parse failed", err);
        }
      };
      ws.onclose = () => {
        state.voiceWs = null;
      };
      ws.send(JSON.stringify({ type: "start", format: "pcm_s16le", sample_rate: 16000, channels: 1 }));

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      if (audioContext.state === "suspended") {
        try { await audioContext.resume(); } catch {}
      }
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(2048, 1, 1);
      processor.onaudioprocess = (audioEvent) => {
        if (!state.voiceRecording || !state.voiceWs || state.voiceWs.readyState !== WebSocket.OPEN) return;
        const input = audioEvent.inputBuffer.getChannelData(0);
        const downsampled = downsampleBuffer(input, audioContext.sampleRate, 16000);
        const pcm = floatTo16BitPCM(downsampled);
        try {
          state.voiceWs.send(pcm.buffer);
          state.voiceSeq += 1;
        } catch (err) {
          console.warn("voice audio chunk send failed", err);
        }
      };
      source.connect(processor);
      processor.connect(audioContext.destination);
      state.voiceMediaStream = stream;
      state.voiceAudioContext = audioContext;
      state.voiceSourceNode = source;
      state.voiceProcessor = processor;
      state.voiceStatus = "recording";
    }

    async function startVoiceCapture(evt) {
      if (evt && evt.cancelable) evt.preventDefault();
      if (state.voiceRecording) return;
      state.voiceRecording = true;
      if (state.voiceTimer) {
        clearTimeout(state.voiceTimer);
        state.voiceTimer = null;
      }
      try {
        await openVoiceRealtimeSession();
      } catch (err) {
        state.voiceRecording = false;
        state.voiceStatus = "error";
        cleanupVoiceRuntime();
        toast(err.message || "无法启动语音识别");
      }
      syncVoiceDraftDisplay();
    }

    function stopVoiceCapture(evt) {
      if (!state.voiceRecording) return;
      if (evt && evt.cancelable) evt.preventDefault();
      state.voiceRecording = false;
      state.voiceStatus = "recognizing";
      if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
        try { state.voiceWs.send(JSON.stringify({ type: "stop" })); } catch {}
      }
      syncVoiceDraftDisplay();
      state.voiceTimer = setTimeout(() => {
        if (!state.voiceDraft && !state.voicePartial) {
          cleanupVoiceRuntime();
          state.voiceStatus = "idle";
        }
        syncVoiceDraftDisplay();
      }, 2500);
    }

    function setOfficeVoiceButtonPressed(pressed) {
      document.querySelectorAll(".office-voice-entry").forEach((btn) => {
        btn.classList.toggle("is-pressing", !!pressed);
      });
    }

    async function startOfficeVoiceCapture(evt) {
      if (evt && evt.cancelable) evt.preventDefault();
      if (state.officeVoiceHoldActive || state.voiceRecording) return;
      state.officeVoiceHoldActive = true;
      setOfficeVoiceButtonPressed(true);
      switchTab("voice");
      await new Promise((resolve) => setTimeout(resolve, 50));
      await startVoiceCapture(evt);
    }

    function stopOfficeVoiceCapture(evt) {
      if (!state.officeVoiceHoldActive) return;
      state.officeVoiceHoldActive = false;
      setOfficeVoiceButtonPressed(false);
      stopVoiceCapture(evt);
    }

    function clearVoiceDraft() {
      state.voiceDraft = "";
      state.voicePartial = "";
      state.voiceExpanded = false;
      if (state.voiceTimer) {
        clearTimeout(state.voiceTimer);
        state.voiceTimer = null;
      }
      state.voiceRecording = false;
      state.officeVoiceHoldActive = false;
      setOfficeVoiceButtonPressed(false);
      state.voiceStatus = "idle";
      resetVoiceIntent();
      cleanupVoiceRuntime();
      syncVoiceDraftDisplay();
    }

    function sendVoiceDraftToMessages(submitNow = false, prefix = "", explicitContent = "") {
      const draft = String(explicitContent || state.voiceDraft || state.voicePartial || "").trim();
      if (!draft) {
        toast("请先录入一段语音内容");
        return;
      }
      const finalText = `${prefix || ""}${draft}`.trim();
      state.lastViewBeforeMessages = "voice";
      switchTab("messages");
      setMessageTemplate(finalText);
      if (submitNow) {
        setTimeout(() => {
          $("sendForm")?.requestSubmit();
        }, 120);
      }
    }

    async function refreshCaptcha() {
      const data = await fetch(apiUrl("/auth/captcha")).then((r) => r.json());
      $("captchaId").value = data.captcha_id || "";
      $("captchaImg").src = data.image || "";
      $("captcha").value = "";
    }

    function normalizePhone(raw) {
      const value = String(raw || "").replace(/\D/g, "");
      return /^1[3-9]\d{9}$/.test(value) ? value : "";
    }

    function normalizeAuthErrorDetail(detail) {
      if (detail == null) return "";
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        return detail.map((item) => {
          if (item == null) return "";
          if (typeof item === "string") return item;
          if (typeof item === "object") return item.msg || item.message || item.detail || JSON.stringify(item);
          return String(item);
        }).filter(Boolean).join("；");
      }
      if (typeof detail === "object") return detail.msg || detail.message || detail.detail || JSON.stringify(detail);
      return String(detail);
    }

    function setAuthTab(tabName) {
      const name = tabName === "password" ? "password" : "sms";
      document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
        const active = btn.dataset.authTab === name;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-selected", active ? "true" : "false");
      });
      document.querySelectorAll("[data-auth-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.authPanel !== name);
      });
    }

    let smsTimer = null;
    function setSmsCooldown(seconds) {
      const btn = $("sendSmsBtn");
      if (smsTimer) {
        clearInterval(smsTimer);
        smsTimer = null;
      }
      if (!seconds) {
        btn.disabled = false;
        btn.textContent = "获取短信验证码";
        return;
      }
      let left = seconds;
      btn.disabled = true;
      const tick = () => {
        btn.textContent = left > 0 ? `已发送（${left}s）` : "获取短信验证码";
        if (left <= 0) {
          clearInterval(smsTimer);
          smsTimer = null;
          btn.disabled = false;
          return;
        }
        left -= 1;
      };
      tick();
      smsTimer = setInterval(tick, 1000);
    }

    async function loadMe() {
      if (!state.token) return false;
      try {
        state.user = await api("/auth/me");
        const name = state.user.email || "已登录";
        $("profileName").textContent = name;
        $("avatarMini").textContent = firstChar(name);
        $("profileAvatar").textContent = firstChar(name);
        syncAgentManageEntry();
        $("loginPanel").classList.add("hidden");
        $("appPanel").classList.remove("hidden");
        switchTab("office");
        await Promise.all([loadHistory(), refreshDeviceStatus(), loadTasks({ reset: true }), loadRuns({ reset: true, limit: 20, compact: true }), loadTaskSkills()]);
        return true;
      } catch (err) {
        localStorage.removeItem("lobster_h5_token");
        state.token = "";
        return false;
      }
    }

    function renderProfileDeviceSelect() {
      const sel = $("profileDeviceSelect");
      if (!sel) return;
      ensureSelectedInstallationId();
      const rows = state.devices || [];
      sel.innerHTML = rows.length
        ? rows.map((device) => {
            const id = String(device.installation_id || "");
            const name = deviceDisplayName(device) || id;
            const accountCount = Number(device.publish_account_count || 0);
            const suffix = `${device.online ? "online" : "offline"}${accountCount ? ` / ${accountCount} accounts` : ""}`;
            return optionHtml(id, `${name} (${suffix})`);
          }).join("")
        : optionHtml("", "No online device");
      sel.value = state.selectedInstallationId || "";
      const selected = selectedDevice();
      const text = selected
        ? `${selected.online ? "online" : "offline"} / ${deviceDisplayName(selected)}`
        : "No device selected";
      if ($("profileSelectedDeviceText")) $("profileSelectedDeviceText").textContent = text;
    }

    async function refreshDeviceStatus() {
      if (!state.token) return;
      try {
        const data = await api("/api/h5-chat/devices/status");
        state.devices = Array.isArray(data.devices) ? data.devices : [];
        ensureSelectedInstallationId();
        state.publishAccountsLoaded = false;
        $("onlineDot").classList.toggle("online", !!data.online);
        const online = state.devices.filter((d) => d.online).length;
        const total = state.devices.length;
        const text = data.online ? `本地在线：${online}/${total || online} 台` : "未检测到本地 online";
        $("deviceText").textContent = text;
        $("profileDeviceText").textContent = text;
        renderProfileDeviceSelect();
        renderWorkflowDeviceSelect();
        renderOfficeEmployees();
      } catch (err) {
        $("onlineDot").classList.remove("online");
        renderProfileDeviceSelect();
        renderWorkflowDeviceSelect();
        $("deviceText").textContent = "设备状态获取失败";
        $("profileDeviceText").textContent = "设备状态获取失败";
        renderOfficeEmployees();
      }
    }

    function normalizeAvatarRows(...groups) {
      const out = [];
      const seen = new Set();
      groups.flat().forEach((row) => {
        const avatar = String((row && row.avatar) || "").trim();
        if (!avatar || seen.has(avatar)) return;
        seen.add(avatar);
        out.push({ avatar, title: row.title || avatar, section: row.section_label || row.section || "" });
      });
      return out;
    }

    function normalizeVoiceRows(...groups) {
      const out = [];
      const seen = new Set();
      groups.flat().forEach((row) => {
        const styles = Array.isArray(row && row.styles) && row.styles.length ? row.styles : [row];
        styles.forEach((style) => {
          const voice = String((style && style.voice) || (row && row.voice) || "").trim();
          if (!voice || seen.has(voice)) return;
          seen.add(voice);
          const base = row && row.title ? row.title : voice;
          const label = style && style.label && style.label !== "默认风格" ? `${base} - ${style.label}` : base;
          out.push({ voice, title: label, section: row.section_label || row.section || "" });
        });
      });
      return out;
    }

    function renderHiflyOptions() {
      const avatarSel = $("taskAvatar");
      const voiceSel = $("taskVoice");
      if (avatarSel) {
        avatarSel.innerHTML = state.avatarRows.length
          ? state.avatarRows.map((row) => `<option value="${escapeHtml(row.avatar)}">${escapeHtml(row.title)}</option>`).join("")
          : `<option value="">暂无可用数字人</option>`;
      }
      if (voiceSel) {
        voiceSel.innerHTML = state.voiceRows.length
          ? state.voiceRows.map((row) => `<option value="${escapeHtml(row.voice)}">${escapeHtml(row.title)}</option>`).join("")
          : `<option value="">暂无可用声音</option>`;
      }
      renderWorkHiflyOptions();
    }

    function optionHtml(value, label) {
      return `<option value="${escapeHtml(value || "")}">${escapeHtml(label || value || "-")}</option>`;
    }

    function taskFieldHtml(label, control, full = false) {
      return `<div class="field ${full ? "full" : ""}"><label>${escapeHtml(label)}</label>${control}</div>`;
    }

    function ipTemplateSelectControl(id) {
      return `<div class="ip-template-select-row">${taskSelectHtml(id, optionHtml("", "模板加载中..."))}<button class="ghost" type="button" data-open-personal-template-settings>配置模板</button></div>`;
    }

    function ipDailyTaskOptionsHtml() {
      return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;">${IP_DAILY_TASK_OPTIONS.map((item) => `
        <label class="task-checkbox" style="min-height:38px;padding:0 10px;border:1px solid rgba(255,255,255,.18);border-radius:10px;background:rgba(255,255,255,.08);">
          <input type="checkbox" data-ip-daily-task="${escapeHtml(item.value)}" checked>
          <span>${escapeHtml(item.label)}</span>
        </label>
      `).join("")}</div>`;
    }

    function selectedIpDailyTasks() {
      return Array.from(document.querySelectorAll("[data-ip-daily-task]"))
        .filter((el) => el.checked)
        .map((el) => String(el.getAttribute("data-ip-daily-task") || "").trim())
        .filter(Boolean);
    }

    function taskSelectHtml(id, options) {
      return `<select id="${escapeHtml(id)}">${options}</select>`;
    }

    function taskTextareaHtml(id, placeholder) {
      return `<textarea id="${escapeHtml(id)}" rows="3" placeholder="${escapeHtml(placeholder || "")}"></textarea>`;
    }

    function videoMemorySelectControl(id) {
      return `<select id="${escapeHtml(id)}" class="video-memory-select" multiple size="4"><option value="" disabled>加载中...</option></select>`;
    }

    function selectedMultiValues(id) {
      const el = $(id);
      if (!el) return [];
      return Array.from(el.selectedOptions || [])
        .map((opt) => String(opt.value || "").trim())
        .filter(Boolean);
    }

    function setMultiSelectValues(id, values) {
      const el = $(id);
      if (!el) return;
      const selected = new Set((Array.isArray(values) ? values : []).map((v) => String(v || "").trim()).filter(Boolean));
      Array.from(el.options || []).forEach((opt) => { opt.selected = selected.has(String(opt.value || "")); });
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function fillVideoMemorySelects() {
      const selects = Array.from(document.querySelectorAll(".video-memory-select")).filter(Boolean);
      if (!selects.length) return;
      const rows = Array.isArray(state.personalMemoryDocs) ? state.personalMemoryDocs : [];
      selects.forEach((sel) => {
        const selected = new Set(Array.from(sel.selectedOptions || []).map((opt) => String(opt.value || "").trim()).filter(Boolean));
        sel.innerHTML = rows.length
          ? rows.map((doc) => optionHtml(personalDocId(doc), personalMemoryTitle(doc))).join("")
          : `<option value="" disabled>暂无记忆文件</option>`;
        Array.from(sel.options || []).forEach((opt) => { opt.selected = selected.has(String(opt.value || "")); });
      });
    }

    async function loadVideoMemoryDocsForSelect() {
      if (Array.isArray(state.personalMemoryDocs) && state.personalMemoryDocs.length) {
        fillVideoMemorySelects();
        return;
      }
      try {
        const rows = await loadPersonalMemoryDocs();
        state.personalMemoryDocs = Array.isArray(rows) ? rows : [];
      } catch {
        state.personalMemoryDocs = [];
      }
      fillVideoMemorySelects();
    }

    function bindGoalVideoModeControls(scope) {
      const isTask = scope === "task";
      const modeId = isTask ? "taskVideoMode" : "abilityVideoMode";
      const mode = $(modeId) ? $(modeId).value : "single_asset";
      const assetField = $(isTask ? "taskVideoAssetField" : "abilityVideoAssetField");
      const memoryField = $(isTask ? "taskVideoMemoryField" : "abilityVideoMemoryField");
      const groupField = $(isTask ? "taskCandidateGroupField" : "abilityVideoCandidateGroupField");
      if (assetField) assetField.classList.toggle("hidden", mode !== "single_asset");
      if (memoryField) memoryField.classList.toggle("hidden", mode !== "memory_image");
      if (groupField) groupField.classList.toggle("hidden", mode !== "asset_group");
      const sel = $(modeId);
      if (sel && !sel.dataset.videoModeBound) {
        sel.dataset.videoModeBound = "1";
        sel.addEventListener("change", () => bindGoalVideoModeControls(scope));
      }
    }

    function collectGoalVideoPayloadFromFields(ids) {
      const mode = ($(ids.modeId) ? $(ids.modeId).value : "single_asset") || "single_asset";
      const prompt = ids.promptId && $(ids.promptId) ? $(ids.promptId).value.trim() : "";
      const payload = { video_mode: mode, prompt };
      if (mode === "single_asset") {
        const ref = ids.assetId && $(ids.assetId) ? $(ids.assetId).value.trim() : "";
        if (!ref) throw new Error("请选择或上传素材图片");
        payload.source_mode = "reference_image";
        if (/^https?:\/\//i.test(ref)) {
          payload.reference_image_urls = [ref];
          payload.reference_image_url = ref;
        } else {
          payload.reference_asset_ids = [ref];
          payload.reference_asset_id = ref;
        }
        return payload;
      }
      if (mode === "memory_image") {
        const memoryDocIds = selectedMultiValues(ids.memoryId);
        if (!memoryDocIds.length) throw new Error("请选择记忆文件");
        payload.source_mode = "ai_image";
        payload.memory_doc_ids = memoryDocIds;
        return payload;
      }
      const group = ids.groupId && $(ids.groupId) ? $(ids.groupId).value.trim() : "";
      if (!group) throw new Error("请选择素材分组");
      payload.source_mode = "asset_random";
      payload.candidate_group = group;
      return payload;
    }

    function goalVideoModeFromPayload(payload) {
      const item = payload && typeof payload === "object" ? payload : {};
      const explicit = String(item.video_mode || "").trim();
      if (["single_asset", "memory_image", "asset_group"].includes(explicit)) return explicit;
      const sourceMode = String(item.source_mode || "").trim();
      if (sourceMode === "reference_image" || (item.reference_image_url || (Array.isArray(item.reference_image_urls) && item.reference_image_urls.length) || item.reference_asset_id || (Array.isArray(item.reference_asset_ids) && item.reference_asset_ids.length))) return "single_asset";
      if (sourceMode === "asset_random" || item.candidate_group) return "asset_group";
      return "memory_image";
    }

    function firstGoalVideoReference(payload) {
      const item = payload && typeof payload === "object" ? payload : {};
      if (item.reference_image_url) return String(item.reference_image_url || "");
      if (Array.isArray(item.reference_image_urls) && item.reference_image_urls[0]) return String(item.reference_image_urls[0] || "");
      if (item.reference_asset_id) return String(item.reference_asset_id || "");
      if (Array.isArray(item.reference_asset_ids) && item.reference_asset_ids[0]) return String(item.reference_asset_ids[0] || "");
      return "";
    }

    function platformDisplayName(platform) {
      return {
        douyin: "抖音",
        xiaohongshu: "小红书",
        toutiao: "今日头条",
        kuaishou: "快手",
        bilibili: "B站",
      }[String(platform || "").trim()] || platform || "-";
    }

    function fillCandidateGroupSelect() {
      const selects = [$("taskCandidateGroup"), $("abilityVideoCandidateGroup"), $("workflowParamVideoCandidateGroup")].filter(Boolean);
      if (!selects.length) return;
      selects.forEach((sel) => {
        const current = sel.value;
        if (!state.candidateGroups.length) {
          sel.innerHTML = optionHtml("", "暂无备选组，请先到素材库设置");
          return;
        }
        sel.innerHTML = optionHtml("", "不选择") + state.candidateGroups.map((row) => optionHtml(row.name, `${row.name}${row.count ? `（${row.count}张）` : ""}`)).join("");
        if (current && state.candidateGroups.some((row) => row.name === current)) sel.value = current;
      });
    }

    async function loadCandidateGroups() {
      try {
        const data = await api("/api/assets/creative-candidate-groups");
        state.candidateGroups = Array.isArray(data.groups) ? data.groups : [];
      } catch {
        state.candidateGroups = [];
      }
      fillCandidateGroupSelect();
    }

    function publishAccountSelectId(row) {
      return String((row && (row.select_id || row.id)) || "");
    }

    function publishAccountLocalId(row) {
      if (!row) return "";
      const value = row.account_id != null && row.account_id !== "" ? row.account_id : row.id;
      const text = String(value || "").trim();
      return /^\d+$/.test(text) ? Number(text) : text;
    }

    function publishAccountOptionLabel(row) {
      const platform = row.platform_name || platformDisplayName(row.platform);
      const nickname = row.nickname || `账号 #${publishAccountLocalId(row)}`;
      const device = row.device_name ? ` / ${row.device_name}` : "";
      return `${platform} - ${nickname}${device}`;
    }

    function fillPublishPlatformSelect() {
      const sel = $("taskPublishPlatform");
      if (!sel) return;
      const current = sel.value;
      const byPlatform = new Map();
      (state.publishAccounts || []).forEach((row) => {
        const platform = String(row && row.platform || "").trim();
        if (!platform) return;
        if (!byPlatform.has(platform)) byPlatform.set(platform, row.platform_name || platformDisplayName(platform));
      });
      sel.innerHTML = optionHtml("", "不发布，仅生成记录")
        + Array.from(byPlatform.entries()).map(([platform, label]) => optionHtml(platform, label)).join("");
      if (current && byPlatform.has(current)) sel.value = current;
      fillPublishAccountSelect();
    }

    function fillPublishAccountSelect() {
      const sel = $("taskPublishAccount");
      if (!sel) return;
      const current = sel.value;
      const platform = $("taskPublishPlatform") ? $("taskPublishPlatform").value : "";
      const rows = (state.publishAccounts || []).filter((row) => !platform || row.platform === platform);
      sel.innerHTML = rows.length
        ? optionHtml("", "请选择账号") + rows.map((row) => optionHtml(publishAccountSelectId(row), publishAccountOptionLabel(row))).join("")
        : optionHtml("", platform ? "该平台暂无账号" : "先选择发布平台");
      if (current && rows.some((row) => publishAccountSelectId(row) === current)) sel.value = current;
    }

    function fillPublishRunPlatformSelect() {
      const sel = $("publishRunPlatform");
      if (!sel) return;
      const draft = state.publishRunDraft || {};
      const draftAccount = draft.account_id ? (state.publishAccounts || []).find((row) => String(publishAccountLocalId(row)) === String(draft.account_id)) : null;
      const current = sel.value || draft.platform || (draftAccount && draftAccount.platform) || "";
      const byPlatform = new Map();
      (state.publishAccounts || []).forEach((row) => {
        const platform = String(row && row.platform || "").trim();
        if (!platform) return;
        if (!byPlatform.has(platform)) byPlatform.set(platform, row.platform_name || platformDisplayName(platform));
      });
      sel.innerHTML = byPlatform.size
        ? Array.from(byPlatform.entries()).map(([platform, label]) => optionHtml(platform, label)).join("")
        : optionHtml("", "暂无账号");
      if (current && byPlatform.has(current)) sel.value = current;
      else if (byPlatform.size) sel.value = Array.from(byPlatform.keys())[0];
      fillPublishRunAccountSelect();
    }

    function fillPublishRunAccountSelect() {
      const sel = $("publishRunAccount");
      if (!sel) return;
      const draft = state.publishRunDraft || {};
      const platform = $("publishRunPlatform") ? $("publishRunPlatform").value : "";
      const rows = (state.publishAccounts || []).filter((row) => !platform || row.platform === platform);
      const current = sel.value || String(draft.account_id || "");
      sel.innerHTML = rows.length
        ? optionHtml("", "请选择账号") + rows.map((row) => optionHtml(publishAccountSelectId(row), publishAccountOptionLabel(row))).join("")
        : optionHtml("", "暂无账号");
      if (current && rows.some((row) => publishAccountSelectId(row) === String(current))) {
        sel.value = String(current);
      } else if (draft.account_nickname) {
        const hit = rows.find((row) => String(row.nickname || "") === String(draft.account_nickname || ""));
        if (hit) sel.value = publishAccountSelectId(hit);
      }
    }

    async function loadPublishAccounts() {
      if (state.publishAccountsLoaded || state.publishAccountsLoading) {
        fillPublishPlatformSelect();
        fillPublishRunPlatformSelect();
        return;
      }
      state.publishAccountsLoading = true;
      try {
        const iid = currentInstallationId();
        const suffix = iid ? `?installation_id=${encodeURIComponent(iid)}` : "";
        const data = await api(`/api/scheduled-tasks/publish/accounts${suffix}`);
        state.publishAccounts = Array.isArray(data.accounts) ? data.accounts : [];
        state.publishAccountsLoaded = true;
      } catch (err) {
        state.publishAccounts = [];
        toast(err.message || "发布账号加载失败");
      } finally {
        state.publishAccountsLoading = false;
        fillPublishPlatformSelect();
        fillPublishRunPlatformSelect();
      }
    }

    function fillIpTemplateSelect() {
      const selects = [$("taskIpTemplate"), $("abilityIpTemplate"), $("workflowParamIpTemplate")].filter(Boolean);
      if (!selects.length) return;
      selects.forEach((sel) => {
        const current = sel.value;
        if (state.ipTemplatesLoading) {
          sel.innerHTML = optionHtml("", "模板加载中...");
          return;
        }
        if (!state.ipTemplates.length) {
          sel.innerHTML = optionHtml("", "暂无服务器模板");
          return;
        }
        sel.innerHTML = optionHtml("", "请选择模板") + state.ipTemplates.map((row) => {
          const k = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
          const c = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
          return optionHtml(String(row.id), `${row.name || "模板"} · 关键词${k} · 同行${c}`);
        }).join("");
        if (current && state.ipTemplates.some((row) => String(row.id) === current)) sel.value = current;
      });
    }

    async function loadIpTemplates(force = false) {
      if (!force && (state.ipTemplatesLoaded || state.ipTemplatesLoading)) {
        fillIpTemplateSelect();
        return;
      }
      state.ipTemplatesLoading = true;
      fillIpTemplateSelect();
      try {
        const data = await api("/api/ip-content/schedule-templates");
        state.ipTemplates = (Array.isArray(data.items) ? data.items : []).filter((row) => !isPersonalDefaultTemplate(row));
        state.ipTemplatesLoaded = true;
      } catch (err) {
        state.ipTemplates = [];
        toast(err.message || "IP模板加载失败");
      } finally {
        state.ipTemplatesLoading = false;
        fillIpTemplateSelect();
      }
    }

    function personalDocId(doc) {
      return String((doc && (doc.doc_id || doc.id)) || "").trim();
    }

    const PERSONAL_DOC_TYPES = [
      { key: "brand_product_intro", label: "产品介绍" },
      { key: "product_service_faq", label: "百问百答" },
      { key: "short_video_scripts", label: "短视频口播稿" },
      { key: "custom_memory", label: "自定义参考文档" },
    ];

    function personalSetStatus(text, isError = false) {
      const el = $("personalStatusMsg");
      if (!el) return;
      el.textContent = text || "";
      el.classList.toggle("hidden", !text);
      el.classList.toggle("error", !!isError);
    }

    function personalSetBusy(btn, busy, label = "处理中...") {
      if (!btn) return;
      if (busy) {
        if (!btn.dataset.oldText) btn.dataset.oldText = btn.textContent || "";
        btn.textContent = label;
        btn.disabled = true;
      } else {
        btn.textContent = btn.dataset.oldText || btn.textContent || "";
        btn.disabled = false;
        delete btn.dataset.oldText;
      }
    }

    function personalDocTypeLabel(key) {
      const row = PERSONAL_DOC_TYPES.find((item) => item.key === key);
      return row ? row.label : (key || "记忆");
    }

    function recommendPersonalMemoryTitle(docTypes, hasCustomReference) {
      const keys = Array.isArray(docTypes) ? docTypes.filter(Boolean) : [];
      if (keys.length === 1 && keys[0] === "custom_memory") return "自定义记忆";
      if (keys.length === 1) return personalDocTypeLabel(keys[0]);
      if (!keys.length && hasCustomReference) return "自定义记忆";
      return "IP人设记忆";
    }

    function selectedPersonalIds(kind) {
      return Array.from(document.querySelectorAll(`[data-personal-select="${kind}"]:checked`))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function personalSelectedMap(kind) {
      if (kind === "keyword") return state.personalSelectedKeywords;
      if (kind === "competitor") return state.personalSelectedCompetitors;
      if (kind === "memory_doc") return state.personalSelectedMemories;
      return {};
    }

    function personalCleanIntIds(map) {
      return Object.keys(map || {}).filter((id) => map[id]).map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0);
    }

    function personalCleanStringIds(map) {
      return Object.keys(map || {}).filter((id) => map[id]).map((id) => String(id || "").trim()).filter(Boolean);
    }

    function setPersonalSettingsTab(tab) {
      const next = ["keywords", "competitors", "profile", "upload", "memory", "template"].includes(String(tab || "")) ? String(tab || "") : "keywords";
      state.personalSettingsTab = next;
      document.querySelectorAll("[data-personal-tab]").forEach((btn) => btn.classList.toggle("active", btn.dataset.personalTab === state.personalSettingsTab));
      document.querySelectorAll("[data-personal-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.personalPanel === state.personalSettingsTab));
      if (next === "profile") renderPersonalSurveyWizard();
    }

    function personalFieldValue(id) {
      return (($(`${id}`) && $(`${id}`).value) || "").trim();
    }

    function setPersonalFieldValue(id, value) {
      const el = $(id);
      if (el) el.value = value || "";
    }

    function personalSurveyQuestions() {
      return [
        { field: "personalProfileName", label: "名字", type: "input" },
        { field: "personalBirthEra", label: "出生年代", type: "input" },
        { field: "personalCurrentCity", label: "现居城市", type: "input" },
        { field: "personalHometown", label: "籍贯", type: "input" },
        { field: "personalRole", label: "你是做什么的", type: "input" },
        { field: "personalShareTopic", label: "主要分享什么", type: "input" },
        { field: "personalVideoStyle", label: "视频风格", type: "input" },
        { field: "personalAfterViewAction", label: "看完后希望用户做什么", type: "input" },
        { field: "personalBusinessProduct", label: "你在做什么/什么产品", type: "textarea" },
        { field: "personalTargetCustomer", label: "想卖给谁/哪些年代的人", type: "textarea" },
        { field: "personalAdvantages", label: "优势/比同行好在哪", type: "textarea" },
      ];
    }

    function syncPersonalSurveyAnswerToField() {
      const questions = personalSurveyQuestions();
      const idx = Math.max(0, Math.min(Number(state.personalSurveyIndex || 0), questions.length - 1));
      const question = questions[idx];
      const answer = $("personalSurveyAnswer");
      if (question && answer) setPersonalFieldValue(question.field, answer.value || "");
    }

    function renderPersonalSurveyWizard() {
      const host = $("personalSurveyAnswerHost");
      const title = $("personalSurveyQuestionTitle");
      const step = $("personalSurveyStepText");
      const progress = $("personalSurveyProgress");
      const prev = $("personalSurveyPrevBtn");
      const next = $("personalSurveyNextBtn");
      const save = $("personalSaveProfileBtn");
      if (!host || !title) return;
      const questions = personalSurveyQuestions();
      const maxIdx = Math.max(0, questions.length - 1);
      const idx = Math.max(0, Math.min(Number(state.personalSurveyIndex || 0), maxIdx));
      state.personalSurveyIndex = idx;
      const question = questions[idx];
      title.textContent = question.label;
      if (step) step.textContent = `${idx + 1}/${questions.length}`;
      if (progress) progress.style.width = `${Math.round(((idx + 1) / questions.length) * 100)}%`;
      const value = personalFieldValue(question.field);
      const tag = question.type === "textarea" ? "textarea" : "input";
      host.innerHTML = tag === "textarea"
        ? `<textarea id="personalSurveyAnswer" rows="5"></textarea>`
        : `<input id="personalSurveyAnswer" type="text">`;
      const answer = $("personalSurveyAnswer");
      if (answer) {
        answer.value = value;
        answer.addEventListener("input", syncPersonalSurveyAnswerToField);
        setTimeout(() => answer.focus(), 0);
      }
      if (prev) prev.disabled = idx <= 0;
      if (next) next.hidden = idx >= maxIdx;
      if (save) save.hidden = idx < maxIdx;
    }

    function movePersonalSurvey(delta) {
      syncPersonalSurveyAnswerToField();
      const questions = personalSurveyQuestions();
      const maxIdx = Math.max(0, questions.length - 1);
      state.personalSurveyIndex = Math.max(0, Math.min(Number(state.personalSurveyIndex || 0) + delta, maxIdx));
      renderPersonalSurveyWizard();
    }

    function personalProfileRequirements() {
      return {
        name: personalFieldValue("personalProfileName"),
        birth_era: personalFieldValue("personalBirthEra"),
        current_city: personalFieldValue("personalCurrentCity"),
        hometown: personalFieldValue("personalHometown"),
        role: personalFieldValue("personalRole"),
        share_topic: personalFieldValue("personalShareTopic"),
        video_style: personalFieldValue("personalVideoStyle"),
        after_view_action: personalFieldValue("personalAfterViewAction"),
      };
    }

    function personalBusinessRequirements() {
      return {
        product: personalFieldValue("personalBusinessProduct"),
        target_customer: personalFieldValue("personalTargetCustomer"),
        advantages: personalFieldValue("personalAdvantages"),
      };
    }

    function personalSurveyRequirements() {
      const basicProfile = personalProfileRequirements();
      const businessDescription = personalBusinessRequirements();
      return {
        basic_profile: basicProfile,
        business_description: businessDescription,
        profile_name: basicProfile.name,
        birth_era: basicProfile.birth_era,
        current_city: basicProfile.current_city,
        hometown: basicProfile.hometown,
        role: basicProfile.role,
        share_topic: basicProfile.share_topic,
        video_style: basicProfile.video_style,
        after_view_action: basicProfile.after_view_action,
        product: businessDescription.product,
        target_customer: businessDescription.target_customer,
        advantages: businessDescription.advantages,
      };
    }

    function isPersonalDefaultTemplate(row) {
      const meta = row && row.meta && typeof row.meta === "object" ? row.meta : {};
      return !!meta.is_personal_default || String((row && row.name) || "") === "个人默认配置";
    }

    async function loadPersonalMemoryDocs() {
      const iid = currentInstallationId();
      if (!iid) return [];
      const data = await api("/api/personal-settings/memory-documents/list", { headers: { "X-Installation-Id": iid } });
      return Array.isArray(data.documents) ? data.documents : [];
    }

    async function loadPersonalTemplateRows() {
      const data = await api("/api/ip-content/schedule-templates").catch(() => ({ items: [] }));
      return (Array.isArray(data.items) ? data.items : []).filter((row) => !isPersonalDefaultTemplate(row));
    }

    function fillPersonalSurveyFields(item) {
      const req = (item && item.requirements) || {};
      const profile = req.basic_profile && typeof req.basic_profile === "object" ? req.basic_profile : req.profile || {};
      const business = req.business_description && typeof req.business_description === "object" ? req.business_description : req.business || {};
      setPersonalFieldValue("personalProfileName", req.profile_name || profile.name || "");
      setPersonalFieldValue("personalBirthEra", req.birth_era || profile.birth_era || "");
      setPersonalFieldValue("personalCurrentCity", req.current_city || profile.current_city || "");
      setPersonalFieldValue("personalHometown", req.hometown || profile.hometown || "");
      setPersonalFieldValue("personalRole", req.role || profile.role || "");
      setPersonalFieldValue("personalShareTopic", req.share_topic || profile.share_topic || "");
      setPersonalFieldValue("personalVideoStyle", req.video_style || profile.video_style || "");
      setPersonalFieldValue("personalAfterViewAction", req.after_view_action || profile.after_view_action || "");
      setPersonalFieldValue("personalBusinessProduct", req.product || business.product || "");
      setPersonalFieldValue("personalTargetCustomer", req.target_customer || business.target_customer || "");
      setPersonalFieldValue("personalAdvantages", req.advantages || business.advantages || "");
      renderPersonalSurveyWizard();
    }

    function applyPersonalSurvey(item) {
      state.personalDefault = item || {};
      fillPersonalSurveyFields(state.personalDefault);
    }

    function applyPersonalTemplate(item, options = {}) {
      item = item || {};
      const editing = !!options.editing;
      state.personalEditingTemplateId = editing && item && item.id ? String(item.id) : "";
      state.personalSelectedKeywords = {};
      state.personalSelectedCompetitors = {};
      state.personalSelectedMemories = {};
      (item.keyword_ids || []).forEach((id) => { if (id) state.personalSelectedKeywords[String(id)] = true; });
      (item.competitor_ids || []).forEach((id) => { if (id) state.personalSelectedCompetitors[String(id)] = true; });
      (item.memory_doc_ids || []).forEach((id) => { if (id) state.personalSelectedMemories[String(id)] = true; });
      if ($("personalTemplateName")) $("personalTemplateName").value = item.name || "";
      if (item.requirements && typeof item.requirements === "object") fillPersonalSurveyFields(item);
    }

    async function refreshPersonalDataPreserveSelection(parts = {}) {
      const jobs = [];
      if (parts.keywords) jobs.push(api("/api/ip-content/keywords").then((data) => { state.personalKeywords = Array.isArray(data.items) ? data.items : []; }).catch(() => {}));
      if (parts.competitors) jobs.push(api("/api/ip-content/competitors").then((data) => { state.personalCompetitors = Array.isArray(data.items) ? data.items : []; }).catch(() => {}));
      if (parts.memories) jobs.push(loadPersonalMemoryDocs().then((rows) => { state.personalMemoryDocs = Array.isArray(rows) ? rows : []; }).catch(() => {}));
      if (parts.templates) jobs.push(loadPersonalTemplateRows().then((rows) => { state.personalTemplates = Array.isArray(rows) ? rows : []; }).catch(() => {}));
      await Promise.all(jobs);
      state.personalSettingsLoaded = true;
      renderPersonalSettings();
    }

    async function loadPersonalSettings(force = false) {
      if (!state.token) return;
      if (!force && (state.personalSettingsLoaded || state.personalSettingsLoading)) {
        renderPersonalSettings();
        return;
      }
      state.personalSettingsLoading = true;
      try {
        const [keywords, competitors, defaults, memories, templates] = await Promise.all([
          api("/api/ip-content/keywords").catch(() => ({ items: [] })),
          api("/api/ip-content/competitors").catch(() => ({ items: [] })),
          api("/api/ip-content/personal-default").catch(() => ({ item: null })),
          loadPersonalMemoryDocs().catch(() => []),
          loadPersonalTemplateRows().catch(() => []),
        ]);
        state.personalKeywords = Array.isArray(keywords.items) ? keywords.items : [];
        state.personalCompetitors = Array.isArray(competitors.items) ? competitors.items : [];
        state.personalMemoryDocs = Array.isArray(memories) ? memories : [];
        state.personalTemplates = Array.isArray(templates) ? templates : [];
        applyPersonalSurvey(defaults.item || {});
        state.personalSettingsLoaded = true;
      } finally {
        state.personalSettingsLoading = false;
        renderPersonalSettings();
      }
    }

    function renderPersonalRows(targetId, rows, kind, titleFn, subtitleFn, deleteAttr, actionLabel = "删除", syncAttr = "") {
      const el = $(targetId);
      if (!el) return;
      if (!rows.length) {
        el.innerHTML = `<div class="personal-empty">暂无数据</div>`;
        return;
      }
      const selectedMap = personalSelectedMap(kind);
      el.innerHTML = rows.map((row) => {
        const id = kind === "memory_doc" ? personalDocId(row) : String(row.id || "");
        const title = titleFn(row);
        const subtitle = subtitleFn ? subtitleFn(row) : "";
        const checked = selectedMap[id] ? " checked" : "";
        const del = deleteAttr ? `<button type="button" ${deleteAttr}="${escapeHtml(id)}">${escapeHtml(actionLabel)}</button>` : "";
        const sync = syncAttr ? `<button type="button" ${syncAttr}="${escapeHtml(id)}">同步数据</button>` : "";
        const actions = (sync || del) ? `<div class="personal-row-actions">${sync}${del}</div>` : "";
        if (deleteAttr) {
          return `<div class="personal-row"><span>${escapeHtml(title)}${subtitle ? ` · ${escapeHtml(subtitle)}` : ""}</span>${actions}</div>`;
        }
        return `<div class="personal-row"><label><input type="checkbox" data-personal-select="${escapeHtml(kind)}" value="${escapeHtml(id)}"${checked}><span>${escapeHtml(title)}${subtitle ? ` · ${escapeHtml(subtitle)}` : ""}</span></label>${actions}</div>`;
      }).join("");
    }

    function bindPersonalOptionChecks(root = document) {
      root.querySelectorAll("[data-personal-select]").forEach((input) => {
        input.onchange = () => {
          const map = personalSelectedMap(input.dataset.personalSelect || "");
          if (input.value) map[String(input.value)] = !!input.checked;
        };
      });
    }

    function personalMemoryTitle(doc) {
      return (doc && (doc.title || doc.filename || personalDocId(doc))) || "未命名记忆";
    }

    function renderPersonalMemorySelect() {
      const select = $("personalTargetMemorySelect");
      if (!select) return;
      const current = select.value || "";
      const editableDocs = state.personalMemoryDocs.filter((doc) => !doc.read_only && doc.source !== "agent");
      select.innerHTML = `<option value="">选择已有文档</option>` + editableDocs.map((doc) => {
        const id = personalDocId(doc);
        return `<option value="${escapeHtml(id)}">${escapeHtml(personalMemoryTitle(doc))}</option>`;
      }).join("");
      if (current && editableDocs.some((doc) => personalDocId(doc) === current)) select.value = current;
      syncPersonalSaveMode();
    }

    function personalSyncSelectionMap(map, ids, defaultSelected = true) {
      ids = (ids || []).map((id) => String(id || "").trim()).filter(Boolean);
      const allowed = new Set(ids);
      Object.keys(map || {}).forEach((id) => {
        if (!allowed.has(String(id))) delete map[id];
      });
      ids.forEach((id) => {
        if (!(id in map)) map[id] = !!defaultSelected;
      });
      return map;
    }

    function isPersonalUploadedMemoryDoc(doc) {
      const notes = String((doc && doc.notes) || "");
      const meta = doc && doc.meta && typeof doc.meta === "object" ? doc.meta : {};
      return notes.includes("上传资料") || meta.save_mode === "new" || meta.uploaded === true;
    }

    function personalMemorySourceDocRows() {
      const rows = (state.personalMemoryDocs || []).filter((doc) => !doc.read_only && doc.source !== "agent");
      const uploadRows = rows.filter(isPersonalUploadedMemoryDoc);
      return uploadRows.length ? uploadRows : rows;
    }

    function personalUploadedDocRows() {
      return (state.personalMemoryDocs || []).filter((doc) => !doc.read_only && doc.source !== "agent" && isPersonalUploadedMemoryDoc(doc));
    }

    function ensurePersonalMemorySourceSelections() {
      if (state.personalMemoryUseProfile !== false) state.personalMemoryUseProfile = true;
      state.personalMemorySourceKeywords = personalSyncSelectionMap(
        state.personalMemorySourceKeywords || {},
        (state.personalKeywords || []).map((row) => row.id)
      );
      state.personalMemorySourceCompetitors = personalSyncSelectionMap(
        state.personalMemorySourceCompetitors || {},
        (state.personalCompetitors || []).map((row) => row.id)
      );
      state.personalMemorySourceDocs = personalSyncSelectionMap(
        state.personalMemorySourceDocs || {},
        personalMemorySourceDocRows().map(personalDocId)
      );
      state.personalMemorySourceFiles = personalSyncSelectionMap(
        state.personalMemorySourceFiles || {},
        selectedPersonalUploadFiles().map(personalUploadFileKey)
      );
    }

    function selectedPersonalMemoryKeywordRows() {
      ensurePersonalMemorySourceSelections();
      return (state.personalKeywords || []).filter((row) => state.personalMemorySourceKeywords[String(row.id || "")]);
    }

    function selectedPersonalMemoryCompetitorRows() {
      ensurePersonalMemorySourceSelections();
      return (state.personalCompetitors || []).filter((row) => state.personalMemorySourceCompetitors[String(row.id || "")]);
    }

    function selectedPersonalMemorySourceDocs() {
      ensurePersonalMemorySourceSelections();
      return personalMemorySourceDocRows().filter((doc) => state.personalMemorySourceDocs[personalDocId(doc)]);
    }

    function selectedPersonalMemoryUploadFiles() {
      ensurePersonalMemorySourceSelections();
      return selectedPersonalUploadFiles().filter((file) => state.personalMemorySourceFiles[personalUploadFileKey(file)]);
    }

    function renderPersonalSourceOptions(targetId, rows, selectedMap, kind, titleFn, subtitleFn) {
      const el = $(targetId);
      if (!el) return;
      if (!rows.length) {
        el.innerHTML = `<div class="personal-empty">暂无</div>`;
        return;
      }
      el.innerHTML = rows.map((row) => {
        const id = kind === "source_file" ? personalUploadFileKey(row) : (kind === "source_doc" ? personalDocId(row) : String(row.id || ""));
        const subtitle = subtitleFn ? String(subtitleFn(row) || "") : "";
        return `<label class="personal-source-option">
          <input type="checkbox" data-personal-memory-source="${escapeHtml(kind)}" value="${escapeHtml(id)}"${selectedMap[id] ? " checked" : ""}>
          <span><strong>${escapeHtml(titleFn(row))}</strong>${subtitle ? `<small>${escapeHtml(subtitle)}</small>` : ""}</span>
        </label>`;
      }).join("");
    }

    function renderPersonalMemorySourceSelectors() {
      ensurePersonalMemorySourceSelections();
      const profile = $("personalMemoryUseProfile");
      if (profile) profile.checked = state.personalMemoryUseProfile !== false;
      renderPersonalSourceOptions(
        "personalMemoryKeywordSourceList",
        state.personalKeywords || [],
        state.personalMemorySourceKeywords,
        "keyword",
        (row) => row.display_name || row.keyword || `关键词 #${row.id}`,
        (row) => row.keyword || ""
      );
      renderPersonalSourceOptions(
        "personalMemoryCompetitorSourceList",
        state.personalCompetitors || [],
        state.personalMemorySourceCompetitors,
        "competitor",
        (row) => row.display_name || row.account_key || `同行 #${row.id}`,
        (row) => `${row.platform || ""}${row.account_key ? ` · ${row.account_key}` : ""}`
      );
      renderPersonalSourceOptions(
        "personalMemoryUploadSourceList",
        personalMemorySourceDocRows(),
        state.personalMemorySourceDocs,
        "source_doc",
        personalMemoryTitle,
        (row) => row.notes || row.filename || ""
      );
      const currentFiles = selectedPersonalUploadFiles();
      if (currentFiles.length) {
        const box = $("personalMemoryUploadSourceList");
        const fileHtml = currentFiles.map((file) => {
          const id = personalUploadFileKey(file);
          const size = file && file.size ? ` · ${Math.ceil(file.size / 1024)}KB` : "";
          return `<label class="personal-source-option">
            <input type="checkbox" data-personal-memory-source="source_file" value="${escapeHtml(id)}"${state.personalMemorySourceFiles[id] ? " checked" : ""}>
            <span><strong>${escapeHtml(file.name || "未命名文件")}</strong><small>当前选择${escapeHtml(size)}</small></span>
          </label>`;
        }).join("");
        if (box) box.innerHTML = (box.innerHTML && !box.innerHTML.includes("personal-empty") ? box.innerHTML : "") + fileHtml;
      }
    }

    function syncPersonalSaveMode() {
      const mode = (($("personalSaveMode") && $("personalSaveMode").value) || "new").trim();
      const target = $("personalTargetMemorySelect");
      const title = $("personalMemoryTitle");
      if (target) {
        target.disabled = mode !== "overwrite";
        if (mode !== "overwrite") target.value = "";
      }
      if (title) {
        title.disabled = mode === "overwrite";
        if (mode === "overwrite") title.value = "";
      }
    }

    function personalTemplateName(row) {
      return String((row && row.name) || "").trim() || "未命名模板";
    }

    function resetPersonalTemplateForm() {
      state.personalEditingTemplateId = "";
      state.personalSelectedKeywords = {};
      state.personalSelectedCompetitors = {};
      state.personalSelectedMemories = {};
      if ($("personalTemplateName")) $("personalTemplateName").value = "";
      personalSetStatus("");
      renderPersonalSettings();
    }

    function renderPersonalSavedTemplates() {
      const list = $("personalSavedTemplateList");
      if (!list) return;
      const rows = Array.isArray(state.personalTemplates) ? state.personalTemplates : [];
      if (!rows.length) {
        list.innerHTML = `<div class="personal-empty">暂无模板</div>`;
        return;
      }
      const editingId = String(state.personalEditingTemplateId || "");
      list.innerHTML = rows.map((row) => {
        const id = String(row.id || "");
        const own = row.source !== "agent";
        const keywordCount = Array.isArray(row.keyword_ids) ? row.keyword_ids.length : 0;
        const competitorCount = Array.isArray(row.competitor_ids) ? row.competitor_ids.length : 0;
        const memoryCount = Array.isArray(row.memory_doc_ids) ? row.memory_doc_ids.length : 0;
        const active = id && id === editingId ? " active" : "";
        const source = row.source === "agent" ? `代理商：${row.owner_name || ""}` : `关键词 ${keywordCount} · 同行 ${competitorCount} · 记忆 ${memoryCount}`;
        const grantBtn = own && canManageAgent() ? `<button type="button" data-agent-dispatch-template="${escapeHtml(id)}">下发</button>` : "";
        return `<div class="personal-template-card${active}">
          <div>
            <strong>${escapeHtml(personalTemplateName(row))}</strong>
            <div class="personal-template-meta">${escapeHtml(source)}</div>
          </div>
          <div class="personal-row-actions">
            <button type="button" data-use-personal-template="${escapeHtml(id)}">设为当前</button>
            <button type="button" data-edit-personal-template="${escapeHtml(id)}">${own ? "编辑" : "套用"}</button>
            ${grantBtn}
          </div>
        </div>`;
      }).join("");
    }

    function renderPersonalCurrentTemplate() {
      const box = $("personalCurrentTemplateBox");
      if (!box) return;
      const current = state.personalDefault || {};
      const keywordCount = Array.isArray(current.keyword_ids) ? current.keyword_ids.length : 0;
      const competitorCount = Array.isArray(current.competitor_ids) ? current.competitor_ids.length : 0;
      const memoryCount = Array.isArray(current.memory_doc_ids) ? current.memory_doc_ids.length : 0;
      const meta = current.meta && typeof current.meta === "object" ? current.meta : {};
      const sourceId = String(meta.current_template_id || "").trim();
      const sourceTemplate = sourceId ? (state.personalTemplates || []).find((row) => String(row.id || "") === sourceId) : null;
      const title = sourceTemplate ? personalTemplateName(sourceTemplate) : (current.name && !isPersonalDefaultTemplate(current) ? personalTemplateName(current) : "未指定模板");
      box.innerHTML = `<div class="personal-template-card active">
        <div>
          <strong>${escapeHtml(title)}</strong>
          <div class="personal-template-meta">关键词 ${keywordCount} · 同行 ${competitorCount} · 记忆 ${memoryCount}</div>
        </div>
      </div>`;
    }

    function renderPersonalSettings() {
      syncAgentManageEntry();
      setPersonalSettingsTab(state.personalSettingsTab);
      const tpl = $("personalTemplateList");
      if (tpl) {
        const keywordRows = state.personalKeywords.map((row) => ({ ...row, _kind: "keyword" }));
        const competitorRows = state.personalCompetitors.map((row) => ({ ...row, _kind: "competitor" }));
        const memoryRows = state.personalMemoryDocs.map((row) => ({ ...row, _kind: "memory_doc" }));
        const section = (label, rows, selectedMap, kind, titleFn, subtitleFn) => rows.length
          ? `<div class="hint">${escapeHtml(label)}</div>` + rows.map((row) => {
              const id = kind === "memory_doc" ? personalDocId(row) : String(row.id || "");
              const subtitle = subtitleFn ? subtitleFn(row) : "";
              return `<div class="personal-row"><label><input type="checkbox" data-personal-select="${escapeHtml(kind)}" value="${escapeHtml(id)}"${selectedMap[id] ? " checked" : ""}><span>${escapeHtml(titleFn(row))}${subtitle ? ` · ${escapeHtml(subtitle)}` : ""}</span></label></div>`;
            }).join("")
          : `<div class="personal-empty">${escapeHtml(label)}：暂无</div>`;
        tpl.innerHTML = section("关键词", keywordRows, state.personalSelectedKeywords, "keyword", (row) => row.display_name || row.keyword || `#${row.id}`, (row) => row.keyword || "")
          + section("同行账号", competitorRows, state.personalSelectedCompetitors, "competitor", (row) => row.display_name || row.account_key || `#${row.id}`, (row) => row.platform || "")
          + section("记忆文件", memoryRows, state.personalSelectedMemories, "memory_doc", personalMemoryTitle, (row) => row.notes || row.filename || "");
      }
      const editingLabel = $("personalTemplateEditState");
      if (editingLabel) {
        const current = (state.personalTemplates || []).find((row) => String(row.id || "") === String(state.personalEditingTemplateId || ""));
        editingLabel.textContent = current ? `编辑：${personalTemplateName(current)}` : "新建模板";
      }
      renderPersonalCurrentTemplate();
      renderPersonalSavedTemplates();
      renderPersonalRows("personalKeywordList", state.personalKeywords, "keyword", (row) => row.display_name || row.keyword || `#${row.id}`, (row) => row.keyword || "", "data-delete-personal-keyword", "删除");
      renderPersonalRows("personalCompetitorList", state.personalCompetitors, "competitor", (row) => row.display_name || row.account_key || `#${row.id}`, (row) => row.platform || "", "data-delete-personal-competitor", "删除", "data-sync-personal-competitor");
      renderPersonalMemorySourceSelectors();
      const uploadDocs = $("personalUploadDocList");
      if (uploadDocs) {
        const rows = personalUploadedDocRows();
        uploadDocs.innerHTML = rows.length
          ? rows.map((doc) => {
              const id = personalDocId(doc);
              return `<div class="personal-row personal-memory-row">
                <span>${escapeHtml(personalMemoryTitle(doc))}</span>
                <div class="personal-row-actions">
                  <button type="button" data-preview-personal-memory="${escapeHtml(id)}">预览</button>
                  <button type="button" data-delete-personal-memory="${escapeHtml(id)}">删除</button>
                </div>
              </div>`;
            }).join("")
          : `<div class="personal-empty">暂无上传资料</div>`;
      }
      const mem = $("personalMemoryList");
      if (mem) {
        mem.innerHTML = state.personalMemoryDocs.length
          ? state.personalMemoryDocs.map((doc) => {
              const id = personalDocId(doc);
              const readOnly = !!doc.read_only || doc.source === "agent";
              const tag = readOnly ? "代理商" : "个人";
              return `<div class="personal-row personal-memory-row">
                <span>${escapeHtml(personalMemoryTitle(doc))} · ${escapeHtml(tag)}</span>
                <div class="personal-row-actions">
                  <button type="button" data-preview-personal-memory="${escapeHtml(id)}">预览</button>
                  ${readOnly ? "" : `<button type="button" data-delete-personal-memory="${escapeHtml(id)}">删除</button>`}
                </div>
              </div>`;
            }).join("")
          : `<div class="personal-empty">暂无记忆文件</div>`;
      }
      bindPersonalOptionChecks(document);
      renderPersonalMemorySelect();
      renderPersonalSelectedFiles();
      renderPersonalCustomReference();
      renderPersonalGeneratedDocs();
    }

    async function savePersonalProfile(btn = null) {
      syncPersonalSurveyAnswerToField();
      personalSetBusy(btn, true, "保存中...");
      try {
        const existing = state.personalDefault || {};
        const data = await api("/api/ip-content/personal-default", {
          method: "PUT",
          json: {
            name: existing.name || "个人默认模板",
            keyword_ids: Array.isArray(existing.keyword_ids) ? existing.keyword_ids : [],
            competitor_ids: Array.isArray(existing.competitor_ids) ? existing.competitor_ids : [],
            memory_doc_ids: Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : [],
            memory_docs: Array.isArray(existing.memory_docs) ? existing.memory_docs : [],
            requirements: personalSurveyRequirements(),
            meta: { ...(existing.meta || {}), source: "h5_personal_profile" },
          },
        });
        state.personalDefault = data.item || { requirements: personalSurveyRequirements() };
        personalSetStatus("资料调查已保存。");
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalDefault(options = {}) {
      const name = (($("personalTemplateName") && $("personalTemplateName").value) || "").trim();
      if (!name) throw new Error("请填写模板名称");
      const memoryIds = personalCleanStringIds(state.personalSelectedMemories);
      const selectedDocs = state.personalMemoryDocs
        .filter((doc) => memoryIds.includes(personalDocId(doc)))
        .map((doc) => ({ doc_id: personalDocId(doc), title: doc.title || doc.filename || "", content_text: doc.content_text || doc.content_preview || "" }));
      const payload = {
        name,
        keyword_ids: personalCleanIntIds(state.personalSelectedKeywords),
        competitor_ids: personalCleanIntIds(state.personalSelectedCompetitors),
        memory_doc_ids: memoryIds,
        memory_docs: selectedDocs,
        requirements: personalSurveyRequirements(),
        meta: { source: "h5_personal_settings" },
      };
      const editingId = String(state.personalEditingTemplateId || "").trim();
      const data = await api(editingId ? `/api/ip-content/schedule-templates/${encodeURIComponent(editingId)}` : "/api/ip-content/schedule-templates", {
        method: editingId ? "PATCH" : "POST",
        json: payload,
      });
      if (data.item && data.item.id) {
        state.personalEditingTemplateId = String(data.item.id);
      }
      await refreshPersonalDataPreserveSelection({ templates: true });
      if (!options.silent) toast("已保存");
    }

    async function usePersonalTemplate(templateId, btn = null) {
      const row = (state.personalTemplates || []).find((item) => String(item.id || "") === String(templateId || ""));
      if (!row) throw new Error("模板不存在");
      personalSetBusy(btn, true, "保存中...");
      try {
        const data = await api("/api/ip-content/personal-default", {
          method: "PUT",
          json: {
            name: personalTemplateName(row),
            keyword_ids: Array.isArray(row.keyword_ids) ? row.keyword_ids : [],
            competitor_ids: Array.isArray(row.competitor_ids) ? row.competitor_ids : [],
            memory_doc_ids: Array.isArray(row.memory_doc_ids) ? row.memory_doc_ids : [],
            memory_docs: Array.isArray(row.memory_docs) ? row.memory_docs : [],
            requirements: { ...(((state.personalDefault || {}).requirements && typeof (state.personalDefault || {}).requirements === "object") ? state.personalDefault.requirements : personalSurveyRequirements()), ...((row.requirements && typeof row.requirements === "object") ? row.requirements : {}) },
            meta: { ...(row.meta || {}), source: "h5_personal_current_template", current_template_id: row.id },
          },
        });
        state.personalDefault = data.item || {};
        applyPersonalSurvey(state.personalDefault);
        renderPersonalSettings();
        personalSetStatus("当前使用模板已更新。");
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function addPersonalKeyword() {
      const input = $("personalKeywordInput");
      const displayInput = $("personalKeywordDisplayName");
      const keyword = (input && input.value || "").trim();
      if (!keyword) throw new Error("请填写关键词");
      const data = await api("/api/ip-content/keywords", { method: "POST", json: { keyword, display_name: (displayInput && displayInput.value || "").trim() || keyword, meta: { source: "h5_personal_settings" } } });
      if (input) input.value = "";
      if (displayInput) displayInput.value = "";
      await refreshPersonalDataPreserveSelection({ keywords: true });
    }

    async function addPersonalCompetitor() {
      const platform = ($("personalCompetitorPlatform") && $("personalCompetitorPlatform").value) || "douyin";
      const input = $("personalCompetitorKey");
      const accountKey = (input && input.value || "").trim();
      if (!accountKey) throw new Error("请填写账号标识");
      const data = await api("/api/ip-content/competitors", { method: "POST", json: { platform, account_key: accountKey, display_name: accountKey, meta: { source: "h5_personal_settings" } } });
      if (input) input.value = "";
      await refreshPersonalDataPreserveSelection({ competitors: true });
      if (data.item && data.item.id) await syncPersonalCompetitor(data.item.id);
    }

    async function syncPersonalCompetitor(competitorId, btn = null) {
      if (!competitorId) return;
      personalSetBusy(btn, true, "同步中...");
      try {
        const data = await api(`/api/ip-content/competitors/${encodeURIComponent(competitorId)}/sync`, {
          method: "POST",
          json: { count: 20 },
        });
        const count = Array.isArray(data.items) ? data.items.length : 0;
        personalSetStatus(`同行账号已同步，入库 ${count} 条。`);
        await refreshPersonalDataPreserveSelection({ competitors: true });
      } finally {
        personalSetBusy(btn, false);
      }
    }

    function personalUploadFileKey(file) {
      return [file && file.name || "", file && file.size || 0, file && file.lastModified || 0, file && file.type || ""].join("|");
    }

    function selectedPersonalUploadFiles() {
      return (state.personalUploadFiles || []).filter((file) => file && (file.name || file.size > 0));
    }

    function personalFileChip(file, idx, attr) {
      const size = file && file.size ? ` · ${Math.ceil(file.size / 1024)}KB` : "";
      return `<div class="personal-file-chip"><span>${escapeHtml(file.name || "未命名文件")}${escapeHtml(size)}</span><button type="button" ${attr}="${idx}">移除</button></div>`;
    }

    function renderPersonalSelectedFiles() {
      const box = $("personalSelectedFiles");
      if (!box) return;
      const files = selectedPersonalUploadFiles();
      box.innerHTML = files.length ? files.map((file, idx) => personalFileChip(file, idx, "data-remove-personal-upload")).join("") : "";
    }

    function handlePersonalUploadFilesChange() {
      const input = $("personalMemoryFiles");
      const picked = input && input.files ? Array.from(input.files) : [];
      const seen = {};
      state.personalUploadFiles = selectedPersonalUploadFiles().concat(picked).filter((file) => {
        const key = personalUploadFileKey(file);
        if (!key || seen[key]) return false;
        seen[key] = true;
        return true;
      });
      if (input) input.value = "";
      renderPersonalSelectedFiles();
      renderPersonalMemorySourceSelectors();
    }

    function renderPersonalCustomReference() {
      const box = $("personalCustomReferenceInfo");
      if (!box) return;
      const file = state.personalCustomReferenceFile;
      box.innerHTML = file ? personalFileChip(file, 0, "data-remove-personal-reference") : "";
    }

    function handlePersonalCustomReferenceChange() {
      const input = $("personalCustomReferenceFile");
      const file = input && input.files && input.files[0] ? input.files[0] : null;
      state.personalCustomReferenceFile = file && (file.name || file.size > 0) ? file : null;
      if (input) input.value = "";
      renderPersonalCustomReference();
    }

    function openPersonalUploadModal() {
      $("personalUploadModal")?.classList.remove("hidden");
    }

    function closePersonalUploadModal() {
      $("personalUploadModal")?.classList.add("hidden");
    }

    function openPersonalMemoryGenerateModal() {
      renderPersonalMemorySourceSelectors();
      renderPersonalGeneratedDocs();
      $("personalMemoryGenerateModal")?.classList.remove("hidden");
    }

    function closePersonalMemoryGenerateModal() {
      $("personalMemoryGenerateModal")?.classList.add("hidden");
    }

    function selectedPersonalDocTypes() {
      return Array.from(document.querySelectorAll("[data-personal-doc-type]:checked")).map((el) => String(el.value || "").trim()).filter(Boolean);
    }

    function formatPersonalGeneratedDocs(docs, order) {
      docs = docs || {};
      const keys = (order && order.length ? order : Object.keys(docs)).filter((key) => docs[key]);
      return keys.map((key) => `# ${personalDocTypeLabel(key)}\n\n${String(docs[key] || "").trim()}`).filter(Boolean).join("\n\n---\n\n").trim();
    }

    function personalGeneratedDocsFromUi() {
      const docs = {};
      const order = [];
      document.querySelectorAll("[data-personal-generated-text]").forEach((textarea) => {
        const key = textarea.dataset.personalGeneratedText || "";
        const keep = document.querySelector(`[data-personal-save-doc="${cssEscape(key)}"]`);
        const text = String(textarea.value || "").trim();
        if (key && text && (!keep || keep.checked)) {
          docs[key] = text;
          order.push(key);
        }
      });
      return { documents: docs, order };
    }

    function renderPersonalGeneratedDocs() {
      const box = $("personalGeneratedDocList");
      if (!box) return;
      const docs = state.personalGeneratedDocuments || {};
      const order = (state.personalGeneratedDocOrder && state.personalGeneratedDocOrder.length ? state.personalGeneratedDocOrder : Object.keys(docs)).filter((key) => docs[key]);
      if (!order.length) {
        box.innerHTML = `<div class="personal-empty">AI 理解后在这里预览。</div>`;
        if ($("personalMemoryReviewText")) $("personalMemoryReviewText").value = "";
        return;
      }
      box.innerHTML = order.map((key) => `<article class="personal-generated-doc">
        <div class="personal-generated-head"><strong>${escapeHtml(personalDocTypeLabel(key))}</strong><label><input type="checkbox" data-personal-save-doc="${escapeHtml(key)}" checked>保存</label></div>
        <textarea data-personal-generated-text="${escapeHtml(key)}" rows="8">${escapeHtml(docs[key])}</textarea>
      </article>`).join("");
      box.querySelectorAll("[data-personal-generated-text]").forEach((textarea) => {
        textarea.addEventListener("input", () => {
          const key = textarea.dataset.personalGeneratedText || "";
          if (key) state.personalGeneratedDocuments[key] = textarea.value || "";
          if ($("personalMemoryReviewText")) $("personalMemoryReviewText").value = formatPersonalGeneratedDocs(state.personalGeneratedDocuments, state.personalGeneratedDocOrder);
        });
      });
      if ($("personalMemoryReviewText")) $("personalMemoryReviewText").value = formatPersonalGeneratedDocs(docs, order);
    }

    function personalMemoryInputText() {
      const parts = [];
      const context = personalMemoryContextText({
        includeProfile: state.personalMemoryUseProfile !== false,
        keywordRows: selectedPersonalMemoryKeywordRows(),
        competitorRows: selectedPersonalMemoryCompetitorRows(),
        sourceDocs: selectedPersonalMemorySourceDocs(),
      });
      if (context) parts.push(context);
      const files = selectedPersonalMemoryUploadFiles();
      if (files.length) parts.push(`当前选择文件：\n${files.map((file) => `- ${file.name || "upload"}`).join("\n")}`);
      return parts.join("\n\n").trim();
    }

    function personalMemoryContextText(options = {}) {
      const includeProfile = options.includeProfile !== false;
      const keywordRows = Array.isArray(options.keywordRows) ? options.keywordRows : selectedPersonalMemoryKeywordRows();
      const competitorRows = Array.isArray(options.competitorRows) ? options.competitorRows : selectedPersonalMemoryCompetitorRows();
      const sourceDocs = Array.isArray(options.sourceDocs) ? options.sourceDocs : selectedPersonalMemorySourceDocs();
      const req = personalSurveyRequirements();
      const profile = req.basic_profile || {};
      const business = req.business_description || {};
      const profileLines = [
        ["名字", profile.name],
        ["出生年代", profile.birth_era],
        ["现居城市", profile.current_city],
        ["籍贯", profile.hometown],
        ["职业/身份", profile.role],
        ["主要分享", profile.share_topic],
        ["视频风格", profile.video_style],
        ["看完后动作", profile.after_view_action],
      ].filter((item) => String(item[1] || "").trim()).map((item) => `${item[0]}：${item[1]}`);
      const businessLines = [
        ["产品/业务", business.product],
        ["目标客户", business.target_customer],
        ["优势", business.advantages],
      ].filter((item) => String(item[1] || "").trim()).map((item) => `${item[0]}：${item[1]}`);
      const keywordLines = keywordRows.map((row) => row.display_name || row.keyword).filter(Boolean);
      const competitorLines = competitorRows.map((row) => `${row.platform || ""} ${row.display_name || row.account_key || ""}`.trim()).filter(Boolean);
      const docLines = sourceDocs.map((doc) => {
        const title = personalMemoryTitle(doc);
        const text = String(doc.content_text || doc.content || doc.text || doc.content_preview || "").trim();
        return text ? `【${title}】\n${text}` : "";
      }).filter(Boolean);
      const sections = [];
      if (includeProfile && profileLines.length) sections.push(`资料调查：\n${profileLines.join("\n")}`);
      if (includeProfile && businessLines.length) sections.push(`业务描述：\n${businessLines.join("\n")}`);
      if (keywordLines.length) sections.push(`关键词：\n${keywordLines.join("\n")}`);
      if (competitorLines.length) sections.push(`同行账号：\n${competitorLines.join("\n")}`);
      if (docLines.length) sections.push(`上传资料：\n${docLines.join("\n\n")}`);
      return sections.join("\n\n").trim();
    }

    function personalMetricText(metrics) {
      if (!metrics || typeof metrics !== "object") return "";
      return [
        ["点赞", metrics.like_count || metrics.digg_count || metrics.likes],
        ["评论", metrics.comment_count || metrics.comments],
        ["分享", metrics.share_count || metrics.shares],
        ["收藏", metrics.collect_count || metrics.favorite_count || metrics.favorites],
        ["播放", metrics.play_count || metrics.view_count || metrics.views],
      ].filter((item) => item[1] !== undefined && item[1] !== null && String(item[1]) !== "").map((item) => `${item[0]}${item[1]}`).join("，");
    }

    async function personalCompetitorSourceText(selectedIds = null) {
      const selected = Array.isArray(selectedIds) ? selectedIds.map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0) : personalCleanIntIds(state.personalMemorySourceCompetitors);
      if (!selected.length) return "";
      const wanted = new Set(selected.map((id) => String(id)));
      const data = await api("/api/ip-content/source-items?source_type=competitor&limit=80").catch(() => ({ items: [] }));
      const rows = (Array.isArray(data.items) ? data.items : []).filter((row) => {
        const meta = row && row.source_meta && typeof row.source_meta === "object" ? row.source_meta : {};
        const cid = String(meta.competitor_account_id || "");
        return wanted.has(cid);
      }).slice(0, 40);
      if (!rows.length) return "";
      const text = rows.map((row, idx) => {
        const metrics = personalMetricText(row.metrics || {});
        const parts = [
          `${idx + 1}. ${row.author_name || ""}${row.title ? `《${row.title}》` : ""}`.trim(),
          row.description ? `内容：${row.description}` : "",
          row.publish_time ? `时间：${row.publish_time}` : "",
          metrics ? `数据：${metrics}` : "",
          row.public_url ? `链接：${row.public_url}` : "",
        ].filter(Boolean);
        return parts.join("\n");
      }).join("\n\n");
      return `同行同步数据：\n${text}`;
    }

    function personalUniqueIds(ids) {
      const seen = {};
      return (ids || []).map((id) => String(id || "").trim()).filter((id) => {
        if (!id || seen[id]) return false;
        seen[id] = true;
        return true;
      });
    }

    function removePersonalDefaultId(kind, id) {
      const item = state.personalDefault || {};
      const key = kind === "keyword" ? "keyword_ids" : (kind === "competitor" ? "competitor_ids" : "memory_doc_ids");
      const strId = String(id || "");
      item[key] = (Array.isArray(item[key]) ? item[key] : []).filter((value) => String(value || "") !== strId);
      if (kind === "memory") {
        item.memory_docs = (Array.isArray(item.memory_docs) ? item.memory_docs : []).filter((doc) => String((doc && (doc.doc_id || doc.id)) || "") !== strId);
      }
      state.personalDefault = item;
    }

    async function savePersonalDefaultSilently() {
      const existing = state.personalDefault || {};
      const keywordIds = personalUniqueIds([...(Array.isArray(existing.keyword_ids) ? existing.keyword_ids : []), ...personalCleanIntIds(state.personalSelectedKeywords)]).map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0);
      const competitorIds = personalUniqueIds([...(Array.isArray(existing.competitor_ids) ? existing.competitor_ids : []), ...personalCleanIntIds(state.personalSelectedCompetitors)]).map((id) => Number(id)).filter((id) => Number.isFinite(id) && id > 0);
      const memoryIds = personalUniqueIds([...(Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : []), ...personalCleanStringIds(state.personalSelectedMemories)]);
      const selectedDocs = state.personalMemoryDocs
        .filter((doc) => memoryIds.includes(personalDocId(doc)))
        .map((doc) => ({ doc_id: personalDocId(doc), title: doc.title || doc.filename || "", content_text: doc.content_text || doc.content_preview || "" }));
      const data = await api("/api/ip-content/personal-default", {
        method: "PUT",
        json: {
          name: existing.name || "个人默认模板",
          keyword_ids: keywordIds,
          competitor_ids: competitorIds,
          memory_doc_ids: memoryIds,
          memory_docs: selectedDocs,
          requirements: { ...((existing.requirements && typeof existing.requirements === "object") ? existing.requirements : {}), ...personalSurveyRequirements() },
          meta: { ...((existing.meta && typeof existing.meta === "object") ? existing.meta : {}), source: "h5_personal_settings" },
        },
      });
      state.personalDefault = data.item || existing;
      personalSetStatus("");
      renderPersonalSettings();
    }

    async function generatePersonalMemoryDocs(btn) {
      const iid = currentInstallationId();
      if (!iid) throw new Error("请先选择在线设备");
      syncPersonalSurveyAnswerToField();
      ensurePersonalMemorySourceSelections();
      const files = selectedPersonalMemoryUploadFiles();
      const keywordRows = selectedPersonalMemoryKeywordRows();
      const competitorRows = selectedPersonalMemoryCompetitorRows();
      const sourceDocs = selectedPersonalMemorySourceDocs();
      const docTypes = selectedPersonalDocTypes();
      const reference = state.personalCustomReferenceFile;
      const contextText = personalMemoryContextText({
        includeProfile: state.personalMemoryUseProfile !== false,
        keywordRows,
        competitorRows,
        sourceDocs,
      });
      const competitorText = await personalCompetitorSourceText(competitorRows.map((row) => row.id));
      if (!files.length && !contextText && !competitorText) throw new Error("请选择要生成的资料来源。");
      if (!docTypes.length && !reference) throw new Error("请选择生成类型，或上传自定义参考文档。");
      const fd = new FormData();
      files.forEach((file) => fd.append("files", file, file.name || "upload"));
      fd.append("urls", "");
      fd.append("direct_intro", [contextText, competitorText].filter(Boolean).join("\n\n"));
      fd.append("direct_faq", "");
      fd.append("direct_scripts", "");
      fd.append("doc_type", docTypes[0] || "");
      fd.append("doc_types", JSON.stringify(docTypes));
      if (reference) fd.append("custom_reference_file", reference, reference.name || "reference");
      fd.append("reference_doc_ids", "");
      personalSetBusy(btn, true, "理解中...");
      personalSetStatus("正在理解资料...");
      try {
        const resp = await fetch(apiUrl("/api/personal-settings/memory-documents/generate"), { method: "POST", headers: authHeaders({ "X-Installation-Id": iid }), body: fd });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.ok === false) throw new Error(data.detail || data.message || "AI 理解失败");
        state.personalGeneratedDocuments = data.documents || {};
        state.personalGeneratedDocOrder = Array.isArray(data.doc_types) && data.doc_types.length ? data.doc_types : docTypes;
        const title = $("personalMemoryTitle");
        if (title && (($("personalSaveMode") && $("personalSaveMode").value) || "new") === "new") title.value = recommendPersonalMemoryTitle(state.personalGeneratedDocOrder, !!reference);
        setPersonalSettingsTab("memory");
        renderPersonalGeneratedDocs();
        personalSetStatus("AI 理解完成，审核后存入记忆。");
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalGeneratedDocuments(btn, title, documents) {
      const iid = currentInstallationId();
      personalSetBusy(btn, true, "保存中...");
      try {
        const data = await api("/api/personal-settings/memory-documents/save", {
          method: "POST",
          headers: { "X-Installation-Id": iid },
          json: { title, notes: "IP人设定位 AI 理解", documents: documents || {} },
        });
        const docs = Array.isArray(data.documents) ? data.documents : [];
        docs.forEach((doc) => { const id = personalDocId(doc); if (id) state.personalSelectedMemories[id] = true; });
        await refreshPersonalDataPreserveSelection({ memories: true });
        await savePersonalDefaultSilently();
        personalSetStatus("已存入记忆。");
        closePersonalMemoryGenerateModal();
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalMemoryContent(btn, title, content, notes, mode, targetDocId) {
      const iid = currentInstallationId();
      personalSetBusy(btn, true, "保存中...");
      try {
        const data = await api("/api/personal-settings/memory-documents/save-raw", {
          method: "POST",
          headers: { "X-Installation-Id": iid },
          json: { title, notes, content, mode: mode || "new", target_doc_id: targetDocId || "" },
        });
        const docs = Array.isArray(data.documents) ? data.documents : (data.document ? [data.document] : []);
        docs.forEach((doc) => { const id = personalDocId(doc); if (id) state.personalSelectedMemories[id] = true; });
        await refreshPersonalDataPreserveSelection({ memories: true });
        await savePersonalDefaultSilently();
        personalSetStatus("已存入记忆。");
        closePersonalMemoryGenerateModal();
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalUploadedMemory(btn, formData) {
      const iid = currentInstallationId();
      personalSetBusy(btn, true, "保存中...");
      try {
        const resp = await fetch(apiUrl("/api/personal-settings/memory-documents/save-upload"), { method: "POST", headers: authHeaders({ "X-Installation-Id": iid }), body: formData });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.ok === false) throw new Error(data.detail || data.message || "保存失败");
        const docs = Array.isArray(data.documents) ? data.documents : (data.document ? [data.document] : []);
        docs.forEach((doc) => { const id = personalDocId(doc); if (id) state.personalSelectedMemories[id] = true; });
        state.personalGeneratedDocuments = {};
        state.personalGeneratedDocOrder = [];
        renderPersonalGeneratedDocs();
        await refreshPersonalDataPreserveSelection({ memories: true });
        await savePersonalDefaultSilently();
        personalSetStatus("已存入记忆。");
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalRawMemory(btn) {
      const files = selectedPersonalUploadFiles();
      if (!files.length) throw new Error("请先上传文件。");
      personalSetBusy(btn, true, "保存中...");
      try {
        for (const file of files) {
          const fd = new FormData();
          fd.append("files", file, file.name || "upload");
          fd.append("title", file.name || "上传资料");
          fd.append("notes", "IP人设定位上传资料");
          fd.append("raw_text", "");
          fd.append("urls", "");
          fd.append("mode", "new");
          fd.append("target_doc_id", "");
          await savePersonalUploadedMemory(null, fd);
        }
        state.personalUploadFiles = [];
        renderPersonalSelectedFiles();
        renderPersonalMemorySourceSelectors();
        closePersonalUploadModal();
      } finally {
        personalSetBusy(btn, false);
      }
    }

    async function savePersonalMemory(btn) {
      const generated = personalGeneratedDocsFromUi();
      const generatedContent = formatPersonalGeneratedDocs(generated.documents, generated.order);
      const hasPreview = document.querySelectorAll("[data-personal-generated-text]").length > 0;
      if (hasPreview && !Object.keys(generated.documents || {}).length) throw new Error("请至少勾选一个要保存的 AI 理解结果。");
      let content = generatedContent || (!hasPreview ? (($("personalMemoryReviewText") && $("personalMemoryReviewText").value) || "").trim() : "");
      const mode = (($("personalSaveMode") && $("personalSaveMode").value) || "new").trim();
      const title = mode === "new" ? (($("personalMemoryTitle") && $("personalMemoryTitle").value) || "").trim() : "";
      const targetDocId = (($("personalTargetMemorySelect") && $("personalTargetMemorySelect").value) || "").trim();
      if (!content) {
        content = personalMemoryInputText();
        if ($("personalMemoryReviewText")) $("personalMemoryReviewText").value = content;
      }
      if (!content) throw new Error("没有可保存的记忆内容。");
      if (mode === "new" && !title) throw new Error("新建文档需要填写文档名字。");
      if (mode === "overwrite" && !targetDocId) throw new Error("覆盖已有文档需要先选择一个文档。");
      if (mode === "new" && Object.keys(generated.documents || {}).length) {
        await savePersonalGeneratedDocuments(btn, title, generated.documents);
        return;
      }
      await savePersonalMemoryContent(btn, title, content, "IP人设定位保存", mode, targetDocId);
    }

    async function previewPersonalMemory(docId) {
      const iid = currentInstallationId();
      const box = $("personalMemoryPreview");
      if (box) box.textContent = "正在读取...";
      const data = await api(`/api/personal-settings/memory-documents/${encodeURIComponent(docId)}/preview`, { headers: { "X-Installation-Id": iid } });
      if (box) box.textContent = data.content_text || "没有内容。";
    }

    async function deletePersonalMemory(docId) {
      const iid = currentInstallationId();
      await api(`/api/personal-settings/memory-documents/${encodeURIComponent(docId)}`, {
        method: "DELETE",
        headers: { "X-Installation-Id": iid },
      });
      delete state.personalSelectedMemories[String(docId)];
      removePersonalDefaultId("memory", docId);
      await refreshPersonalDataPreserveSelection({ memories: true });
      await savePersonalDefaultSilently();
      personalSetStatus("记忆文件已删除。");
    }

    function packageById(packageId) {
      const id = String(packageId || "").trim();
      return (state.taskSkillPackages || []).find((pkg) => String(pkg && pkg.id || "").trim() === id) || null;
    }

    function packageVisible(packageId) {
      const pkg = packageById(packageId);
      return !!pkg && (pkg.status === "installed" || pkg.default_installed || pkg.unlocked);
    }

    function isScheduledTaskAbility(capabilityId) {
      return SCHEDULED_TASK_CAPABILITY_IDS.includes(String(capabilityId || "").trim());
    }

    function taskAbilityVisible(capabilityId) {
      const meta = TASK_CAPABILITIES[capabilityId];
      if (!meta) return false;
      if (!state.taskSkillsLoaded) {
        return isScheduledTaskAbility(capabilityId);
      }
      const allowedCaps = new Set((state.taskAllowedCapabilityIds || []).map((id) => String(id || "").trim()).filter(Boolean));
      if (allowedCaps.has(capabilityId)) return true;
      if (meta.featureKey) return !!(state.user && state.user.features && state.user.features[meta.featureKey]);
      return packageVisible(meta.packageId);
    }

    function visibleTaskAbilities() {
      const rows = SCHEDULED_TASK_CAPABILITY_IDS.filter(taskAbilityVisible);
      if (rows.length) return rows;
      return state.taskSkillsLoaded ? [] : SCHEDULED_TASK_CAPABILITY_IDS.slice();
    }

    function renderTaskAbilityBoard() {
      const host = $("taskAbilityBoard");
      if (!host) return;
      const visible = visibleTaskAbilities();
      if (!visible.includes(state.taskAbility)) {
        state.taskAbility = visible[0] || "goal.video.pipeline";
      }
      if (!visible.length) {
        host.innerHTML = `<div class="task-skill-empty">当前账号暂无可定时下发技能，请先到技能商店开通权限。</div>`;
        return;
      }
      host.innerHTML = `<div class="task-skill-dept">
        <div class="task-skill-dept-title">可定时下发</div>
        <div class="task-skill-grid">${visible.map((ability) => {
          const meta = TASK_CAPABILITIES[ability] || {};
          const active = ability === state.taskAbility ? " active" : "";
          return `<button type="button" class="task-skill-card${active}" data-task-ability="${escapeHtml(ability)}">
            <strong>${escapeHtml(meta.label || ability)}</strong>
            <span>${escapeHtml(meta.description || "")}</span>
          </button>`;
        }).join("")}</div>
      </div>`;
    }

    function workQuickItemVisible(item) {
      if (!item) return false;
      if (item.always) return true;
      if (item.featureKey) return !!(state.user && state.user.features && state.user.features[item.featureKey]);
      if (!state.taskSkillsLoaded) return false;
      const capabilityId = String(item.capabilityId || "").trim();
      if (capabilityId) {
        const allowedCaps = new Set((state.taskAllowedCapabilityIds || []).map((id) => String(id || "").trim()).filter(Boolean));
        if (allowedCaps.has(capabilityId)) return true;
      }
      if (item.packageId && packageVisible(item.packageId)) return true;
      return false;
    }

    function workQuickCardHtml(item) {
      const classes = `quick-card${item.highlight ? " quick-card-leads" : ""}${item.disabled ? " disabled" : ""}`;
      const attrs = [
        `class="${classes}"`,
        `type="button"`,
        `data-work-quick="${escapeHtml(item.key || item.label || "")}"`,
      ];
      if (item.disabled) {
        attrs.push("disabled");
        attrs.push(`aria-disabled="true"`);
      }
      if (item.homeTarget) attrs.push(`data-home-target="${escapeHtml(item.homeTarget)}"`);
      if (item.prompt) attrs.push(`data-prompt="${escapeHtml(item.prompt)}"`);
      if (item.imageVideo) attrs.push(`data-image-video="1"`);
      return `<button ${attrs.join(" ")}>
        <div class="quick-mark">${escapeHtml(item.mark || (item.label || "?").slice(0, 1))}</div>
        <strong>${escapeHtml(item.label || item.key || "工作")}</strong>
      </button>`;
    }

    function renderHomeQuickGrid() {
      const host = $("homeQuickGrid");
      if (!host) return;
      if (state.token && state.taskSkillsLoading && !state.taskSkillsLoaded) {
        host.innerHTML = `<div class="quick-empty">正在读取岗位工作入口...</div>`;
        return;
      }
      if (state.taskSkillsError && !state.taskSkillsLoaded) {
        host.innerHTML = `<div class="quick-empty">${escapeHtml(state.taskSkillsError)}</div>`;
        return;
      }
      const visible = WORK_QUICK_ITEMS.filter(workQuickItemVisible);
      const html = TASK_DEPARTMENTS.map((department) => {
        const rows = visible.filter((item) => (item.department || "运营部") === department);
        if (!rows.length) return "";
        return `<section class="quick-section" aria-label="${escapeHtml(department)}">
          <div class="quick-section-head"><strong>${escapeHtml(department)}</strong></div>
          <div class="quick-grid">${rows.map(workQuickCardHtml).join("")}</div>
        </section>`;
      }).join("");
      host.innerHTML = html || `<div class="quick-empty">当前账号暂无可安排工作入口，请先到技能商店开通权限。</div>`;
    }

    function workQuickItemByKey(key) {
      const wanted = String(key || "").trim();
      return WORK_QUICK_ITEMS.find((item) => String(item.key || item.label || "").trim() === wanted) || null;
    }

    function workInputHtml(id, type, value = "", attrs = "") {
      return `<input id="${escapeHtml(id)}" type="${escapeHtml(type || "text")}" value="${escapeHtml(value)}" ${attrs || ""}>`;
    }

    function workCheckboxHtml(id, label, checked = false) {
      return `<label class="task-checkbox" style="min-height:42px;color:#46516a;"><input id="${escapeHtml(id)}" type="checkbox" ${checked ? "checked" : ""}><span>${escapeHtml(label)}</span></label>`;
    }

    function workNumber(value, fallback, min, max) {
      const parsed = parseInt(value, 10);
      const safe = Number.isNaN(parsed) ? fallback : parsed;
      return Math.max(min, Math.min(max, safe));
    }

    function workSplitList(value) {
      return String(value || "")
        .split(/[,\s，、；;]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function workMaterialPayload(value) {
      const raw = String(value || "").trim();
      if (!raw) throw new Error("请选择素材");
      if (/^https?:\/\//i.test(raw)) return { url: raw };
      return { asset_id: raw };
    }

    function assetPickerCacheKey(mediaType) {
      return String(mediaType || "").trim() || "all";
    }

    function normalizeUserUploadAsset(row) {
      const item = row && typeof row === "object" ? row : {};
      return {
        asset_id: String(item.asset_id || "").trim(),
        filename: String(item.filename || item.name || "已上传素材").trim(),
        media_type: String(item.media_type || "").trim() || "image",
        source_url: String(item.source_url || item.url || item.open_url || item.preview_url || "").trim(),
        preview_url: String(item.preview_url || item.local_preview_url || item.open_url || item.source_url || item.url || "").trim(),
        asset_origin: item.asset_origin || "user_upload",
        created_at: item.created_at || "",
      };
    }

    function userUploadAssetRows(mediaType) {
      return state.userUploadAssetCache[assetPickerCacheKey(mediaType)] || [];
    }

    function assetPickerOutputValue(row, output) {
      const item = normalizeUserUploadAsset(row);
      if (output === "url") return item.source_url || item.preview_url || "";
      return item.asset_id || item.source_url || item.preview_url || "";
    }

    function assetPickerPreviewUrl(row) {
      const item = normalizeUserUploadAsset(row);
      if (item.preview_url) return item.preview_url;
      if (item.source_url) return item.source_url;
      return item.asset_id ? apiUrl(`/api/assets/${encodeURIComponent(item.asset_id)}/content`) : "";
    }

    function assetPickerControlHtml(id, opts = {}) {
      const mediaType = String(opts.mediaType || "image").trim();
      const accept = String(opts.accept || (mediaType === "video" ? "video/*" : "image/*"));
      const output = String(opts.output || "asset_id");
      const uploadText = String(opts.uploadText || "上传");
      const selectText = String(opts.selectText || "选择已上传素材");
      return `<div class="asset-picker" data-asset-picker="${escapeHtml(id)}" data-asset-media-type="${escapeHtml(mediaType)}" data-asset-output="${escapeHtml(output)}">
        <input id="${escapeHtml(id)}" type="hidden">
        <input id="${escapeHtml(id)}File" type="file" accept="${escapeHtml(accept)}" data-asset-upload-input="${escapeHtml(id)}" hidden>
        <div class="asset-picker-row">
          <button class="ghost" type="button" data-asset-upload-trigger="${escapeHtml(id)}">${escapeHtml(uploadText)}</button>
          <select id="${escapeHtml(id)}Select" data-asset-select="${escapeHtml(id)}"><option value="">${escapeHtml(selectText)}</option></select>
        </div>
        <div class="asset-picker-preview" id="${escapeHtml(id)}Preview"></div>
      </div>`;
    }

    function renderAssetPickerControl(id) {
      const box = document.querySelector(`[data-asset-picker="${cssEscape(id)}"]`);
      const hidden = $(id);
      const select = $(`${id}Select`);
      const preview = $(`${id}Preview`);
      if (!box || !hidden || !select || !preview) return;
      const mediaType = box.dataset.assetMediaType || "";
      const output = box.dataset.assetOutput || "asset_id";
      const rows = userUploadAssetRows(mediaType);
      const loading = !!state.userUploadAssetLoading[assetPickerCacheKey(mediaType)];
      const current = String(hidden.value || "").trim();
      const matched = rows.find((row) => row.asset_id === current || assetPickerOutputValue(row, output) === current) || null;
      select.innerHTML = `<option value="">${loading ? "加载中..." : "选择已上传素材"}</option>` + rows.map((row) => {
        const item = normalizeUserUploadAsset(row);
        return `<option value="${escapeHtml(item.asset_id)}">${escapeHtml(item.filename || item.asset_id)}</option>`;
      }).join("");
      select.value = matched ? matched.asset_id : "";
      if (matched) {
        const item = normalizeUserUploadAsset(matched);
        const nextValue = assetPickerOutputValue(item, output);
        if (nextValue && nextValue !== current) hidden.value = nextValue;
        const src = assetPickerPreviewUrl(item);
        const media = item.media_type === "video"
          ? `<video src="${escapeHtml(src)}" muted playsinline preload="metadata"></video>`
          : (item.media_type === "image" ? `<img src="${escapeHtml(src)}" alt="">` : `<div class="asset-picker-file">${escapeHtml((item.filename || "FILE").split(".").pop() || "FILE")}</div>`);
        preview.innerHTML = `<div class="asset-picker-thumb">${media}</div><span>${escapeHtml(item.filename || item.asset_id)}</span>`;
      } else if (current) {
        preview.innerHTML = `<span>已选择素材</span>`;
      } else {
        preview.innerHTML = "";
      }
    }

    function renderAssetPickerControls(mediaType = "") {
      document.querySelectorAll("[data-asset-picker]").forEach((box) => {
        if (!mediaType || assetPickerCacheKey(box.dataset.assetMediaType || "") === assetPickerCacheKey(mediaType)) {
          renderAssetPickerControl(box.dataset.assetPicker || "");
        }
      });
    }

    async function loadUserUploadAssets(mediaType = "", force = false) {
      const key = assetPickerCacheKey(mediaType);
      if (!force && Object.prototype.hasOwnProperty.call(state.userUploadAssetCache, key)) return state.userUploadAssetCache[key];
      if (state.userUploadAssetLoading[key]) return state.userUploadAssetCache[key] || [];
      state.userUploadAssetLoading[key] = true;
      renderAssetPickerControls(mediaType);
      try {
        const params = new URLSearchParams({ origin: "user_upload", limit: "120" });
        if (mediaType) params.set("media_type", mediaType);
        const data = await api(`/api/assets?${params.toString()}`);
        const rows = (Array.isArray(data.assets) ? data.assets : [])
          .map(normalizeUserUploadAsset)
          .filter((row) => row.asset_id && row.asset_origin === "user_upload");
        state.userUploadAssetCache[key] = rows;
        return rows;
      } finally {
        state.userUploadAssetLoading[key] = false;
        renderAssetPickerControls(mediaType);
      }
    }

    function addUserUploadAssetToCache(row) {
      const item = normalizeUserUploadAsset(row);
      if (!item.asset_id) return item;
      [assetPickerCacheKey(item.media_type), "all"].forEach((key) => {
        const rows = state.userUploadAssetCache[key] || [];
        state.userUploadAssetCache[key] = [item].concat(rows.filter((old) => old.asset_id !== item.asset_id));
      });
      return item;
    }

    function initAssetPickerControls(root = document) {
      const host = root && root.querySelectorAll ? root : document;
      host.querySelectorAll("[data-asset-picker]").forEach((box) => {
        const mediaType = box.dataset.assetMediaType || "";
        renderAssetPickerControl(box.dataset.assetPicker || "");
        loadUserUploadAssets(mediaType).catch((err) => toast(err.message || "素材库加载失败"));
      });
    }

    async function uploadUserAssetForPicker(id, file) {
      if (!file) return;
      const box = document.querySelector(`[data-asset-picker="${cssEscape(id)}"]`);
      const hidden = $(id);
      const preview = $(`${id}Preview`);
      if (!box || !hidden) return;
      if (preview) preview.innerHTML = "<span>上传中...</span>";
      const fd = new FormData();
      fd.append("file", file, file.name || "upload");
      const resp = await fetch(apiUrl("/api/assets/upload"), { method: "POST", headers: authHeaders(), body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || data.message || `上传失败：HTTP ${resp.status}`);
      const item = addUserUploadAssetToCache({ ...data, asset_origin: "user_upload" });
      const output = box.dataset.assetOutput || "asset_id";
      hidden.value = assetPickerOutputValue(item, output);
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
      renderAssetPickerControls(item.media_type);
      renderAssetPickerControls("");
      toast("已上传，之后可直接选择");
    }

    function renderWorkHiflyOptions() {
      const avatarSelects = [$("workAvatar"), $("workflowParamAvatar")].filter(Boolean);
      const voiceSelects = [$("workVoice"), $("workflowParamVoice")].filter(Boolean);
      avatarSelects.forEach((avatarSel) => {
        avatarSel.innerHTML = state.avatarRows.length
          ? state.avatarRows.map((row) => `<option value="${escapeHtml(row.avatar)}">${escapeHtml(row.title)}</option>`).join("")
          : `<option value="">暂无可用数字人</option>`;
      });
      voiceSelects.forEach((voiceSel) => {
        voiceSel.innerHTML = state.voiceRows.length
          ? state.voiceRows.map((row) => `<option value="${escapeHtml(row.voice)}">${escapeHtml(row.title)}</option>`).join("")
          : `<option value="">暂无可用声音</option>`;
      });
    }

    function workDispatchFieldsHtml(item) {
      const key = String(item && item.key || "");
      if (key === "image_composer_studio") {
        return taskFieldHtml("任务标题", workInputHtml("workImageTitle", "text", "创作图片"))
          + taskFieldHtml("图片需求", taskTextareaHtml("workImagePrompt", "例如：一张适合小红书封面的精致产品场景图，暖色自然光，突出卖点"), true);
      }
      if (key === "comfly.seedance.tvc.pipeline") {
        return taskFieldHtml("参考图片", assetPickerControlHtml("workSeedanceAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }), true)
          + taskFieldHtml("视频需求", taskTextareaHtml("workSeedanceText", "例如：围绕护肤品做 3 个高级感分镜，突出补水和通透肤感"), true)
          + taskFieldHtml("视频时长", taskSelectHtml("workSeedanceDuration", [10, 20, 30, 40, 50, 60].map((n) => optionHtml(String(n), `${n} 秒`)).join("")))
          + taskFieldHtml("画幅", taskSelectHtml("workSeedanceAspect", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏") + optionHtml("1:1", "1:1 方图")));
      }
      if (key === "comfly.daihuo.pipeline") {
        return taskFieldHtml("参考图片", assetPickerControlHtml("workComflyAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }), true)
          + taskFieldHtml("视频需求", taskTextareaHtml("workComflyText", "例如：生成一个突出产品卖点和使用场景的爆款TVC"), true)
          + taskFieldHtml("分镜数量", workInputHtml("workComflyStoryboardCount", "number", "5", 'min="1" max="8"'))
          + taskFieldHtml("自动入库", workCheckboxHtml("workComflyAutoSave", "完成后保存到素材库", true));
      }
      if (key === "hifly.video.create_by_tts") {
        return taskFieldHtml("数字人", taskSelectHtml("workAvatar", optionHtml("", "加载中...")))
          + taskFieldHtml("声音", taskSelectHtml("workVoice", optionHtml("", "加载中...")))
          + taskFieldHtml("任务标题", workInputHtml("workHiflyTitle", "text", "数字人口播"))
          + taskFieldHtml("口播文案", taskTextareaHtml("workHiflyScript", "填写要让数字人口播的完整文案"), true);
      }
      if (key === "douyin_leads") {
        return taskFieldHtml("采集关键词", taskTextareaHtml("workDouyinKeyword", "例如：深圳装修、口腔种植、母婴门店"), true)
          + taskFieldHtml("地区", workInputHtml("workDouyinRegions", "text", "全国", 'placeholder="全国，或用逗号分隔多个城市"'))
          + taskFieldHtml("搜索数量", workInputHtml("workDouyinMaxResults", "number", "50", 'min="10" max="100"'))
          + taskFieldHtml("搜索方式", taskSelectHtml("workDouyinMode", optionHtml("script", "浏览器脚本") + optionHtml("api", "接口模式")));
      }
      if (key === "local_bestseller") {
        return taskFieldHtml("生成方式", taskSelectHtml("workLocalMode", optionHtml("plan", "先生成 30 天内容方案") + optionHtml("scene_batch", "直接批量生成场景图")))
          + taskFieldHtml("天数", workInputHtml("workLocalDays", "number", "30", 'min="1" max="30"'))
          + taskFieldHtml("姓名", workInputHtml("workLocalName", "text", "", 'placeholder="真实姓名，可选"'))
          + taskFieldHtml("短视频昵称", workInputHtml("workLocalNickname", "text", "", 'placeholder="不填则使用姓名或“我”"'))
          + taskFieldHtml("性别", taskSelectHtml("workLocalGender", optionHtml("female", "女") + optionHtml("male", "男")))
          + taskFieldHtml("人设身份", workInputHtml("workLocalIdentity", "text", "女老板"))
          + taskFieldHtml("行业/赛道", workInputHtml("workLocalIndustry", "text", "大健康"))
          + taskFieldHtml("城市", workInputHtml("workLocalCity", "text", "深圳"))
          + taskFieldHtml("省份", workInputHtml("workLocalProvince", "text", "广东"))
          + taskFieldHtml("人物照片", assetPickerControlHtml("workLocalPhoto", { mediaType: "image", output: "url", uploadText: "从相册上传" }), true);
      }
      if (key === "viral_video_remix") {
        return taskFieldHtml("参考视频", assetPickerControlHtml("workViralVideoUrl", { mediaType: "video", output: "url", accept: "video/*", uploadText: "上传视频" }), true)
          + taskFieldHtml("人物参考图", assetPickerControlHtml("workViralCharacterUrl", { mediaType: "image", output: "url", uploadText: "上传人物图" }))
          + taskFieldHtml("产品参考图", assetPickerControlHtml("workViralProductUrl", { mediaType: "image", output: "url", uploadText: "上传产品图" }))
          + taskFieldHtml("复刻要求", taskTextareaHtml("workViralPrompt", "例如：保留原视频节奏，替换为我们的产品卖点，口吻更高级自然"), true)
          + taskFieldHtml("分段时长", taskSelectHtml("workViralDuration", optionHtml("10", "10 秒/段") + optionHtml("5", "5 秒/段")))
          + taskFieldHtml("画幅", taskSelectHtml("workViralRatio", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏") + optionHtml("1:1", "1:1 方图")))
          + taskFieldHtml("音频", workCheckboxHtml("workViralGenerateAudio", "生成音频", true));
      }
      if (key === "wecom_reply") {
        return taskFieldHtml("执行动作", taskSelectHtml("workWecomAction", optionHtml("poll_reply", "拉取待处理消息并自动回复一次")))
          + taskFieldHtml("备注", taskTextareaHtml("workWecomNote", "可选：这次希望客服重点关注的业务场景或回复口径"), true);
      }
      if (key === "publish_center") {
        return taskFieldHtml("发布素材", assetPickerControlHtml("workPublishMaterial", { mediaType: "", output: "url", accept: "image/*,video/*,.pdf,.doc,.docx,.ppt,.pptx", uploadText: "上传素材" }), true)
          + taskFieldHtml("素材类型", taskSelectHtml("workPublishMediaType", optionHtml("video", "视频") + optionHtml("image", "图片") + optionHtml("document", "文档")))
          + taskFieldHtml("发布账号昵称", workInputHtml("workPublishAccount", "text", "", 'placeholder="与 online 发布中心里的账号昵称一致"'))
          + taskFieldHtml("标题", workInputHtml("workPublishTitle", "text", "", 'placeholder="可选，不填可让 AI 补全"'), true)
          + taskFieldHtml("正文/描述", taskTextareaHtml("workPublishDescription", "可选：发布正文、卖点或备注"), true)
          + taskFieldHtml("话题标签", workInputHtml("workPublishTags", "text", "", 'placeholder="#品牌 #同城 或逗号分隔"'), true)
          + taskFieldHtml("AI 补全文案", workCheckboxHtml("workPublishAiCopy", "缺少标题/正文时让 AI 自动补全", true));
      }
      return `<div class="quick-empty">这个入口暂未配置下发表单。</div>`;
    }

    function openWorkDispatchModal(key) {
      const item = workQuickItemByKey(key);
      if (!item || item.disabled) return;
      const modal = $("workDispatchModal");
      if (!modal) return;
      state.workDispatchKey = String(item.key || item.label || "");
      $("workDispatchMark").textContent = item.mark || firstChar(item.label);
      $("workDispatchTitle").textContent = item.label || "安排工作";
      $("workDispatchFields").innerHTML = workDispatchFieldsHtml(item);
      modal.classList.remove("hidden");
      initAssetPickerControls(modal);
      if (item.key === "hifly.video.create_by_tts") {
        renderWorkHiflyOptions();
        loadHiflyLibraries();
      }
      const first = modal.querySelector("input, textarea, select");
      if (first && typeof first.focus === "function") setTimeout(() => first.focus(), 80);
    }

    function closeWorkDispatchModal() {
      const modal = $("workDispatchModal");
      if (modal) modal.classList.add("hidden");
      state.workDispatchKey = "";
      state.workDispatchSubmitting = false;
      const btn = $("workDispatchSubmit");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "下发任务";
      }
    }

    function workValue(id) {
      const el = $(id);
      return ((el && el.value) || "").trim();
    }

    function collectWorkDispatchPlan(item) {
      const key = String(item && item.key || "");
      if (key === "image_composer_studio") {
        const prompt = workValue("workImagePrompt");
        if (!prompt) throw new Error("请填写图片需求");
        return {
          title: workValue("workImageTitle") || "创作图片",
          taskKind: "capability",
          content: "H5 安排工作：创作图片",
          payload: { capability_id: "goal.image.pipeline", payload: { prompt } },
        };
      }
      if (key === "comfly.seedance.tvc.pipeline") {
        const taskText = workValue("workSeedanceText");
        const asset = assetOrImagePayload(workValue("workSeedanceAsset"), "参考图片");
        return {
          title: "创意分镜头视频",
          taskKind: "capability",
          content: "H5 安排工作：创意分镜头视频",
          payload: {
            capability_id: "comfly.seedance.tvc.pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: taskText,
              total_duration_seconds: workNumber(workValue("workSeedanceDuration"), 20, 5, 120),
              aspect_ratio: workValue("workSeedanceAspect") || "9:16",
              auto_save: true,
            },
          },
        };
      }
      if (key === "comfly.daihuo.pipeline") {
        const asset = assetOrImagePayload(workValue("workComflyAsset"), "参考图片");
        return {
          title: "爆款TVC",
          taskKind: "capability",
          content: "H5 能力工作台：爆款TVC",
          payload: {
            capability_id: "comfly.daihuo.pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: workValue("workComflyText"),
              storyboard_count: workNumber(workValue("workComflyStoryboardCount"), 5, 1, 8),
              auto_save: !!($("workComflyAutoSave") && $("workComflyAutoSave").checked),
            },
          },
        };
      }
      if (key === "hifly.video.create_by_tts") {
        const avatar = workValue("workAvatar");
        const voice = workValue("workVoice");
        const script = workValue("workHiflyScript");
        if (!avatar) throw new Error("请选择数字人");
        if (!voice) throw new Error("请选择声音");
        if (!script) throw new Error("请填写口播文案");
        return {
          title: workValue("workHiflyTitle") || "数字人口播",
          taskKind: "capability",
          content: "H5 安排工作：数字人口播",
          payload: { capability_id: "hifly.video.create_by_tts", payload: { avatar, voice, script, prompt: script } },
        };
      }
      if (key === "douyin_leads") {
        const keyword = workValue("workDouyinKeyword");
        if (!keyword) throw new Error("请填写采集关键词");
        const regions = workSplitList(workValue("workDouyinRegions"));
        return {
          title: `抖音获客 - ${keyword.slice(0, 24)}`,
          taskKind: "douyin_leads",
          content: "H5 安排工作：抖音获客采集客户",
          payload: {
            action: "search_collect",
            params: {
              keyword,
              max_results: workNumber(workValue("workDouyinMaxResults"), 50, 10, 100),
              regions: regions.length ? regions : ["全国"],
              mode: workValue("workDouyinMode") || "script",
            },
          },
        };
      }
      if (key === "local_bestseller") {
        const photo = workValue("workLocalPhoto");
        const profile = {
          name: workValue("workLocalName"),
          nickname: workValue("workLocalNickname"),
          gender: workValue("workLocalGender") || "female",
          identity: workValue("workLocalIdentity") || "女老板",
          industry: workValue("workLocalIndustry") || "大健康",
          city: workValue("workLocalCity") || "深圳",
          province: workValue("workLocalProvince") || "广东",
        };
        if (/^https?:\/\//i.test(photo)) profile.photo_url = photo;
        else if (photo) profile.photo_asset_id = photo;
        const mode = workValue("workLocalMode") || "plan";
        return {
          title: `同城爆款 - ${profile.city || "本地"}`,
          taskKind: "client_workflow",
          content: "H5 安排工作：同城爆款",
          payload: {
            action: mode === "scene_batch" ? "local_bestseller_scene_batch" : "local_bestseller_plan",
            params: { profile, days: workNumber(workValue("workLocalDays"), 30, 1, 30) },
          },
        };
      }
      if (key === "viral_video_remix") {
        const originalVideoUrl = workValue("workViralVideoUrl");
        const characterImageUrl = workValue("workViralCharacterUrl");
        const productImageUrl = workValue("workViralProductUrl");
        if (!/^https?:\/\//i.test(originalVideoUrl)) throw new Error("请上传或选择参考视频");
        if (!characterImageUrl && !productImageUrl) throw new Error("请至少上传或选择人物图、产品图其中一个");
        return {
          title: "爆款复刻",
          taskKind: "client_workflow",
          content: "H5 安排工作：爆款复刻",
          payload: {
            action: "viral_video_remix_start",
            params: {
              original_video_url: originalVideoUrl,
              character_image_url: characterImageUrl,
              product_image_url: productImageUrl,
              prompt: workValue("workViralPrompt"),
              duration: workNumber(workValue("workViralDuration"), 10, 5, 10),
              ratio: workValue("workViralRatio") || "9:16",
              generate_audio: !!($("workViralGenerateAudio") && $("workViralGenerateAudio").checked),
              billing_confirmed: true,
            },
          },
        };
      }
      if (key === "wecom_reply") {
        return {
          title: "企业微信客服 - 拉取回复",
          taskKind: "client_workflow",
          content: "H5 安排工作：企业微信客服",
          payload: { action: "wecom_poll_reply", params: { note: workValue("workWecomNote") } },
        };
      }
      if (key === "publish_center") {
        const material = workMaterialPayload(workValue("workPublishMaterial"));
        const accountNickname = workValue("workPublishAccount");
        if (!accountNickname) throw new Error("请填写发布账号昵称");
        return {
          title: "发布中心入库",
          taskKind: "client_workflow",
          content: "H5 安排工作：发布中心入库",
          payload: {
            action: "publish_content",
            params: {
              ...material,
              media_type: workValue("workPublishMediaType") || "video",
              account_nickname: accountNickname,
              title: workValue("workPublishTitle"),
              description: workValue("workPublishDescription"),
              tags: workValue("workPublishTags"),
              ai_publish_copy: !!($("workPublishAiCopy") && $("workPublishAiCopy").checked),
            },
          },
        };
      }
      throw new Error("这个岗位入口暂不支持直接下发");
    }

    function collectAbilityCapabilityPlan(node) {
      const capabilityId = String((node && (node.capabilityId || node.key)) || "").trim();
      if (capabilityId === "ip_content_daily") {
        const templateId = parseInt(abilityValue("abilityIpTemplate") || "0", 10);
        if (!templateId || Number.isNaN(templateId)) throw new Error("请选择 IP日更服务器模板");
        const tasks = selectedAbilityIpDailyTasks();
        if (!tasks.length) throw new Error("请选择至少一种生成内容");
        const extra = abilityValue("abilityIpRequirement");
        const requirements = extra ? { common: extra, oral: extra, moments: extra, image: extra } : {};
        return {
          title: node.label || "IP日更文案",
          taskKind: "ip_content_daily",
          content: "H5 能力工作台：IP日更文案",
          payload: {
            template_id: templateId,
            tasks,
            sync_before: !!($("abilityIpSyncBefore") && $("abilityIpSyncBefore").checked),
            requirements,
            industry_count: 5,
            ip_count: 5,
            moments_count: 20,
          },
        };
      }
      if (capabilityId === "goal.video.pipeline") {
        const payload = collectGoalVideoPayloadFromFields({
          modeId: "abilityVideoMode",
          assetId: "abilityVideoAsset",
          memoryId: "abilityVideoMemoryDocs",
          groupId: "abilityVideoCandidateGroup",
          promptId: "abilityVideoPrompt",
        });
        return {
          title: abilityValue("abilityVideoTitle") || node.label || "创意视频",
          taskKind: "capability",
          content: "H5 能力工作台：创意视频",
          payload: {
            capability_id: "goal.video.pipeline",
            payload,
          },
        };
      }
      if (capabilityId === "wewrite.article.pipeline") {
        const idea = abilityValue("abilityArticleIdea");
        if (!idea) throw new Error("请填写公众号主题");
        return {
          title: abilityValue("abilityArticleTitle") || node.label || "公众号文章",
          taskKind: "capability",
          content: "H5 能力工作台：公众号文章",
          payload: {
            capability_id: "wewrite.article.pipeline",
            payload: {
              idea,
              style: abilityValue("abilityArticleStyle"),
              include_images: !!($("abilityArticleIncludeImages") && $("abilityArticleIncludeImages").checked),
              image_count: abilityNumber("abilityArticleImageCount", 3, 0, 6),
              image_aspect_ratio: "16:9",
            },
          },
        };
      }
      if (capabilityId === "ppt.create") {
        const topic = abilityValue("abilityPptTopic");
        if (!topic) throw new Error("请填写 PPT 主题");
        return {
          title: abilityValue("abilityPptTitle") || node.label || "PPT生成",
          taskKind: "capability",
          content: "H5 能力工作台：PPT生成",
          payload: {
            capability_id: "ppt.create",
            payload: {
              mode: abilityValue("abilityPptMode") || "ai",
              topic,
              slide_count: abilityNumber("abilityPptSlideCount", 10, 1, 80),
              instructions: abilityValue("abilityPptInstructions"),
              language: "zh-CN",
            },
          },
        };
      }
      if (capabilityId === "comfly.ecommerce.detail_pipeline") {
        const asset = assetOrImagePayload(abilityValue("abilityEcommerceAsset"), "商品主图");
        return {
          title: abilityValue("abilityEcommerceTitle") || node.label || "电商详情页",
          taskKind: "capability",
          content: "H5 能力工作台：电商详情页",
          payload: {
            capability_id: "comfly.ecommerce.detail_pipeline",
            payload: {
              action: "start_pipeline",
              ...asset,
              task_text: abilityValue("abilityEcommerceText"),
              page_count: abilityNumber("abilityEcommercePageCount", 12, 1, 20),
              auto_save: !!($("abilityEcommerceAutoSave") && $("abilityEcommerceAutoSave").checked),
            },
          },
        };
      }
      const prompt = abilityValue("abilityGenericPrompt");
      if (!prompt) throw new Error("请填写任务要求");
      return {
        title: abilityValue("abilityGenericTitle") || node.label || capabilityName(capabilityId) || "能力任务",
        taskKind: "capability",
        content: `H5 能力工作台：${node.label || capabilityId}`,
        payload: { capability_id: capabilityId, payload: { prompt, task_text: prompt } },
      };
    }

    function collectSocialLeadsPayload(platform) {
      const keywords = splitTextareaList(abilityValue("abilityLeadKeywords"));
      if (!keywords.length) throw new Error("请填写精准用户方向");
      const mode = abilityValue("abilityLeadMode") || "source";
      const accounts = splitTextareaList(abilityValue("abilityLeadAccounts"));
      const sources = splitTextareaList(abilityValue("abilityLeadSources"));
      const payload = {
        platform,
        title: abilityValue("abilityLeadTitle") || `${socialPlatformLabel(platform)}线索采集`,
        keywords,
        max_items: abilityNumber("abilityLeadMaxItems", 100, 1, 100),
        include_comments: true,
        include_account_posts: true,
        auto_run: true,
      };
      if (mode === "account") {
        if (!accounts.length) throw new Error("请填写账号");
        payload.accounts = accounts;
      } else if (platform === "reddit") {
        if (!sources.length) throw new Error("请填写社区");
        payload.communities = sources;
      } else {
        if (!sources.length) throw new Error("请填写来源关键词");
        payload.source_keywords = sources;
      }
      return payload;
    }

    function collectLinkedinPayload(node) {
      const payload = {
        title: abilityValue("abilityLinkedinTitle") || (node && node.label) || "LinkedIn线索采集",
        target_profile: abilityValue("abilityLinkedinTarget"),
        seed_profile_urls: splitTextareaList(abilityValue("abilityLinkedinProfiles")),
        seed_company_urls: splitTextareaList(abilityValue("abilityLinkedinCompanies")),
        keywords: splitTextareaList(abilityValue("abilityLinkedinKeywords")),
        hashtags: splitTextareaList(abilityValue("abilityLinkedinHashtags")),
        max_people: abilityNumber("abilityLinkedinMaxPeople", 30, 5, 80),
        auto_run: true,
      };
      if (!payload.seed_profile_urls.length && !payload.seed_company_urls.length && !payload.keywords.length && !payload.hashtags.length) {
        throw new Error("请至少填写个人主页、公司主页、关键词或话题");
      }
      return payload;
    }

    function collectWechatTranscriptPayload(node) {
      const query = abilityValue("abilityWechatQuery");
      if (!query) throw new Error("请填写视频号账号、链接或关键词");
      return {
        title: (node && node.label) || "视频号文案提取",
        query,
        max_pages: abilityNumber("abilityWechatPages", 1, 1, 20),
        limit: abilityNumber("abilityWechatLimit", 10, 1, 50),
        page_size: 20,
      };
    }

    async function submitSocialLeadsWorkbench(node, platform) {
      const payload = collectSocialLeadsPayload(platform);
      const data = await api("/api/social-leads/jobs", { method: "POST", json: payload });
      return data;
    }

    async function submitLinkedinWorkbench(node) {
      const payload = {
        title: abilityValue("abilityLinkedinTitle") || node.label || "LinkedIn线索挖掘",
        target_profile: abilityValue("abilityLinkedinTarget"),
        seed_profile_urls: splitTextareaList(abilityValue("abilityLinkedinProfiles")),
        seed_company_urls: splitTextareaList(abilityValue("abilityLinkedinCompanies")),
        keywords: splitTextareaList(abilityValue("abilityLinkedinKeywords")),
        hashtags: splitTextareaList(abilityValue("abilityLinkedinHashtags")),
        max_people: abilityNumber("abilityLinkedinMaxPeople", 30, 5, 80),
        auto_run: true,
      };
      if (!payload.seed_profile_urls.length && !payload.seed_company_urls.length && !payload.keywords.length && !payload.hashtags.length) {
        throw new Error("请至少填写个人主页、公司主页、关键词或话题");
      }
      const data = await api("/api/linkedin-mining/jobs", { method: "POST", json: payload });
      return data;
    }

    async function submitWechatTranscriptWorkbench(node) {
      const query = abilityValue("abilityWechatQuery");
      if (!query) throw new Error("请填写视频号账号、链接或关键词");
      const search = await api(`/api/wechat-channels-transcript/users/search?q=${encodeURIComponent(query)}`);
      const account = Array.isArray(search.items) ? search.items[0] : null;
      const username = String((account && (account.username || account.finder_username || account.id)) || "").trim();
      if (!username) throw new Error("没有找到可用的视频号账号");
      const videos = await api("/api/wechat-channels-transcript/videos", {
        method: "POST",
        json: {
          username,
          max_pages: abilityNumber("abilityWechatPages", 1, 1, 20),
          page_size: 20,
        },
      });
      const items = (Array.isArray(videos.items) ? videos.items : []).slice(0, abilityNumber("abilityWechatLimit", 10, 1, 50));
      if (!items.length) throw new Error("这个账号暂时没有拉到可转写的视频");
      const data = await api("/api/wechat-channels-transcript/jobs", {
        method: "POST",
        json: { username, videos: items },
      });
      return data;
    }

    async function submitAbilityWorkbench() {
      if (state.abilityWorkSubmitting) return;
      const lookup = activeAbilityLookup();
      const node = lookup && lookup.node;
      if (!node) throw new Error("未找到当前能力");
      if (node.routeTab) {
        switchTab(node.routeTab);
        return;
      }
      state.abilityWorkSubmitting = true;
      const btn = $("abilityWorkbenchSubmit");
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      try {
        const schedule = collectAbilityScheduleOptions();
        const platform = socialPlatformFromAbilityKey(node.key);
        let plan = null;
        let submittedDirect = false;
        if (platform) {
          const payload = collectSocialLeadsPayload(platform);
          if (schedule.schedule_type === "once") {
            await submitSocialLeadsWorkbench(node, platform);
            submittedDirect = true;
          } else {
            plan = {
              title: payload.title || `${socialPlatformLabel(platform)}线索采集`,
              taskKind: "social_leads",
              content: `H5 ${socialPlatformLabel(platform)}线索采集`,
              payload,
            };
          }
        } else if (node.key === "linkedin_leads") {
          const payload = collectLinkedinPayload(node);
          if (schedule.schedule_type === "once") {
            await submitLinkedinWorkbench(node);
            submittedDirect = true;
          } else {
            plan = {
              title: payload.title || "LinkedIn线索采集",
              taskKind: "linkedin_mining",
              content: "H5 LinkedIn线索采集",
              payload,
            };
          }
        } else if (node.key === "wechat_channels_transcript") {
          const payload = collectWechatTranscriptPayload(node);
          if (schedule.schedule_type === "once") {
            await submitWechatTranscriptWorkbench(node);
            submittedDirect = true;
          } else {
            plan = {
              title: payload.title || "视频号文案提取",
              taskKind: "wechat_channels_transcript",
              content: "H5 视频号文案提取",
              payload,
            };
          }
        } else if (node.workQuickKey) {
          const quick = workQuickItemByKey(node.workQuickKey);
          if (!quick) throw new Error("未找到对应下发入口");
          plan = collectWorkDispatchPlan(quick);
        } else if (node.capabilityId || node.serverTask) {
          plan = collectAbilityCapabilityPlan(node);
        } else {
          throw new Error("这个能力暂未配置下发方式");
        }
        if (plan) plan.h5Context = contextFromAbility(lookup);
        if (!submittedDirect) await submitScheduledClientTask(plan, schedule);
        await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true }).catch(() => {})]);
        renderOfficeEmployees();
        renderWorkList();
        if (schedule.schedule_type !== "once") {
          showTaskSuccessDialog("定时任务已创建，可在定时任务管理里暂停、启用、编辑或删除。");
          return;
        }
        showTaskSuccessDialog("任务已下发成功，可在工作历史查看进度。");
      } finally {
        state.abilityWorkSubmitting = false;
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "下发任务";
        }
      }
    }

    function isServerSideScheduledKind(kind) {
      return ["ip_content_daily", "lead_collection_templates", "social_leads", "linkedin_mining", "wechat_channels_transcript"].includes(String(kind || ""));
    }

    async function submitScheduledClientTask(plan, scheduleOptions = {}) {
      const installationId = currentInstallationId();
      const scheduleType = scheduleOptions.schedule_type || "once";
      const serverSide = isServerSideScheduledKind(plan.taskKind) || plan.serverSide;
      if (!serverSide && !installationId) throw new Error("暂未检测到在线设备，请先让本机 online 客户端保持登录");
      const body = {
        title: plan.title || "安排工作",
        task_kind: plan.taskKind || "client_workflow",
        content: plan.content || "H5 安排工作",
        payload: attachH5ContextToPayload(plan.payload || {}, plan.h5Context),
        schedule_type: scheduleType,
        interval_seconds: scheduleOptions.interval_seconds || 60,
        start_at: scheduleType === "daily_times" ? "" : (scheduleOptions.start_at || ""),
        daily_times: scheduleType === "daily_times" ? (scheduleOptions.daily_times || []) : [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: serverSide ? [] : [installationId],
      };
      const data = await api("/api/scheduled-tasks/tasks", {
        method: "POST",
        json: body,
        headers: installationId ? { "X-Installation-Id": installationId } : {},
      });
      if (data.task) {
        state.tasks = [data.task].concat((state.tasks || []).filter((row) => String(row.id) !== String(data.task.id)));
      }
      if (Array.isArray(data.runs)) mergeRuns(data.runs);
      await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      return data;
    }

    async function submitOnceClientTask(plan) {
      return submitScheduledClientTask(plan, { schedule_type: "once" });
      const installationId = currentInstallationId();
      const serverSide = plan.taskKind === "ip_content_daily" || plan.serverSide;
      if (!serverSide && !installationId) throw new Error("暂未检测到在线设备，请先让本机 online 端保持登录");
      const body = {
        title: plan.title || "安排工作",
        task_kind: plan.taskKind || "client_workflow",
        content: plan.content || "H5 安排工作",
        payload: plan.payload || {},
        schedule_type: "once",
        interval_seconds: 60,
        start_at: "",
        daily_times: [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: serverSide ? [] : [installationId],
      };
      const data = await api("/api/scheduled-tasks/tasks", {
        method: "POST",
        json: body,
        headers: installationId ? { "X-Installation-Id": installationId } : {},
      });
      await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      return data;
    }

    async function submitWorkDispatch() {
      if (state.workDispatchSubmitting) return;
      const item = workQuickItemByKey(state.workDispatchKey);
      if (!item) throw new Error("未找到岗位入口");
      const plan = collectWorkDispatchPlan(item);
      state.workDispatchSubmitting = true;
      const btn = $("workDispatchSubmit");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "下发中...";
      }
      try {
        await submitOnceClientTask(plan);
        closeWorkDispatchModal();
        showTaskSuccessDialog("任务已下发成功，可在工作历史查看进度。");
      } catch (err) {
        state.workDispatchSubmitting = false;
        if (btn) {
          btn.disabled = false;
          btn.textContent = "下发任务";
        }
        throw err;
      }
    }

    async function loadTaskSkills(force = false) {
      if (!state.token) return;
      if (!force && (state.taskSkillsLoaded || state.taskSkillsLoading)) {
        renderTaskAbilityBoard();
        renderHomeQuickGrid();
        return;
      }
      state.taskSkillsLoading = true;
      state.taskSkillsError = "";
      renderHomeQuickGrid();
      try {
        const [store, allowed] = await Promise.all([
          api("/skills/store").catch(() => ({ packages: [] })),
          api("/skills/user-allowed-capability-ids").catch(() => ({ capability_ids: [] })),
        ]);
        state.taskSkillPackages = Array.isArray(store.packages) ? store.packages : [];
        state.taskAllowedCapabilityIds = Array.isArray(allowed.capability_ids) ? allowed.capability_ids : [];
        state.taskSkillsLoaded = true;
      } catch (err) {
        state.taskSkillPackages = [];
        state.taskAllowedCapabilityIds = [];
        state.taskSkillsError = err.message || "岗位工作入口加载失败，请稍后重试";
        toast(err.message || "岗位工作入口加载失败");
      } finally {
        state.taskSkillsLoading = false;
        renderTaskAbilityBoard();
        renderHomeQuickGrid();
      }
    }

    function renderTaskParamFields() {
      const host = $("taskParamFields");
      if (!host) return;
      if (state.taskAbility === "ip_content_daily") {
        host.innerHTML = taskFieldHtml("关键词和同行模板", ipTemplateSelectControl("taskIpTemplate"))
          + taskFieldHtml("生成内容", ipDailyTaskOptionsHtml(), true)
          + taskFieldHtml("执行前同步", `<label class="task-checkbox"><input id="taskIpSyncBefore" type="checkbox" checked>每次执行前同步新数据</label>`, true)
          + taskFieldHtml("补充要求（可选）", taskTextareaHtml("taskIpRequirement", "例如：口播更有案例感；朋友圈短句分行、多段落留白、适当 Emoji、强痛点和结果导向；图片干净真实"), true);
        loadIpTemplates(true);
        return;
      }
      if (state.taskAbility === "hifly.video.create_by_tts") {
        host.innerHTML = taskFieldHtml("数字人", taskSelectHtml("taskAvatar", optionHtml("", "加载中...")))
          + taskFieldHtml("声音", taskSelectHtml("taskVoice", optionHtml("", "加载中...")))
          + `<div class="field"><label>&nbsp;</label><button class="ghost" type="button" id="autoPickDigitalBtn">自动选择</button></div>`;
        renderHiflyOptions();
        if ($("autoPickDigitalBtn")) $("autoPickDigitalBtn").addEventListener("click", autoPickDigital);
        if (state.taskPanelOpen) loadHiflyLibraries();
        return;
      }
      if (state.taskAbility === "goal.image.pipeline") {
        host.innerHTML = taskFieldHtml("发布平台", taskSelectHtml("taskPublishPlatform", optionHtml("", "不发布，仅生成记录")))
          + taskFieldHtml("发布账号", taskSelectHtml("taskPublishAccount", optionHtml("", "先选择发布平台")))
          + taskFieldHtml("发布方式", `<label class="task-checkbox"><input id="taskPublishAuto" type="checkbox">生成后自动发布</label><p class="task-param-note">不勾选时只推送到 H5/小程序/online 记录，之后可手动点击发布。</p>`, true)
          + taskFieldHtml("提示词（可选）", taskTextareaHtml("taskCreativePrompt", "填写后直接按这段提示词生成图片；留空则根据记忆资料自动生成文案和画面方向"), true);
        if ($("taskPublishPlatform")) $("taskPublishPlatform").addEventListener("change", fillPublishAccountSelect);
        loadPublishAccounts();
        return;
      }
      if (state.taskAbility === "wewrite.article.pipeline") {
        host.innerHTML = taskFieldHtml("公众号主题", taskTextareaHtml("taskArticleIdea", "例如：必火AI龙虾盒子如何帮老板搭建一人公司"), true)
          + taskFieldHtml("文章风格", `<input id="taskArticleStyle" placeholder="例如：专业、有案例、适合老板阅读" />`)
          + taskFieldHtml("配图数量", `<input id="taskArticleImageCount" type="number" min="0" max="6" value="3" />`)
          + taskFieldHtml("自动配图", `<label class="task-checkbox"><input id="taskArticleIncludeImages" type="checkbox" checked>生成 16:9 横屏配图并插入</label>`, true);
        return;
      }
      if (state.taskAbility === "ppt.create") {
        host.innerHTML = taskFieldHtml("PPT主题", taskTextareaHtml("taskPptTopic", "例如：必火AI龙虾盒子招商路演PPT"), true)
          + taskFieldHtml("页数", `<input id="taskPptSlideCount" type="number" min="1" max="80" value="10" />`)
          + taskFieldHtml("风格要求", `<input id="taskPptInstructions" placeholder="例如：科技感、适合招商、案例更具体" />`)
          + taskFieldHtml("生成模式", taskSelectHtml("taskPptMode", optionHtml("ai", "AI视觉页") + optionHtml("outline", "结构化大纲")));
        return;
      }
      if (state.taskAbility === "create.video.pipeline") {
        host.innerHTML = taskFieldHtml("视频主题", taskTextareaHtml("taskCreateVideoPrompt", "例如：给必火AI龙虾盒子生成一个30秒招商宣传视频"), true)
          + taskFieldHtml("时长秒数", `<input id="taskCreateVideoDuration" type="number" min="3" max="60" value="8" />`)
          + taskFieldHtml("分镜数量", `<input id="taskCreateVideoSceneCount" type="number" min="1" max="6" value="1" />`)
          + taskFieldHtml("画幅", taskSelectHtml("taskCreateVideoAspect", optionHtml("16:9", "16:9 横屏") + optionHtml("9:16", "9:16 竖屏") + optionHtml("1:1", "1:1 方图")));
        return;
      }
      if (state.taskAbility === "comfly.daihuo.pipeline") {
        host.innerHTML = taskFieldHtml("参考图片", assetPickerControlHtml("taskComflyAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }))
          + taskFieldHtml("视频要求", taskTextareaHtml("taskComflyText", "例如：生成一个突出产品卖点和使用场景的爆款TVC"), true)
          + taskFieldHtml("分镜数量", `<input id="taskComflyStoryboardCount" type="number" min="1" max="8" value="5" />`)
          + taskFieldHtml("自动入库", `<label class="task-checkbox"><input id="taskComflyAutoSave" type="checkbox" checked>完成后保存到素材库</label>`);
        initAssetPickerControls(host);
        return;
      }
      if (state.taskAbility === "comfly.seedance.tvc.pipeline") {
        host.innerHTML = taskFieldHtml("参考图片", assetPickerControlHtml("taskSeedanceAsset", { mediaType: "image", output: "url", uploadText: "上传图片" }))
          + taskFieldHtml("视频要求", taskTextareaHtml("taskSeedanceText", "例如：明亮真实的品牌广告，镜头连续，适合投放"), true)
          + taskFieldHtml("总时长", taskSelectHtml("taskSeedanceDuration", [10,20,30,40,50,60].map((n) => optionHtml(String(n), `${n} 秒`)).join("")))
          + taskFieldHtml("画幅", taskSelectHtml("taskSeedanceAspect", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏")));
        initAssetPickerControls(host);
        return;
      }
      if (state.taskAbility === "comfly.ecommerce.detail_pipeline") {
        host.innerHTML = taskFieldHtml("商品主图", assetPickerControlHtml("taskEcommerceAsset", { mediaType: "image", output: "url", uploadText: "上传主图" }))
          + taskFieldHtml("详情页要求", taskTextareaHtml("taskEcommerceText", "例如：突出材质、卖点、使用场景和购买理由"), true)
          + taskFieldHtml("页面数量", `<input id="taskEcommercePageCount" type="number" min="1" max="20" value="12" />`)
          + taskFieldHtml("自动入库", `<label class="task-checkbox"><input id="taskEcommerceAutoSave" type="checkbox" checked>完成后保存到素材库</label>`);
        initAssetPickerControls(host);
        return;
      }
      host.innerHTML = taskFieldHtml("生成模式", taskSelectHtml("taskVideoMode", optionHtml("single_asset", "单个素材生成视频") + optionHtml("memory_image", "根据记忆先生成图片") + optionHtml("asset_group", "素材分组轮换生成")))
        + `<div class="field full" id="taskVideoAssetField"><label>素材图片</label>${assetPickerControlHtml("taskVideoAsset", { mediaType: "image", output: "url", uploadText: "上传图片", selectText: "选择已上传图片" })}</div>`
        + `<div class="field full hidden" id="taskVideoMemoryField"><label>记忆文件</label>${videoMemorySelectControl("taskVideoMemoryDocs")}</div>`
        + `<div class="field hidden" id="taskCandidateGroupField"><label>素材分组</label>${taskSelectHtml("taskCandidateGroup", optionHtml("", "加载中..."))}</div>`
        + taskFieldHtml("补充提示词", taskTextareaHtml("taskCreativePrompt", "可选"), true);
      bindGoalVideoModeControls("task");
      initAssetPickerControls(host);
      fillVideoMemorySelects();
      loadVideoMemoryDocsForSelect();
      fillCandidateGroupSelect();
      loadCandidateGroups();
    }

    async function loadHiflyLibraries() {
      if (state.hiflyLoaded || state.hiflyLoading) return;
      state.hiflyLoading = true;
      try {
        const [myAvatar, publicAvatar, myVoice, publicVoice] = await Promise.all([
          api("/api/hifly/my/avatar/list?page=1&size=100").catch(() => ({ items: [] })),
          api("/api/hifly/avatar/library", { method: "POST", json: { page: 1, size: 100, include_mine: true } }).catch(() => ({ public: [] })),
          api("/api/hifly/my/voice/list?page=1&size=100").catch(() => ({ items: [] })),
          api("/api/hifly/voice/library", { method: "POST", json: {} }).catch(() => ({ public: [] })),
        ]);
        state.avatarRows = normalizeAvatarRows(myAvatar.items || [], publicAvatar.public || []);
        state.voiceRows = normalizeVoiceRows(myVoice.items || [], publicVoice.public || []);
        state.hiflyLoaded = true;
        renderHiflyOptions();
      } catch (err) {
        toast(err.message || "数字人资源加载失败");
      } finally {
        state.hiflyLoading = false;
      }
    }

    function setTaskAbility(ability) {
      const next = ability || "goal.video.pipeline";
      if (!isScheduledTaskAbility(next)) {
        toast("这个技能不支持定时下发，请在下方岗位工作入口发起");
        renderTaskAbilityBoard();
        return;
      }
      if (!taskAbilityVisible(next)) {
        toast("当前账号没有这个技能的安排权限");
        renderTaskAbilityBoard();
        return;
      }
      const meta = TASK_CAPABILITIES[next] || {};
      if (meta.routeTab) {
        switchTab(meta.routeTab);
        return;
      }
      state.taskAbility = next;
      renderTaskAbilityBoard();
      renderTaskParamFields();
      if (state.taskPanelOpen && state.taskAbility === "hifly.video.create_by_tts") loadHiflyLibraries();
    }

    function setTaskPanelOpen(open) {
      state.taskPanelOpen = !!open;
      $("taskPanelBody").classList.toggle("hidden", !state.taskPanelOpen);
      $("toggleTaskPanelBtn").textContent = state.taskPanelOpen ? "收起" : "开始";
      if (state.taskPanelOpen) {
        loadTaskSkills();
        renderTaskAbilityBoard();
        renderHomeQuickGrid();
        renderTaskParamFields();
        updateScheduleFields();
      }
      if (state.taskPanelOpen && state.taskAbility === "hifly.video.create_by_tts") loadHiflyLibraries();
    }

    function autoPickDigital() {
      if (state.avatarRows[0]) $("taskAvatar").value = state.avatarRows[0].avatar;
      if (state.voiceRows[0]) $("taskVoice").value = state.voiceRows[0].voice;
      loadHiflyLibraries();
    }

    function timezoneOffsetMinutes() {
      return -new Date().getTimezoneOffset();
    }

    function collectDailyTimes() {
      return Array.from(document.querySelectorAll("[data-task-daily-time]"))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function addDailyTime(value = "") {
      const list = $("taskDailyTimesList");
      if (!list) return;
      const row = document.createElement("div");
      row.className = "task-daily-row";
      row.innerHTML = `<input type="time" step="60" data-task-daily-time value="${escapeHtml(value)}"><button class="ghost" type="button" aria-label="删除时间点">-</button>`;
      row.querySelector("button").addEventListener("click", () => row.remove());
      list.appendChild(row);
    }

    function updateScheduleFields() {
      const type = $("taskScheduleType") ? $("taskScheduleType").value : "once";
      if ($("taskIntervalBlock")) $("taskIntervalBlock").classList.toggle("hidden", type !== "interval");
      if ($("taskDailyTimesBlock")) $("taskDailyTimesBlock").classList.toggle("hidden", type !== "daily_times");
      if ($("taskStartAt")) $("taskStartAt").closest(".field").classList.toggle("hidden", type === "daily_times");
      if (type === "daily_times" && $("taskDailyTimesList") && !$("taskDailyTimesList").children.length) addDailyTime("09:00");
    }

    function scheduleTypeOptionsHtml() {
      return optionHtml("once", "一次性") + optionHtml("interval", "循环间隔") + optionHtml("daily_times", "每天固定时间");
    }

    function abilityScheduleFieldsHtml() {
      return `<div class="field full ability-schedule-panel">
        <div class="ability-schedule-head">
          <strong>执行方式</strong>
          <span>默认一次性；选择定时后按当前定时任务配置创建。</span>
        </div>
      </div>`
        + taskFieldHtml("执行方式", taskSelectHtml("abilityScheduleType", scheduleTypeOptionsHtml()))
        + taskFieldHtml("间隔分钟", workInputHtml("abilityIntervalMinutes", "number", "60", 'min="1"'))
        + taskFieldHtml("开始时间（可选）", '<input id="abilityStartAt" type="datetime-local">')
        + `<div class="field full hidden" id="abilityDailyTimesBlock">
            <label>每天执行时间</label>
            <div class="task-daily-times" id="abilityDailyTimesList"></div>
            <button class="ghost" type="button" id="abilityAddDailyTimeBtn">添加时间点</button>
          </div>`;
    }

    function collectAbilityDailyTimes() {
      return Array.from(document.querySelectorAll("[data-ability-daily-time]"))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function addAbilityDailyTime(value = "") {
      const list = $("abilityDailyTimesList");
      if (!list) return;
      const row = document.createElement("div");
      row.className = "task-daily-row";
      row.innerHTML = `<input type="time" step="60" data-ability-daily-time value="${escapeHtml(value)}"><button class="ghost" type="button" aria-label="删除时间点">-</button>`;
      row.querySelector("button").addEventListener("click", () => row.remove());
      list.appendChild(row);
    }

    function updateAbilityScheduleFields() {
      const type = $("abilityScheduleType") ? $("abilityScheduleType").value : "once";
      const interval = $("abilityIntervalMinutes");
      const startAt = $("abilityStartAt");
      const dailyBlock = $("abilityDailyTimesBlock");
      if (interval) interval.closest(".field").classList.toggle("hidden", type !== "interval");
      if (startAt) startAt.closest(".field").classList.toggle("hidden", type === "daily_times");
      if (dailyBlock) dailyBlock.classList.toggle("hidden", type !== "daily_times");
      if (type === "daily_times" && $("abilityDailyTimesList") && !$("abilityDailyTimesList").children.length) addAbilityDailyTime("09:00");
      const submit = $("abilityWorkbenchSubmit");
      if (submit && !state.abilityWorkSubmitting) submit.textContent = type === "once" ? "下发任务" : "创建定时任务";
    }

    function collectAbilityScheduleOptions() {
      const scheduleType = $("abilityScheduleType") ? $("abilityScheduleType").value : "once";
      const intervalMinutes = parseInt(($("abilityIntervalMinutes") && $("abilityIntervalMinutes").value) || "60", 10);
      const dailyTimes = collectAbilityDailyTimes();
      if (scheduleType === "daily_times" && !dailyTimes.length) throw new Error("请填写每天执行时间，例如 09:00");
      return {
        schedule_type: scheduleType,
        interval_seconds: Math.max(60, (Number.isNaN(intervalMinutes) ? 60 : intervalMinutes) * 60),
        start_at: $("abilityStartAt") ? $("abilityStartAt").value : "",
        daily_times: scheduleType === "daily_times" ? dailyTimes : [],
      };
    }

    function assetOrImagePayload(raw, fieldLabel) {
      const value = String(raw || "").trim();
      if (!value) throw new Error(`请填写${fieldLabel}`);
      if (/^https?:\/\//i.test(value)) return { image_url: value };
      return { asset_id: value };
    }

    function collectCapabilityPayload() {
      if (state.taskAbility === "ip_content_daily") {
        const templateId = parseInt(($("taskIpTemplate") && $("taskIpTemplate").value) || "0", 10);
        if (!templateId || Number.isNaN(templateId)) throw new Error("请选择 IP日更服务器模板");
        const tasks = selectedIpDailyTasks();
        if (!tasks.length) throw new Error("请选择至少一种生成内容");
        const extra = (($("taskIpRequirement") && $("taskIpRequirement").value) || "").trim();
        const requirements = extra ? { common: extra, oral: extra, moments: extra, image: extra } : {};
        return {
          template_id: templateId,
          tasks,
          sync_before: !!($("taskIpSyncBefore") && $("taskIpSyncBefore").checked),
          requirements,
          industry_count: 5,
          ip_count: 5,
          moments_count: 20,
        };
      }
      if (state.taskAbility === "hifly.video.create_by_tts") {
        const avatar = $("taskAvatar") ? $("taskAvatar").value : "";
        const voice = $("taskVoice") ? $("taskVoice").value : "";
        if (!avatar) throw new Error("请选择数字人");
        if (!voice) throw new Error("请选择声音");
        return { avatar, voice };
      }
      if (state.taskAbility === "goal.video.pipeline") {
        return collectGoalVideoPayloadFromFields({
          modeId: "taskVideoMode",
          assetId: "taskVideoAsset",
          memoryId: "taskVideoMemoryDocs",
          groupId: "taskCandidateGroup",
          promptId: "taskCreativePrompt",
        });
      }
      if (state.taskAbility === "goal.image.pipeline") {
        const publishPlatform = $("taskPublishPlatform") ? $("taskPublishPlatform").value : "";
        const publishAccountId = $("taskPublishAccount") ? $("taskPublishAccount").value : "";
        const autoPublish = !!($("taskPublishAuto") && $("taskPublishAuto").checked);
        let account = null;
        if (publishAccountId) {
          account = state.publishAccounts.find((row) => publishAccountSelectId(row) === String(publishAccountId)) || null;
        }
        const payload = {
          prompt: $("taskCreativePrompt") ? $("taskCreativePrompt").value.trim() : "",
        };
        if (publishPlatform || publishAccountId || autoPublish) {
          if (!publishPlatform) throw new Error("请选择发布平台");
          if (!publishAccountId) throw new Error("请选择发布账号");
          const parsedId = publishAccountLocalId(account);
          if (Number.isNaN(parsedId)) throw new Error("发布账号无效");
          payload.publish_platform = publishPlatform;
          payload.publish_platform_name = account ? (account.platform_name || platformDisplayName(publishPlatform)) : platformDisplayName(publishPlatform);
          payload.publish_account_id = parsedId;
          payload.publish_account_nickname = account ? (account.nickname || "") : "";
          payload.publish_installation_id = account ? (account.installation_id || currentInstallationId()) : currentInstallationId();
          payload.publish_auto = autoPublish;
        }
        return payload;
      }
      if (state.taskAbility === "wewrite.article.pipeline") {
        const idea = (($("taskArticleIdea") && $("taskArticleIdea").value) || "").trim();
        if (!idea) throw new Error("请填写公众号主题");
        const imageCount = parseInt(($("taskArticleImageCount") && $("taskArticleImageCount").value) || "3", 10);
        return {
          idea,
          style: (($("taskArticleStyle") && $("taskArticleStyle").value) || "").trim(),
          include_images: !!($("taskArticleIncludeImages") && $("taskArticleIncludeImages").checked),
          image_count: Number.isNaN(imageCount) ? 3 : Math.max(0, Math.min(6, imageCount)),
          image_aspect_ratio: "16:9",
        };
      }
      if (state.taskAbility === "ppt.create") {
        const topic = (($("taskPptTopic") && $("taskPptTopic").value) || "").trim();
        if (!topic) throw new Error("请填写 PPT 主题");
        const slideCount = parseInt(($("taskPptSlideCount") && $("taskPptSlideCount").value) || "10", 10);
        return {
          mode: (($("taskPptMode") && $("taskPptMode").value) || "ai"),
          topic,
          slide_count: Number.isNaN(slideCount) ? 10 : Math.max(1, Math.min(80, slideCount)),
          instructions: (($("taskPptInstructions") && $("taskPptInstructions").value) || "").trim(),
          language: "zh-CN",
        };
      }
      if (state.taskAbility === "create.video.pipeline") {
        const prompt = (($("taskCreateVideoPrompt") && $("taskCreateVideoPrompt").value) || "").trim();
        if (!prompt) throw new Error("请填写视频主题");
        const duration = parseInt(($("taskCreateVideoDuration") && $("taskCreateVideoDuration").value) || "8", 10);
        const sceneCount = parseInt(($("taskCreateVideoSceneCount") && $("taskCreateVideoSceneCount").value) || "1", 10);
        return {
          action: "start_pipeline",
          prompt,
          duration: Number.isNaN(duration) ? 8 : Math.max(3, Math.min(60, duration)),
          scene_count: Number.isNaN(sceneCount) ? 1 : Math.max(1, Math.min(6, sceneCount)),
          aspect_ratio: (($("taskCreateVideoAspect") && $("taskCreateVideoAspect").value) || "16:9"),
        };
      }
      if (state.taskAbility === "comfly.daihuo.pipeline") {
        const asset = assetOrImagePayload($("taskComflyAsset") && $("taskComflyAsset").value, "参考图片");
        const storyboardCount = parseInt(($("taskComflyStoryboardCount") && $("taskComflyStoryboardCount").value) || "5", 10);
        return {
          action: "start_pipeline",
          ...asset,
          task_text: (($("taskComflyText") && $("taskComflyText").value) || "").trim(),
          storyboard_count: Number.isNaN(storyboardCount) ? 5 : Math.max(1, Math.min(8, storyboardCount)),
          auto_save: !!($("taskComflyAutoSave") && $("taskComflyAutoSave").checked),
        };
      }
      if (state.taskAbility === "comfly.seedance.tvc.pipeline") {
        const asset = assetOrImagePayload($("taskSeedanceAsset") && $("taskSeedanceAsset").value, "参考图片");
        const totalDuration = parseInt(($("taskSeedanceDuration") && $("taskSeedanceDuration").value) || "20", 10);
        return {
          action: "start_pipeline",
          ...asset,
          task_text: (($("taskSeedanceText") && $("taskSeedanceText").value) || "").trim(),
          total_duration_seconds: Number.isNaN(totalDuration) ? 20 : totalDuration,
          aspect_ratio: (($("taskSeedanceAspect") && $("taskSeedanceAspect").value) || "9:16"),
          auto_save: true,
        };
      }
      if (state.taskAbility === "comfly.ecommerce.detail_pipeline") {
        const asset = assetOrImagePayload($("taskEcommerceAsset") && $("taskEcommerceAsset").value, "商品主图");
        const pageCount = parseInt(($("taskEcommercePageCount") && $("taskEcommercePageCount").value) || "12", 10);
        return {
          action: "start_pipeline",
          ...asset,
          task_text: (($("taskEcommerceText") && $("taskEcommerceText").value) || "").trim(),
          page_count: Number.isNaN(pageCount) ? 12 : Math.max(1, Math.min(20, pageCount)),
          auto_save: !!($("taskEcommerceAutoSave") && $("taskEcommerceAutoSave").checked),
        };
      }
      return {};
    }

    function setDouyinTaskInlineMessage(text, isError) {
      const el = $("douyinTaskInlineMsg");
      if (!el) return;
      el.textContent = text || "";
      el.classList.toggle("error", !!isError);
    }

    function addDouyinDailyTime(value = "") {
      const list = $("douyinTaskDailyTimesList");
      if (!list) return;
      const row = document.createElement("div");
      row.className = "task-daily-row";
      row.innerHTML = `<input type="time" step="60" data-douyin-daily-time value="${escapeHtml(value)}"><button class="ghost" type="button" aria-label="删除时间点">-</button>`;
      row.querySelector("button").addEventListener("click", () => row.remove());
      list.appendChild(row);
    }

    function collectDouyinDailyTimes() {
      return Array.from(document.querySelectorAll("[data-douyin-daily-time]"))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function updateDouyinScheduleFields() {
      const type = $("douyinTaskScheduleType") ? $("douyinTaskScheduleType").value : "once";
      if ($("douyinTaskIntervalBlock")) $("douyinTaskIntervalBlock").classList.toggle("hidden", type !== "interval");
      if ($("douyinTaskDailyTimesBlock")) $("douyinTaskDailyTimesBlock").classList.toggle("hidden", type !== "daily_times");
      if ($("douyinTaskStartAtBlock")) $("douyinTaskStartAtBlock").classList.toggle("hidden", type === "daily_times");
      if (type === "daily_times" && $("douyinTaskDailyTimesList") && !$("douyinTaskDailyTimesList").children.length) addDouyinDailyTime("09:00");
    }

    function normalizeDouyinRegionSelection() {
      const boxes = Array.from(document.querySelectorAll("[data-douyin-region]"));
      if (!boxes.length) return;
      const nationwide = boxes.find((el) => String(el.value || "") === "全国");
      const custom = boxes.filter((el) => String(el.value || "") !== "全国");
      if (!nationwide) return;
      const customChecked = custom.filter((el) => el.checked);
      if (customChecked.length) {
        nationwide.checked = false;
      } else if (!nationwide.checked) {
        nationwide.checked = true;
      }
    }

    function selectedDouyinRegions() {
      return Array.from(document.querySelectorAll("[data-douyin-region]:checked"))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function refreshDouyinRegionSummary() {
      const label = $("douyinRegionSummary");
      if (!label) return;
      const regions = selectedDouyinRegions();
      if (!regions.length) {
        label.textContent = "全国";
      } else if (regions.includes("全国")) {
        label.textContent = "全国";
      } else {
        label.textContent = regions.join("、");
      }
    }

    function setDouyinRegionPanelOpen(open) {
      const panel = $("douyinRegionPanel");
      const trigger = $("douyinRegionTrigger");
      if (!panel || !trigger) return;
      panel.classList.toggle("hidden", !open);
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      const icon = trigger.querySelector("span");
      if (icon) icon.textContent = open ? "▴" : "▾";
    }

    function renderDouyinTaskActions() {
      const host = $("douyinTaskActionGrid");
      if (!host) return;
      host.innerHTML = Object.entries(DOUYIN_TASK_ACTIONS).map(([key, item]) => `
        <button class="douyin-task-action ${state.douyinTaskAction === key ? "active" : ""}" type="button" data-douyin-task-action="${escapeHtml(key)}">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.description)}</span>
          <em>${state.douyinTaskAction === key ? "当前选择" : "点击切换"}</em>
        </button>
      `).join("");
      const label = $("douyinTaskActionLabel");
      if (label) label.textContent = (DOUYIN_TASK_ACTIONS[state.douyinTaskAction] || {}).description || "请先选择一个任务类型";
    }

    function renderDouyinTaskDetailFields() {
      const host = $("douyinTaskDetailFields");
      if (!host) return;
      const action = state.douyinTaskAction || "search_collect";
      const regionOptions = ["全国", "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "武汉", "西安"];
      let html = ``;
      if (action === "search_collect") {
        html += `
          <div class="douyin-field full">
            <label for="douyinTaskKeyword">行业关键词</label>
            <textarea id="douyinTaskKeyword" placeholder="例如：医美、口腔、装修、留学"></textarea>
          </div>
          <div class="douyin-field full">
            <label>采集地区</label>
            <div class="douyin-region-select">
              <button class="douyin-region-trigger" type="button" id="douyinRegionTrigger" aria-expanded="false">
                <strong id="douyinRegionSummary">全国</strong>
                <span>▾</span>
              </button>
              <div class="douyin-region-panel hidden" id="douyinRegionPanel">
                <div class="douyin-region-panel-head">
                  <div class="douyin-field-note">默认选中“全国”，表示不做地区过滤。</div>
                  <button class="ghost douyin-region-reset" type="button" id="douyinRegionResetBtn">恢复全国</button>
                </div>
                <div class="douyin-check-grid">
                  ${regionOptions.map((name, index) => `<label class="douyin-check-item"><input type="checkbox" data-douyin-region value="${escapeHtml(name)}" ${index === 0 ? "checked" : ""}><span>${escapeHtml(name)}</span></label>`).join("")}
                </div>
              </div>
            </div>
          </div>
          <div class="douyin-field full">
            <div class="douyin-field-note">筛选规则按照龙虾盒子上的设置执行。</div>
          </div>
        `;
      } else if (action === "comment_collect") {
        html += `
          <div class="douyin-field full">
            <label for="douyinTaskCommentMode">评论内容模式</label>
            <select id="douyinTaskCommentMode">
              <option value="fixed">固定评论</option>
              <option value="ai_generate">AI 合成</option>
              <option value="ai_rewrite">AI 同方向改编</option>
            </select>
          </div>
          <div class="douyin-field full">
            <label for="douyinTaskCommentText">评论内容</label>
            <textarea id="douyinTaskCommentText" placeholder="填写固定评论内容，或填写 AI 合成 / 同方向改编的参考要求"></textarea>
          </div>
        `;
      } else if (action === "interaction") {
        html += `
          <div class="douyin-field full">
            <label for="douyinTaskDmMode">私信内容模式</label>
            <select id="douyinTaskDmMode">
              <option value="fixed">固定私信</option>
              <option value="ai_generate">AI 合成</option>
              <option value="ai_rewrite">AI 同方向改编</option>
            </select>
          </div>
          <div class="douyin-field full">
            <label for="douyinTaskDmText">私信内容</label>
            <textarea id="douyinTaskDmText" placeholder="填写固定私信内容，或填写 AI 合成 / 同方向改编的参考要求"></textarea>
          </div>
        `;
      } else if (action === "tasks_from_search") {
        html += `
          <div class="douyin-field full">
            <label for="douyinTaskKeyword">行业关键词</label>
            <textarea id="douyinTaskKeyword" placeholder="例如：医美、装修、本地服务"></textarea>
          </div>
          <div class="douyin-field full">
            <label for="douyinTaskNotes">监控说明</label>
            <input id="douyinTaskNotes" type="text" placeholder="例如：优先盯高点赞同行账号">
          </div>
        `;
      }
      host.innerHTML = html;
      normalizeDouyinRegionSelection();
      refreshDouyinRegionSummary();
      setDouyinRegionPanelOpen(false);
    }

    function collectDouyinTaskPayload() {
      const action = state.douyinTaskAction || "search_collect";
      const keyword = (($("douyinTaskKeyword") || {}).value || "").trim();
      const commentText = (($("douyinTaskCommentText") || {}).value || "").trim();
      const dmText = (($("douyinTaskDmText") || {}).value || "").trim();
      const params = {};
      if (keyword) {
        params.keyword = keyword;
        params.query = keyword;
        params.search_keyword = keyword;
      }
      if (commentText) {
        params.comment_text = commentText;
        params.reply_text = commentText;
        params.comment_content = commentText;
      }
      if (dmText) {
        params.message = dmText;
        params.dm_text = dmText;
        params.private_message = dmText;
      }
      if (action === "search_collect") {
        if (!keyword) throw new Error("请先填写行业关键词");
        params.mode = "script";
        const regions = Array.from(document.querySelectorAll("[data-douyin-region]:checked")).map((el) => String(el.value || "").trim()).filter(Boolean);
        params.region_mode = regions.includes("全国") ? "nationwide" : "custom";
        params.regions = regions;
        params.region_list = params.regions;
        params.area_list = params.regions;
        if (!regions.length) throw new Error("请至少选择一个地区");
      } else if (action === "comment_collect") {
        const commentMode = (($("douyinTaskCommentMode") || {}).value || "fixed").trim();
        params.comment_mode = commentMode;
        params.content_mode = commentMode;
        if (!commentText) throw new Error("请先填写评论内容或 AI 生成要求");
      } else if (action === "interaction") {
        const dmMode = (($("douyinTaskDmMode") || {}).value || "fixed").trim();
        params.dm_mode = dmMode;
        params.private_message_mode = dmMode;
        if (!dmText) throw new Error("请先填写私信发送内容");
      } else if (action === "tasks_from_search") {
        params.notes = (($("douyinTaskNotes") || {}).value || "").trim();
        if (!keyword) throw new Error("请先填写需要监控的行业关键词");
      }
      return { action, params };
    }

    function renderDouyinStatus() {
      const data = state.douyinStatus || {};
      const accounts = Array.isArray(data.accounts) ? data.accounts : [];
      const metrics = data.metrics && typeof data.metrics === "object" ? data.metrics : {};
      const runtime = data.runtime && typeof data.runtime === "object" ? data.runtime : {};
      const accountList = $("douyinAccountList");
      const runtimeList = $("douyinRuntimeList");
      const metricGrid = $("douyinMetricGrid");
      const accountHint = $("douyinLeadsAccountHint");
      const runtimeHint = $("douyinRuntimeHint");
      const normalizedAccounts = accounts.map((row) => {
        const accountId = row && row.account_id != null ? row.account_id : row && row.id != null ? row.id : "";
        const nickname = String((row && (row.nickname || row.name)) || "").trim();
        const installationId = String((row && row.installation_id) || "").trim();
        const statusText = String((row && row.status) || "").trim().toLowerCase();
        const online = row && Object.prototype.hasOwnProperty.call(row, "online")
          ? !!row.online
          : statusText === "online" || statusText === "active";
        return {
          accountId,
          nickname,
          installationId,
          statusText,
          online,
          lastLogin: String((row && row.last_login) || "").trim(),
        };
      });
      const onlineCount = normalizedAccounts.filter((row) => row.online).length;
      const preferredAccountId = normalizedAccounts.find((row) => row.online)?.accountId
        || normalizedAccounts[0]?.accountId
        || "";
      if (accountHint) accountHint.textContent = normalizedAccounts.length ? `在线 ${onlineCount} / ${normalizedAccounts.length}` : "暂无账号";
      if (accountList) {
        if (!normalizedAccounts.length) {
          accountList.innerHTML = '<div class="douyin-empty">当前还没有检测到抖音账号在线。等本机登录抖音后，这里会展示账号状态。</div>';
        } else {
          accountList.innerHTML = normalizedAccounts.slice(0, 1).map((row) => {
            const active = String(row.accountId || "") === String(preferredAccountId || "");
            const title = row.nickname || `账号 ${row.accountId || "-"}`;
            const sublineParts = [];
            if (row.accountId !== "") sublineParts.push(`账号 ID ${escapeHtml(String(row.accountId))}`);
            if (row.installationId) sublineParts.push(`设备 ${escapeHtml(row.installationId.slice(0, 8))}`);
            if (row.lastLogin) sublineParts.push(`登录 ${escapeHtml(row.lastLogin.replace("T", " ").slice(0, 16))}`);
            return `<div class="douyin-account-item concise">
              <div>
                <strong>${active ? "当前账号" : "抖音账号"} ${escapeHtml(title)}</strong>
                <span>${sublineParts.join(" · ") || "等待账号状态上报"}</span>
              </div>
              <span class="douyin-badge ${row.online ? "success" : "warn"}">${row.online ? "在线" : "离线"}</span>
            </div>`;
          }).join("");
        }
      }
      const commentMessage = String(runtime.comment_message || "").trim();
      const interactionMessage = String(runtime.interaction_message || "").trim();
      const monitorMessage = String(runtime.monitor_message || "").trim();
      const runtimeRows = [
        { label: "评论采集", active: !!commentMessage, text: commentMessage || "当前没有运行中的评论任务" },
        { label: "私信互动", active: !!interactionMessage, text: interactionMessage || "当前没有运行中的私信任务" },
        { label: "同行监控", active: !!monitorMessage, text: monitorMessage || "当前没有额外的监控提示" },
      ];
      if (runtimeHint) runtimeHint.textContent = runtimeRows.some((row) => row.active) ? "有任务在跑" : "当前空闲";
      if (runtimeList) {
        runtimeList.innerHTML = runtimeRows.map((row) => `<div class="douyin-runtime-item concise ${row.active ? "active" : "pending"}"><div><strong>${escapeHtml(row.label)}</strong></div><span class="douyin-badge ${row.active ? "success" : "warn"}">${row.active ? "进行中" : "待命"}</span></div>`).join("");
      }
      const metricItems = [
        ["已采集视频数量", metrics.collected_videos || 0],
        ["全部客户数据", metrics.all_customers || 0],
        ["精准客户数据", metrics.precise_customers || 0],
        ["已发送评论的视频数量", metrics.commented_videos || 0],
        ["已发送私信的用户数量", metrics.private_messages_sent || metrics.dm_users || 0],
        ["监控同行任务数", metrics.monitor_tasks || 0],
        ["今日新增客户数", metrics.today_new_customers || 0],
        ["今日执行任务数", metrics.today_task_runs || 0],
      ];
      if (metricGrid) {
        metricGrid.innerHTML = metricItems.map(([label, value]) => `<div class="douyin-metric-card"><strong>${escapeHtml(compactNumber(value))}</strong><span>${escapeHtml(label)}</span></div>`).join("");
      }
    }

    async function loadDouyinStatus() {
      const accountList = $("douyinAccountList");
      const runtimeList = $("douyinRuntimeList");
      const metricGrid = $("douyinMetricGrid");
      if (accountList) accountList.innerHTML = '<div class="douyin-empty">正在读取抖音账号状态...</div>';
      if (runtimeList) runtimeList.innerHTML = '<div class="douyin-empty">正在整理当前运行状态...</div>';
      if (metricGrid) metricGrid.innerHTML = '<div class="douyin-empty">正在加载核心指标...</div>';
      try {
        const data = await api('/api/douyin/dashboard-status');
        state.douyinStatus = data || {};
      } catch (err) {
        state.douyinStatus = {
          accounts: [],
          metrics: {
            collected_videos: 0,
            all_customers: 0,
            precise_customers: 0,
            commented_videos: 0,
            private_messages_sent: 0,
            monitor_tasks: 0,
            today_new_customers: 0,
            today_task_runs: 0,
          },
          runtime: {
            comment_message: "",
            interaction_message: "",
            monitor_message: err.message || "暂时还拿不到运行状态，先展示占位数据",
          },
        };
      }
      renderDouyinStatus();
    }

    function resetDouyinTaskForm() {
      if ($("douyinTaskTitle")) $("douyinTaskTitle").value = "";
      if ($("douyinTaskKeyword")) $("douyinTaskKeyword").value = "";
      if ($("douyinTaskCommentText")) $("douyinTaskCommentText").value = "";
      if ($("douyinTaskDmText")) $("douyinTaskDmText").value = "";
      if ($("douyinTaskLimit")) $("douyinTaskLimit").value = "20";
      if ($("douyinTaskAccountId")) $("douyinTaskAccountId").value = "";
      if ($("douyinTaskScheduleType")) $("douyinTaskScheduleType").value = "once";
      if ($("douyinTaskIntervalMinutes")) $("douyinTaskIntervalMinutes").value = "60";
      if ($("douyinTaskStartAt")) $("douyinTaskStartAt").value = "";
      const list = $("douyinTaskDailyTimesList");
      if (list) list.innerHTML = "";
      updateDouyinScheduleFields();
      setDouyinTaskInlineMessage("任务表单已重置，可以重新选择类型后填写。", false);
    }

    async function submitDouyinTask() {
      const installationId = currentInstallationId();
      if (!installationId) throw new Error("暂未检测到在线设备，请先让本机 online 端保持登录");
      const douyinPayload = collectDouyinTaskPayload();
      const scheduleType = (("douyinTaskScheduleType" && $("douyinTaskScheduleType")) ? $("douyinTaskScheduleType").value : "once") || "once";
      const dailyTimes = collectDouyinDailyTimes();
      if (scheduleType === "daily_times" && !dailyTimes.length) throw new Error("请至少添加一个每天执行时间");
      const interval = parseInt((($("douyinTaskIntervalMinutes") || {}).value || "60"), 10);
      const actionInfo = DOUYIN_TASK_ACTIONS[douyinPayload.action] || {};
      const title = (($("douyinTaskTitle") || {}).value || "").trim() || `抖音获客 - ${actionInfo.label || douyinPayload.action}`;
      const body = {
        title,
        task_kind: "douyin_leads",
        content: `执行抖音获客任务：${douyinPayload.action}`,
        payload: douyinPayload,
        schedule_type: scheduleType,
        interval_seconds: Math.max(60, (Number.isNaN(interval) ? 60 : interval) * 60),
        start_at: ($("douyinTaskStartAt") || {}).value || "",
        daily_times: scheduleType === "daily_times" ? dailyTimes : [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: [installationId],
      };
      setDouyinTaskInlineMessage("正在下发抖音获客任务...", false);
      const data = await api('/api/scheduled-tasks/tasks', {
        method: 'POST',
        json: body,
        headers: { 'X-Installation-Id': installationId },
      });
      setDouyinTaskInlineMessage("任务已经下发成功，稍后会在执行记录里看到进度。", false);
      showTaskSuccessDialog("抖音获客任务已下发，可在工作历史查看进度。");
      await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      return data;
    }

    async function submitVoiceDouyinTask(voicePayload) {
      const installationId = currentInstallationId();
      if (!installationId) throw new Error("暂未检测到在线设备，请先让本机 online 端保持登录");
      const actionInfo = DOUYIN_TASK_ACTIONS[voicePayload.action] || {};
      const body = {
        title: `抖音获客 - ${actionInfo.label || voicePayload.action}`,
        task_kind: "douyin_leads",
        content: `执行抖音获客任务：${voicePayload.action}`,
        payload: voicePayload,
        schedule_type: "once",
        interval_seconds: 60,
        start_at: "",
        daily_times: [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: [installationId],
      };
      const data = await api('/api/scheduled-tasks/tasks', {
        method: 'POST',
        json: body,
        headers: { 'X-Installation-Id': installationId },
      });
      await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      return data;
    }

    async function submitVoiceCapabilityTask(capabilityId, payload, title) {
      const installationId = currentInstallationId();
      if (!installationId) throw new Error("暂未检测到在线设备，请先让本机 online 端保持登录");
      const body = {
        title: title || capabilityName(capabilityId),
        task_kind: "capability",
        content: `语音下发能力 ${capabilityId}`,
        payload: { capability_id: capabilityId, payload: payload || {} },
        schedule_type: "once",
        interval_seconds: 60,
        start_at: "",
        daily_times: [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: [installationId],
      };
      const data = await api("/api/scheduled-tasks/tasks", {
        method: "POST",
        json: body,
        headers: { "X-Installation-Id": installationId },
      });
      await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      return data;
    }

    async function executeVoicePlan() {
      const plan = buildVoiceExecutionPlan();
      if (!plan) {
        toast("请先录入一段语音内容");
        return;
      }
      if (!plan.direct) {
        routeVoicePlanToRefine(plan);
        return;
      }
      if (plan.kind === "image_generate") {
        await submitVoiceCapabilityTask("goal.image.pipeline", plan.payload, "语音图片任务");
        showTaskSuccessDialog("图片任务已下发，可在工作历史查看进度。");
        return;
      }
      if (plan.kind === "creative_storyboard_video") {
        await submitVoiceCapabilityTask("goal.video.pipeline", plan.payload, "语音创意分镜任务");
        showTaskSuccessDialog("创意分镜任务已下发，可在工作历史查看进度。");
        return;
      }
      if (plan.kind === "video_generate") {
        await submitVoiceCapabilityTask("goal.video.pipeline", plan.payload, "语音视频任务");
        showTaskSuccessDialog("视频任务已下发，可在工作历史查看进度。");
        return;
      }
      if (plan.kind === "douyin_leads_task") {
        await submitVoiceDouyinTask(plan.payload);
        showTaskSuccessDialog("抖音获客任务已下发，可在工作历史查看进度。");
        return;
      }
      sendVoiceDraftToMessages(true, "", plan.content);
    }

    function routeVoicePlanToRefine(plan) {
      if (!plan) return;
      if (plan.kind === "douyin_leads_task") {
        const payload = plan.payload && typeof plan.payload === "object" ? plan.payload : null;
        state.douyinTaskAction = normalizeVoiceDouyinAction(payload && payload.action);
        switchTab("douyinLeadsSchedule");
        renderDouyinTaskActions();
        renderDouyinTaskDetailFields();
        setTimeout(() => {
          if (!payload || !payload.params) return;
          if ($("douyinTaskKeyword")) $("douyinTaskKeyword").value = payload.params.keyword || "";
          if ($("douyinTaskCommentText")) $("douyinTaskCommentText").value = payload.params.comment_text || "";
          if ($("douyinTaskDmText")) $("douyinTaskDmText").value = payload.params.dm_text || payload.params.message || "";
          if ($("douyinTaskCommentMode")) $("douyinTaskCommentMode").value = payload.params.comment_mode || "fixed";
          if ($("douyinTaskDmMode")) $("douyinTaskDmMode").value = payload.params.dm_mode || "fixed";
        }, 80);
        toast("请补充抖音获客参数后下发");
        return;
      }
      switchTab("messages");
      setMessageTemplate(plan.content || state.voiceDraft || "");
      toast("已带入消息页，你可以继续补充要求");
    }

    function shouldRouteVoiceActionThroughPlan(kind, plan) {
      if (kind !== "submit_message" || !plan) return false;
      return ["image_generate", "video_generate", "creative_storyboard_video", "douyin_leads_task"].includes(String(plan.kind || "").trim());
    }

    async function createScheduledTask() {
      const btn = $("createTaskBtn");
      const installationId = currentInstallationId();
      if (!isScheduledTaskAbility(state.taskAbility)) {
        toast("这个技能暂不支持定时下发，请在下方岗位工作入口发起");
        renderTaskAbilityBoard();
        return;
      }
      if (state.taskSkillsLoaded && !taskAbilityVisible(state.taskAbility)) {
        toast("当前账号没有这个技能的安排权限");
        renderTaskAbilityBoard();
        return;
      }
      if (state.taskAbility !== "ip_content_daily" && !installationId) {
        toast("未检测到本地 online 设备");
        return;
      }
      const capPayload = collectCapabilityPayload();
      const scheduleType = $("taskScheduleType").value || "once";
      const dailyTimes = collectDailyTimes();
      if (scheduleType === "daily_times" && !dailyTimes.length) throw new Error("请填写每天执行时间，例如 09:00、12:00、18:00");
      const interval = parseInt($("taskIntervalMinutes").value || "60", 10);
      const title = ($("taskTitle").value || "").trim() || capabilityName(state.taskAbility);
      const isIpDaily = state.taskAbility === "ip_content_daily";
      const body = {
        title,
        task_kind: isIpDaily ? "ip_content_daily" : "capability",
        content: isIpDaily ? "定时生成 IP日更文案" : `定时调用能力 ${state.taskAbility}`,
        payload: isIpDaily ? capPayload : { capability_id: state.taskAbility, payload: capPayload },
        schedule_type: scheduleType,
        interval_seconds: Math.max(60, (Number.isNaN(interval) ? 60 : interval) * 60),
        start_at: $("taskStartAt") ? $("taskStartAt").value : "",
        daily_times: scheduleType === "daily_times" ? dailyTimes : [],
        timezone_offset_minutes: timezoneOffsetMinutes(),
        installation_ids: isIpDaily ? [] : [installationId],
      };
      const startAt = $("taskStartAt") ? $("taskStartAt").value : "";
      const shouldOptimisticRun = scheduleType !== "daily_times" && !startAt;
      const optimisticRunId = shouldOptimisticRun ? addOptimisticRun(body, title, isIpDaily) : "";
      btn.disabled = true;
      try {
        const data = await api("/api/scheduled-tasks/tasks", {
          method: "POST",
          json: body,
          headers: installationId ? { "X-Installation-Id": installationId } : {},
        });
        removeRun(optimisticRunId);
        if (data.task) {
          state.tasks = [data.task].concat((state.tasks || []).filter((row) => String(row.id) !== String(data.task.id)));
        }
        if (Array.isArray(data.runs)) mergeRuns(data.runs);
        renderOfficeEmployees();
        renderWorkList();
        showTaskSuccessDialog("任务已下发成功，可在工作历史查看进度。");
        setTaskPanelOpen(false);
        await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
      } catch (err) {
        removeRun(optimisticRunId);
        renderOfficeEmployees();
        renderWorkList();
        toast(err.message || "下发失败");
      } finally {
        btn.disabled = false;
      }
    }

    async function loadTasks(options = {}) {
      const reset = options.reset !== false;
      const append = !!options.append;
      const pageSize = Math.max(1, Math.min(100, parseInt(options.limit || "10", 10) || 10));
      const box = $("taskList");
      if (!state.token) return;
      if (reset) {
        state.taskListOffset = 0;
        state.taskListHasNext = false;
      }
      const offset = append ? state.taskListOffset : 0;
      if (box && !append) box.innerHTML = `<div class="hint">加载中...</div>`;
      try {
        const data = await api(`/api/scheduled-tasks/tasks?limit=${pageSize}&offset=${offset}`);
        const rows = Array.isArray(data.tasks) ? data.tasks : [];
        const pagination = data.pagination || {};
        state.taskListOffset = offset + rows.length;
        state.taskListHasNext = !!pagination.has_next;
        state.tasks = append ? (state.tasks || []).concat(rows) : rows;
        renderWorkList();
        if (document.querySelector("#departmentView.active")) renderDepartmentDayBoard();
        if (document.querySelector("#workflowView.active")) renderWorkflowDayBoard();
        if (!box) return;
        const allRows = state.tasks || [];
        if (!allRows.length) {
          box.innerHTML = `<div class="hint">暂无定时任务。</div>`;
          $("loadMoreTasksBtn")?.classList.add("hidden");
          return;
        }
        box.innerHTML = allRows.map((row) => {
          const interval = row.schedule_label || (row.schedule_type === "daily_times"
            ? `每天 ${(row.schedule_config && Array.isArray(row.schedule_config.daily_times) ? row.schedule_config.daily_times.join("、") : "")}`
            : (row.schedule_type === "interval" ? `每 ${Math.round((row.interval_seconds || 0) / 60)} 分钟` : "一次性"));
          return `<div class="task-row">
            <div class="task-row-main">
              <div class="task-row-title">${escapeHtml(row.title || capabilityName(taskCapabilityId(row)))}</div>
              <div class="task-row-meta">${escapeHtml(capabilityName(taskCapabilityId(row)))} · ${escapeHtml(interval)} · ${escapeHtml(statusText(row.status))}</div>
            </div>
            <div class="task-row-actions">
              <button class="ghost" type="button" data-open-task-detail="${escapeHtml(row.id)}">详情</button>
              ${taskActionMenuHtml(row)}
            </div>
          </div>`;
        }).join("");
        $("loadMoreTasksBtn")?.classList.toggle("hidden", !state.taskListHasNext);
      } catch (err) {
        state.tasks = [];
        state.taskListHasNext = false;
        renderWorkList();
        if (document.querySelector("#departmentView.active")) renderDepartmentDayBoard();
        if (document.querySelector("#workflowView.active")) renderWorkflowDayBoard();
        if (box) box.innerHTML = `<div class="hint">${escapeHtml(err.message || "定时任务加载失败")}</div>`;
        $("loadMoreTasksBtn")?.classList.add("hidden");
      }
    }

    async function deleteTask(taskId, btn) {
      if (!taskId) return;
      if (!confirm("删除前会先停止任务，并取消未完成的执行记录。确认删除？")) return;
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "删除中...";
      }
      try {
        await api(`/api/scheduled-tasks/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
        toast("已停止并删除任务");
        await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true })]);
        if (document.querySelector("#taskDetailView.active")) switchTab("taskList");
      } catch (err) {
        toast(err.message || "删除失败");
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "删除";
        }
      }
    }

    async function setTaskStatus(taskId, nextStatus, btn) {
      if (!taskId || !nextStatus) return;
      const oldText = btn ? btn.textContent : "";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "保存中";
      }
      try {
        const data = await api(`/api/scheduled-tasks/tasks/${encodeURIComponent(taskId)}`, {
          method: "PATCH",
          json: { status: nextStatus },
        });
        if (data.task) {
          state.tasks = (state.tasks || []).map((row) => String(row.id) === String(taskId) ? data.task : row);
        }
        toast(nextStatus === "active" ? "任务已启用" : "任务已暂停");
        await loadTasks({ reset: true });
        if (document.querySelector("#taskDetailView.active")) openTaskDetail(taskId, state.taskDetailBackTab || "taskList");
      } catch (err) {
        toast(err.message || "状态更新失败");
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText;
        }
      }
    }

    function taskScheduleConfig(task) {
      return task && task.schedule_config && typeof task.schedule_config === "object" ? task.schedule_config : {};
    }

    function updateTaskEditScheduleFields() {
      const type = $("taskEditScheduleType") ? $("taskEditScheduleType").value : "once";
      $("taskEditIntervalBlock")?.classList.toggle("hidden", type !== "interval");
      $("taskEditStartAtBlock")?.classList.toggle("hidden", type === "daily_times");
      $("taskEditDailyTimesBlock")?.classList.toggle("hidden", type !== "daily_times");
      if (type === "daily_times" && $("taskEditDailyTimesList") && !$("taskEditDailyTimesList").children.length) addTaskEditDailyTime("09:00");
    }

    function collectTaskEditDailyTimes() {
      return Array.from(document.querySelectorAll("[data-task-edit-daily-time]"))
        .map((el) => String(el.value || "").trim())
        .filter(Boolean);
    }

    function addTaskEditDailyTime(value = "") {
      const list = $("taskEditDailyTimesList");
      if (!list) return;
      const row = document.createElement("div");
      row.className = "task-daily-row";
      row.innerHTML = `<input type="time" step="60" data-task-edit-daily-time value="${escapeHtml(value)}"><button class="ghost" type="button" aria-label="删除时间点">-</button>`;
      row.querySelector("button").addEventListener("click", () => row.remove());
      list.appendChild(row);
    }

    function openTaskEditModal(taskId) {
      const task = (state.tasks || []).find((row) => String(row.id) === String(taskId));
      if (!task) {
        toast("任务不存在，请刷新后再试");
        return;
      }
      state.taskEditId = String(task.id);
      const cfg = taskScheduleConfig(task);
      $("taskEditName").value = task.title || "";
      $("taskEditScheduleType").value = task.schedule_type || "once";
      $("taskEditIntervalMinutes").value = Math.max(1, Math.round((task.interval_seconds || 3600) / 60));
      $("taskEditStartAt").value = cfg.start_at ? String(cfg.start_at).slice(0, 16) : datetimeLocalValue(task.next_run_at);
      const list = $("taskEditDailyTimesList");
      if (list) list.innerHTML = "";
      const dailyTimes = Array.isArray(cfg.daily_times) ? cfg.daily_times : [];
      dailyTimes.forEach((value) => addTaskEditDailyTime(value));
      updateTaskEditScheduleFields();
      $("taskEditModal")?.classList.remove("hidden");
    }

    function closeTaskEditModal() {
      state.taskEditId = "";
      $("taskEditModal")?.classList.add("hidden");
    }

    async function submitTaskEdit() {
      const taskId = state.taskEditId;
      if (!taskId) return;
      const type = $("taskEditScheduleType").value || "once";
      const dailyTimes = collectTaskEditDailyTimes();
      if (type === "daily_times" && !dailyTimes.length) throw new Error("请填写每天执行时间");
      const intervalMinutes = parseInt($("taskEditIntervalMinutes").value || "60", 10);
      const btn = $("taskEditSubmit");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "保存中";
      }
      try {
        const data = await api(`/api/scheduled-tasks/tasks/${encodeURIComponent(taskId)}`, {
          method: "PATCH",
          json: {
            title: ($("taskEditName").value || "").trim(),
            schedule_type: type,
            interval_seconds: Math.max(60, (Number.isNaN(intervalMinutes) ? 60 : intervalMinutes) * 60),
            start_at: type === "daily_times" ? "" : ($("taskEditStartAt").value || ""),
            daily_times: type === "daily_times" ? dailyTimes : [],
            timezone_offset_minutes: timezoneOffsetMinutes(),
          },
        });
        if (data.task) {
          state.tasks = (state.tasks || []).map((row) => String(row.id) === String(taskId) ? data.task : row);
        }
        closeTaskEditModal();
        toast("定时任务已保存");
        await loadTasks({ reset: true });
        if (document.querySelector("#taskDetailView.active")) openTaskDetail(taskId, state.taskDetailBackTab || "taskList");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "保存";
        }
      }
    }

    async function loadWorkbenchJobs(options = {}) {
      if (!state.token) return;
      const limit = Math.max(1, Math.min(100, parseInt(options.limit || "80", 10) || 80));
      const [social, linkedin, wechat] = await Promise.allSettled([
        api(`/api/social-leads/jobs?limit=${limit}&offset=0`),
        api(`/api/linkedin-mining/jobs?limit=${limit}&offset=0`),
        api(`/api/wechat-channels-transcript/jobs?limit=${limit}`),
      ]);
      if (social.status === "fulfilled") {
        state.socialLeadJobs = Array.isArray(social.value.items) ? social.value.items : [];
      }
      if (linkedin.status === "fulfilled") {
        state.linkedinJobs = Array.isArray(linkedin.value.items) ? linkedin.value.items : [];
      }
      if (wechat.status === "fulfilled") {
        state.wechatTranscriptJobs = Array.isArray(wechat.value.items) ? wechat.value.items : [];
      }
      renderWorkList();
    }

    function taskPageHtml(task) {
      if (!task || !task.id) return `<div class="hint">任务不存在。</div>`;
      const interval = task.schedule_label || (task.schedule_type === "daily_times"
        ? `每天 ${((task.schedule_config && task.schedule_config.daily_times) || []).join("、")}`
        : (task.schedule_type === "interval" ? `每 ${Math.round((task.interval_seconds || 0) / 60)} 分钟` : "一次性"));
      const rows = [
        ["执行方式", interval],
        ["状态", statusText(task.status)],
        ["下次执行", task.next_run_at ? fmtTime(task.next_run_at) : "无"],
        ["最近执行", task.last_run_at ? fmtTime(task.last_run_at) : "无"],
        ["执行次数", compactNumber(task.run_count || 0)],
        ["创建时间", fmtTime(task.created_at)],
        ["最后错误", task.last_error || ""],
      ].filter((row) => String(row[1] == null ? "" : row[1]).trim() !== "");
      return `<div class="task-detail-section">
        <h4>${escapeHtml(task.title || "定时任务")}</h4>
        ${rows.map(([label, value]) => `<div class="task-detail-record"><strong>${escapeHtml(label)}</strong><pre>${escapeHtml(String(value))}</pre></div>`).join("")}
      </div>
      <div class="run-publish-actions task-detail-actions">
        ${taskActionMenuHtml(task)}
      </div>`;
    }

    function openTaskDetail(taskId, backTab = "taskList") {
      const task = (state.tasks || []).find((row) => String(row.id || "") === String(taskId)) || null;
      state.taskDetailBackTab = backTab || "taskList";
      $("taskPageTitle").textContent = task ? (task.title || "定时任务详情") : "定时任务详情";
      const body = $("taskPageBody");
      body.innerHTML = taskPageHtml(task);
      switchTab("taskDetail");
    }

    function renderUploads() {
      const box = $("uploadPreview");
      if (!box) return;
      box.innerHTML = "";
      box.classList.toggle("show", state.uploads.length > 0);
      state.uploads.forEach((item, idx) => {
        const card = document.createElement("div");
        card.className = `upload-card ${item.status === "failed" ? "failed" : ""}`;
        const img = document.createElement("img");
        img.src = item.previewUrl || item.url || "";
        img.alt = item.name || "上传图片";
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "upload-remove";
        rm.textContent = "x";
        rm.addEventListener("click", () => {
          const old = state.uploads[idx];
          if (old && old.previewUrl) {
            try { URL.revokeObjectURL(old.previewUrl); } catch {}
          }
          state.uploads.splice(idx, 1);
          renderUploads();
        });
        const status = document.createElement("div");
        status.className = "upload-status";
        status.textContent = item.status === "uploading" ? "上传中..." : (item.status === "failed" ? (item.error || "上传失败") : "已上传");
        card.appendChild(img);
        card.appendChild(rm);
        card.appendChild(status);
        box.appendChild(card);
      });
    }

    async function uploadImageFile(file) {
      const item = { name: file.name || "图片", previewUrl: URL.createObjectURL(file), url: "", status: "uploading", error: "" };
      state.uploads.push(item);
      renderUploads();
      const fd = new FormData();
      fd.append("file", file);
      try {
        const resp = await fetch(apiUrl("/api/h5-chat/uploads"), { method: "POST", headers: authHeaders(), body: fd });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || "上传失败");
        item.url = data.url || "";
        item.status = item.url ? "ready" : "failed";
        if (!item.url) item.error = "缺少图片链接";
      } catch (err) {
        item.status = "failed";
        item.error = err.message || "上传失败";
      } finally {
        renderUploads();
      }
    }

    function hasUploadingImages() {
      return state.uploads.some((item) => item.status === "uploading");
    }

    function readyImageUrls() {
      return state.uploads.filter((item) => item.status === "ready" && item.url).map((item) => item.url);
    }

    function clearUploads() {
      state.uploads.forEach((item) => {
        if (item.previewUrl) {
          try { URL.revokeObjectURL(item.previewUrl); } catch {}
        }
      });
      state.uploads = [];
      renderUploads();
    }

    function buildMessageContent(raw) {
      const content = `${(raw || "").trim()}${chatContextMarker()}`;
      const urls = readyImageUrls();
      if (!urls.length) return content;
      return `${content}\n\n【用户已上传图片】\n${urls.map((url, idx) => `${idx + 1}. ${url}`).join("\n")}\n\n请在本轮任务中使用以上图片链接作为参考素材；如果用户要求图生视频，请直接基于图片生成视频，不要只写文案。`;
    }

    function normalizeMarkdownLinks(text) {
      return String(text || "")
        .replace(/`(https?:\/\/[^`\s]+)`/g, "$1")
        .replace(/\[([^\]\n]{1,120})\]\s*\n\s*\((https?:\/\/[^\s)]+)\)/g, "[$1]($2)");
    }

    function mediaProxyUrl(url, disposition, filename) {
      const params = new URLSearchParams({
        url,
        disposition: disposition || "inline",
        filename: filename || filenameFromUrl(url, "lobster-media"),
        token: state.token || "",
      });
      return apiUrl(`/api/h5-chat/media?${params.toString()}`);
    }

    function linkAnchor(url, label) {
      if (!/^https?:\/\//i.test(url || "")) return escapeHtml(label || url || "");
      const href = mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-media"));
      return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label || url)}</a>`;
    }

    function linkifyText(text) {
      let value = normalizeMarkdownLinks(text);
      const links = [];
      function stash(anchor) {
        const token = `LOBSTERH5LINK${links.length}TOKEN`;
        links.push({ token, anchor });
        return token;
      }
      value = value.replace(/\[([^\]\n]{1,120})\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => stash(linkAnchor(url, label)));
      value = value.replace(/https?:\/\/[^\s<>"'`]+/g, (raw) => {
        let url = raw;
        let suffix = "";
        while (/[)\].,!?，。！？、；：]$/.test(url)) {
          suffix = url.slice(-1) + suffix;
          url = url.slice(0, -1);
        }
        return stash(linkAnchor(url, url)) + suffix;
      });
      let html = escapeHtml(value);
      for (const item of links) html = html.replaceAll(item.token, item.anchor);
      return html;
    }

    function ensureBubbleTextElement(bubble) {
      if (!bubble) return null;
      if (bubble._textEl && bubble._textEl.isConnected) return bubble._textEl;
      const textEl = document.createElement("span");
      textEl.className = "bubble-text";
      if (bubble.firstChild && bubble.firstChild.nodeType === Node.TEXT_NODE) {
        bubble._rawText = bubble.firstChild.nodeValue || bubble._rawText || "";
        bubble.removeChild(bubble.firstChild);
      }
      bubble.insertBefore(textEl, bubble.firstChild || null);
      bubble._textEl = textEl;
      return textEl;
    }

    function renderBubbleText(bubble) {
      const textEl = ensureBubbleTextElement(bubble);
      if (!textEl) return;
      textEl.innerHTML = linkifyText(bubble._rawText || "");
    }

    function collectMediaUrls(payload) {
      const out = [];
      const seen = new Set();
      const savedOut = [];
      const savedSeen = new Set();
      function add(value) {
        if (Array.isArray(value)) { value.forEach(add); return; }
        if (value && typeof value === "object") { Object.values(value).forEach(add); return; }
        const matches = String(value || "").match(/https?:\/\/[^\s<>"'`]+/gi) || [];
        matches.forEach((raw) => {
          let url = raw;
          while (/[)\].,!?，。！？、；：]$/.test(url)) url = url.slice(0, -1);
          if (!url || seen.has(url)) return;
          seen.add(url);
          out.push(url);
        });
      }
      function addSaved(value) {
        if (Array.isArray(value)) { value.forEach(addSaved); return; }
        if (!value || typeof value !== "object") return;
        const url = String(value.url || value.source_url || value.public_url || "").trim();
        if (url && /^https?:\/\//i.test(url) && !savedSeen.has(url)) {
          savedSeen.add(url);
          savedOut.push(url);
        }
      }
      const refs = payload && payload.result_refs && typeof payload.result_refs === "object" ? payload.result_refs : {};
      addSaved(payload && payload.saved_assets);
      addSaved(refs.saved_assets);
      if (savedOut.length) return savedOut.slice(0, 6);
      if (Array.isArray(payload && payload.media_urls) || Array.isArray(refs.urls)) {
        add(payload && payload.media_urls);
        if (!out.length) add(refs.urls);
      } else {
        add(payload);
      }
      return out.slice(0, 6);
    }

    function filenameFromUrl(url, fallback) {
      try {
        const parsed = new URL(url);
        const name = decodeURIComponent(parsed.pathname.split("/").pop() || "");
        return name || fallback;
      } catch {
        const clean = String(url || "").split(/[?#]/)[0].split("/").pop() || "";
        return clean || fallback;
      }
    }

    function mediaActions(url, downloadLabel, fallbackName) {
      const actions = document.createElement("div");
      actions.className = "media-actions";
      const filename = filenameFromUrl(url, fallbackName);
      const open = document.createElement("a");
      open.href = mediaProxyUrl(url, "inline", filename);
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      open.textContent = "打开";
      actions.appendChild(open);
      if (IS_WECHAT) {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.textContent = "复制链接";
        copy.addEventListener("click", () => copyMediaLink(url));
        actions.appendChild(copy);
      } else {
        const download = document.createElement("a");
        download.href = mediaProxyUrl(url, "attachment", filename);
        download.target = "_blank";
        download.rel = "noopener noreferrer";
        download.download = filename;
        download.textContent = IS_IOS ? "下载到文件" : downloadLabel;
        download.addEventListener("click", onIosDownloadClick);
        actions.appendChild(download);
      }
      return actions;
    }

    function mediaActionHtml(url, downloadLabel, fallbackName) {
      const filename = filenameFromUrl(url, fallbackName);
      const openUrl = escapeHtml(mediaProxyUrl(url, "inline", filename));
      const downloadUrl = escapeHtml(mediaProxyUrl(url, "attachment", filename));
      const safeName = escapeHtml(filename);
      if (IS_WECHAT) {
        return `<div class="run-media-actions"><a href="${openUrl}" target="_blank" rel="noopener noreferrer">打开</a><button type="button" data-copy-media="${escapeHtml(url)}">复制链接</button></div>`;
      }
      const label = IS_IOS ? "下载到文件" : downloadLabel;
      const iosAttr = IS_IOS ? ` data-ios-download="1"` : "";
      return `<div class="run-media-actions"><a href="${openUrl}" target="_blank" rel="noopener noreferrer">打开</a><a href="${downloadUrl}" download="${safeName}" target="_blank" rel="noopener noreferrer"${iosAttr}>${escapeHtml(label)}</a></div>`;
    }

    function renderMediaPreviews(bubble, urls) {
      if (!bubble || !urls || !urls.length) return;
      let box = bubble._mediaEl;
      if (!box || !box.isConnected) {
        box = document.createElement("div");
        box.className = "media-preview";
        bubble.appendChild(box);
        bubble._mediaEl = box;
      }
      box.innerHTML = "";
      urls.forEach((url) => {
        const low = url.toLowerCase();
        const item = document.createElement("div");
        item.className = "media-item";
        let el;
        let downloadLabel = "下载文件";
        let fallbackName = "lobster-media";
        if (/\.(mp4|webm|mov)(\?|#|$)/.test(low)) {
          el = document.createElement("video");
          el.src = mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-video.mp4"));
          el.controls = true;
          el.playsInline = true;
          downloadLabel = "下载视频";
          fallbackName = "lobster-video.mp4";
        } else if (/\.(mp3|wav|m4a|aac|ogg)(\?|#|$)/.test(low)) {
          el = document.createElement("audio");
          el.src = mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-audio.mp3"));
          el.controls = true;
          downloadLabel = "下载音频";
          fallbackName = "lobster-audio.mp3";
        } else if (/\.(png|jpe?g|webp|gif)(\?|#|$)/.test(low)) {
          const a = document.createElement("a");
          a.href = mediaProxyUrl(url, "inline", "lobster-image.png");
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          el = document.createElement("img");
          el.src = mediaProxyUrl(url, "inline", "lobster-image.png");
          el.alt = "生成素材预览";
          a.appendChild(el);
          item.appendChild(a);
          item.appendChild(mediaActions(url, "下载图片", "lobster-image.png"));
          box.appendChild(item);
          return;
        } else {
          el = document.createElement("a");
          el.href = mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-media"));
          el.target = "_blank";
          el.rel = "noopener noreferrer";
          el.textContent = "打开预览";
        }
        item.appendChild(el);
        item.appendChild(mediaActions(url, downloadLabel, fallbackName));
        box.appendChild(item);
      });
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function publishDraftFromPayload(payload) {
      const draft = payload && payload.publish_draft && typeof payload.publish_draft === "object" ? payload.publish_draft : null;
      if (!draft) return null;
      return { ...draft, run_id: draft.run_id || (payload && (payload.run_id || payload.scheduled_run_id)) || "" };
    }

    function publishDraftLabel(draft) {
      const status = String((draft && draft.status) || "ready").toLowerCase();
      return {
        ready: "待发布",
        draft: "待发布",
        pending: "等待发布",
        processing: "发布中",
        published: "已发布",
        failed: "发布失败"
      }[status] || "待发布";
    }

    function renderPublishDraftActions(bubble, payload) {
      if (!bubble) return;
      const draft = publishDraftFromPayload(payload || {});
      if (!draft) return;
      let box = bubble._publishEl;
      if (!box || !box.isConnected) {
        box = document.createElement("div");
        box.className = "publish-actions";
        bubble.appendChild(box);
        bubble._publishEl = box;
      }
      const status = String(draft.status || "ready").toLowerCase();
      const platform = draft.platform_name || draft.platform || "";
      const account = draft.account_nickname || draft.account_id || "";
      box.innerHTML = "";
      const label = document.createElement("span");
      label.textContent = `${publishDraftLabel(draft)}${platform || account ? ` · ${platform}${account ? " · " + account : ""}` : ""}`;
      box.appendChild(label);
      if (status !== "published" && status !== "pending" && status !== "processing") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = status === "failed" ? "重新发布" : "发布";
        btn.dataset.openPublishRun = draft.run_id || "";
        btn.addEventListener("click", () => openPublishRunModal(btn.dataset.openPublishRun));
        box.appendChild(btn);
      }
      if (draft.error) {
        const err = document.createElement("span");
        err.style.color = "var(--red)";
        err.textContent = String(draft.error).slice(0, 80);
        box.appendChild(err);
      }
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    async function requestRunPublish(runId, btn, draft = null) {
      if (!runId) {
        toast("缺少任务记录 ID");
        return;
      }
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      try {
        await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}/publish-request`, { method: "POST", json: draft ? { publish_draft: draft } : {} });
        toast("已提交发布，online 会用已绑定账号发布");
        if (btn) {
          btn.disabled = true;
          btn.textContent = "等待发布";
        }
        await loadRuns({ reset: true });
        return true;
      } catch (err) {
        toast(err.message || "提交发布失败");
        if (btn) {
          btn.disabled = false;
          btn.textContent = draft ? "提交发布" : "发布";
        }
        return false;
      }
    }

    function closePublishRunModal() {
      $("publishRunModal")?.classList.add("hidden");
      state.publishRunDraft = null;
      state.publishRunSubmitting = false;
      if ($("publishRunSubmit")) {
        $("publishRunSubmit").disabled = false;
        $("publishRunSubmit").textContent = "提交发布";
      }
    }

    function findRunById(runId) {
      const id = String(runId || "");
      return (state.runs || []).find((row) => String(row && row.id || "") === id) || null;
    }

    function buildPublishRunDraft(row, mediaIndex = -1) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const existing = publishDraftFromPayload({ ...payload, run_id: row && row.id }) || {};
      const entries = collectRunMediaEntries(row);
      const idx = Number.isNaN(mediaIndex) ? -1 : mediaIndex;
      const selected = idx >= 0 ? (entries[idx] || {}) : (entries[0] || {});
      const defaults = publishDefaultsFromRun(row);
      const draft = { ...selected, ...existing };
      if (idx >= 0) {
        draft.url = selected.url || selected.source_url || existing.url || existing.source_url || "";
        draft.source_url = selected.source_url || selected.url || existing.source_url || existing.url || "";
        draft.asset_id = selected.asset_id || existing.asset_id || "";
        draft.media_type = selected.media_type || existing.media_type || "";
      }
      return {
        ...draft,
        run_id: (row && row.id) || existing.run_id || "",
        title: valueLabel(draft.title || defaults.title || ""),
        description: valueLabel(draft.description || defaults.description || ""),
        tags: valueLabel(draft.tags || defaults.tags || ""),
        media_type: mediaTypeFromUrl(draft.url || draft.source_url || "", draft.media_type || selected.media_type || "video"),
      };
    }

    function setPublishRunValue(id, value) {
      const el = $(id);
      if (!el) return;
      el.value = value == null ? "" : String(value);
    }

    async function openPublishRunModal(runId, mediaIndex = -1) {
      let row = findRunById(runId);
      if (!row) {
        try {
          const data = await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}`);
          row = data.run || null;
          if (row) mergeRuns([row]);
        } catch (err) {
          toast(err.message || "记录加载失败");
          return;
        }
      }
      if (!row) {
        toast("记录不存在");
        return;
      }
      state.publishRunDraft = buildPublishRunDraft(row, Number(mediaIndex));
      setPublishRunValue("publishRunMaterial", state.publishRunDraft.asset_id || state.publishRunDraft.url || state.publishRunDraft.source_url || "");
      setPublishRunValue("publishRunTitleInput", state.publishRunDraft.title || "");
      setPublishRunValue("publishRunDescription", state.publishRunDraft.description || "");
      setPublishRunValue("publishRunMediaType", state.publishRunDraft.media_type || "video");
      setPublishRunValue("publishRunTags", state.publishRunDraft.tags || "");
      if ($("publishRunAiCopy")) $("publishRunAiCopy").checked = !!state.publishRunDraft.ai_publish_copy;
      $("publishRunModal")?.classList.remove("hidden");
      await loadPublishAccounts();
    }

    function selectedPublishRunAccount() {
      const accountId = $("publishRunAccount") ? $("publishRunAccount").value : "";
      return (state.publishAccounts || []).find((row) => publishAccountSelectId(row) === String(accountId)) || null;
    }

    async function submitPublishRunForm(evt) {
      if (evt) evt.preventDefault();
      if (state.publishRunSubmitting) return;
      const draft = state.publishRunDraft || {};
      const runId = draft.run_id || "";
      const account = selectedPublishRunAccount();
      const platform = $("publishRunPlatform") ? $("publishRunPlatform").value : "";
      if (!draft.asset_id) {
        toast("当前素材还没有入库，不能直接发布");
        return;
      }
      if (!account) {
        toast("请选择发布账号");
        return;
      }
      const body = {
        ...draft,
        asset_id: String(draft.asset_id || "").trim(),
        source_url: String(draft.source_url || draft.url || "").trim(),
        media_type: $("publishRunMediaType") ? $("publishRunMediaType").value : (draft.media_type || "video"),
        platform: platform || account.platform || "",
        platform_name: account.platform_name || platformDisplayName(platform || account.platform),
        account_id: publishAccountLocalId(account),
        account_nickname: account.nickname || "",
        installation_id: account.installation_id || currentInstallationId(),
        title: $("publishRunTitleInput") ? $("publishRunTitleInput").value.trim() : "",
        description: $("publishRunDescription") ? $("publishRunDescription").value.trim() : "",
        tags: $("publishRunTags") ? $("publishRunTags").value.trim() : "",
        ai_publish_copy: !!($("publishRunAiCopy") && $("publishRunAiCopy").checked),
        status: "ready",
      };
      state.publishRunSubmitting = true;
      const btn = $("publishRunSubmit");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      try {
        const ok = await requestRunPublish(runId, btn, body);
        if (ok) closePublishRunModal();
      } finally {
        state.publishRunSubmitting = false;
        if (btn && !$("publishRunModal")?.classList.contains("hidden")) {
          btn.disabled = false;
          btn.textContent = "提交发布";
        }
      }
    }

    function clearEmpty() {
      $("empty")?.classList.add("hidden");
    }

    function addBubble(role, text) {
      clearEmpty();
      const el = document.createElement("div");
      el.className = `bubble ${role}`;
      el._rawText = text || "";
      const textEl = document.createElement("span");
      textEl.className = "bubble-text";
      el._textEl = textEl;
      el.appendChild(textEl);
      renderBubbleText(el);
      if (role === "bot") {
        const steps = document.createElement("div");
        steps.className = "steps";
        el.appendChild(steps);
        el._steps = steps;
      }
      $("messages").appendChild(el);
      $("messages").scrollTop = $("messages").scrollHeight;
      return el;
    }

    function addStep(bubble, text) {
      if (!SHOW_INTERNAL_STEPS || !bubble || !bubble._steps || !text) return;
      const s = document.createElement("span");
      s.className = "step";
      s.textContent = text;
      bubble._steps.appendChild(s);
    }

    function setBubbleText(bubble, text) {
      if (!bubble) return;
      bubble._rawText = text || "";
      renderBubbleText(bubble);
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function appendBubbleText(bubble, text) {
      if (!text || !bubble) return;
      bubble._rawText = (bubble._rawText || "") + text;
      renderBubbleText(bubble);
      $("messages").scrollTop = $("messages").scrollHeight;
    }

    function eventLabel(ev) {
      const p = ev.payload || {};
      if (!SHOW_INTERNAL_STEPS) return "";
      if (ev.type === "queued") return "已进入云端队列";
      if (ev.type === "claimed") return "本地设备已领取";
      if (ev.type === "thinking") return p.text || "正在处理";
      if (ev.type === "tool_start") return p.name ? `调用能力：${p.name}` : "调用能力";
      if (ev.type === "tool_end") return p.name ? `能力完成：${p.name}` : "能力完成";
      if (ev.type === "progress") return p.text || p.message || "处理中";
      return "";
    }

    function closeStream(messageId) {
      const es = state.streams.get(messageId);
      if (es) es.close();
      state.streams.delete(messageId);
      const poll = state.pollers.get(messageId);
      if (poll) clearInterval(poll);
      state.pollers.delete(messageId);
    }

    function handleEvent(ev, bubble, messageId) {
      if (!ev || !bubble) return;
      if (messageId) {
        const hit = state.historyItems.find((entry) => {
          const msg = entry && entry.message ? entry.message : entry;
          return msg && msg.id === messageId;
        });
        const msg = hit && hit.message ? hit.message : hit;
        if (msg) {
          if (hit && hit.events && ev.id && !hit.events.some((item) => item && item.id === ev.id)) {
            hit.events.push(ev);
          }
          if (ev.type === "claimed") {
            msg.status = "processing";
            msg.claimed_by_installation_id = (ev.payload && ev.payload.installation_id) || msg.claimed_by_installation_id;
          }
          if (ev.type === "final") {
            msg.status = "completed";
            msg.reply_text = (ev.payload && (ev.payload.reply_text || ev.payload.text)) || msg.reply_text;
            msg.finished_at = ev.created_at || new Date().toISOString();
          }
          if (ev.type === "error") {
            msg.status = "failed";
            msg.error = (ev.payload && (ev.payload.error || ev.payload.detail || ev.payload.message)) || msg.error;
            msg.finished_at = ev.created_at || new Date().toISOString();
          }
          renderOfficeEmployees();
          renderWorkList();
        }
      }
      const label = eventLabel(ev);
      if (label) addStep(bubble, label);
      if (ev.type === "delta" && ev.payload && ev.payload.text) appendBubbleText(bubble, ev.payload.text);
      renderMediaPreviews(bubble, collectMediaUrls(ev.payload || {}));
      renderPublishDraftActions(bubble, ev.payload || {});
      if (ev.type === "final") {
        const reply = (ev.payload && (ev.payload.reply_text || ev.payload.text)) || "处理完成。";
        setBubbleText(bubble, reply);
        renderMediaPreviews(bubble, collectMediaUrls(ev.payload || {}));
        renderPublishDraftActions(bubble, ev.payload || {});
        closeStream(messageId);
      }
      if (ev.type === "publish_pending" || ev.type === "publish_claimed" || ev.type === "publish_result") {
        renderPublishDraftActions(bubble, ev.payload || {});
      }
      if (ev.type === "error") {
        bubble.classList.add("err");
        setBubbleText(bubble, (ev.payload && (ev.payload.error || ev.payload.detail)) || "处理失败");
        closeStream(messageId);
      }
    }

    function startPolling(messageId, bubble, lastEventId = 0) {
      if (state.pollers.has(messageId)) return;
      let last = lastEventId;
      const timer = setInterval(async () => {
        try {
          const data = await api(`/api/h5-chat/messages/${messageId}?after_event_id=${last}`);
          for (const ev of data.events || []) {
            last = Math.max(last, ev.id || 0);
            handleEvent(ev, bubble, messageId);
          }
          if (data.message && ["completed", "failed", "cancelled"].includes(data.message.status)) {
            const hit = state.historyItems.find((entry) => {
              const msg = entry && entry.message ? entry.message : entry;
              return msg && msg.id === messageId;
            });
            if (hit) {
              if (hit.message) hit.message = data.message;
              else Object.assign(hit, data.message);
              renderOfficeEmployees();
              renderWorkList();
            }
            if (data.message.status === "completed" && data.message.reply_text) setBubbleText(bubble, data.message.reply_text);
            if (data.message.status === "failed") {
              bubble.classList.add("err");
              setBubbleText(bubble, data.message.error || "处理失败");
            }
            closeStream(messageId);
          }
        } catch (err) {
          bubble.classList.add("err");
          setBubbleText(bubble, err.message || "查询失败");
          closeStream(messageId);
        }
      }, 1400);
      state.pollers.set(messageId, timer);
    }

    function startSse(messageId, bubble) {
      if (!window.EventSource) {
        startPolling(messageId, bubble, 0);
        return;
      }
      let last = 0;
      const es = new EventSource(apiUrl(`/api/h5-chat/messages/${messageId}/events?token=${encodeURIComponent(state.token)}`));
      state.streams.set(messageId, es);
      ["queued", "claimed", "thinking", "progress", "tool_start", "tool_end", "delta", "final", "error", "publish_pending", "publish_claimed", "publish_result"].forEach((type) => {
        es.addEventListener(type, (evt) => {
          try {
            const ev = JSON.parse(evt.data || "{}");
            last = Math.max(last, ev.id || 0);
            handleEvent(ev, bubble, messageId);
          } catch (err) {
            console.warn("Bad SSE event", err);
          }
        });
      });
      es.onerror = () => {
        es.close();
        state.streams.delete(messageId);
        startPolling(messageId, bubble, last);
      };
    }

    function clearRenderedMessages() {
      $("messages").querySelectorAll(".bubble").forEach((el) => el.remove());
      const empty = $("empty");
      if (empty) empty.classList.remove("hidden");
    }

    function renderHistoryItem(item) {
      const msg = (item && item.message) || {};
      if (!msg.id) return;
      const events = Array.isArray(item.events) ? item.events : [];
      const isFinal = ["completed", "failed", "cancelled"].includes(msg.status);
      const hasDelta = events.some((ev) => ev && ev.type === "delta");
      addBubble("user", msg.content || "");
      let initialText = "已恢复，等待本地设备处理...";
      if (msg.status === "completed") initialText = msg.reply_text || "处理完成。";
      if (msg.status === "failed") initialText = msg.error || "处理失败";
      if (!isFinal && hasDelta) initialText = "";
      const bot = addBubble("bot", initialText);
      if (msg.status === "failed") bot.classList.add("err");
      let finalPayload = null;
      for (const ev of events) {
        if (ev && ev.type === "final") finalPayload = ev.payload || null;
        handleEvent(ev, bot, msg.id);
      }
      if (msg.status === "completed" && msg.reply_text) setBubbleText(bot, msg.reply_text);
      if (finalPayload) renderMediaPreviews(bot, collectMediaUrls(finalPayload));
      if (msg.status === "failed") {
        bot.classList.add("err");
        setBubbleText(bot, msg.error || "处理失败");
      }
      if (!isFinal) startSse(msg.id, bot);
    }

    function mediaTypeFromUrl(url, fallback = "") {
      const raw = String(fallback || "").trim().toLowerCase();
      if (["image", "video", "document"].includes(raw)) return raw;
      const low = String(url || "").split(/[?#]/)[0].toLowerCase();
      if (/\.(mp4|webm|mov|m4v)$/.test(low)) return "video";
      if (/\.(png|jpe?g|webp|gif|bmp)$/.test(low)) return "image";
      return "document";
    }

    function publishDefaultsFromRun(row) {
      const payload = runTaskInputPayload(row);
      const inner = runInnerPayload(row);
      const params = payload.params && typeof payload.params === "object" ? payload.params : {};
      const result = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const generated = result.generated_content && typeof result.generated_content === "object" ? result.generated_content : {};
      return {
        title: valueLabel(params.title || inner.title || payload.title || generated.title || result.title || ""),
        description: valueLabel(params.description || inner.description || params.prompt || inner.prompt || inner.task_text || payload.prompt || generated.prompt || generated.caption || ""),
        tags: valueLabel(params.tags || generated.tags || result.tags || ""),
      };
    }

    function collectRunMediaEntries(row) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const refs = payload.result_refs && typeof payload.result_refs === "object" ? payload.result_refs : {};
      const defaults = publishDefaultsFromRun(row);
      const out = [];
      const seen = new Set();
      const addEntry = (entry) => {
        if (!entry || typeof entry !== "object") return;
        const url = String(entry.url || entry.source_url || entry.public_url || entry.image_url || entry.video_url || entry.final_url || entry.output_url || entry.media_url || "").trim();
        const assetId = String(entry.asset_id || entry.id || entry.final_asset_id || entry.video_asset_id || entry.image_asset_id || "").trim();
        if (!url && !assetId) return;
        const key = assetId || url;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({
          ...defaults,
          url,
          source_url: url,
          asset_id: assetId,
          media_type: mediaTypeFromUrl(url, entry.media_type || entry.type || ""),
          title: valueLabel(entry.title || entry.filename || defaults.title),
          description: valueLabel(entry.description || entry.prompt || entry.caption || defaults.description),
          tags: valueLabel(entry.tags || defaults.tags),
        });
      };
      const addUrl = (url) => addEntry({ url });
      const draft = publishDraftFromPayload({ ...payload, run_id: row && row.id });
      const ids = Array.isArray(refs.asset_ids) ? refs.asset_ids : [];
      const urls = Array.isArray(refs.urls) ? refs.urls : [];
      urls.forEach((url, idx) => addEntry({ url, asset_id: ids[idx] || "", media_type: mediaTypeFromUrl(url) }));
      ids.forEach((assetId, idx) => addEntry({ asset_id: assetId, url: urls[idx] || "" }));
      const walk = (value) => {
        if (!value) return;
        if (Array.isArray(value)) {
          value.forEach(walk);
          return;
        }
        if (typeof value === "object") {
          addEntry(value);
          Object.values(value).forEach(walk);
          return;
        }
        const matches = String(value || "").match(/https?:\/\/[^\s<>"'`]+/gi) || [];
        matches.forEach((raw) => {
          let url = raw;
          while (/[)\].,!?，。！？、；：]$/.test(url)) url = url.slice(0, -1);
          addUrl(url);
        });
      };
      walk(payload.saved_assets);
      walk(refs.saved_assets);
      walk(payload.media_urls);
      if (draft) addEntry(draft);
      if (!out.length) collectMediaUrls(payload).forEach(addUrl);
      return out.slice(0, 8);
    }

    async function loadHistory() {
      if (!state.token) return;
      try {
        const data = await api("/api/h5-chat/messages?limit=40");
        const items = Array.isArray(data.messages) ? data.messages : [];
        state.historyItems = items;
        renderOfficeEmployees();
        if (!items.length) return;
        clearRenderedMessages();
        for (const item of items) renderHistoryItem(item);
      } catch (err) {
        console.warn("Load H5 history failed", err);
      }
    }

    function collectRunMediaUrls(row) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      return collectMediaUrls(payload).slice(0, 4);
    }

    function normalizeRunMediaEntries(input) {
      return (Array.isArray(input) ? input : []).map((item) => {
        if (typeof item === "string") return { url: item, source_url: item, media_type: mediaTypeFromUrl(item) };
        if (item && typeof item === "object") {
          const url = String(item.url || item.source_url || item.public_url || item.image_url || item.video_url || "").trim();
          return { ...item, url, source_url: item.source_url || url, media_type: mediaTypeFromUrl(url, item.media_type || item.type || "") };
        }
        return null;
      }).filter(Boolean);
    }

    function runMediaPublishButton(row, index) {
      if (!row || !row.id) return "";
      return `<button type="button" data-open-publish-run="${escapeHtml(row.id)}" data-publish-media-index="${escapeHtml(index)}">发布</button>`;
    }

    function renderRunMedia(input, row = null) {
      const entries = normalizeRunMediaEntries(input);
      if (!entries.length) return "";
      return `<div class="run-media">${entries.map((entry, index) => {
        const url = String(entry.url || entry.source_url || "").trim();
        if (!url && entry.asset_id) {
          return `<div class="run-media-item"><div class="hint">素材已入库</div><div class="run-media-actions">${runMediaPublishButton(row, index)}</div></div>`;
        }
        if (!url) return "";
        const low = url.toLowerCase();
        if (/\.(mp4|webm|mov)(\?|#|$)/.test(low)) {
          return `<div class="run-media-item"><video controls src="${escapeHtml(mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-video.mp4")))}"></video>${mediaActionHtml(url, "下载视频", "lobster-video.mp4")}<div class="run-media-actions">${runMediaPublishButton(row, index)}</div></div>`;
        }
        if (/\.(png|jpe?g|webp|gif)(\?|#|$)/.test(low)) {
          const previewUrl = escapeHtml(mediaProxyUrl(url, "inline", "lobster-image.png"));
          return `<div class="run-media-item"><a href="${previewUrl}" target="_blank" rel="noopener noreferrer"><img src="${previewUrl}" alt="预览"></a>${mediaActionHtml(url, "下载图片", "lobster-image.png")}<div class="run-media-actions">${runMediaPublishButton(row, index)}</div></div>`;
        }
        return `<div class="run-media-item"><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">打开预览</a>${mediaActionHtml(url, "下载文件", "lobster-media")}<div class="run-media-actions">${runMediaPublishButton(row, index)}</div></div>`;
      }).join("")}</div>`;
    }

    function renderRunPublishActions(row) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const draft = publishDraftFromPayload({ ...payload, run_id: row && row.id });
      const hasMedia = collectRunMediaEntries(row).length > 0;
      if (!draft && !hasMedia) return "";
      const status = String((draft && draft.status) || "ready").toLowerCase();
      const platform = (draft && (draft.platform_name || draft.platform)) || "";
      const account = (draft && (draft.account_nickname || draft.account_id)) || "";
      const label = draft ? `${publishDraftLabel(draft)}${platform || account ? ` · ${platform}${account ? " · " + account : ""}` : ""}` : "待发布";
      const canPublish = status !== "published" && status !== "pending" && status !== "processing";
      return `<div class="run-publish-actions">
        <span>${escapeHtml(label)}</span>
        ${canPublish ? `<button type="button" data-open-publish-run="${escapeHtml((draft && draft.run_id) || (row && row.id) || "")}">${status === "failed" ? "重新发布" : "发布"}</button>` : ""}
        ${draft && draft.error ? `<span style="color:var(--red);">${escapeHtml(String(draft.error).slice(0, 80))}</span>` : ""}
      </div>`;
    }

    async function loadRuns(options = {}) {
      const reset = options.reset !== false;
      const append = !!options.append;
      const pageSize = Math.max(1, Math.min(100, parseInt(options.limit || "10", 10) || 10));
      const compact = !!options.compact;
      const box = $("runList");
      if (!state.token) return;
      if (reset) {
        state.runListOffset = 0;
        state.runListHasNext = false;
      }
      const offset = append ? state.runListOffset : 0;
      if (box && !append) box.innerHTML = `<div class="hint">加载中...</div>`;
      try {
        const data = await api(`/api/scheduled-tasks/runs?limit=${pageSize}&offset=${offset}${compact ? "&compact=1" : ""}`);
        const rows = Array.isArray(data.runs) ? data.runs : [];
        const pagination = data.pagination || {};
        state.runListOffset = offset + rows.length;
        state.runListHasNext = !!pagination.has_next;
        state.runs = append ? (state.runs || []).concat(rows) : rows;
        renderOfficeEmployees();
        renderWorkList();
        if (document.querySelector("#departmentView.active")) renderDepartmentDayBoard();
        if (document.querySelector("#workflowView.active")) renderWorkflowDayBoard();
        if (!box) return;
        const allRows = state.runs || [];
        if (!allRows.length) {
          box.innerHTML = `<div class="hint">暂无执行记录。</div>`;
          $("loadMoreRunsBtn")?.classList.add("hidden");
          return;
        }
        box.innerHTML = allRows.map((row) => {
          const result = row.error || row.result_text || (row.progress && (row.progress.text || row.progress.message)) || "";
          return `<div class="run-card">
            <div class="run-top"><span>${escapeHtml(fmtTime(row.created_at))}</span><span>${escapeHtml(statusText(row.status))}</span></div>
            <div class="run-title">${escapeHtml(row.title || "定时任务")}</div>
            <div class="run-result">${escapeHtml(result || "等待结果")}</div>
            ${renderRunMedia(collectRunMediaEntries(row), row)}
            ${renderRunPublishActions(row)}
            <div class="run-publish-actions"><button type="button" data-open-run-detail="${escapeHtml(row.id || "")}">查看详情</button></div>
          </div>`;
        }).join("");
        $("loadMoreRunsBtn")?.classList.toggle("hidden", !state.runListHasNext);
      } catch (err) {
        state.runs = [];
        state.runListHasNext = false;
        renderOfficeEmployees();
        renderWorkList();
        if (document.querySelector("#departmentView.active")) renderDepartmentDayBoard();
        if (document.querySelector("#workflowView.active")) renderWorkflowDayBoard();
        if (box) box.innerHTML = `<div class="hint">${escapeHtml(err.message || "执行记录加载失败")}</div>`;
        $("loadMoreRunsBtn")?.classList.add("hidden");
      }
    }

    function syncTopNavigationActions() {
      const homeBtn = document.querySelector('.top-action[data-tab-target="home"]');
      if (homeBtn) {
        homeBtn.remove();
      }
      document.querySelectorAll('.top-action[data-tab-target="messages"]').forEach((btn) => btn.remove());
    }

    syncTopNavigationActions();

    document.querySelectorAll("[data-tab-target]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.tabTarget;
        if (target === "agentManage") {
          openAgentManage({ backTab: activeViewKey() || "profile" }).catch((err) => toast(err.message || "打开失败"));
          return;
        }
        if (target === "personalSettings") {
          state.personalSettingsBackTab = activeViewKey() === "office" ? "office" : "profile";
        }
        if (target === "taskList") {
          const back = backTargetFromCurrent("profile");
          state.taskListBackTarget = back;
          state.taskListBackTab = back.tab || "profile";
        }
        if (target === "workList") {
          const back = backTargetFromCurrent("profile");
          state.workListBackTarget = back;
          state.workListBackTab = back.tab || "profile";
        }
        switchTab(target);
      });
    });
    document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
      btn.addEventListener("click", () => setAuthTab(btn.dataset.authTab));
    });
    $("topBackBtn").addEventListener("click", () => {
      const activeView = document.querySelector(".view.active");
      const activeId = activeView ? String(activeView.id || "") : "";
      if (activeId === "abilityView") {
        switchTab("department");
        return;
      }
      if (activeId === "departmentView") {
        switchTab("office");
        return;
      }
      if (activeId === "secretaryView") {
        switchTab("office");
        return;
      }
      if (activeId === "messagesView" && state.lastViewBeforeMessages) {
        const next = state.lastViewBeforeMessages;
        state.lastViewBeforeMessages = "";
        switchTab(next);
        return;
      }
      if (activeId === "taskDetailView") {
        switchTab(state.taskDetailBackTab || "taskList");
        return;
      }
      if (activeId === "runDetailView") {
        switchTab(state.runDetailBackTab || "runList");
        return;
      }
      if (activeId === "workListView") {
        restoreViewTarget(state.workListBackTarget || state.workListBackTab || "profile", "profile");
        return;
      }
      if (activeId === "taskListView") {
        restoreViewTarget(state.taskListBackTarget || state.taskListBackTab || "profile", "profile");
        return;
      }
      if (activeId === "runListView") {
        switchTab("profile");
        return;
      }
      if (activeId === "personalSettingsView") {
        switchTab(state.personalSettingsBackTab || "profile");
        return;
      }
      if (activeId === "agentManageView") {
        switchTab(state.agentManageBackTab || "profile");
        return;
      }
      switchTab("office");
    });
    $("departmentSkillGrid")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-ability-key]");
      if (!btn) return;
      const lookup = abilityLookup(btn.dataset.abilityKey || "");
      if (lookup && lookup.node && lookup.node.comingSoon) {
        toast("敬请期待");
        return;
      }
      openAbilityView(btn.dataset.abilityKey || "");
    });
    $("departmentView")?.addEventListener("click", (evt) => {
      const runBtn = evt.target.closest("[data-open-run-detail]");
      if (!runBtn) return;
      evt.preventDefault();
      openRunDetail(runBtn.dataset.openRunDetail || "", "department");
    });
    $("secretaryView")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-secretary-dept]");
      if (!btn) return;
      const department = departmentById(btn.dataset.secretaryDept || "");
      if (!department) return;
      openWorkHistory(departmentScope(department), { tab: "secretary" });
    });
    $("abilityChildren")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-ability-key]");
      if (!btn) return;
      const lookup = abilityLookup(btn.dataset.abilityKey || "");
      if (lookup && lookup.node && lookup.node.comingSoon) {
        toast("敬请期待");
        return;
      }
      openAbilityView(btn.dataset.abilityKey || "");
    });
    $("departmentChatBtn")?.addEventListener("click", () => {
      const department = departmentById(state.currentDepartmentId);
      if (department) openContextChat(contextFromDepartment(department));
    });
    $("abilityChatBtn")?.addEventListener("click", () => {
      const lookup = activeAbilityLookup();
      if (lookup) openContextChat(contextFromAbility(lookup));
    });
    $("departmentWorkHistoryBtn")?.addEventListener("click", () => openWorkHistory(departmentScope(departmentById(state.currentDepartmentId))));
    $("abilityWorkHistoryBtn")?.addEventListener("click", () => openWorkHistory(abilityScope(activeAbilityLookup())));
    $("floatingScheduleBtn")?.addEventListener("click", () => openScheduleManager());
    $("workScopeChips")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-work-scope]");
      if (!btn) return;
      const id = btn.dataset.workScope || "";
      const found = (state.workListScopeOptions || []).find((item) => scopeId(item) === id);
      if (found) setWorkListScope(found, state.workListScopeOptions);
    });
    $("abilityWorkbenchFields")?.addEventListener("change", (evt) => {
      if (evt.target && evt.target.id === "abilityScheduleType") updateAbilityScheduleFields();
    });
    $("abilityWorkbenchFields")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("#abilityAddDailyTimeBtn");
      if (btn) addAbilityDailyTime();
    });
    $("abilityRouteBtn")?.addEventListener("click", () => {
      const lookup = activeAbilityLookup();
      const routeTab = lookup && lookup.node && lookup.node.routeTab;
      if (routeTab) switchTab(routeTab);
    });
    $("abilityDispatchBtn")?.addEventListener("click", () => {
      const lookup = activeAbilityLookup();
      const key = lookup && lookup.node && lookup.node.workQuickKey;
      if (!key) return;
      openWorkDispatchModal(key);
    });
    $("clearChatContextBtn")?.addEventListener("click", () => {
      setChatContext(null);
      toast("已取消来源标记");
    });
    document.querySelectorAll("[data-home-target]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = String(btn.dataset.homeTarget || "").trim();
        if (!target) return;
        openHomeTarget(target, activeViewKey() || "office");
      });
    });
    $("toggleTaskPanelBtn").addEventListener("click", () => setTaskPanelOpen(!state.taskPanelOpen));
    $("taskAbilityBoard")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-task-ability]");
      if (!btn) return;
      setTaskAbility(btn.dataset.taskAbility || "");
    });
    $("taskScheduleType").addEventListener("change", updateScheduleFields);
    $("addDailyTimeBtn").addEventListener("click", () => addDailyTime());
    $("createTaskBtn").addEventListener("click", () => createScheduledTask().catch((err) => toast(err.message || "下发失败")));
    $("taskEditScheduleType")?.addEventListener("change", updateTaskEditScheduleFields);
    $("taskEditAddDailyTimeBtn")?.addEventListener("click", () => addTaskEditDailyTime());
    $("taskEditBackdrop")?.addEventListener("click", closeTaskEditModal);
    $("taskEditClose")?.addEventListener("click", closeTaskEditModal);
    $("taskEditCancel")?.addEventListener("click", closeTaskEditModal);
    $("taskEditForm")?.addEventListener("submit", (evt) => {
      evt.preventDefault();
      submitTaskEdit().catch((err) => toast(err.message || "保存失败"));
    });
    $("publishRunBackdrop")?.addEventListener("click", closePublishRunModal);
    $("publishRunClose")?.addEventListener("click", closePublishRunModal);
    $("publishRunCancel")?.addEventListener("click", closePublishRunModal);
    $("publishRunPlatform")?.addEventListener("change", fillPublishRunAccountSelect);
    $("publishRunForm")?.addEventListener("submit", submitPublishRunForm);
    $("refreshStatusBtn").addEventListener("click", refreshDeviceStatus);
    $("assetLibraryTabs")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-asset-section]");
      if (!btn) return;
      state.assetLibrarySection = btn.dataset.assetSection || "uploads";
      renderAssetLibrary();
      refreshAssetLibrary();
    });
    $("assetLibraryAddBtn")?.addEventListener("click", openAssetLibraryAddModal);
    $("assetUploadBackdrop")?.addEventListener("click", closeAssetUploadModal);
    $("assetUploadClose")?.addEventListener("click", closeAssetUploadModal);
    $("assetUploadCancel")?.addEventListener("click", closeAssetUploadModal);
    $("assetUploadForm")?.addEventListener("submit", (evt) => {
      evt.preventDefault();
      uploadAssetLibraryFiles($("assetLibraryUploadBtn")).catch((err) => toast(err.message || "上传失败"));
    });
    $("assetAvatarBackdrop")?.addEventListener("click", closeAssetAvatarModal);
    $("assetAvatarClose")?.addEventListener("click", closeAssetAvatarModal);
    $("assetAvatarCancel")?.addEventListener("click", closeAssetAvatarModal);
    $("assetAvatarSourceType")?.addEventListener("change", syncAssetAvatarFileAccept);
    $("assetAvatarForm")?.addEventListener("submit", (evt) => submitAssetAvatarForm(evt).catch((err) => toast(err.message || "提交失败")));
    $("assetVoiceBackdrop")?.addEventListener("click", closeAssetVoiceModal);
    $("assetVoiceClose")?.addEventListener("click", closeAssetVoiceModal);
    $("assetVoiceCancel")?.addEventListener("click", closeAssetVoiceModal);
    $("assetVoiceForm")?.addEventListener("submit", (evt) => submitAssetVoiceForm(evt).catch((err) => toast(err.message || "提交失败")));
    $("assetLibraryPrevBtn")?.addEventListener("click", () => {
      const section = state.assetLibrarySection || "uploads";
      if (section === "avatars") state.assetLibraryAvatarPage = Math.max(1, Number(state.assetLibraryAvatarPage || 1) - 1);
      else if (section === "voices") state.assetLibraryVoicePage = Math.max(1, Number(state.assetLibraryVoicePage || 1) - 1);
      else state.assetLibraryPage.user_upload = Math.max(1, Number(state.assetLibraryPage.user_upload || 1) - 1);
      refreshAssetLibrary();
    });
    $("assetLibraryNextBtn")?.addEventListener("click", () => {
      const section = state.assetLibrarySection || "uploads";
      if (section === "avatars") state.assetLibraryAvatarPage = Math.max(1, Number(state.assetLibraryAvatarPage || 1) + 1);
      else if (section === "voices") state.assetLibraryVoicePage = Math.max(1, Number(state.assetLibraryVoicePage || 1) + 1);
      else state.assetLibraryPage.user_upload = Math.max(1, Number(state.assetLibraryPage.user_upload || 1) + 1);
      refreshAssetLibrary();
    });
    $("contentRecordTabs")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-content-record-media]");
      if (!btn) return;
      state.contentRecordMediaType = String(btn.dataset.contentRecordMedia || "");
      state.contentRecordPage = 1;
      loadContentRecords();
    });
    $("contentRecordPrevBtn")?.addEventListener("click", () => {
      state.contentRecordPage = Math.max(1, Number(state.contentRecordPage || 1) - 1);
      loadContentRecords();
    });
    $("contentRecordNextBtn")?.addEventListener("click", () => {
      state.contentRecordPage = Math.max(1, Number(state.contentRecordPage || 1) + 1);
      loadContentRecords();
    });
    $("contentRecordList")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-asset-preview-id]");
      if (!btn) return;
      openAssetPreview(btn.dataset.assetPreviewId || "");
    });
    $("customEmployeeCreateBtn")?.addEventListener("click", () => openHomeTarget("workflowNew", "office"));
    $("customEmployeeMoreBtn")?.addEventListener("click", () => {
      loadWorkflowTemplates().then(openCustomEmployeeList).catch((err) => toast(err.message || "员工模板加载失败"));
    });
    $("customEmployeeStrip")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-custom-employee-detail]");
      if (!btn) return;
      openCustomEmployeeDetail(btn.dataset.customEmployeeDetail || "");
    });
    $("customEmployeeBackdrop")?.addEventListener("click", closeCustomEmployeeDialog);
    $("customEmployeeCloseBtn")?.addEventListener("click", closeCustomEmployeeDialog);
    $("customEmployeeDialogBody")?.addEventListener("click", (evt) => {
      const listBtn = evt.target.closest("[data-custom-employee-list]");
      const detailBtn = evt.target.closest("[data-custom-employee-detail]");
      const editBtn = evt.target.closest("[data-custom-employee-edit]");
      const activateBtn = evt.target.closest("[data-custom-employee-activate]");
      const deleteBtn = evt.target.closest("[data-custom-employee-delete]");
      const demoBtn = evt.target.closest("[data-custom-employee-demo-node]");
      if (demoBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        demoWorkflowTemplateNode(demoBtn.dataset.customEmployeeDemoNode || "", demoBtn);
        return;
      }
      if (listBtn) {
        openCustomEmployeeList();
        return;
      }
      if (detailBtn) {
        openCustomEmployeeDetail(detailBtn.dataset.customEmployeeDetail || "");
        return;
      }
      if (editBtn) {
        const tpl = workflowTemplateById(editBtn.dataset.customEmployeeEdit || "");
        if (!tpl || !workflowTemplateCanEdit(tpl)) {
          toast("只能编辑自己创建的模板");
          return;
        }
        applyWorkflowTemplate(tpl);
        closeCustomEmployeeDialog();
        switchTab("workflow");
        return;
      }
      if (activateBtn) {
        activateWorkflowTemplate(activateBtn.dataset.customEmployeeActivate || "")
          .then(closeCustomEmployeeDialog)
          .catch((err) => toast(err.message || "启用失败"));
        return;
      }
      if (deleteBtn) {
        deleteWorkflowTemplateById(deleteBtn.dataset.customEmployeeDelete || "").catch((err) => toast(err.message || "删除失败"));
      }
    });
    $("workflowDeviceSelect")?.addEventListener("change", (evt) => {
      setSelectedInstallationId(evt.target.value || "");
      loadWorkflowActive().catch((err) => toast(err.message || "工作流状态加载失败"));
    });
    $("workflowOpenAddNodeBtn")?.addEventListener("click", openWorkflowNodeModal);
    $("workflowNodeBackdrop")?.addEventListener("click", closeWorkflowNodeModal);
    $("workflowNodeClose")?.addEventListener("click", closeWorkflowNodeModal);
    $("workflowNodeCancel")?.addEventListener("click", closeWorkflowNodeModal);
    $("workflowNodeForm")?.addEventListener("submit", (evt) => {
      evt.preventDefault();
      try {
        addWorkflowNodeFromInput();
      } catch (err) {
        toast(err.message || "添加失败");
      }
    });
    $("workflowCalendarDays")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-workflow-date]");
      if (!btn) return;
      state.workflowSelectedDate = btn.dataset.workflowDate || todayDateKey();
      renderWorkflowDayBoard();
      renderWorkflowTimeline();
    });
    $("workflowTimeline")?.addEventListener("click", (evt) => {
      const runBtn = evt.target.closest("[data-open-run-detail]");
      if (runBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        openRunDetail(runBtn.dataset.openRunDetail || "", "workflow");
        return;
      }
      const taskBtn = evt.target.closest("[data-open-task-detail]");
      if (taskBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        openTaskDetail(taskBtn.dataset.openTaskDetail || "", "workflow");
        return;
      }
      const demoBtn = evt.target.closest("[data-workflow-demo-node]");
      if (demoBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        demoWorkflowNode(demoBtn.dataset.workflowDemoNode || "", demoBtn);
        return;
      }
      const removeBtn = evt.target.closest("[data-workflow-remove-node]");
      if (removeBtn) {
        const nodeId = String(removeBtn.dataset.workflowRemoveNode || "");
        state.workflowNodesDraft = (state.workflowNodesDraft || []).filter((item) => String(item.id || "") !== nodeId);
        renderWorkflow();
        return;
      }
      const editTarget = evt.target.closest("[data-workflow-edit-node]");
      if (!editTarget) return;
      openWorkflowParamModal(editTarget.dataset.workflowEditNode || "");
    });
    $("workflowSaveTemplateBtn")?.addEventListener("click", () => saveWorkflowTemplate().catch((err) => toast(err.message || "保存失败")));
    $("workflowActivateBtn")?.addEventListener("click", () => activateWorkflowTemplate().catch((err) => toast(err.message || "启用失败")));
    $("workflowStopBtn")?.addEventListener("click", () => stopWorkflowActive().catch((err) => toast(err.message || "停用失败")));
    $("workflowTemplateListBtn")?.addEventListener("click", () => {
      $("workflowTemplateDrawer")?.classList.toggle("hidden");
      loadWorkflowTemplates(true).catch((err) => toast(err.message || "模板加载失败"));
    });
    $("workflowTemplateCloseBtn")?.addEventListener("click", () => $("workflowTemplateDrawer")?.classList.add("hidden"));
    $("workflowTemplateList")?.addEventListener("click", (evt) => {
      const loadBtn = evt.target.closest("[data-workflow-load]");
      const activateBtn = evt.target.closest("[data-workflow-activate-template]");
      const deleteBtn = evt.target.closest("[data-workflow-delete]");
      const grantBtn = evt.target.closest("[data-workflow-grant]");
      if (loadBtn) {
        const tpl = (state.workflowTemplates || []).find((item) => String(item.id) === String(loadBtn.dataset.workflowLoad));
        applyWorkflowTemplate(tpl);
        $("workflowTemplateDrawer")?.classList.add("hidden");
        return;
      }
      if (activateBtn) {
        activateWorkflowTemplate(activateBtn.dataset.workflowActivateTemplate || "").catch((err) => toast(err.message || "启用失败"));
        return;
      }
      if (deleteBtn) {
        const id = deleteBtn.dataset.workflowDelete || "";
        api(`/api/h5-workflows/templates/${encodeURIComponent(id)}`, { method: "DELETE" })
          .then(() => {
            if (String(state.workflowEditingTemplateId) === String(id)) {
              state.workflowEditingTemplateId = "";
              state.workflowNodesDraft = [];
            }
            state.workflowTemplatesLoaded = false;
            return loadWorkflowTemplates(true);
          })
          .then(() => toast("模板已删除"))
          .catch((err) => toast(err.message || "删除失败"));
        return;
      }
      if (grantBtn) {
        state.workflowGrantTemplateId = grantBtn.dataset.workflowGrant || "";
        const tpl = (state.workflowTemplates || []).find((item) => String(item.id) === String(state.workflowGrantTemplateId));
        state.workflowGrantSelectedUserIds = {};
        ((tpl && tpl.granted_user_ids) || []).forEach((id) => { state.workflowGrantSelectedUserIds[String(id)] = true; });
        state.workflowSubUserQuery = "";
        state.workflowSubUserOffset = 0;
        if ($("workflowGrantSearchInput")) $("workflowGrantSearchInput").value = "";
        renderWorkflowGrantPanel();
        loadWorkflowSubUsers(true).catch((err) => toast(err.message || "下级用户加载失败"));
      }
    });
    $("workflowGrantCancelBtn")?.addEventListener("click", () => {
      state.workflowGrantTemplateId = "";
      state.workflowGrantSelectedUserIds = {};
      renderWorkflowGrantPanel();
    });
    $("workflowGrantSearchBtn")?.addEventListener("click", () => {
      state.workflowSubUserQuery = (($("workflowGrantSearchInput") && $("workflowGrantSearchInput").value) || "").trim();
      loadWorkflowSubUsers(true).catch((err) => toast(err.message || "搜索失败"));
    });
    $("workflowGrantSearchInput")?.addEventListener("keydown", (evt) => {
      if (evt.key !== "Enter") return;
      state.workflowSubUserQuery = (($("workflowGrantSearchInput") && $("workflowGrantSearchInput").value) || "").trim();
      loadWorkflowSubUsers(true).catch((err) => toast(err.message || "搜索失败"));
    });
    $("workflowGrantPrevBtn")?.addEventListener("click", () => {
      state.workflowSubUserOffset = Math.max(0, state.workflowSubUserOffset - state.workflowSubUserLimit);
      loadWorkflowSubUsers().catch((err) => toast(err.message || "加载失败"));
    });
    $("workflowGrantNextBtn")?.addEventListener("click", () => {
      if (state.workflowSubUserOffset + state.workflowSubUserLimit >= state.workflowSubUserTotal) return;
      state.workflowSubUserOffset += state.workflowSubUserLimit;
      loadWorkflowSubUsers().catch((err) => toast(err.message || "加载失败"));
    });
    $("workflowGrantSaveBtn")?.addEventListener("click", () => saveWorkflowGrant().catch((err) => toast(err.message || "授权失败")));
    $("workflowParamBackdrop")?.addEventListener("click", closeWorkflowParamModal);
    $("workflowParamClose")?.addEventListener("click", closeWorkflowParamModal);
    $("workflowParamCancel")?.addEventListener("click", closeWorkflowParamModal);
    $("workflowParamForm")?.addEventListener("submit", (evt) => {
      evt.preventDefault();
      try {
        saveWorkflowParamNode();
      } catch (err) {
        toast(err.message || "保存失败");
      }
    });
    $("leadDomainTabs")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-lead-domain]");
      if (!btn) return;
      state.leadCenterDomain = btn.dataset.leadDomain === "private" ? "private" : "public";
      renderLeadCenter();
    });
    $("leadPlatformTabs")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-lead-platform]");
      if (!btn) return;
      state.leadCenterPlatform = btn.dataset.leadPlatform || "all";
      renderLeadCenter();
    });
    $("assetLibraryList")?.addEventListener("click", (evt) => {
      const hiflyBtn = evt.target.closest("[data-hifly-asset-kind]");
      if (hiflyBtn) {
        openHiflyAssetPreview(hiflyBtn.dataset.hiflyAssetKind || "", hiflyBtn.dataset.hiflyAssetId || "");
        return;
      }
      const btn = evt.target.closest("[data-asset-preview-id]");
      if (!btn) return;
      openAssetPreview(btn.dataset.assetPreviewId || "");
    });
    $("leadCenterList")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-lead-detail-index]");
      if (!btn) return;
      openLeadDetail(btn.dataset.leadDetailIndex || "0");
    });
    $("taskSuccessBackdrop")?.addEventListener("click", closeTaskSuccessDialog);
    $("taskSuccessCloseBtn")?.addEventListener("click", closeTaskSuccessDialog);
    $("taskSuccessHistoryBtn")?.addEventListener("click", () => openWorkHistory(scopeFromActiveView(), viewTargetFromCurrent("profile")));
    $("personalTemplateHelpBtn")?.addEventListener("click", openPersonalTemplateHelpDialog);
    $("personalTemplateHelpBackdrop")?.addEventListener("click", closePersonalTemplateHelpDialog);
    $("personalTemplateHelpCloseBtn")?.addEventListener("click", closePersonalTemplateHelpDialog);
    $("assetPreviewBackdrop")?.addEventListener("click", closeAssetPreviewDialog);
    $("assetPreviewCloseBtn")?.addEventListener("click", closeAssetPreviewDialog);
    $("assetPreviewBody")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-delete-hifly-asset]");
      if (!btn) return;
      deleteHiflyAsset(btn.dataset.deleteHiflyAsset || "", btn.dataset.deleteHiflyId || "").catch((err) => toast(err.message || "删除失败"));
    });
    $("leadDetailBackdrop")?.addEventListener("click", closeLeadDetailDialog);
    $("leadDetailCloseBtn")?.addEventListener("click", closeLeadDetailDialog);
    document.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-open-personal-template-settings]");
      if (!btn) return;
      evt.preventDefault();
      openPersonalTemplateSettings();
    });
    document.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-asset-upload-trigger]");
      if (!btn) return;
      evt.preventDefault();
      const id = btn.dataset.assetUploadTrigger || "";
      const input = id ? $(`${id}File`) : null;
      if (input) input.click();
    });
    document.addEventListener("change", (evt) => {
      const select = evt.target.closest("[data-asset-select]");
      if (select) {
        const id = select.dataset.assetSelect || "";
        const box = id ? document.querySelector(`[data-asset-picker="${cssEscape(id)}"]`) : null;
        const hidden = id ? $(id) : null;
        if (!box || !hidden) return;
        const rows = userUploadAssetRows(box.dataset.assetMediaType || "");
        const row = rows.find((item) => item.asset_id === select.value) || null;
        hidden.value = row ? assetPickerOutputValue(row, box.dataset.assetOutput || "asset_id") : "";
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
        renderAssetPickerControl(id);
        return;
      }
      const input = evt.target.closest("[data-asset-upload-input]");
      if (input) {
        const id = input.dataset.assetUploadInput || "";
        const file = input.files && input.files[0] ? input.files[0] : null;
        input.value = "";
        uploadUserAssetForPicker(id, file).catch((err) => {
          toast(err.message || "上传失败");
          renderAssetPickerControl(id);
        });
      }
    });
    $("refreshProfileBtn").addEventListener("click", refreshDeviceStatus);
    $("profileDeviceSelect")?.addEventListener("change", (evt) => setSelectedInstallationId(evt.target.value || ""));
    $("personalSettingsTabs")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-personal-tab]");
      if (btn) setPersonalSettingsTab(btn.dataset.personalTab || "template");
    });
    $("personalSettingsRefreshBtn")?.addEventListener("click", () => loadPersonalSettings(true));
    $("personalSurveyPrevBtn")?.addEventListener("click", () => movePersonalSurvey(-1));
    $("personalSurveyNextBtn")?.addEventListener("click", () => movePersonalSurvey(1));
    $("personalSaveProfileBtn")?.addEventListener("click", (evt) => savePersonalProfile(evt.currentTarget).catch((err) => personalSetStatus(err.message || "保存失败", true)));
    $("personalSaveDefaultBtn")?.addEventListener("click", () => savePersonalDefault().catch((err) => toast(err.message || "保存失败")));
    $("personalNewTemplateBtn")?.addEventListener("click", resetPersonalTemplateForm);
    $("personalAddKeywordBtn")?.addEventListener("click", () => addPersonalKeyword().catch((err) => toast(err.message || "添加失败")));
    $("personalAddCompetitorBtn")?.addEventListener("click", () => addPersonalCompetitor().catch((err) => toast(err.message || "添加失败")));
    $("personalOpenUploadBtn")?.addEventListener("click", openPersonalUploadModal);
    $("personalUploadBackdrop")?.addEventListener("click", closePersonalUploadModal);
    $("personalUploadClose")?.addEventListener("click", closePersonalUploadModal);
    $("personalUploadCancel")?.addEventListener("click", closePersonalUploadModal);
    $("personalOpenMemoryGenerateBtn")?.addEventListener("click", openPersonalMemoryGenerateModal);
    $("personalMemoryGenerateBackdrop")?.addEventListener("click", closePersonalMemoryGenerateModal);
    $("personalMemoryGenerateClose")?.addEventListener("click", closePersonalMemoryGenerateModal);
    $("personalGenerateMemoryBtn")?.addEventListener("click", (evt) => generatePersonalMemoryDocs(evt.currentTarget).catch((err) => personalSetStatus(err.message || "AI 理解失败", true)));
    $("personalSaveMemoryBtn")?.addEventListener("click", (evt) => savePersonalMemory(evt.currentTarget).catch((err) => personalSetStatus(err.message || "保存失败", true)));
    $("personalSaveRawMemoryBtn")?.addEventListener("click", (evt) => savePersonalRawMemory(evt.currentTarget).catch((err) => personalSetStatus(err.message || "保存失败", true)));
    $("personalMemoryFiles")?.addEventListener("change", handlePersonalUploadFilesChange);
    $("personalCustomReferenceFile")?.addEventListener("change", handlePersonalCustomReferenceChange);
    $("personalSaveMode")?.addEventListener("change", syncPersonalSaveMode);
    $("personalMemoryUseProfile")?.addEventListener("change", (evt) => {
      state.personalMemoryUseProfile = !!evt.target.checked;
    });
    $("personalTargetMemorySelect")?.addEventListener("change", () => {
      syncPersonalSaveMode();
      const id = ($("personalTargetMemorySelect") && $("personalTargetMemorySelect").value) || "";
      if (id) previewPersonalMemory(id).catch((err) => personalSetStatus(err.message || "读取失败", true));
    });
    $("personalSettingsView")?.addEventListener("change", (evt) => {
      const input = evt.target.closest("[data-personal-memory-source]");
      if (!input) return;
      const kind = input.dataset.personalMemorySource || "";
      const map = kind === "keyword"
        ? state.personalMemorySourceKeywords
        : (kind === "competitor"
          ? state.personalMemorySourceCompetitors
          : (kind === "source_doc" ? state.personalMemorySourceDocs : state.personalMemorySourceFiles));
      if (input.value) map[String(input.value)] = !!input.checked;
    });
    $("personalSettingsView")?.addEventListener("click", async (evt) => {
      const keywordBtn = evt.target.closest("[data-delete-personal-keyword]");
      const competitorBtn = evt.target.closest("[data-delete-personal-competitor]");
      const competitorSyncBtn = evt.target.closest("[data-sync-personal-competitor]");
      const uploadRemove = evt.target.closest("[data-remove-personal-upload]");
      const referenceRemove = evt.target.closest("[data-remove-personal-reference]");
      const previewMemoryBtn = evt.target.closest("[data-preview-personal-memory]");
      const deleteMemoryBtn = evt.target.closest("[data-delete-personal-memory]");
      const editTemplateBtn = evt.target.closest("[data-edit-personal-template]");
      const useTemplateBtn = evt.target.closest("[data-use-personal-template]");
      const dispatchTemplateBtn = evt.target.closest("[data-agent-dispatch-template]");
      try {
        if (useTemplateBtn) {
          await usePersonalTemplate(useTemplateBtn.dataset.usePersonalTemplate || "", useTemplateBtn);
          return;
        }
        if (dispatchTemplateBtn) {
          await openAgentManage({ ipTemplateId: dispatchTemplateBtn.dataset.agentDispatchTemplate || "" });
          return;
        }
        if (editTemplateBtn) {
          const row = (state.personalTemplates || []).find((item) => String(item.id || "") === String(editTemplateBtn.dataset.editPersonalTemplate || ""));
          if (row) {
            applyPersonalTemplate(row, { editing: row.source !== "agent" });
            renderPersonalSettings();
          }
          return;
        }
        if (uploadRemove) {
          const idx = Number(uploadRemove.dataset.removePersonalUpload || "-1");
          state.personalUploadFiles = selectedPersonalUploadFiles().filter((_file, fileIdx) => fileIdx !== idx);
          renderPersonalSelectedFiles();
          renderPersonalMemorySourceSelectors();
          return;
        }
        if (referenceRemove) {
          state.personalCustomReferenceFile = null;
          renderPersonalCustomReference();
          return;
        }
        if (previewMemoryBtn) {
          await previewPersonalMemory(previewMemoryBtn.dataset.previewPersonalMemory || "");
          return;
        }
        if (deleteMemoryBtn) {
          await deletePersonalMemory(deleteMemoryBtn.dataset.deletePersonalMemory || "");
          return;
        }
        if (competitorSyncBtn) {
          await syncPersonalCompetitor(competitorSyncBtn.dataset.syncPersonalCompetitor || "", competitorSyncBtn);
          return;
        }
        if (keywordBtn) {
          await api(`/api/ip-content/keywords/${encodeURIComponent(keywordBtn.dataset.deletePersonalKeyword || "")}`, { method: "DELETE" });
          delete state.personalSelectedKeywords[String(keywordBtn.dataset.deletePersonalKeyword || "")];
          removePersonalDefaultId("keyword", keywordBtn.dataset.deletePersonalKeyword || "");
          await refreshPersonalDataPreserveSelection({ keywords: true });
          await savePersonalDefaultSilently();
        }
        if (competitorBtn) {
          await api(`/api/ip-content/competitors/${encodeURIComponent(competitorBtn.dataset.deletePersonalCompetitor || "")}`, { method: "DELETE" });
          delete state.personalSelectedCompetitors[String(competitorBtn.dataset.deletePersonalCompetitor || "")];
          removePersonalDefaultId("competitor", competitorBtn.dataset.deletePersonalCompetitor || "");
          await refreshPersonalDataPreserveSelection({ competitors: true });
          await savePersonalDefaultSilently();
        }
      } catch (err) {
        personalSetStatus(err.message || "操作失败", true);
      }
    });
    $("agentUserSearchBtn")?.addEventListener("click", () => {
      state.agentUsersQuery = (($("agentUserSearchInput") && $("agentUserSearchInput").value) || "").trim();
      state.agentSelectedUserId = "";
      loadAgentUsers(true).catch((err) => toast(err.message || "搜索失败"));
    });
    $("agentUserSearchInput")?.addEventListener("keydown", (evt) => {
      if (evt.key !== "Enter") return;
      state.agentUsersQuery = (($("agentUserSearchInput") && $("agentUserSearchInput").value) || "").trim();
      state.agentSelectedUserId = "";
      loadAgentUsers(true).catch((err) => toast(err.message || "搜索失败"));
    });
    $("agentUserPrevBtn")?.addEventListener("click", () => {
      state.agentUsersOffset = Math.max(0, state.agentUsersOffset - state.agentUsersLimit);
      loadAgentUsers().catch((err) => toast(err.message || "加载失败"));
    });
    $("agentUserNextBtn")?.addEventListener("click", () => {
      if (state.agentUsersOffset + state.agentUsersLimit >= state.agentUsersTotal) return;
      state.agentUsersOffset += state.agentUsersLimit;
      loadAgentUsers().catch((err) => toast(err.message || "加载失败"));
    });
    $("agentSaveGrantBtn")?.addEventListener("click", (evt) => saveAgentGrants(evt.currentTarget).catch((err) => toast(err.message || "授权失败")));
    $("installIosWebclipBtn").addEventListener("click", installIosWebclip);
    $("refreshTasksBtn").addEventListener("click", () => loadTasks({ reset: true }));
    $("refreshRunsBtn").addEventListener("click", () => loadRuns({ reset: true }));
    $("loadMoreTasksBtn")?.addEventListener("click", () => loadTasks({ append: true, reset: false }));
    $("loadMoreRunsBtn")?.addEventListener("click", () => loadRuns({ append: true, reset: false }));
    $("officeWorkHistoryBtn")?.addEventListener("click", () => openWorkHistory({ type: "all", label: "全部记录" }, "office"));
    $("departmentCalendarDays")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-department-date]");
      if (!btn) return;
      state.departmentSelectedDate = btn.dataset.departmentDate || todayDateKey();
      renderDepartmentDayBoard();
    });
    $("officeView")?.addEventListener("click", (evt) => {
      const runBtn = evt.target.closest("[data-open-run-detail]");
      if (runBtn) {
        evt.preventDefault();
        openRunDetail(runBtn.dataset.openRunDetail || "", "office");
        return;
      }
      const taskBtn = evt.target.closest("[data-open-task-detail]");
      if (taskBtn) {
        evt.preventDefault();
        openTaskDetail(taskBtn.dataset.openTaskDetail || "", "office");
        return;
      }
      const departmentBtn = evt.target.closest("[data-role-department]");
      if (departmentBtn && !evt.target.closest("#employeeFloor")) {
        evt.preventDefault();
        openDepartmentView(departmentBtn.dataset.roleDepartment || "");
      }
    });
    $("employeeFloor").addEventListener("click", (evt) => {
      const homeTargetBtn = evt.target.closest("[data-home-target]");
      if (homeTargetBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        const target = String(homeTargetBtn.dataset.homeTarget || "").trim();
        openHomeTarget(target, "office");
        return;
      }
      const pageBtn = evt.target.closest("[data-office-page]");
      if (pageBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        if (pageBtn.disabled) return;
        const nextPage = Number(pageBtn.dataset.officePage || "1");
        if (!Number.isFinite(nextPage)) return;
        state.officePage = nextPage;
        renderOfficeEmployees();
        return;
      }
      const comingSoonRole = evt.target.closest("[data-role-coming-soon]");
      if (comingSoonRole) {
        evt.preventDefault();
        evt.stopPropagation();
        toast("敬请期待");
        return;
      }
      const metric = evt.target.closest("[data-device-filter]");
      if (metric) {
        evt.preventDefault();
        evt.stopPropagation();
        setOfficeDeviceFilter(metric.dataset.deviceFilter || "all");
        return;
      }
      const secretaryCard = evt.target.closest("[data-secretary-role]");
      if (secretaryCard) {
        evt.preventDefault();
        evt.stopPropagation();
        switchTab("secretary");
        return;
      }
      const departmentCard = evt.target.closest("[data-role-department]");
      if (departmentCard) {
        evt.preventDefault();
        evt.stopPropagation();
        openDepartmentView(departmentCard.dataset.roleDepartment || "");
        return;
      }
      const sign = evt.target.closest("#companySignBtn");
      if (!sign) return;
      evt.preventDefault();
      evt.stopPropagation();
      const next = prompt("给你的 AI 公司起个名字", state.companyName || "我的AI公司");
      if (next === null) return;
      const name = next.trim().slice(0, 24) || "我的AI公司";
      state.companyName = name;
      localStorage.setItem("lobster_h5_company_name", name);
      updateCompanySign();
      updateBossOfficeStats(
        state.devices.filter((d) => d.online).length,
        state.devices.filter((d) => d.online && (activeRunForDevice(d) || unassignedActiveRunForDevice(d) || activeMessageForDevice(d))).length
      );
    });
    document.querySelectorAll("[data-device-filter]").forEach((btn) => {
      btn.addEventListener("click", () => setOfficeDeviceFilter(btn.dataset.deviceFilter || "all"));
    });
    $("hireEmployeeBtn").addEventListener("click", () => {
      if (confirm("雇佣新员工需要到官网下载安装电脑端 online。现在打开官网吗？")) {
        window.open("https://bhzn.top/", "_blank", "noopener,noreferrer");
      }
    });
    $("employeeFloor").addEventListener("click", (evt) => {
      if (evt.target.closest("#companySignBtn")) return;
      if (evt.target.closest("[data-secretary-role]")) return;
      if (evt.target.closest("[data-role-department]")) return;
      const card = evt.target.closest("[data-employee-id]");
      if (!card) return;
      openEmployeeModal(card.dataset.employeeId || "");
    });
    $("employeeModalBackdrop").addEventListener("click", (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      closeEmployeeModal();
    });
    ["click", "touchend", "pointerup"].forEach((type) => {
      $("employeeModalClose").addEventListener(type, (evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        closeEmployeeModal();
      });
    });
    $("employeeWorkListBtn").addEventListener("click", () => {
      closeEmployeeModal();
      openWorkHistory({ type: "all", label: "全部记录" }, "profile");
    });
    $("employeeRenameBtn").addEventListener("click", () => {
      renameActiveEmployee();
    });
    $("employeeWorkbenchBtn").addEventListener("click", () => {
      if ($("employeeWorkbenchBtn").disabled) return;
      closeEmployeeModal();
      switchTab("office");
    });
    window.addEventListener("resize", () => {
      if (document.querySelector("#officeView.active")) renderOfficeEmployees();
    });
    $("workTimeline").addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-run-task-now]");
      if (!btn) return;
      runTaskNow(btn.dataset.runTaskNow || "", btn);
    });
    $("logoutBtn").addEventListener("click", () => {
      localStorage.removeItem("lobster_h5_token");
      state.token = "";
      location.reload();
    });
    $("captchaImg").addEventListener("click", refreshCaptcha);
    $("sendSmsBtn").addEventListener("click", async () => {
      const phone = normalizePhone($("phone").value);
      const captchaAnswer = $("captcha").value.trim();
      if (!phone) {
        toast("请输入有效手机号");
        return;
      }
      if (!captchaAnswer) {
        toast("请先填写图形验证码");
        return;
      }
      $("sendSmsBtn").disabled = true;
      try {
        const resp = await fetch(apiUrl("/auth/sms/send"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            phone,
            captcha_id: $("captchaId").value || "",
            captcha_answer: captchaAnswer,
          }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || "发送失败");
        toast("验证码已发送");
        setSmsCooldown(60);
        await refreshCaptcha();
      } catch (err) {
        toast(err.message || "发送失败");
        $("sendSmsBtn").disabled = false;
        await refreshCaptcha();
      }
    });

    $("taskList").addEventListener("click", (evt) => {
      const taskDetailBtn = evt.target.closest("[data-open-task-detail]");
      if (taskDetailBtn) {
        openTaskDetail(taskDetailBtn.dataset.openTaskDetail || "", "taskList");
        return;
      }
      const detailBtn = evt.target.closest("[data-open-run-detail]");
      if (detailBtn) {
        openRunDetail(detailBtn.dataset.openRunDetail || "", "taskList");
        return;
      }
      const runBtn = evt.target.closest("[data-run-task-now]");
      if (runBtn) {
        runTaskNow(runBtn.dataset.runTaskNow || "", runBtn);
        return;
      }
      const editBtn = evt.target.closest("[data-edit-task]");
      if (editBtn) {
        openTaskEditModal(editBtn.dataset.editTask || "");
        return;
      }
      const statusBtn = evt.target.closest("[data-task-status]");
      if (statusBtn) {
        setTaskStatus(statusBtn.dataset.taskStatus || "", statusBtn.dataset.nextStatus || "", statusBtn);
        return;
      }
      const btn = evt.target.closest("[data-delete-task]");
      if (!btn) return;
      deleteTask(btn.dataset.deleteTask, btn);
    });

    $("runList").addEventListener("click", (evt) => {
      const detailBtn = evt.target.closest("[data-open-run-detail]");
      if (detailBtn) {
        openRunDetail(detailBtn.dataset.openRunDetail || "", "runList");
        return;
      }
      const publishBtn = evt.target.closest("[data-publish-run]");
      if (publishBtn) {
        openPublishRunModal(publishBtn.dataset.publishRun || "");
        return;
      }
      const openPublishBtn = evt.target.closest("[data-open-publish-run]");
      if (openPublishBtn) {
        openPublishRunModal(openPublishBtn.dataset.openPublishRun || "", parseInt(openPublishBtn.dataset.publishMediaIndex || "-1", 10));
        return;
      }
      const copyBtn = evt.target.closest("[data-copy-media]");
      if (copyBtn) {
        copyMediaLink(copyBtn.dataset.copyMedia || "");
        return;
      }
      const iosDownload = evt.target.closest("[data-ios-download]");
      if (iosDownload) onIosDownloadClick();
    });

    $("taskPageBody")?.addEventListener("click", (evt) => {
      const runBtn = evt.target.closest("[data-run-task-now]");
      if (runBtn) {
        runTaskNow(runBtn.dataset.runTaskNow || "", runBtn);
        return;
      }
      const detailBtn = evt.target.closest("[data-open-run-detail]");
      if (detailBtn) {
        openRunDetail(detailBtn.dataset.openRunDetail || "", "taskDetail");
        return;
      }
      const editBtn = evt.target.closest("[data-edit-task]");
      if (editBtn) {
        openTaskEditModal(editBtn.dataset.editTask || "");
        return;
      }
      const statusBtn = evt.target.closest("[data-task-status]");
      if (statusBtn) {
        setTaskStatus(statusBtn.dataset.taskStatus || "", statusBtn.dataset.nextStatus || "", statusBtn);
        return;
      }
      const delBtn = evt.target.closest("[data-delete-task]");
      if (delBtn) deleteTask(delBtn.dataset.deleteTask || "", delBtn);
    });

    $("runPageBody")?.addEventListener("click", (evt) => {
      const refillBtn = evt.target.closest("[data-refill-run]");
      if (refillBtn) {
        refillRunToWorkbench(refillBtn.dataset.refillRun || "").catch((err) => toast(err.message || "回填失败"));
        return;
      }
      const toggle = evt.target.closest("[data-toggle-moment]");
      if (toggle) {
        const item = toggle.closest(".moment-item");
        if (item) item.classList.toggle("open");
        return;
      }
      const copyToggle = evt.target.closest("[data-toggle-copy-record]");
      if (copyToggle) {
        const item = copyToggle.closest(".copy-item");
        if (item) {
          item.classList.toggle("open");
          const tip = copyToggle.querySelector("small");
          if (tip) tip.textContent = item.classList.contains("open") ? "收起" : "展开";
        }
        return;
      }
      const selectGroupBtn = evt.target.closest("[data-select-draft-group]");
      if (selectGroupBtn) {
        toggleDraftGroupSelection(selectGroupBtn.dataset.selectDraftGroup || "");
        return;
      }
      const copySelectedBtn = evt.target.closest("[data-copy-selected-drafts]");
      if (copySelectedBtn) {
        copySelectedDrafts(copySelectedBtn.dataset.copySelectedDrafts || "");
        return;
      }
      const genBtn = evt.target.closest("[data-generate-selected-moment-images]");
      if (genBtn) {
        generateSelectedMomentImages(genBtn);
        return;
      }
      const publishBtn = evt.target.closest("[data-publish-run]");
      if (publishBtn) {
        openPublishRunModal(publishBtn.dataset.publishRun || "");
        return;
      }
      const openPublishBtn = evt.target.closest("[data-open-publish-run]");
      if (openPublishBtn) {
        openPublishRunModal(openPublishBtn.dataset.openPublishRun || "", parseInt(openPublishBtn.dataset.publishMediaIndex || "-1", 10));
        return;
      }
      const copyBtn = evt.target.closest("[data-copy-media]");
      if (copyBtn) {
        copyMediaLink(copyBtn.dataset.copyMedia || "");
        return;
      }
      const iosDownload = evt.target.closest("[data-ios-download]");
      if (iosDownload) onIosDownloadClick();
    });

    $("workTimeline").addEventListener("click", (evt) => {
      if (evt.target.closest("[data-run-task-now]")) return;
      const node = evt.target.closest("[data-open-run-detail]");
      if (!node) return;
      openRunDetail(node.dataset.openRunDetail || "", "workList");
    });

    $("openDouyinLeadsScheduleBtn")?.addEventListener("click", () => switchTab("douyinLeadsSchedule"));
    $("backToDouyinStatusBtn")?.addEventListener("click", () => switchTab("douyinLeads"));
    $("refreshDouyinLeadsBtn")?.addEventListener("click", () => loadDouyinStatus());
    $("douyinTaskScheduleType")?.addEventListener("change", updateDouyinScheduleFields);
    $("douyinAddDailyTimeBtn")?.addEventListener("click", () => addDouyinDailyTime());
    $("douyinTaskResetBtn")?.addEventListener("click", () => resetDouyinTaskForm());
    $("submitDouyinTaskBtn")?.addEventListener("click", async () => {
      try { await submitDouyinTask(); } catch (err) { setDouyinTaskInlineMessage(err.message || "提交失败，请稍后重试", true); toast(err.message || "提交失败，请稍后重试"); }
    });
    $("submitDouyinTaskBtnBottom")?.addEventListener("click", async () => {
      try { await submitDouyinTask(); } catch (err) { setDouyinTaskInlineMessage(err.message || "提交失败，请稍后重试", true); toast(err.message || "提交失败，请稍后重试"); }
    });
    $("douyinTaskActionGrid")?.addEventListener("click", (evt) => {
      const btn = evt.target.closest("[data-douyin-task-action]");
      if (!btn) return;
      state.douyinTaskAction = btn.getAttribute("data-douyin-task-action") || "search_collect";
      renderDouyinTaskActions();
      renderDouyinTaskDetailFields();
    });
    $("douyinTaskDetailFields")?.addEventListener("change", (evt) => {
      const target = evt.target;
      if (!target) return;
      if (target.matches && target.matches("[data-douyin-region]")) {
        const value = String(target.value || "");
        const boxes = Array.from(document.querySelectorAll("[data-douyin-region]"));
        if (value === "全国" && target.checked) {
          boxes.forEach((el) => {
            if (String(el.value || "") !== "全国") el.checked = false;
          });
        } else if (value !== "全国" && target.checked) {
          const nationwide = boxes.find((el) => String(el.value || "") === "全国");
          if (nationwide) nationwide.checked = false;
        }
        normalizeDouyinRegionSelection();
        refreshDouyinRegionSummary();
      }
    });
    $("douyinTaskDetailFields")?.addEventListener("click", (evt) => {
      const target = evt.target;
      if (!target) return;
      if (target.id === "douyinRegionTrigger" || target.closest?.("#douyinRegionTrigger")) {
        const trigger = $("douyinRegionTrigger");
        const expanded = trigger?.getAttribute("aria-expanded") === "true";
        setDouyinRegionPanelOpen(!expanded);
        return;
      }
      if (target.id === "douyinRegionResetBtn" || target.closest?.("#douyinRegionResetBtn")) {
        evt.preventDefault();
        document.querySelectorAll("[data-douyin-region]").forEach((el) => {
          el.checked = String(el.value || "") === "全国";
        });
        normalizeDouyinRegionSelection();
        refreshDouyinRegionSummary();
        setDouyinRegionPanelOpen(false);
      }
    });

    $("homeQuickGrid").addEventListener("click", (evt) => {
      const btn = evt.target.closest("button");
      if (!btn) return;
      const item = workQuickItemByKey(btn.dataset.workQuick || "");
      if (item && item.dispatchKind) {
        openWorkDispatchModal(item.key);
        return;
      }
      const homeTarget = (btn.dataset.homeTarget || "").trim();
      if (homeTarget) {
        openHomeTarget(homeTarget, "office");
        return;
      }
      switchTab("messages");
      setMessageTemplate(btn.dataset.prompt || "");
      if (btn.dataset.imageVideo === "1") setTimeout(() => $("imageInput").click(), 120);
      setTimeout(scrollMessagesToBottom, 160);
    });

    $("workDispatchBackdrop").addEventListener("click", closeWorkDispatchModal);
    $("workDispatchClose").addEventListener("click", closeWorkDispatchModal);
    $("workDispatchCancel").addEventListener("click", closeWorkDispatchModal);
    $("workDispatchForm").addEventListener("submit", (evt) => {
      evt.preventDefault();
      submitWorkDispatch().catch((err) => toast(err.message || "下发失败"));
    });
    $("abilityWorkbenchForm")?.addEventListener("submit", (evt) => {
      evt.preventDefault();
      submitAbilityWorkbench().catch((err) => toast(err.message || "提交失败"));
    });

    $("messageInput").addEventListener("input", autosizeMessageInput);
    $("messageInput").addEventListener("keydown", (evt) => {
      if (evt.key === "Enter" && !evt.shiftKey && !evt.isComposing) {
        if ($("sendForm").classList.contains("expanded")) return;
        evt.preventDefault();
        $("sendForm").requestSubmit();
      }
    });
    $("toggleComposerBtn").addEventListener("click", () => {
      setComposerExpanded(!$("sendForm").classList.contains("expanded"));
    });
    $("voiceMicBtn")?.addEventListener("mousedown", startVoiceCapture);
    $("voiceMicBtn")?.addEventListener("touchstart", startVoiceCapture, { passive: false });
    document.addEventListener("mousedown", (evt) => {
      const btn = evt.target.closest(".office-voice-entry");
      if (!btn) return;
      startOfficeVoiceCapture(evt).catch((err) => {
        state.officeVoiceHoldActive = false;
        setOfficeVoiceButtonPressed(false);
        toast(err.message || "无法启动语音识别");
      });
    });
    document.addEventListener("touchstart", (evt) => {
      const btn = evt.target.closest(".office-voice-entry");
      if (!btn) return;
      startOfficeVoiceCapture(evt).catch((err) => {
        state.officeVoiceHoldActive = false;
        setOfficeVoiceButtonPressed(false);
        toast(err.message || "无法启动语音识别");
      });
    }, { passive: false });
    window.addEventListener("mouseup", stopVoiceCapture);
    window.addEventListener("touchend", stopVoiceCapture);
    window.addEventListener("touchcancel", stopVoiceCapture);
    window.addEventListener("mouseup", stopOfficeVoiceCapture);
    window.addEventListener("touchend", stopOfficeVoiceCapture);
    window.addEventListener("touchcancel", stopOfficeVoiceCapture);
    $("voiceMicBtn")?.addEventListener("mouseleave", stopVoiceCapture);
    document.addEventListener("mouseleave", stopOfficeVoiceCapture);
    $("voiceToMessagesBtn")?.addEventListener("click", () => sendVoiceDraftToMessages(false));
    $("voiceClearBtn")?.addEventListener("click", clearVoiceDraft);
    $("voiceExpandBtn")?.addEventListener("click", () => {
      state.voiceExpanded = !state.voiceExpanded;
      syncVoiceDraftDisplay();
    });
    $("voiceActionCards")?.addEventListener("click", (evt) => {
      const planAction = evt.target.closest("[data-voice-plan-action]");
      if (planAction) {
        const mode = String(planAction.dataset.voicePlanAction || "").trim();
        const plan = buildVoiceExecutionPlan();
        if (!plan) {
          toast("请先录入一段语音内容");
          return;
        }
        if (mode === "execute") {
          executeVoicePlan().catch((err) => toast(err.message || "执行失败，请稍后重试"));
          return;
        }
        routeVoicePlanToRefine(plan);
        return;
      }
      const actionCard = evt.target.closest("[data-voice-action-index]");
      if (actionCard) {
        const index = Number(actionCard.dataset.voiceActionIndex || "-1");
        const action = Number.isFinite(index) ? (state.voiceActions[index] || null) : null;
        if (!action) return;
        const kind = String(action.kind || "");
        const payload = action.payload && typeof action.payload === "object" ? action.payload : {};
        const content = String(payload.content || state.voiceDraft || "").trim();
        const plan = buildVoiceExecutionPlan();
        if (!content) {
          toast("请先录入一段语音内容");
          return;
        }
        if (shouldRouteVoiceActionThroughPlan(kind, plan)) {
          if (plan.direct) {
            executeVoicePlan().catch((err) => toast(err.message || "鎵ц澶辫触锛岃绋嶅悗閲嶈瘯"));
          } else {
            routeVoicePlanToRefine(plan);
          }
          return;
        }
        if (kind === "submit_message") {
          sendVoiceDraftToMessages(true, "", content);
          return;
        }
        switchTab("messages");
        setMessageTemplate(content);
        return;
      }
      const card = evt.target.closest("[data-voice-template], [data-voice-submit]");
      if (!card) return;
      if (card.dataset.voiceSubmit === "1") {
        sendVoiceDraftToMessages(true);
        return;
      }
      sendVoiceDraftToMessages(false, card.dataset.voiceTemplate || "");
    });

    $("imageInput").addEventListener("change", async () => {
      const files = Array.from($("imageInput").files || []);
      $("imageInput").value = "";
      if (!files.length) return;
      if (!state.token) {
        toast("请先登录后再上传图片");
        return;
      }
      for (const file of files) {
        if (!/^image\//i.test(file.type || "")) {
          toast("只能上传图片");
          continue;
        }
        uploadImageFile(file);
      }
    });

    $("loginForm").addEventListener("submit", async (evt) => {
      evt.preventDefault();
      $("loginBtn").disabled = true;
      try {
        const phone = normalizePhone($("phone").value);
        const code = $("smsCode").value.trim();
        if (!phone) throw new Error("请输入有效手机号");
        if (!code) throw new Error("请输入短信验证码");
        const resp = await fetch(apiUrl("/auth/register-phone"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone, code }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || "进入失败");
        state.token = data.access_token;
        localStorage.setItem("lobster_h5_token", state.token);
        await loadMe();
      } catch (err) {
        toast(err.message || "进入失败");
        await refreshCaptcha();
      } finally {
        $("loginBtn").disabled = false;
      }
    });

    $("passwordLoginForm").addEventListener("submit", async (evt) => {
      evt.preventDefault();
      $("passwordLoginBtn").disabled = true;
      try {
        const account = ($("passwordAccount").value || "").trim();
        const password = $("loginPassword").value;
        if (!account) throw new Error("请输入账号或手机号");
        if (!password) throw new Error("请输入密码");
        const resp = await fetch(apiUrl("/auth/login-phone-password"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account, password }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(normalizeAuthErrorDetail(data.detail) || "登录失败");
        state.token = data.access_token;
        localStorage.setItem("lobster_h5_token", state.token);
        await loadMe();
      } catch (err) {
        toast(err.message || "登录失败");
      } finally {
        $("passwordLoginBtn").disabled = false;
      }
    });

    $("sendForm").addEventListener("submit", async (evt) => {
      evt.preventDefault();
      const input = $("messageInput");
      const content = input.value.trim();
      if (hasUploadingImages()) {
        toast("图片还在上传中，请稍等");
        return;
      }
      if (!content) return;
      input.value = "";
      autosizeMessageInput();
      $("sendBtn").disabled = true;
      const messageContent = buildMessageContent(content);
      addBubble("user", content);
      const bot = addBubble("bot", "已发送，等待本地设备处理...");
      try {
        const data = await api("/api/h5-chat/messages", { method: "POST", json: { content: messageContent, mode: state.mode, installation_id: currentInstallationId() } });
        const msg = data.message || {};
        if (msg.id) {
          state.historyItems.push({ message: msg, events: [] });
          state.historyItems = state.historyItems.slice(-40);
          renderOfficeEmployees();
          renderWorkList();
        }
        if (msg.id) startSse(msg.id, bot);
        clearUploads();
        await refreshDeviceStatus();
      } catch (err) {
        bot.classList.add("err");
        setBubbleText(bot, err.message || "发送失败");
      } finally {
        $("sendBtn").disabled = false;
        input.focus();
      }
    });

    (async function init() {
      setAuthTab("sms");
      setTaskAbility("goal.video.pipeline");
      const ok = await loadMe();
      if (!ok) {
        $("loginPanel").classList.remove("hidden");
        $("appPanel").classList.add("hidden");
        $("topActions").classList.add("hidden");
        await refreshCaptcha();
      }
    })();
