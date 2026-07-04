    const $ = (id) => document.getElementById(id);
    const state = {
      token: localStorage.getItem("lobster_h5_token") || "",
      mode: "direct",
      user: null,
      streams: new Map(),
      pollers: new Map(),
      uploads: [],
      devices: [],
      tasks: [],
      runs: [],
      taskListOffset: 0,
      taskListHasNext: false,
      runListOffset: 0,
      runListHasNext: false,
      runDetailBackTab: "runList",
      taskDetailBackTab: "taskList",
      historyItems: [],
      socialLeadJobs: [],
      linkedinJobs: [],
      wechatTranscriptJobs: [],
      companyName: localStorage.getItem("lobster_h5_company_name") || "我的AI公司",
      officeDeviceFilter: "all",
      officePage: 1,
      officePageSize: 6,
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
      ipTemplates: [],
      ipTemplatesLoaded: false,
      ipTemplatesLoading: false,
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
          {
            key: "personal_memory",
            label: "个人记忆（知识库）",
            mark: "记",
            description: "维护品牌资料、同行、关键词和可复用记忆文件。",
            routeTab: "profile",
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
          {
            key: "sales_personal_memory",
            label: "个人记忆（知识库）",
            mark: "记",
            description: "沉淀客户话术、产品资料和销售案例。",
            routeTab: "profile",
            always: true,
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
          {
            key: "ops_personal_memory",
            label: "个人记忆（知识库）",
            mark: "记",
            description: "维护运营资料、商品资料和案例库。",
            routeTab: "profile",
            always: true,
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
          {
            key: "cs_personal_memory",
            label: "个人记忆（知识库）",
            mark: "记",
            description: "客服标准话术、产品问答和服务规则。",
            routeTab: "profile",
            always: true,
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

    function openWorkHistory() {
      closeTaskSuccessDialog();
      switchTab("workList");
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
      return String(payload.capability_id || "");
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
      if ($("bossName")) $("bossName").textContent = `${state.companyName || "我的AI公司"}老板`;
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
        <p class="department-skill-desc">${escapeHtml(node.description || "")}</p>
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
      $("chatContextTitle").textContent = ctx.ability || ctx.department || "来源能力";
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
      $("departmentSubtitle").textContent = department.description || "";
      $("departmentBreadcrumb").innerHTML = `<span>首页</span><span>${escapeHtml(department.name || "")}</span>`;
      $("departmentSkillGrid").innerHTML = (department.children || []).map(abilityCardHtml).join("") || `<div class="quick-empty">这个部门暂时没有配置能力。</div>`;
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
        return taskFieldHtml("模板", taskSelectHtml("abilityIpTemplate", optionHtml("", "模板加载中...")))
          + taskFieldHtml("生成内容", abilityIpDailyTaskOptionsHtml(), true)
          + taskFieldHtml("执行前同步", `<label class="task-checkbox"><input id="abilityIpSyncBefore" type="checkbox" checked>每次执行前同步新数据</label>`, true)
          + taskFieldHtml("补充要求", taskTextareaHtml("abilityIpRequirement", "可选"), true);
      }
      if (id === "goal.video.pipeline") {
        return taskFieldHtml("任务名称", workInputHtml("abilityVideoTitle", "text", "创意视频"))
          + taskFieldHtml("视频要求", taskTextareaHtml("abilityVideoPrompt", "填写视频主题、卖点、场景、风格。不填素材组时默认用 AI 生成首帧。"), true)
          + taskFieldHtml("首帧来源", taskSelectHtml("abilityVideoSourceMode", optionHtml("ai_image", "AI生成首帧") + optionHtml("asset_random", "素材库备选组")))
          + taskFieldHtml("备选素材组", taskSelectHtml("abilityVideoCandidateGroup", optionHtml("", "不选择")));
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
          + taskFieldHtml("商品素材ID或公网图", workInputHtml("abilityEcommerceAsset", "text", "", 'placeholder="填商品主图素材ID；没有可填公网图片URL"'), true)
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
      const subtitle = $("abilityWorkbenchSubtitle");
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
            fillCandidateGroupSelect();
            loadCandidateGroups();
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
        html = `<div class="field full"><button type="submit" id="abilityRouteOpenBtn">${node.routeTab === "profile" ? "打开个人设置" : "打开页面"}</button></div>`;
        submitText = node.routeTab === "profile" ? "打开个人设置" : "打开页面";
        badgeText = "配置入口";
      }
      if (!html) {
        hideAbilityWorkbench();
        return;
      }
      box.classList.remove("hidden");
      if (title) title.textContent = `${node.label || "能力"}工作台`;
      if (subtitle) subtitle.textContent = "填写参数后直接创建任务。";
      if (badge) badge.textContent = badgeText;
      if (fields) fields.innerHTML = html;
      if (submit) {
        submit.disabled = false;
        submit.textContent = submitText;
      }
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
      $("abilityTitle").textContent = node.label || "能力详情";
      $("abilitySubtitle").textContent = node.description || "查看能力说明，继续进入下一级或发起对话。";
      $("abilityBreadcrumb").innerHTML = `<span>首页</span>${labels.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}`;
      $("abilityMark").textContent = node.mark || firstChar(node.label || node.key);
      $("abilityDetailTitle").textContent = node.label || node.key || "能力";
      $("abilityDetailDesc").textContent = node.description || "";
      $("abilityChildren").innerHTML = (node.children || []).map(abilityCardHtml).join("");
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

    function renderOfficeEmployees() {
      const floor = $("employeeFloor");
      if (!floor) return;
      const voiceFab = `<div class="office-voice-fab-wrap"><button class="office-voice-entry" type="button" data-home-target="voice" aria-label="进入语音输入"><span class="voice-entry-icon" aria-hidden="true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"></path><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"></path></svg></span></button></div>`;
      const devices = state.devices || [];
      const snapshots = devices.map((device) => ({ device, snapshot: deviceSnapshot(device) }));
      const workingCount = snapshots.filter((row) => row.snapshot.mode === "working").length;
      const idleCount = snapshots.filter((row) => row.snapshot.mode === "idle").length;
      const offlineCount = snapshots.filter((row) => row.snapshot.mode === "offline").length;
      const onlineCount = workingCount + idleCount;
      if ($("officeTotalCount")) $("officeTotalCount").textContent = String(devices.length);
      if ($("officeOnlineCount")) $("officeOnlineCount").textContent = String(onlineCount);
      if ($("officeWorkingCount")) $("officeWorkingCount").textContent = String(workingCount);
      if ($("officeIdleCount")) $("officeIdleCount").textContent = String(idleCount);
      if ($("officeOfflineCount")) $("officeOfflineCount").textContent = String(offlineCount);
      document.querySelectorAll("[data-device-filter]").forEach((btn) => {
        btn.classList.toggle("active", (state.officeDeviceFilter || "all") === btn.dataset.deviceFilter);
      });
      updateCompanySign();
      updateBossOfficeStats(onlineCount, workingCount);
      const roleDepartments = DEPARTMENT_SKILL_TREE;
      floor.innerHTML = `<button class="company-sign" type="button" id="companySignBtn" aria-label="设置公司名字"><span id="companySignText">${escapeHtml(state.companyName || "我的AI公司")}</span></button><div class="department-role-grid">${roleDepartments.map((department, index) => {
        const childCount = (department.children || []).length;
        const img = employeeAsset({ installation_id: department.id }, index, "idle");
        const bubble = departmentBubbleFor(department, index);
        const hue = ["rgba(19,168,115,.18)", "rgba(36,92,255,.16)", "rgba(240,139,45,.16)", "rgba(19,183,216,.16)", "rgba(126,92,255,.14)"][index % 5];
        return `<button class="department-role-card${bubble ? " has-bubble" : ""}" type="button" data-role-department="${escapeHtml(department.id)}" style="--role-glow:${escapeHtml(hue)}" aria-label="${escapeHtml(department.name)}">
          ${bubble ? `<div class="department-role-bubble">${escapeHtml(bubble)}</div>` : ""}
          <img class="department-role-avatar" src="${escapeHtml(img)}" alt="" loading="lazy">
          <div class="department-role-meta">
            <div class="department-role-name">${escapeHtml(department.name)}</div>
            <div class="department-role-count">${escapeHtml(childCount + "项")}</div>
          </div>
        </button>`;
      }).join("")}</div>`;
      return;
      const departments = DEPARTMENT_SKILL_TREE;
      const metricsForDepartments = employeeLayoutMetrics(Math.max(departments.length, 1));
      floor.style.minHeight = `${metricsForDepartments.minHeight}px`;
      floor.innerHTML = departments.map((department, index) => {
        const childCount = (department.children || []).length;
        const img = employeeAsset({ installation_id: department.id }, index, "idle");
        return `<button class="employee-card idle online" type="button" data-role-department="${escapeHtml(department.id)}" style="${escapeHtml(employeePositionStyle(index, departments.length))}" aria-label="${escapeHtml(department.name)}">
          <div class="employee-scene" aria-hidden="true">
            <div class="employee-progress-bubble">${escapeHtml(department.alias || "职能中心")}</div>
            <div class="screen"></div>
            <div class="desk"></div>
            <div class="person"><img class="employee-avatar-img" src="${escapeHtml(img)}" alt="" loading="lazy"></div>
          </div>
          <div class="employee-meta">
            <div class="employee-title"><strong>${escapeHtml(department.name)}</strong><span class="employee-state">${escapeHtml(childCount + "项")}</span></div>
            <div class="employee-desc">${escapeHtml(department.description || "")}</div>
          </div>
        </button>`;
      }).join("");
      floor.insertAdjacentHTML("afterbegin", `<button class="company-sign" type="button" id="companySignBtn" aria-label="设置公司名字"><span id="companySignText">${escapeHtml(state.companyName || "我的AI公司")}</span></button>`);
      floor.insertAdjacentHTML("beforeend", voiceFab);
      return;
      const visibleDevices = filteredOfficeDevices(devices);
      const pageSize = Math.max(1, Number(state.officePageSize) || 6);
      const pageCount = Math.max(1, Math.ceil(visibleDevices.length / pageSize));
      state.officePage = Math.min(Math.max(1, Number(state.officePage) || 1), pageCount);
      const pageStart = (state.officePage - 1) * pageSize;
      const pageDevices = visibleDevices.slice(pageStart, pageStart + pageSize);
      const metrics = employeeLayoutMetrics(Math.max(pageDevices.length, 1));
      floor.style.minHeight = `${metrics.minHeight}px`;
      if (!devices.length) {
        floor.innerHTML = `<button class="company-sign" type="button" id="companySignBtn" aria-label="设置公司名字"><span id="companySignText">${escapeHtml(state.companyName || "我的AI公司")}</span></button><div class="office-empty"><div class="office-empty-copy">暂未检测到 online 员工。电脑端启动并登录后，这里会出现它的办公位。</div></div><div class="office-voice-fab-wrap"><button class="office-voice-entry" type="button" data-home-target="voice" aria-label="进入语音输入"><span class="voice-entry-icon" aria-hidden="true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"></path><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"></path></svg></span></button></div>`;
        return;
      }
      if (!visibleDevices.length) {
        const label = officeFilterLabel(state.officeDeviceFilter);
        floor.innerHTML = `<button class="company-sign" type="button" id="companySignBtn" aria-label="设置公司名字"><span id="companySignText">${escapeHtml(state.companyName || "我的AI公司")}</span></button><div class="office-filter-note">${escapeHtml(label)}</div><div class="office-empty"><div class="office-empty-copy">当前没有${escapeHtml(label || "符合条件的员工")}。</div></div><div class="office-voice-fab-wrap"><button class="office-voice-entry" type="button" data-home-target="voice" aria-label="进入语音输入"><span class="voice-entry-icon" aria-hidden="true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"></path><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"></path></svg></span></button></div>`;
        return;
      }
      const filterNote = state.officeDeviceFilter !== "all"
        ? `<div class="office-filter-note">${escapeHtml(officeFilterLabel(state.officeDeviceFilter))}</div>`
        : "";
      const pager = pageCount > 1
        ? `<div class="employee-pager" role="navigation" aria-label="员工分页"><button type="button" data-office-page="${state.officePage - 1}" ${state.officePage <= 1 ? "disabled" : ""}>上一页</button><span>${state.officePage}/${pageCount}</span><button type="button" data-office-page="${state.officePage + 1}" ${state.officePage >= pageCount ? "disabled" : ""}>下一页</button></div>`
        : "";
      floor.innerHTML = pageDevices.map((device, index) => {
        const originalIndex = Math.max(0, state.devices.indexOf(device));
        const snapshot = employeeSnapshot(device, originalIndex);
        const mode = snapshot.mode;
        const label = snapshot.label;
        const desc = mode === "working" ? `正在处理：${snapshot.title}` : snapshot.detail;
        const img = employeeAsset(device, originalIndex, mode);
        return `<button class="employee-card ${mode} ${device.online ? "online" : "offline"}" type="button" data-employee-id="${escapeHtml(device.installation_id || "")}" style="${escapeHtml(employeePositionStyle(index, pageDevices.length))}" aria-label="${escapeHtml(employeeName(device, originalIndex))}">
          <div class="employee-scene" aria-hidden="true">
            ${snapshot.bubble ? `<div class="employee-progress-bubble">${escapeHtml(String(snapshot.bubble).slice(0, 46))}</div>` : ""}
            <div class="screen"></div>
            <div class="desk"></div>
            <div class="person"><img class="employee-avatar-img" src="${escapeHtml(img)}" alt="" loading="lazy"></div>
          </div>
          <div class="employee-meta">
            <div class="employee-title"><strong>${escapeHtml(employeeName(device, originalIndex))}</strong><span class="employee-state">${escapeHtml(label)}</span></div>
            <div class="employee-desc">${escapeHtml(desc)}${device.installation_id ? ` · ${escapeHtml(shortId(device.installation_id))}` : ""}</div>
          </div>
        </button>`;
      }).join("");
      floor.insertAdjacentHTML("afterbegin", `<button class="company-sign" type="button" id="companySignBtn" aria-label="设置公司名字"><span id="companySignText">${escapeHtml(state.companyName || "我的AI公司")}</span></button>`);
      if (pager) floor.insertAdjacentHTML("afterbegin", pager);
      if (filterNote) floor.insertAdjacentHTML("afterbegin", filterNote);
      floor.insertAdjacentHTML("beforeend", voiceFab);
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
      const tasks = state.tasks || [];
      const runs = state.runs || [];
      const platforms = new Set();
      [...tasks, ...runs].forEach((row) => collectPlatforms(row).forEach((p) => platforms.add(p)));
      (state.socialLeadJobs || []).forEach((job) => {
        const platform = String((job && (job.platform || (job.request_payload || {}).platform)) || "").trim();
        if (platform) platforms.add(socialPlatformLabel(platform));
      });
      if ((state.linkedinJobs || []).length) platforms.add("LinkedIn");
      if ((state.wechatTranscriptJobs || []).length) platforms.add("视频号");
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
      const messages = state.historyItems.slice(-18).map((entry) => {
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
        ...(state.socialLeadJobs || []).map((job) => workbenchJobItem(job, "social")),
        ...(state.linkedinJobs || []).map((job) => workbenchJobItem(job, "linkedin")),
        ...(state.wechatTranscriptJobs || []).map((job) => workbenchJobItem(job, "wechat")),
      ].filter(Boolean);
      const scheduled = tasks
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
      if ($("workTaskCount")) $("workTaskCount").textContent = String(items.length);
      if ($("workPlatformCount")) $("workPlatformCount").textContent = String(platforms.size);
      if ($("workListSubtitle")) $("workListSubtitle").textContent = `共 ${items.length} 个任务节点 · AI 自动执行`;
      if (!items.length) {
        timeline.innerHTML = `<div class="office-empty">暂无工作记录。创建定时任务或从消息里下发任务后，这里会形成时间线。</div>`;
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
      function douyinLeadActionLabel(action) {
        return ((DOUYIN_TASK_ACTIONS[action] || {}).label || action || "抖音获客");
      }
      function renderDouyinLeadSummary(data) {
        const action = String(data.action || "").trim();
        const stats = data.stats && typeof data.stats === "object" ? data.stats : {};
        const finalStatus = data.final_status && typeof data.final_status === "object" ? data.final_status : {};
        const finalState = finalStatus.state && typeof finalStatus.state === "object" ? finalStatus.state : {};
        const rows = [["任务类型", douyinLeadActionLabel(action)]];
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
        add("任务类型", data.task_kind || data.action || data.capability_id || "");
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
        const media = renderRunMedia(collectRunMediaUrls(run));
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
        ? `${capabilityName(taskCapabilityId(cachedRun) || cachedRun.task_kind)} · ${statusText(cachedRun.status)} · ${fmtTime(cachedRun.created_at)}`
        : "正在读取结果";
      body.innerHTML = cachedRun ? taskDetailHtml(cachedRun) : `<div class="hint">加载中...</div>`;
      switchTab("runDetail");
      try {
        const data = await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}`);
        const run = data.run || {};
        $("runPageTitle").textContent = run.title || "执行详情";
        $("runPageSubtitle").textContent = `${capabilityName(taskCapabilityId(run) || run.task_kind)} · ${statusText(run.status)} · ${fmtTime(run.created_at)}`;
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

    function currentInstallationId() {
      const online = state.devices.find((d) => d.online && d.installation_id);
      const any = state.devices.find((d) => d.installation_id);
      return (online || any || {}).installation_id || "";
    }

    function scrollMessagesToBottom() {
      const messages = $("messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
      const composer = $("sendForm");
      if (composer && typeof composer.scrollIntoView === "function") {
        composer.scrollIntoView({ block: "end", behavior: "smooth" });
      }
    }

    function focusMessageInput() {
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

    function switchTab(tab) {
      const key = tab || "office";
      document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${key}View`));
      document.querySelectorAll("[data-tab-target]").forEach((btn) => btn.classList.toggle("active", btn.dataset.tabTarget === key));
      const titleMap = {
        office: ["必火AI员工", "我的AI员工办公室"],
        home: ["安排工作", "远程任务、消息和执行记录"],
        workList: ["工作列表", "已完成、当前和待执行的工作节点"],
        messages: ["手机会话", "消息结果和素材预览"],
        voice: ["龙虾AI语音助手", ""],
        profile: ["个人中心", "账号和功能入口"],
        taskList: ["定时任务", "默认展示 10 条，更多用翻页加载"],
        taskDetail: ["定时任务详情", "任务配置和最近执行入口"],
        runList: ["执行记录", "默认展示 10 条，点开查看具体内容"],
        runDetail: ["执行详情", "结果、文案和生成图片"],
        douyinLeads: ["抖音获客", "先看账号与机器状态，再安排采集、评论和私信任务"],
        douyinLeadsSchedule: ["安排抖音获客", "按当前在线设备给抖音账号下发具体获客工作"],
      };
      titleMap.department = ["职能中心", "按部门查看能力"];
      titleMap.ability = ["能力详情", "查看能力说明和下一级能力"];
      titleMap.home = ["定时任务", "按时间自动执行内容任务"];
      const nextTitle = titleMap[key] || titleMap.office;
      $("pageTitle").textContent = nextTitle[0];
      $("pageSubtitle").textContent = nextTitle[1];
      $("topBackBtn").classList.toggle("hidden", key === "office");
      $("topActions").classList.toggle("hidden", key !== "office" || !state.token);
      $("topbar").classList.toggle("subpage", key !== "office");
      $("topbar").classList.toggle("voice-page", key === "voice");
      if (key === "office") renderOfficeEmployees();
      if (key === "department") renderDepartmentView();
      if (key === "ability") renderAbilityView();
      if (key !== "office") closeEmployeeModal();
      if (key === "taskList") loadTasks({ reset: true });
      if (key === "runList") loadRuns({ reset: true });
      if (key === "workList") {
        renderWorkList();
        Promise.all([
          loadTasks({ reset: true, limit: 80 }),
          loadRuns({ reset: true, limit: 80 }),
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
        $("loginPanel").classList.add("hidden");
        $("appPanel").classList.remove("hidden");
        switchTab("office");
        await Promise.all([loadHistory(), refreshDeviceStatus(), loadTasks({ reset: true }), loadRuns({ reset: true }), loadTaskSkills()]);
        return true;
      } catch (err) {
        localStorage.removeItem("lobster_h5_token");
        state.token = "";
        return false;
      }
    }

    async function refreshDeviceStatus() {
      if (!state.token) return;
      try {
        const data = await api("/api/h5-chat/devices/status");
        state.devices = Array.isArray(data.devices) ? data.devices : [];
        $("onlineDot").classList.toggle("online", !!data.online);
        const online = state.devices.filter((d) => d.online).length;
        const total = state.devices.length;
        const text = data.online ? `本地在线：${online}/${total || online} 台` : "未检测到本地 online";
        $("deviceText").textContent = text;
        $("profileDeviceText").textContent = text;
        renderOfficeEmployees();
      } catch (err) {
        $("onlineDot").classList.remove("online");
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
      const selects = [$("taskCandidateGroup"), $("abilityVideoCandidateGroup")].filter(Boolean);
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
        const data = await api("/api/scheduled-tasks/assets/creative-candidate-groups");
        state.candidateGroups = Array.isArray(data.groups) ? data.groups : [];
      } catch {
        state.candidateGroups = [];
      }
      fillCandidateGroupSelect();
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
        ? optionHtml("", "请选择账号") + rows.map((row) => optionHtml(String(row.id), `${row.platform_name || platformDisplayName(row.platform)} - ${row.nickname || `账号 #${row.id}`}`)).join("")
        : optionHtml("", platform ? "该平台暂无账号" : "先选择发布平台");
      if (current && rows.some((row) => String(row.id) === current)) sel.value = current;
    }

    async function loadPublishAccounts() {
      if (state.publishAccountsLoaded || state.publishAccountsLoading) {
        fillPublishPlatformSelect();
        return;
      }
      state.publishAccountsLoading = true;
      try {
        const data = await api("/api/scheduled-tasks/publish/accounts");
        state.publishAccounts = Array.isArray(data.accounts) ? data.accounts : [];
        state.publishAccountsLoaded = true;
      } catch (err) {
        state.publishAccounts = [];
        toast(err.message || "发布账号加载失败");
      } finally {
        state.publishAccountsLoading = false;
        fillPublishPlatformSelect();
      }
    }

    function fillIpTemplateSelect() {
      const selects = [$("taskIpTemplate"), $("abilityIpTemplate")].filter(Boolean);
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
        state.ipTemplates = Array.isArray(data.items) ? data.items : [];
        state.ipTemplatesLoaded = true;
      } catch (err) {
        state.ipTemplates = [];
        toast(err.message || "IP模板加载失败");
      } finally {
        state.ipTemplatesLoading = false;
        fillIpTemplateSelect();
      }
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
      if (!raw) throw new Error("请填写素材 ID 或公网链接");
      if (/^https?:\/\//i.test(raw)) return { url: raw };
      return { asset_id: raw };
    }

    function renderWorkHiflyOptions() {
      const avatarSel = $("workAvatar");
      const voiceSel = $("workVoice");
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
    }

    function workDispatchFieldsHtml(item) {
      const key = String(item && item.key || "");
      if (key === "image_composer_studio") {
        return taskFieldHtml("任务标题", workInputHtml("workImageTitle", "text", "创作图片"))
          + taskFieldHtml("图片需求", taskTextareaHtml("workImagePrompt", "例如：一张适合小红书封面的精致产品场景图，暖色自然光，突出卖点"), true);
      }
      if (key === "comfly.seedance.tvc.pipeline") {
        return taskFieldHtml("素材 ID / 公网图", workInputHtml("workSeedanceAsset", "text", "", 'placeholder="输入素材库 asset_id 或 https:// 图片链接"'), true)
          + taskFieldHtml("视频需求", taskTextareaHtml("workSeedanceText", "例如：围绕护肤品做 3 个高级感分镜，突出补水和通透肤感"), true)
          + taskFieldHtml("视频时长", taskSelectHtml("workSeedanceDuration", [10, 20, 30, 40, 50, 60].map((n) => optionHtml(String(n), `${n} 秒`)).join("")))
          + taskFieldHtml("画幅", taskSelectHtml("workSeedanceAspect", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏") + optionHtml("1:1", "1:1 方图")));
      }
      if (key === "comfly.daihuo.pipeline") {
        return taskFieldHtml("素材 ID / 公网图", workInputHtml("workComflyAsset", "text", "", 'placeholder="输入素材库 asset_id 或 https:// 图片链接"'), true)
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
          + taskFieldHtml("人物照片素材 ID / URL", workInputHtml("workLocalPhoto", "text", "", 'placeholder="可选，用于保持人物身份"'), true);
      }
      if (key === "viral_video_remix") {
        return taskFieldHtml("参考视频链接", workInputHtml("workViralVideoUrl", "text", "", 'placeholder="抖音/视频号解析后的直链或可下载公网视频"'), true)
          + taskFieldHtml("人物参考图 URL", workInputHtml("workViralCharacterUrl", "text", "", 'placeholder="可选，人物图公网链接"'))
          + taskFieldHtml("产品参考图 URL", workInputHtml("workViralProductUrl", "text", "", 'placeholder="可选，产品图公网链接"'))
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
        return taskFieldHtml("素材 ID / 公网链接", workInputHtml("workPublishMaterial", "text", "", 'placeholder="asset_id 或 https:// 素材链接"'), true)
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
        const asset = assetOrImagePayload(workValue("workSeedanceAsset"), "素材 ID 或公网图");
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
        const asset = assetOrImagePayload(workValue("workComflyAsset"), "素材 ID 或公网图");
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
        if (!/^https?:\/\//i.test(originalVideoUrl)) throw new Error("请填写参考视频公网链接");
        if (!characterImageUrl && !productImageUrl) throw new Error("请至少填写人物图或产品图公网链接");
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
        const sourceMode = abilityValue("abilityVideoSourceMode") || "ai_image";
        const group = abilityValue("abilityVideoCandidateGroup");
        if (sourceMode !== "ai_image" && !group) throw new Error("请选择备选素材组，或把首帧来源改成 AI生成首帧");
        return {
          title: abilityValue("abilityVideoTitle") || node.label || "创意视频",
          taskKind: "capability",
          content: "H5 能力工作台：创意视频",
          payload: {
            capability_id: "goal.video.pipeline",
            payload: {
              source_mode: sourceMode,
              candidate_group: sourceMode === "ai_image" ? "" : group,
              prompt: abilityValue("abilityVideoPrompt"),
            },
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
        const asset = assetOrImagePayload(abilityValue("abilityEcommerceAsset"), "商品素材ID或公网图");
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
        const platform = socialPlatformFromAbilityKey(node.key);
        if (platform) {
          await submitSocialLeadsWorkbench(node, platform);
        } else if (node.key === "linkedin_leads") {
          await submitLinkedinWorkbench(node);
        } else if (node.key === "wechat_channels_transcript") {
          await submitWechatTranscriptWorkbench(node);
        } else if (node.workQuickKey) {
          const quick = workQuickItemByKey(node.workQuickKey);
          if (!quick) throw new Error("未找到对应下发入口");
          await submitOnceClientTask(collectWorkDispatchPlan(quick));
        } else if (node.capabilityId || node.serverTask) {
          await submitOnceClientTask(collectAbilityCapabilityPlan(node));
        } else {
          throw new Error("这个能力暂未配置下发方式");
        }
        await Promise.all([loadTasks({ reset: true }), loadRuns({ reset: true }).catch(() => {})]);
        renderOfficeEmployees();
        renderWorkList();
        showTaskSuccessDialog("任务已下发成功，可在工作历史查看进度。");
      } finally {
        state.abilityWorkSubmitting = false;
        if (btn) {
          btn.disabled = false;
          btn.textContent = oldText || "下发任务";
        }
      }
    }

    async function submitOnceClientTask(plan) {
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

    function updateGoalVideoSourceMode() {
      const mode = $("taskVideoSourceMode") ? $("taskVideoSourceMode").value : "asset_random";
      if ($("taskCandidateGroupField")) $("taskCandidateGroupField").classList.toggle("hidden", mode === "ai_image");
    }

    function renderTaskParamFields() {
      const host = $("taskParamFields");
      if (!host) return;
      if (state.taskAbility === "ip_content_daily") {
        host.innerHTML = taskFieldHtml("关键词和同行模板", taskSelectHtml("taskIpTemplate", optionHtml("", "模板加载中...")))
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
        host.innerHTML = taskFieldHtml("素材ID或公网图", `<input id="taskComflyAsset" placeholder="填素材ID；没有可填公网图片URL" />`)
          + taskFieldHtml("视频要求", taskTextareaHtml("taskComflyText", "例如：生成一个突出产品卖点和使用场景的爆款TVC"), true)
          + taskFieldHtml("分镜数量", `<input id="taskComflyStoryboardCount" type="number" min="1" max="8" value="5" />`)
          + taskFieldHtml("自动入库", `<label class="task-checkbox"><input id="taskComflyAutoSave" type="checkbox" checked>完成后保存到素材库</label>`);
        return;
      }
      if (state.taskAbility === "comfly.seedance.tvc.pipeline") {
        host.innerHTML = taskFieldHtml("素材ID或公网图", `<input id="taskSeedanceAsset" placeholder="填素材ID；没有可填公网图片URL" />`)
          + taskFieldHtml("视频要求", taskTextareaHtml("taskSeedanceText", "例如：明亮真实的品牌广告，镜头连续，适合投放"), true)
          + taskFieldHtml("总时长", taskSelectHtml("taskSeedanceDuration", [10,20,30,40,50,60].map((n) => optionHtml(String(n), `${n} 秒`)).join("")))
          + taskFieldHtml("画幅", taskSelectHtml("taskSeedanceAspect", optionHtml("9:16", "9:16 竖屏") + optionHtml("16:9", "16:9 横屏")));
        return;
      }
      if (state.taskAbility === "comfly.ecommerce.detail_pipeline") {
        host.innerHTML = taskFieldHtml("商品素材ID或公网图", `<input id="taskEcommerceAsset" placeholder="填商品主图素材ID；没有可填公网图片URL" />`)
          + taskFieldHtml("详情页要求", taskTextareaHtml("taskEcommerceText", "例如：突出材质、卖点、使用场景和购买理由"), true)
          + taskFieldHtml("页面数量", `<input id="taskEcommercePageCount" type="number" min="1" max="20" value="12" />`)
          + taskFieldHtml("自动入库", `<label class="task-checkbox"><input id="taskEcommerceAutoSave" type="checkbox" checked>完成后保存到素材库</label>`);
        return;
      }
      host.innerHTML = taskFieldHtml("首帧图片来源", taskSelectHtml("taskVideoSourceMode", optionHtml("asset_random", "从素材库备选组随机图片") + optionHtml("ai_image", "AI 生成图片")))
        + `<div class="field" id="taskCandidateGroupField"><label>备选素材组</label>${taskSelectHtml("taskCandidateGroup", optionHtml("", "加载中..."))}</div>`
        + taskFieldHtml("提示词（可选）", taskTextareaHtml("taskCreativePrompt", "填写后直接按这段提示词生成；留空则根据记忆资料自动生成文案和画面方向"), true);
      if ($("taskVideoSourceMode")) $("taskVideoSourceMode").addEventListener("change", updateGoalVideoSourceMode);
      updateGoalVideoSourceMode();
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
        const sourceMode = $("taskVideoSourceMode") ? $("taskVideoSourceMode").value : "asset_random";
        const group = $("taskCandidateGroup") ? $("taskCandidateGroup").value : "";
        if (sourceMode !== "ai_image" && !group) throw new Error("请选择创意成片备选素材组");
        return {
          source_mode: sourceMode,
          candidate_group: sourceMode === "ai_image" ? "" : group,
          prompt: $("taskCreativePrompt") ? $("taskCreativePrompt").value.trim() : "",
        };
      }
      if (state.taskAbility === "goal.image.pipeline") {
        const publishPlatform = $("taskPublishPlatform") ? $("taskPublishPlatform").value : "";
        const publishAccountId = $("taskPublishAccount") ? $("taskPublishAccount").value : "";
        const autoPublish = !!($("taskPublishAuto") && $("taskPublishAuto").checked);
        let account = null;
        if (publishAccountId) {
          account = state.publishAccounts.find((row) => String(row.id) === String(publishAccountId)) || null;
        }
        const payload = {
          prompt: $("taskCreativePrompt") ? $("taskCreativePrompt").value.trim() : "",
        };
        if (publishPlatform || publishAccountId || autoPublish) {
          if (!publishPlatform) throw new Error("请选择发布平台");
          if (!publishAccountId) throw new Error("请选择发布账号");
          const parsedId = parseInt(publishAccountId, 10);
          if (Number.isNaN(parsedId)) throw new Error("发布账号无效");
          payload.publish_platform = publishPlatform;
          payload.publish_platform_name = account ? (account.platform_name || platformDisplayName(publishPlatform)) : platformDisplayName(publishPlatform);
          payload.publish_account_id = parsedId;
          payload.publish_account_nickname = account ? (account.nickname || "") : "";
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
        const asset = assetOrImagePayload($("taskComflyAsset") && $("taskComflyAsset").value, "素材ID或公网图");
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
        const asset = assetOrImagePayload($("taskSeedanceAsset") && $("taskSeedanceAsset").value, "素材ID或公网图");
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
        const asset = assetOrImagePayload($("taskEcommerceAsset") && $("taskEcommerceAsset").value, "商品素材ID或公网图");
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
              ${row.last_run_id ? `<button class="ghost" type="button" data-open-run-detail="${escapeHtml(row.last_run_id)}">最近结果</button>` : ""}
              <button class="ghost" type="button" data-delete-task="${escapeHtml(row.id)}">删除</button>
            </div>
          </div>`;
        }).join("");
        $("loadMoreTasksBtn")?.classList.toggle("hidden", !state.taskListHasNext);
      } catch (err) {
        state.tasks = [];
        state.taskListHasNext = false;
        renderWorkList();
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
        ["任务类型", capabilityName(taskCapabilityId(task) || task.task_kind)],
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
      <div class="run-publish-actions">
        <button type="button" data-run-task-now="${escapeHtml(task.id)}">立即执行</button>
        ${task.last_run_id ? `<button type="button" data-open-run-detail="${escapeHtml(task.last_run_id)}">查看最近结果</button>` : ""}
        <button type="button" data-delete-task="${escapeHtml(task.id)}">删除任务</button>
      </div>`;
    }

    function openTaskDetail(taskId, backTab = "taskList") {
      const task = (state.tasks || []).find((row) => String(row.id || "") === String(taskId)) || null;
      state.taskDetailBackTab = backTab || "taskList";
      $("taskPageTitle").textContent = task ? (task.title || "定时任务详情") : "定时任务详情";
      $("taskPageBody").innerHTML = taskPageHtml(task);
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
        btn.dataset.publishRun = draft.run_id || "";
        btn.addEventListener("click", () => requestRunPublish(btn.dataset.publishRun, btn));
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

    async function requestRunPublish(runId, btn) {
      if (!runId) {
        toast("缺少任务记录 ID");
        return;
      }
      if (btn) {
        btn.disabled = true;
        btn.textContent = "提交中...";
      }
      try {
        await api(`/api/scheduled-tasks/runs/${encodeURIComponent(runId)}/publish-request`, { method: "POST", json: {} });
        toast("已提交发布，online 会用已绑定账号发布");
        if (btn) {
          btn.disabled = true;
          btn.textContent = "等待发布";
        }
        await loadRuns({ reset: true });
      } catch (err) {
        toast(err.message || "提交发布失败");
        if (btn) {
          btn.disabled = false;
          btn.textContent = "发布";
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

    function renderRunMedia(urls) {
      if (!urls || !urls.length) return "";
      return `<div class="run-media">${urls.map((url) => {
        const low = url.toLowerCase();
        if (/\.(mp4|webm|mov)(\?|#|$)/.test(low)) {
          return `<div class="run-media-item"><video controls src="${escapeHtml(mediaProxyUrl(url, "inline", filenameFromUrl(url, "lobster-video.mp4")))}"></video>${mediaActionHtml(url, "下载视频", "lobster-video.mp4")}</div>`;
        }
        if (/\.(png|jpe?g|webp|gif)(\?|#|$)/.test(low)) {
          const previewUrl = escapeHtml(mediaProxyUrl(url, "inline", "lobster-image.png"));
          return `<div class="run-media-item"><a href="${previewUrl}" target="_blank" rel="noopener noreferrer"><img src="${previewUrl}" alt="预览"></a>${mediaActionHtml(url, "下载图片", "lobster-image.png")}</div>`;
        }
        return `<div class="run-media-item"><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">打开预览</a>${mediaActionHtml(url, "下载文件", "lobster-media")}</div>`;
      }).join("")}</div>`;
    }

    function renderRunPublishActions(row) {
      const payload = row && row.result_payload && typeof row.result_payload === "object" ? row.result_payload : {};
      const draft = publishDraftFromPayload({ ...payload, run_id: row && row.id });
      if (!draft) return "";
      const status = String(draft.status || "ready").toLowerCase();
      const platform = draft.platform_name || draft.platform || "";
      const account = draft.account_nickname || draft.account_id || "";
      const label = `${publishDraftLabel(draft)}${platform || account ? ` · ${platform}${account ? " · " + account : ""}` : ""}`;
      const canPublish = status !== "published" && status !== "pending" && status !== "processing";
      return `<div class="run-publish-actions">
        <span>${escapeHtml(label)}</span>
        ${canPublish ? `<button type="button" data-publish-run="${escapeHtml(draft.run_id || (row && row.id) || "")}">${status === "failed" ? "重新发布" : "发布"}</button>` : ""}
        ${draft.error ? `<span style="color:var(--red);">${escapeHtml(String(draft.error).slice(0, 80))}</span>` : ""}
      </div>`;
    }

    async function loadRuns(options = {}) {
      const reset = options.reset !== false;
      const append = !!options.append;
      const pageSize = Math.max(1, Math.min(100, parseInt(options.limit || "10", 10) || 10));
      const box = $("runList");
      if (!state.token) return;
      if (reset) {
        state.runListOffset = 0;
        state.runListHasNext = false;
      }
      const offset = append ? state.runListOffset : 0;
      if (box && !append) box.innerHTML = `<div class="hint">加载中...</div>`;
      try {
        const data = await api(`/api/scheduled-tasks/runs?limit=${pageSize}&offset=${offset}`);
        const rows = Array.isArray(data.runs) ? data.runs : [];
        const pagination = data.pagination || {};
        state.runListOffset = offset + rows.length;
        state.runListHasNext = !!pagination.has_next;
        state.runs = append ? (state.runs || []).concat(rows) : rows;
        renderOfficeEmployees();
        renderWorkList();
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
            ${renderRunMedia(collectRunMediaUrls(row))}
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
        if (box) box.innerHTML = `<div class="hint">${escapeHtml(err.message || "执行记录加载失败")}</div>`;
        $("loadMoreRunsBtn")?.classList.add("hidden");
      }
    }

    function syncTopNavigationActions() {
      const homeBtn = document.querySelector('.top-action[data-tab-target="home"]');
      if (homeBtn) {
        homeBtn.setAttribute("aria-label", "定时任务");
        const label = homeBtn.querySelector(".top-action-label");
        if (label) label.textContent = "定时任务";
      }
      document.querySelectorAll('.top-action[data-tab-target="messages"]').forEach((btn) => btn.remove());
    }

    syncTopNavigationActions();

    document.querySelectorAll("[data-tab-target]").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tabTarget));
    });
    document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
      btn.addEventListener("click", () => setAuthTab(btn.dataset.authTab));
    });
    $("topBackBtn").addEventListener("click", () => {
      const activeView = document.querySelector(".view.active");
      const activeId = activeView ? String(activeView.id || "") : "";
      if (activeId === "abilityView") {
        const lookup = activeAbilityLookup();
        const parent = lookup && lookup.trail && lookup.trail.length > 1 ? lookup.trail[lookup.trail.length - 2] : null;
        if (parent && parent.key) {
          openAbilityView(parent.key);
        } else {
          switchTab("department");
        }
        return;
      }
      if (activeId === "departmentView") {
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
      if (activeId === "taskListView" || activeId === "runListView") {
        switchTab("profile");
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
    $("clearChatContextBtn")?.addEventListener("click", () => setChatContext(null));
    document.querySelectorAll("[data-home-target]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = String(btn.dataset.homeTarget || "").trim();
        if (!target) return;
        switchTab(target);
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
    $("refreshStatusBtn").addEventListener("click", refreshDeviceStatus);
    $("chatHistoryBtn")?.addEventListener("click", openWorkHistory);
    $("taskSuccessBackdrop")?.addEventListener("click", closeTaskSuccessDialog);
    $("taskSuccessCloseBtn")?.addEventListener("click", closeTaskSuccessDialog);
    $("taskSuccessHistoryBtn")?.addEventListener("click", openWorkHistory);
    $("refreshProfileBtn").addEventListener("click", refreshDeviceStatus);
    $("installIosWebclipBtn").addEventListener("click", installIosWebclip);
    $("refreshTasksBtn").addEventListener("click", () => loadTasks({ reset: true }));
    $("refreshRunsBtn").addEventListener("click", () => loadRuns({ reset: true }));
    $("loadMoreTasksBtn")?.addEventListener("click", () => loadTasks({ append: true, reset: false }));
    $("loadMoreRunsBtn")?.addEventListener("click", () => loadRuns({ append: true, reset: false }));
    $("employeeFloor").addEventListener("click", (evt) => {
      const homeTargetBtn = evt.target.closest("[data-home-target]");
      if (homeTargetBtn) {
        evt.preventDefault();
        evt.stopPropagation();
        const target = String(homeTargetBtn.dataset.homeTarget || "").trim();
        if (target) switchTab(target);
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
      const metric = evt.target.closest("[data-device-filter]");
      if (metric) {
        evt.preventDefault();
        evt.stopPropagation();
        setOfficeDeviceFilter(metric.dataset.deviceFilter || "all");
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
      switchTab("workList");
    });
    $("employeeRenameBtn").addEventListener("click", () => {
      renameActiveEmployee();
    });
    $("employeeWorkbenchBtn").addEventListener("click", () => {
      if ($("employeeWorkbenchBtn").disabled) return;
      closeEmployeeModal();
      switchTab("home");
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
        requestRunPublish(publishBtn.dataset.publishRun || "", publishBtn);
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
      const delBtn = evt.target.closest("[data-delete-task]");
      if (delBtn) deleteTask(delBtn.dataset.deleteTask || "", delBtn);
    });

    $("runPageBody")?.addEventListener("click", (evt) => {
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
        requestRunPublish(publishBtn.dataset.publishRun || "", publishBtn);
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
        switchTab(homeTarget);
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
        const data = await api("/api/h5-chat/messages", { method: "POST", json: { content: messageContent, mode: state.mode } });
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
      setInterval(refreshDeviceStatus, 7000);
      setInterval(() => {
        if (!state.token) return;
        const activeView = document.querySelector(".view.active");
        if (activeView && (activeView.id === "officeView" || activeView.id === "workListView")) loadRuns({ reset: true });
      }, 5000);
    })();
