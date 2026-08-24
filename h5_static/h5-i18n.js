(function initLobsterH5I18n() {
  if (window.LobsterH5I18n) return;

  var DEFAULT_LANGUAGE = "zh-CN";
  var SUPPORTED = { "zh-CN": true, "en-US": true };
  var brand = "bihuo";
  try {
    var host = String(location.hostname || "").trim().toLowerCase().replace(/\.$/, "");
    var domainBrands = { "hikongai.com": "hikong", "www.hikongai.com": "hikong", "admin.hikongai.com": "hikong" };
    brand = domainBrands[host] || String(new URLSearchParams(location.search || "").get("brand") || "bihuo").trim().toLowerCase() || "bihuo";
  } catch (e) {}
  var STORAGE_KEY = "lobster_h5_language:" + brand;
  var USER_STORAGE_PREFIX = "lobster_h5_language:user:";
  var currentUserId = "";
  var applying = false;
  var scheduled = false;
  var textState = new WeakMap();
  var attrState = new WeakMap();

  var GENERATED_EN = window.LobsterGeneratedEn || {};
  var CURATED_EN = {
    "关联数字人形象 / 分身": "Linked Digital Avatars / Clones",
    "关联声音": "Linked Voices",
    "正在加载资源...": "Loading resources...",
    "暂无可用资源，请先创建并完成数字人资源": "No available resources. Create and finish a digital human resource first.",
    "未命名资源": "Untitled Resource",
    "选择数字人资源": "Select Digital Human Resources",
    "选择数字人形象 / 分身": "Select Digital Avatars / Clones",
    "选择声音": "Select Voices",
    "形象 / 分身": "Avatars / Clones",
    "搜索名称、编号或来源": "Search name, ID, or source",
    "全选搜索结果": "Select All Search Results",
    "取消全选搜索结果": "Deselect All Search Results",
    "清空当前类型": "Clear Current Type",
    "确认选择": "Confirm Selection",
    "尚未选择，点击添加": "Nothing selected. Tap to add.",
    "没有匹配的资源": "No matching resources",
    "上一页": "Previous",
    "下一页": "Next",
    "采集关键词（可选）": "Collection Keywords (Optional)",
    "留空使用当前设备 Online 已配置的全部关键词；手动填写可用逗号或换行分隔": "Leave blank to use all keywords configured in Online for the current device. Separate custom keywords with commas or line breaks.",
    "抖音获客 - Online 全部关键词": "Douyin Leads - All Online Keywords",
    "首页": "Home", "AI员工": "AI Employees", "创作": "Create", "作品": "Content", "我的": "Profile",
    "定时任务": "Scheduled Tasks", "语音输入": "Voice Input", "个人中心": "Profile", "返回顶部": "Back to top",
    "手机号注册和登录": "Phone Sign-in", "默认用验证码登录；设置过密码后也可以直接用账号密码登录。": "Sign in with a verification code by default, or use your password after setting one.",
    "登录方式": "Sign-in method", "验证码登录": "Code Sign-in", "密码登录": "Password Sign-in", "手机号": "Phone Number",
    "图形验证码": "Captcha", "验证码": "Verification Code", "点击刷新": "Tap to refresh", "获取短信验证码": "Send SMS Code",
    "短信验证码": "SMS Code", "进入": "Open", "账号": "Account", "账号 / 手机号": "Account / Phone Number",
    "密码": "Password", "登录": "Sign In", "新手机号会自动创建账号；已有手机号会直接登录。": "A new phone number creates an account automatically; an existing number signs in directly.",

    "企业AI数字化运营": "Enterprise AI Operations", "让AI员工\n24小时为我工作": "AI employees working for me 24/7",
    "台手机": " devices", "个员工": " employees", "个任务运行": " tasks running", "启用中": "Enabled",
    "设置首页图片": "Set home image", "快捷入口": "Shortcuts", "IP人设定位": "IP Persona", "素材库": "Assets",
    "内容记录": "Content Records", "客资线索": "Leads", "教程": "Tutorial", "我的AI员工": "My AI Employees",
    "AI秘书": "AI Secretary", "+定制员工": "+ Custom Employee", "定制员工": "Custom Employee", "正在检查员工状态...": "Checking employee status...",
    "AI营销创作": "AI Marketing", "一站式生成分身 · 视频 · 海报 · 爆款文案": "Create avatars, videos, posters and viral copy in one place",
    "AI数字人口播视频": "AI Digital Human Video", "1分钟克隆你的形象和声音": "Clone your appearance and voice in one minute",
    "AI创作视频": "AI Video Creation", "一键生成爆款短视频": "Generate viral short videos in one click", "AI设计图": "AI Image Design",
    "海报/详情页/朋友圈": "Posters / Detail Pages / Moments", "AI文案智能体": "AI Copy Agent", "朋友圈/种草文/销售话术": "Moments / Social Copy / Sales Scripts",
    "当天执行": "Today's Runs", "全部": "All", "雇佣新员工": "Hire Employee", "老板驾驶舱": "Executive Cockpit",
    "今日交付、类目趋势和风险预警": "Today's delivery, category trends and risk alerts", "能力类目态势": "Capability Overview", "能力类目排行": "Capability Ranking",

    "我的AI员工办公室": "My AI Employee Office", "今日交付、趋势和风险": "Today's delivery, trends and risks", "安排工作": "Schedule Work",
    "远程任务、消息和执行记录": "Remote tasks, messages and run history", "24小时任务编排": "24-hour workflow scheduling",
    "代理商管理": "Agency Management", "工作列表": "Work List", "已完成、当前和待执行的工作节点": "Completed, current and pending workflow nodes",
    "内容详情": "Content Details", "AI 调度助手": "AI Assistant", "用文字或语音安排工作": "Schedule work by text or voice",
    "现场执行台": "On-site Console", "拍照、语音和四类现场任务": "Photos, voice and four on-site task types",
    "整理录音、提炼重点、跟进待办": "Organize recordings, extract key points and follow up on action items",
    "秘书记录": "Secretary Records", "摘要、待办和完整转写": "Summary, action items and full transcript", "记忆文件": "Memory Files",
    "查看完整内容": "View full content", "平台账号": "Platform Accounts", "微信接管中枢": "WeChat Operations Center",
    "客户画像、学习建议和执行结果": "Customer profiles, learning suggestions and execution results", "模板、关键词、同行账号和记忆文件": "Templates, keywords, competitor accounts and memory files",
    "默认展示 10 条，更多用翻页加载": "Shows 10 items by default; use pagination for more", "定时任务详情": "Scheduled Task Details",
    "任务配置和最近执行入口": "Task configuration and recent runs", "执行记录": "Run History", "默认展示 10 条，点开查看具体内容": "Shows 10 items by default; open one for details",
    "执行详情": "Run Details", "结果、文案和生成图片": "Results, copy and generated images", "抖音获客": "Douyin Lead Generation",
    "先看账号与机器状态，再安排采集、评论和私信任务": "Check account and device status before scheduling collection, comments and DMs",
    "安排抖音获客": "Schedule Douyin Lead Generation", "按当前在线设备给抖音账号下发具体获客工作": "Send lead-generation work to the Douyin account on the selected online device",

    "当前设备": "Current Device", "设备状态检查中": "Checking device status", "算力余额": "Credit Balance", "设置": "Settings",
    "消息和任务都走当前账号": "Messages and tasks use the current account", "刷新": "Refresh", "界面语言": "Interface Language",
    "选择后同步到当前账号的所有设备": "Syncs to all devices for this account", "中文": "Chinese", "任务语音播报": "Task Voice Alerts",
    "任务完成、失败或取消时播报状态": "Announce when a task completes, fails or is canceled", "播报音色": "Voice",
    "自动匹配设备中文女声": "Automatically match a Chinese female voice", "温柔女声一": "Gentle Female Voice 1", "温柔女声二": "Gentle Female Voice 2",
    "试听音色": "Preview voice", "iPhone 桌面入口": "iPhone Home Screen", "下载描述文件后，到系统设置里确认安装。": "Download the profile, then confirm installation in Settings.",
    "添加到桌面": "Add to Home Screen", "退出登录": "Sign Out", "用文字或语音安排工作，自动调用现有技能": "Schedule work by text or voice and automatically use available skills",
    "选择在线设备，拍照或上传素材后下发秘书、获客、视频和个微任务": "Select an online device, capture or upload assets, then send secretary, lead, video or WeChat tasks",
    "微信、发布中心、抖音获客": "WeChat, Publishing and Douyin Leads", "查看下级用户，给下级授权员工定制、模板和记忆资料": "View sub-users and grant access to custom employees, templates and memory files",
    "查看已创建的定时任务，进入后默认加载 10 条。": "View scheduled tasks; the first 10 load by default.", "查看任务执行结果，详情页再展示具体内容。": "View task results and open details for full content.",

    "操作": "Actions", "保存模板": "Save Template", "启用到设备": "Enable on Device", "停用": "Disable", "删除员工": "Delete Employee",
    "添加节点": "Add Node", "模板列表": "Templates", "员工模板名称": "Employee Template Name", "给这个自定义员工取一个名字": "Name this custom employee",
    "系统推荐模板": "Recommended Templates", "关闭": "Close", "图片/视频": "Images / Videos", "形象分身": "Avatar Clones", "声音分身": "Voice Clones",
    "内容记录": "Content Records", "生成内容": "Generated Content", "用户上传": "User Uploads", "加载更多": "Load More", "正在读取结果": "Loading results",
    "工作历史": "Work History", "查看工作历史": "View Work History", "任务已进入工作历史。": "The task was added to work history.", "知道了": "Got It",

    "搜索": "Search", "取消": "Cancel", "确认": "Confirm", "确定": "OK", "保存": "Save", "删除": "Delete", "编辑": "Edit",
    "添加": "Add", "新建": "New", "创建": "Create", "启用": "Enable", "开启": "On", "关闭": "Close", "返回": "Back",
    "下一步": "Next", "上一步": "Previous", "提交": "Submit", "查看": "View", "详情": "Details", "更多": "More",
    "名称": "Name", "标题": "Title", "状态": "Status", "时间": "Time", "类型": "Type", "来源": "Source", "设备": "Device",
    "员工": "Employee", "任务": "Task", "进度": "Progress", "结果": "Result", "备注": "Notes", "配置": "Configure",
    "在线": "Online", "离线": "Offline", "执行中": "Running", "排队中": "Queued", "待执行": "Pending", "已完成": "Completed",
    "完成": "Completed", "失败": "Failed", "已取消": "Canceled", "已停止": "Stopped", "已启用": "Enabled", "未启用": "Not Enabled",
    "暂无数据": "No data", "暂无记录": "No records", "暂无任务": "No tasks", "暂无设备": "No devices", "未选择设备": "No device selected",
    "加载中...": "Loading...", "加载中…": "Loading...", "正在加载...": "Loading...", "正在提交": "Submitting", "请稍候，完成后会自动返回结果": "Please wait. The result will appear automatically.",
    "男": "Male", "女": "Female", "是": "Yes", "否": "No", "无": "None", "天": "days", "停止": "Stop", "必火": "Bihuo",
    "张": "images", "段": "clips", "个": "items", "条": "items", "份": "files", "页": "pages", "B站": "Bilibili", "SKU 图": "SKU Images",
    "加载失败": "Failed to load", "保存失败": "Failed to save", "提交失败": "Submission failed", "操作失败": "Operation failed", "请求失败": "Request failed",
    "保存成功": "Saved", "操作成功": "Success", "任务下发成功": "Task Sent", "素材预览": "Asset Preview", "下载素材": "Download Asset",
    "客资明细": "Lead Details", "返回": "Back", "关闭预览": "Close preview", "自定义员工": "Custom Employee",

    "录音设备": "Recording Device", "等待连接录音设备": "Waiting for recording device", "连接设备": "Connect Device", "更换设备": "Change Device",
    "同步录音": "Sync Recordings", "转写记录": "Transcription Records", "录音文件": "Recording Files", "开始录音": "Start Recording", "停止录音": "Stop Recording",
    "播放": "Play", "改名": "Rename", "上一页": "Previous", "下一页": "Next", "转写": "Transcribe", "摘要": "Summary", "待办": "Action Items", "完整转写": "Full Transcript",
    "平台账号": "Platform Accounts", "控制设备": "Controlled Device", "微信接管设置": "WeChat Takeover Settings", "发布账号": "Publishing Accounts",
    "资料调查": "Profile Survey", "关键词": "Keywords", "同行账号": "Competitor Accounts", "个人模板": "Personal Templates", "生成设置": "Generation Settings",
    "销售员工": "Sales Employees", "系统模板": "System Templates", "我的模板": "My Templates", "员工详情": "Employee Details", "节点设置": "Node Settings",
    "设置这个时间点要执行的任务参数": "Configure the task parameters for this time", "开始时间": "Start Time", "结束时间": "End Time", "执行动作": "Actions",
    "固定话术": "Fixed Script", "AI引导加绿泡泡": "AI-guided WeChat", "是否加好友": "Add as Friend", "演示": "Demo",
    "当前执行": "Current Task", "正在执行的任务": "Running Tasks", "停止任务": "Stop Task", "任务执行详情": "Task Details",
    "没有正在执行的任务": "No running tasks", "当前没有任务": "No current task", "精准获客": "Targeted Leads", "抖音私信接管": "Douyin DM Assistant",
    "微信私信接管": "WeChat DM Assistant", "LinkedIn线索挖掘": "LinkedIn Lead Mining", "X线索采集": "X Lead Collection", "TikTok线索采集": "TikTok Lead Collection",
    "最终报告": "Final Report", "候选人": "Candidates", "联系方式": "Contact", "公司": "Company", "职位": "Role", "执行摘要": "Executive Summary",
    "请选择": "Please select", "请输入": "Please enter", "请先选择设备": "Select a device first", "正在检查设备在线状态...": "Checking device status...",
    "设备状态获取失败": "Failed to get device status", "设备状态已刷新": "Device status refreshed", "设备刷新失败": "Failed to refresh devices",

    "已登录": "Signed In", "未启用员工定制": "No employee workflow enabled", "让AI员工": "Let AI employees",
    "24小时为我工作": "work for me 24/7", "一": "Mon", "二": "Tue", "三": "Wed", "四": "Thu", "五": "Fri", "六": "Sat", "日": "Sun",
    "图片": "Images", "视频": "Videos", "文案": "Copy", "公众号文章": "WeChat Articles", "PPT": "PPT",
    "正在读取内容": "Loading content", "内容": "Content", "信息": "Information", "累计触达客资线索": "Total Leads Reached",
    "总触达客户": "Customers Reached", "线索数": "Lead Count", "加好友": "Friend Requests", "拉群数": "Group Invitations",
    "小红书": "Xiaohongshu", "抖音": "Douyin", "快手": "Kuaishou", "视频号": "WeChat Channels",
    "新手入门：如何快速定制你的第一个AI员工？": "Getting Started: Customize Your First AI Employee",
    "基础必看": "Getting Started", "进阶技巧：利用AI营销创作生成爆款文案": "Advanced: Create Viral Copy with AI Marketing",
    "内容创作": "Content Creation", "形象克隆：如何拍摄高质量的数字分身素材": "Avatar Cloning: Capture High-quality Digital Human Footage",
    "全自动化：抖音号矩阵日常获客配置指南": "Automation: Configure Daily Lead Generation for a Douyin Account Network",
    "运营实操": "Operations", "把记忆变成按时交付的内容资产": "Turn Memory into Scheduled Content Assets", "开始": "Start", "可定时下发": "Can be scheduled",

    "创意分镜头视频": "Creative Storyboard Video", "按 10 秒一段生成连续分镜和视频，并合成为完整成片。": "Generate continuous 10-second storyboard clips and combine them into a complete video.",
    "文案+创意图片": "Copy + Creative Images", "根据记忆或自定义提示词生成文案和创意图片，生成图片后结束。": "Generate copy and creative images from memory or custom prompts.",
    "IP日更文案": "Daily IP Copy", "服务器定时同步关键词和同行数据，生成行业口播、专业IP口播和朋友圈文案。": "Sync keywords and competitor data on schedule to create industry narration, professional IP narration and Moments copy.",
    "数字人口播": "Digital Human Narration", "选择本机已有数字人模板和声音，生成数字人口播视频。": "Choose a local digital human template and voice to generate a narration video.",
    "执行方式": "Execution Mode", "一次性": "One-time", "循环执行": "Recurring", "每天固定时间": "Fixed Time Daily",
    "开始时间（可选）": "Start Time (optional)", "间隔分钟": "Interval (minutes)", "每天执行时间": "Daily Execution Time", "添加时间点": "Add Time",
    "输入方式": "Input Method", "上传图片，AI 自动分析分镜": "Upload an image and let AI plan the storyboard",
    "图片 + 提示词共同控制": "Control with image + prompt", "只写提示词，不上传素材": "Prompt only, without uploading assets",
    "参考图片": "Reference Image", "上传图片": "Upload Image", "选择素材": "Choose Asset", "视频需求": "Video Requirements",
    "松开识别 · 上滑取消": "Release to recognize · Swipe up to cancel", "视频时长": "Video Duration", "10 秒": "10 sec", "20 秒": "20 sec",
    "30 秒": "30 sec", "40 秒": "40 sec", "50 秒": "50 sec", "60 秒": "60 sec", "画面比例": "Aspect Ratio",
    "9:16 竖屏带货": "9:16 Vertical Commerce", "16:9 横屏展示": "16:9 Landscape", "1:1 方形信息流": "1:1 Square Feed", "4:5 内容种草": "4:5 Social Content",
    "高级设置": "Advanced Settings", "已按 Online 默认值填充": "Filled with Online defaults", "生成模型": "Generation Model",
    "参考图用途": "Reference Image Use", "分镜参考": "Storyboard Reference", "指定人物": "Specified Person", "指定产品": "Specified Product",
    "参考风格": "Style Reference", "参考场景": "Scene Reference", "普通参考": "General Reference", "视觉基调": "Visual Tone",
    "明亮干净": "Bright and Clean", "生活感暖调": "Warm Lifestyle", "精致高级": "Refined Premium", "叙事电影感": "Cinematic Narrative",
    "镜头节奏": "Shot Rhythm", "平滑推进": "Smooth Progression", "更有动势": "More Dynamic", "偏产品展示": "Product-focused",
    "偏情绪叙事": "Emotion-focused", "结果处理": "Result Handling", "多分镜最终合成一个视频": "Combine all storyboard clips into one video",
    "需要保留音频或配乐规划": "Keep audio or soundtrack planning", "下发任务": "Send Task",

    "快捷消息": "Quick Messages", "正在读取岗位工作入口...": "Loading role workbench...", "查询条件": "Filters",
    "默认按入口筛选": "Filter by entry by default", "暂无工作记录。": "No work records.", "上滑加载更多": "Swipe up to load more",
    "抖音获客工作台": "Douyin Lead Workbench", "读取中": "Loading", "正在读取当前机器上的抖音账号状态...": "Loading Douyin account status on this device...",
    "运行状态": "Run Status", "待刷新": "Refresh needed", "正在整理评论、私信和监控任务状态...": "Loading comment, DM and monitoring task status...",
    "核心数据": "Key Metrics", "安排工作前先看一眼": "Review before scheduling", "正在加载核心指标...": "Loading key metrics...",
    "安排抖音获客工作": "Schedule Douyin Lead Work", "按当前机器的抖音账号状态，给这台设备下发获客任务。你可以先选一个任务类型，再补行业关键词、评论内容和私信内容。": "Send a lead-generation task based on the Douyin account status on this device. Select a task type, then add industry keywords, comment copy and DM copy.",
    "返回状态页": "Back to Status", "先选一个方向": "Choose a direction", "任务参数": "Task Parameters", "请先选择任务类型": "Select a task type first",
    "和现有 H5 定时任务保持一致": "Uses the same settings as H5 scheduled tasks", "执行一次": "Run Once", "循环间隔": "Repeat Interval",
    "任务会通过当前在线的本机设备执行。": "The task runs on the currently online local device.", "重置内容": "Reset", "确认下发": "Send Task",

    "职能中心": "Capability Center", "执行": "Run", "安排": "Schedule", "能力": "Capability", "能力工作台": "Capability Workbench", "独立任务": "Standalone Task",
    "新会话": "New Conversation", "需要确认": "Confirmation Required", "本次对话来源": "Conversation Source", "类目 / 能力": "Category / Capability",
    "发一条消息试试。": "Send a message to get started.", "正在处理": "Processing", "上传本机素材": "Upload Local Asset", "从素材库选择": "Choose from Assets",
    "执行任务前询问": "Ask before running tasks", "完全授权": "Full Access", "直接调用技能执行": "Run skills automatically", "引导": "Guidance",
    "松开发送": "Release to send", "上滑或点击取消": "Swipe up or tap to cancel", "展开全文": "Show Full Text", "等待您的指令...": "Waiting for your instructions...",
    "AI 理解结果": "AI Understanding", "待识别": "Awaiting Recognition", "识别完成后，这里会告诉你当前更像是哪一种任务。": "After recognition, the detected task type will appear here.",
    "建议动作": "Suggested Action", "按住说话": "Hold to Talk", "转到消息页编辑": "Edit on Messages Page", "清空内容": "Clear",

    "快捷中控": "Quick Controls", "语音指令": "Voice Command", "总经办": "Executive Office", "秘书 · 长录音转写摘要": "Secretary · Long Recording Summary",
    "市场部": "Marketing", "获客 · 搜索采集线索": "Leads · Search and Collect", "设计部": "Design", "生成视频 · 图片到分镜视频": "Video · Image to Storyboard",
    "客服部": "Customer Service", "个微 · 执行一轮接管": "WeChat · Run One Takeover Round", "最近执行": "Latest Run",
    "只显示最近一条，点击查看结果。": "Only the latest run is shown. Tap to view the result.", "暂无执行记录": "No run history",
    "录音整理工作台": "Recording Workbench", "整理谈话、提炼重点，让每段录音都变成可跟进的信息。": "Organize conversations and extract key points so every recording becomes actionable.",
    "新整理": "New Processing", "全部记录": "All Records", "本页已整理": "Processed on This Page", "本页处理中": "Processing on This Page",
    "上传音频": "Upload Audio", "记忆音频": "Memory Audio", "本地音频": "Local Audio", "单个文件不超过 200MB": "Maximum 200 MB per file",
    "选择音频文件": "Choose Audio File", "请选择需要转写的音频。": "Select audio to transcribe.", "记忆中的音频": "Audio in Memory",
    "正在读取记忆文件…": "Loading memory files...", "刷新列表": "Refresh List", "刷新只读取目录，选择未同步录音后再上传": "Refresh only reads the directory. Select unsynced recordings before uploading.",
    "正在加载已同步录音…": "Loading synced recordings...", "最新录音优先，可查看摘要、待办和完整原文": "Newest recordings first. View summary, action items and full transcript.",
    "还没有上传的录音。": "No recordings uploaded yet.", "AI秘书 · 整理结果": "AI Secretary · Results"
  };

  var EN = Object.assign({}, GENERATED_EN, CURATED_EN);

  var PHRASES = Object.keys(CURATED_EN).sort(function(a, b) { return b.length - a.length; });
  var ATTRS = ["placeholder", "title", "aria-label", "alt"];
  var SKIP_SELECTOR = "script,style,textarea,pre,code,[contenteditable=true],[data-i18n-skip],.bubble-text,.lead-detail-meta,.lead-detail-item p,.content-record-copy,.personal-memory-preview,.wechat-contact-detail-block p";

  function normalizeLanguage(value) { return SUPPORTED[value] ? value : DEFAULT_LANGUAGE; }
  function readLanguage(userId) {
    try {
      return normalizeLanguage((userId && localStorage.getItem(USER_STORAGE_PREFIX + userId)) || localStorage.getItem(STORAGE_KEY) || DEFAULT_LANGUAGE);
    } catch (e) { return DEFAULT_LANGUAGE; }
  }
  var language = readLanguage("");

  function formatDynamic(core) {
    var match = core.match(/^共\s*(\d+)\s*(条|个|项|人|次|份|页|台)$/);
    if (match) return match[1] + " total";
    match = core.match(/^第\s*(\d+)\s*页$/);
    if (match) return "Page " + match[1];
    match = core.match(/^(\d+)\s*条消息$/);
    if (match) return match[1] + " messages";
    match = core.match(/^(\d+)\s*台手机\s*·\s*(\d+)\s*个员工$/);
    if (match) return match[1] + " devices · " + match[2] + " employees";
    match = core.match(/^(\d+)\s*个任务运行$/);
    if (match) return match[1] + " tasks running";
    return "";
  }

  function translate(value) {
    if (language === DEFAULT_LANGUAGE || !value) return value;
    var source = String(value);
    var core = source.trim();
    if (!core) return value;
    var exact = EN[core] || formatDynamic(core);
    if (exact) return source.slice(0, source.indexOf(core)) + exact + source.slice(source.indexOf(core) + core.length);
    var generatedReplaced = source.replace(/[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z \t，。！？：；、（）《》【】“”‘’·+\-/%&,.!?]{0,159}/g, function(segment) {
      var leading = (segment.match(/^\s*/) || [""])[0];
      var trailing = (segment.match(/\s*$/) || [""])[0];
      var end = segment.length - trailing.length;
      var segmentCore = segment.slice(leading.length, end > leading.length ? end : segment.length);
      return leading + (GENERATED_EN[segmentCore] || EN[segmentCore] || segmentCore) + trailing;
    });
    if (generatedReplaced !== source) return generatedReplaced;
    var replaced = source;
    PHRASES.forEach(function(phrase) {
      if (phrase.length < 2 || replaced.indexOf(phrase) < 0) return;
      var cursor = 0;
      var output = "";
      var hit;
      while ((hit = replaced.indexOf(phrase, cursor)) >= 0) {
        var before = hit > 0 ? replaced.charAt(hit - 1) : "";
        var after = replaced.charAt(hit + phrase.length);
        var adjacentChinese = /[\u4e00-\u9fff]/;
        if ((before && adjacentChinese.test(before)) || (after && adjacentChinese.test(after))) {
          output += replaced.slice(cursor, hit + phrase.length);
        } else {
          output += replaced.slice(cursor, hit) + EN[phrase];
        }
        cursor = hit + phrase.length;
      }
      if (cursor) replaced = output + replaced.slice(cursor);
    });
    return replaced;
  }

  function skipNode(node) {
    return !node || !node.parentElement || Boolean(node.parentElement.closest(SKIP_SELECTOR));
  }

  function translateTextNode(node) {
    if (!node || node.nodeType !== Node.TEXT_NODE || skipNode(node)) return;
    var value = node.nodeValue || "";
    var state = textState.get(node);
    if (!state) state = { original: value, rendered: value };
    else if (value !== state.original && value !== state.rendered) state = { original: value, rendered: value };
    var next = translate(state.original);
    state.rendered = next;
    textState.set(node, state);
    if (value !== next) node.nodeValue = next;
  }

  function translateAttrs(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE || element.matches("[data-i18n-skip]")) return;
    var state = attrState.get(element) || {};
    ATTRS.forEach(function(attr) {
      if (!element.hasAttribute(attr)) return;
      var value = element.getAttribute(attr) || "";
      var item = state[attr];
      if (!item || (value !== item.original && value !== item.rendered)) item = { original: value, rendered: value };
      var next = translate(item.original);
      item.rendered = next;
      state[attr] = item;
      if (value !== next) element.setAttribute(attr, next);
    });
    attrState.set(element, state);
  }

  function apply(root) {
    root = root || document.body;
    if (!root) return;
    applying = true;
    try {
      translateAttrs(root);
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      var node;
      while ((node = walker.nextNode())) {
        if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
        else translateAttrs(node);
      }
      document.documentElement.lang = language;
      document.documentElement.setAttribute("data-language", language);
      var select = document.getElementById("profileLanguageSelect");
      if (select && select.value !== language) select.value = language;
    } finally {
      applying = false;
    }
  }

  function scheduleApply() {
    if (applying || scheduled) return;
    scheduled = true;
    requestAnimationFrame(function() {
      scheduled = false;
      apply(document.body);
    });
  }

  function localApiBase() {
    try {
      var query = new URLSearchParams(location.search || "").get("api_base");
      if (query) return String(query).replace(/\/$/, "");
    } catch (e) {}
    var host = String(location.hostname || "").toLowerCase();
    if ((host === "127.0.0.1" || host === "localhost") && String(location.port || "") === "8000") return "http://127.0.0.1:8002";
    return String(location.origin || "").replace(/\/$/, "");
  }

  function authToken() {
    try {
      return localStorage.getItem("lobster_h5_token:" + brand) || (brand === "bihuo" ? localStorage.getItem("lobster_h5_token") : "") || "";
    } catch (e) { return ""; }
  }

  function persistRemote(next) {
    var token = authToken();
    if (!token) return Promise.resolve(false);
    var url = localApiBase() + "/auth/language?brand=" + encodeURIComponent(brand);
    return fetch(url, {
      method: "POST",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json", "X-Lobster-Brand": brand },
      body: JSON.stringify({ language: next })
    }).then(function(resp) { return resp.ok; }).catch(function() { return false; });
  }

  function saveLocal(next) {
    try {
      localStorage.setItem(STORAGE_KEY, next);
      if (currentUserId) localStorage.setItem(USER_STORAGE_PREFIX + currentUserId, next);
    } catch (e) {}
  }

  function setLanguage(next, options) {
    options = options || {};
    next = normalizeLanguage(next);
    var changed = language !== next;
    language = next;
    saveLocal(next);
    apply(document.body);
    if (changed) window.dispatchEvent(new CustomEvent("lobster:languagechange", { detail: { language: next, surface: "h5" } }));
    return options.persist === false ? Promise.resolve(true) : persistRemote(next);
  }

  function syncUser(userId, serverLanguage) {
    currentUserId = String(userId || "");
    var next = SUPPORTED[serverLanguage] ? serverLanguage : readLanguage(currentUserId);
    return setLanguage(next, { persist: false });
  }

  function bindSelect() {
    var select = document.getElementById("profileLanguageSelect");
    if (!select || select.dataset.i18nBound === "1") return;
    select.dataset.i18nBound = "1";
    select.value = language;
    select.addEventListener("change", function() { setLanguage(select.value); });
  }

  window.LobsterH5I18n = {
    apply: apply,
    getLanguage: function() { return language; },
    setLanguage: setLanguage,
    syncUser: syncUser,
    t: translate,
    supported: ["zh-CN", "en-US"]
  };

  var observer = new MutationObserver(function(records) {
    if (applying) return;
    for (var i = 0; i < records.length; i += 1) {
      if (records[i].type === "characterData" || records[i].addedNodes.length) {
        scheduleApply();
        break;
      }
    }
  });

  function start() {
    bindSelect();
    apply(document.body);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
