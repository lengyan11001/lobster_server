const app = getApp();
const api = require("../../utils/api");
const share = require("../../utils/share");

const MODEL_ID = "grok-video-3";
const IMAGE_MODEL_ID = "gpt-image-2";
const POLL_INTERVAL = 6000;
const MAX_POLLS = 30;
const CREDITS_PER_VIDEO = 160;
const TASK_KEY = "lobster_video_inspiration_pending_task";
const WORKS_TASKS_KEY = "lobster_super_video_pending_tasks";
const MAX_REFERENCES = 4;

const COVER_BASE = "https://images.unsplash.com";

const IDEAS = [
  {
    id: "factory-shoes",
    category: "factory",
    title: "鞋厂探货",
    author: "索影",
    plays: "3.8w",
    cover: `${COVER_BASE}/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，真实工厂探访风格。镜头从手拿一双白色运动鞋开始，切到流水线、材料检测、工人细节操作。年轻女主播自然出镜讲解鞋面材质、脚感和工厂直发优势，语气真实、有信任感。画面干净明亮，轻微手持感，适合短视频获客。",
    desc: "工厂源头、产品细节、真人讲解，适合鞋服箱包等源头工厂获客。"
  },
  {
    id: "materials-factory",
    category: "factory",
    title: "木材工厂",
    author: "索影",
    plays: "2.1w",
    cover: `${COVER_BASE}/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，工业工厂实拍质感。大型木材加工车间，传送带、切割设备、堆放整齐的板材，镜头缓慢推进。男主播边走边介绍材料等级、加工能力和交付周期，强调厂家直供、质量稳定。自然环境声，画面真实可信。",
    desc: "适合制造业、建材、机械设备，用工厂实力建立信任。"
  },
  {
    id: "tour-guide",
    category: "commerce",
    title: "工厂带货",
    author: "索影",
    plays: "6.2w",
    cover: `${COVER_BASE}/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，年轻女性在户外景区或展厅前自然讲解产品，背景有品牌展板和开阔环境。她对镜头介绍产品使用场景、价格优势和售后服务，表情亲切专业。傍晚柔和光线，画面高级但真实，适合本地生活和产品带货。",
    desc: "真人口播结合场景展示，适合本地生活、旅游、展厅和产品推广。"
  },
  {
    id: "home-appliance",
    category: "commerce",
    title: "家居装修",
    author: "龙虾精选",
    plays: "2.0w",
    cover: `${COVER_BASE}/photo-1556228453-efd6c1ff04f6?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，家居装修带货短视频。男主播站在整洁家电和装修材料旁，开场直接提出痛点：装修怕踩坑、预算超支。随后展示产品细节、安装前后对比和真实使用场景。语气专业、有成交感，画面明亮干净。",
    desc: "适合家装、家电、建材门店，突出痛点和解决方案。"
  },
  {
    id: "beauty-live",
    category: "commerce",
    title: "美妆直播",
    author: "创意库",
    plays: "4.7w",
    cover: `${COVER_BASE}/photo-1522335789203-aabd1fc54bc9?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，美妆产品种草视频。干净桌面上摆放护肤品，女性主播拿起产品展示质地、上脸效果和适合人群。镜头有特写、试用、包装细节，整体明亮柔和，有直播切片的真实感。字幕节奏强，适合引导咨询。",
    desc: "适合美妆、护肤、日化产品，用试用细节提升转化。"
  },
  {
    id: "restaurant",
    category: "commerce",
    title: "餐饮探店",
    author: "创意库",
    plays: "5.6w",
    cover: `${COVER_BASE}/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，餐饮探店短视频。镜头从门头进入，展示后厨出餐、招牌菜特写、顾客用餐氛围。主播自然介绍套餐价格、口味特色和到店福利。画面热气腾腾、色彩诱人，节奏快，适合同城获客。",
    desc: "适合同城餐饮、门店活动，用环境和菜品刺激到店。"
  },
  {
    id: "logo-brand",
    category: "logo",
    title: "品牌开场",
    author: "品牌组",
    plays: "1.9w",
    cover: `${COVER_BASE}/photo-1633409361618-c73427e4e206?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，高级品牌宣传开场。产品或品牌标识在干净背景中出现，镜头用柔和推近和光影扫过，随后出现真实使用场景和一句强卖点文案。整体现代、明亮、有高级商业广告感，不要暗黑风。",
    desc: "适合品牌首屏、企业宣传、产品发布开场。"
  },
  {
    id: "product-demo",
    category: "creative",
    title: "产品演示",
    author: "创意库",
    plays: "3.4w",
    cover: `${COVER_BASE}/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，科技产品带货视频。产品放在明亮桌面，主播用手演示核心功能，镜头穿插细节特写、使用前后对比和应用场景。节奏清晰，突出一个痛点、一个亮点、一个行动引导。",
    desc: "适合数码、工具、智能硬件，用演示说明价值。"
  },
  {
    id: "knowledge",
    category: "copy",
    title: "知识获客",
    author: "脚本组",
    plays: "2.8w",
    cover: `${COVER_BASE}/photo-1497366754035-f200968a6e72?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，专业知识分享短视频。人物在办公室或工作室对镜头讲解，开头用一句行业痛点吸引注意，中段给出三条实用建议，结尾引导评论咨询。画面稳定、可信、干净，适合老板IP和服务业获客。",
    desc: "适合咨询、培训、招商、服务业，用专业内容拿线索。"
  },
  {
    id: "before-after",
    category: "creative",
    title: "前后对比",
    author: "转化组",
    plays: "7.2w",
    cover: `${COVER_BASE}/photo-1484154218962-a197022b5858?auto=format&fit=crop&w=640&q=80`,
    prompt: "竖屏9:16，强转化前后对比视频。前半段展示用户痛点和混乱状态，后半段展示使用产品或服务后的整洁、高效、满意状态。镜头切换明确，人物表情自然，字幕突出变化和结果，适合广告投放素材。",
    desc: "适合服务、清洁、装修、教育、美业，用结果制造购买理由。"
  }
];

const CATEGORIES = [
  { id: "all", label: "全部" },
  { id: "commerce", label: "电商" },
  { id: "factory", label: "工厂" },
  { id: "creative", label: "创意" },
  { id: "copy", label: "写实" },
  { id: "logo", label: "Logo" }
];

function cleanText(value) {
  return String(value || "").trim();
}

function findIdea(id) {
  return IDEAS.find((item) => item.id === id) || IDEAS[0];
}

function uniqueRows(rows) {
  const seen = {};
  const out = [];
  (rows || []).forEach((item) => {
    const url = cleanText((item && (item.url || item.source_url || item.preview_url)) || "");
    if (!url || seen[url]) return;
    seen[url] = true;
    out.push(item);
  });
  return out;
}

function parseJsonMaybe(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  const text = cleanText(value);
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

function normalizeGeneratedImage(item) {
  const url = cleanText((item && (item.url || item.source_url || item.file_url || item.image_url || item.path || item.b64_json)) || "");
  if (!url) return null;
  return {
    asset_id: cleanText((item && item.asset_id) || ""),
    url,
    source_url: url,
    preview_url: url,
    media_type: cleanText((item && item.media_type) || "image") || "image"
  };
}

function extractGeneratedImages(data) {
  const out = [];
  const visit = (obj, depth) => {
    if (!obj || depth > 5) return;
    if (typeof obj === "string") {
      const parsed = parseJsonMaybe(obj);
      if (parsed) visit(parsed, depth + 1);
      return;
    }
    if (Array.isArray(obj)) {
      obj.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (typeof obj !== "object") return;
    const saved = obj.saved_assets || (obj.result && obj.result.saved_assets);
    if (Array.isArray(saved)) saved.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const images = obj.output && Array.isArray(obj.output.images) ? obj.output.images : [];
    images.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const mediaUrls = Array.isArray(obj.media_urls) ? obj.media_urls : [];
    mediaUrls.forEach((url) => {
      const normalized = normalizeGeneratedImage({ url, media_type: "image" });
      if (normalized) out.push(normalized);
    });
    const dataImages = Array.isArray(obj.data) ? obj.data : [];
    dataImages.forEach((item) => {
      const normalized = normalizeGeneratedImage(item);
      if (normalized) out.push(normalized);
    });
    const single = normalizeGeneratedImage(obj);
    if (single && (obj.url || obj.image_url || obj.file_url || obj.b64_json)) out.push(single);
  };
  visit(data, 0);
  return uniqueRows(out).filter((item) => item.media_type === "image" || !item.media_type);
}

function normalizePickerAsset(item) {
  const url = cleanText((item && (item.source_url || item.url || item.preview_url || item.download_url)) || "");
  if (!url) return null;
  return {
    asset_id: cleanText((item && (item.asset_id || item.id)) || url),
    url,
    source_url: url,
    preview_url: cleanText((item && (item.preview_url || item.source_url || item.url)) || url),
    title: cleanText((item && (item.prompt || item.title || item.filename)) || "素材图片"),
    media_type: "image",
    selected: false
  };
}

function extractTaskId(payload) {
  if (!payload) return "";
  if (typeof payload === "string") {
    try {
      return extractTaskId(JSON.parse(payload));
    } catch (err) {
      return "";
    }
  }
  if (Array.isArray(payload)) {
    for (let i = 0; i < payload.length; i += 1) {
      const value = extractTaskId(payload[i]);
      if (value) return value;
    }
    return "";
  }
  if (typeof payload !== "object") return "";
  const keys = ["id", "task_id", "video_id", "job_id", "request_id", "generation_id", "run_id"];
  for (let i = 0; i < keys.length; i += 1) {
    const value = cleanText(payload[keys[i]]);
    if (value) return value;
  }
  return extractTaskId(payload.data || payload.result || payload.output || payload.task);
}

function extractVideoUrl(payload) {
  const urls = [];
  const add = (value) => {
    const url = cleanText(value);
    if (!url) return;
    if (/^https?:\/\//i.test(url) && /\.(mp4|mov|webm)(\?|#|$)/i.test(url) && urls.indexOf(url) < 0) urls.push(url);
  };
  const visit = (value, depth) => {
    if (!value || depth > 7 || urls.length) return;
    if (typeof value === "string") {
      if (value[0] === "{" || value[0] === "[") {
        try {
          visit(JSON.parse(value), depth + 1);
          return;
        } catch (err) {}
      }
      add(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (typeof value !== "object") return;
    ["url", "video_url", "video", "file_url", "download_url", "output_url", "source_url"].forEach((key) => add(value[key]));
    Object.keys(value).forEach((key) => visit(value[key], depth + 1));
  };
  visit(payload, 0);
  return urls[0] || "";
}

function isTerminalFailure(payload) {
  const text = JSON.stringify(payload || {}).toLowerCase();
  return text.indexOf("failed") >= 0 || text.indexOf("failure") >= 0 || text.indexOf("error") >= 0 || text.indexOf("cancel") >= 0;
}

Page({
  data: {
    categories: CATEGORIES,
    ideas: IDEAS,
    filteredIdeas: IDEAS,
    activeCategory: "all",
    searchText: "",
    previewVisible: false,
    previewIdea: null,
    mode: "gallery",
    selectedIdea: null,
    prompt: "",
    ratio: "9:16",
    count: 1,
    duration: 10,
    countOptions: [1, 2, 3, 4],
    referenceImages: [],
    uploadingReference: false,
    assetPickerVisible: false,
    assetPickerLoading: false,
    assetImages: [],
    selectedAssetIds: [],
    submitting: false,
    progressText: "",
    resultVideoUrl: "",
    costText: `${CREDITS_PER_VIDEO}算力`
  },

  pollTimer: null,
  pollCount: 0,
  activeTaskId: "",

  onLoad(options) {
    share.showShareMenu();
    const ideaId = options && options.idea ? decodeURIComponent(options.idea) : "";
    if (ideaId) this.openCreate(findIdea(ideaId));
  },

  onShow() {
    this.resumePendingTask();
  },

  onUnload() {
    this.stopPolling();
  },

  onHide() {
    this.stopPolling();
  },

  goBack() {
    if (this.data.mode === "create") {
      this.setData({ mode: "gallery", resultVideoUrl: "", progressText: "" });
      return;
    }
    if (getCurrentPages().length > 1) wx.navigateBack();
    else wx.switchTab({ url: "/pages/index/index" });
  },

  filterIdeas() {
    const category = this.data.activeCategory;
    const kw = cleanText(this.data.searchText).toLowerCase();
    const rows = IDEAS.filter((item) => {
      const categoryOk = category === "all" || item.category === category;
      const text = `${item.title} ${item.desc} ${item.prompt}`.toLowerCase();
      return categoryOk && (!kw || text.indexOf(kw) >= 0);
    });
    this.setData({ filteredIdeas: rows });
  },

  setCategory(evt) {
    this.setData({ activeCategory: evt.currentTarget.dataset.category || "all" }, () => this.filterIdeas());
  },

  onSearchInput(evt) {
    this.setData({ searchText: evt.detail.value || "" }, () => this.filterIdeas());
  },

  openPreview(evt) {
    const idea = findIdea(evt.currentTarget.dataset.id);
    this.setData({ previewVisible: true, previewIdea: idea });
  },

  closePreview() {
    this.setData({ previewVisible: false, previewIdea: null });
  },

  createFromPreview() {
    const idea = this.data.previewIdea;
    if (!idea) return;
    this.closePreview();
    this.openCreate(idea);
  },

  createFromCard(evt) {
    this.openCreate(findIdea(evt.currentTarget.dataset.id));
  },

  openCreate(idea) {
    const item = idea || IDEAS[0];
    this.setData({
      mode: "create",
      selectedIdea: item,
      prompt: item.prompt,
      ratio: "9:16",
      count: 1,
      duration: 10,
      referenceImages: [],
      selectedAssetIds: [],
      resultVideoUrl: "",
      progressText: ""
    });
  },

  onPromptInput(evt) {
    this.setData({ prompt: evt.detail.value || "" });
  },

  formatPrompt() {
    const text = cleanText(this.data.prompt);
    if (!text) return;
    const suffix = "画面真实自然，主体清晰，适合短视频获客，避免低清、变形、杂乱文字。";
    if (text.indexOf("适合短视频获客") >= 0) return;
    this.setData({ prompt: `${text}\n${suffix}` });
  },

  clearPrompt() {
    this.setData({ prompt: "" });
  },

  setRatio(evt) {
    this.setData({ ratio: evt.currentTarget.dataset.ratio || "9:16" });
  },

  setCount(evt) {
    this.setData({ count: Number(evt.currentTarget.dataset.count || 1) || 1 });
  },

  setDuration(evt) {
    this.setData({ duration: Number(evt.currentTarget.dataset.duration || 10) || 10 });
  },

  ensureAuth() {
    app.restoreSession();
    if (app.globalData.token && app.globalData.phone) return true;
    wx.showModal({
      title: "需要登录",
      content: "生成视频前需要先登录并绑定手机号。",
      confirmText: "去我的",
      success: (res) => {
        if (res.confirm) wx.switchTab({ url: "/pages/profile/profile" });
      }
    });
    return false;
  },

  chooseReferenceImage() {
    if (!this.ensureAuth()) return;
    const remain = MAX_REFERENCES - (this.data.referenceImages || []).length;
    if (remain <= 0) {
      wx.showToast({ title: `最多添加${MAX_REFERENCES}张`, icon: "none" });
      return;
    }
    wx.chooseMedia({
      count: remain,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      sizeType: ["compressed"],
      success: (res) => {
        const paths = (res.tempFiles || []).map((item) => item.tempFilePath || item.path || "").filter(Boolean);
        if (paths.length) this.uploadReferenceImages(paths);
      }
    });
  },

  uploadReferenceImages(paths) {
    this.setData({ uploadingReference: true });
    const uploaded = [];
    let chain = Promise.resolve();
    paths.forEach((filePath) => {
      chain = chain.then(() => api.uploadFile({
        url: "/api/assets/upload",
        filePath,
        name: "file",
        formData: {
          tags: "input,video_inspiration,reference",
          prompt: this.data.prompt || "",
          model: MODEL_ID
        },
        token: app.globalData.token || wx.getStorageSync("lobster_token") || "",
        timeout: 180000
      }).then((data) => {
        const url = cleanText(data.source_url || data.url || "");
        if (!url) throw new Error("上传成功但没有返回图片链接");
        uploaded.push({
          asset_id: cleanText(data.asset_id || ""),
          url,
          source_url: url,
          preview_url: filePath,
          media_type: "image"
        });
      }));
    });
    chain
      .then(() => {
        const rows = uniqueRows((this.data.referenceImages || []).concat(uploaded)).slice(0, MAX_REFERENCES);
        this.setData({ referenceImages: rows, assetPickerVisible: false });
        wx.showToast({ title: "参考图已添加", icon: "success" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ uploadingReference: false }));
  },

  openAssetPicker() {
    if (!this.ensureAuth()) return;
    this.setData({ assetPickerVisible: true });
    this.loadAssetImages();
  },

  closeAssetPicker() {
    this.setData({ assetPickerVisible: false });
  },

  loadAssetImages() {
    this.setData({ assetPickerLoading: true });
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    const assetReq = app.request({ url: "/api/assets?media_type=image&limit=80" }).catch(() => ({ assets: [] }));
    const mobileReq = deviceId
      ? app.request({ url: `/api/mobile/downloads?device_id=${encodeURIComponent(deviceId)}&media_type=image&limit=80` }).catch(() => ({ items: [] }))
      : Promise.resolve({ items: [] });
    Promise.all([assetReq, mobileReq])
      .then(([assetData, mobileData]) => {
        const rows = uniqueRows(
          (assetData.assets || [])
            .map(normalizePickerAsset)
            .concat((mobileData.items || []).map(normalizePickerAsset))
            .filter(Boolean)
        );
        this.setData({ assetImages: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ assetPickerLoading: false }));
  },

  toggleAssetImage(evt) {
    const assetId = cleanText(evt.currentTarget.dataset.assetId || "");
    if (!assetId) return;
    const selected = this.data.selectedAssetIds || [];
    const exists = selected.indexOf(assetId) >= 0;
    const next = exists ? selected.filter((id) => id !== assetId) : selected.concat(assetId);
    const nextMap = {};
    next.forEach((id) => { nextMap[id] = true; });
    this.setData({
      selectedAssetIds: next,
      assetImages: (this.data.assetImages || []).map((item) => Object.assign({}, item, { selected: !!nextMap[item.asset_id] }))
    });
  },

  confirmAssetPicker() {
    const remain = MAX_REFERENCES - (this.data.referenceImages || []).length;
    if (remain <= 0) {
      wx.showToast({ title: `最多添加${MAX_REFERENCES}张`, icon: "none" });
      return;
    }
    const selectedMap = {};
    (this.data.selectedAssetIds || []).forEach((id) => { selectedMap[id] = true; });
    const picked = (this.data.assetImages || [])
      .filter((item) => selectedMap[item.asset_id])
      .slice(0, remain)
      .map((item) => ({
        asset_id: item.asset_id,
        url: item.url,
        source_url: item.source_url || item.url,
        preview_url: item.preview_url || item.url,
        media_type: "image"
      }));
    if (!picked.length) {
      wx.showToast({ title: "请选择图片", icon: "none" });
      return;
    }
    const rows = uniqueRows((this.data.referenceImages || []).concat(picked)).slice(0, MAX_REFERENCES);
    this.setData({ referenceImages: rows, selectedAssetIds: [], assetPickerVisible: false });
  },

  removeReferenceImage(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const rows = (this.data.referenceImages || []).filter((_, i) => i !== index);
    this.setData({ referenceImages: rows });
  },

  buildTaskRecord(taskId, payload) {
    const idea = this.data.selectedIdea || {};
    const referenceImages = this.data.referenceImages || [];
    const now = Date.now();
    return {
      id: `openmind-${taskId}`,
      task_id: taskId,
      provider: "openmind",
      model: MODEL_ID,
      title: idea.title || "AI视频获客",
      prompt: this.data.prompt,
      ratio: this.data.ratio,
      count: this.data.count,
      duration: this.data.duration,
      cover_url: referenceImages[0] && (referenceImages[0].preview_url || referenceImages[0].source_url || referenceImages[0].url || ""),
      referenceImages,
      status: "processing",
      created_at: new Date(now).toISOString(),
      created_at_ms: now,
      submit_payload: payload || null
    };
  },

  upsertWorksTask(task) {
    if (!task || !task.task_id) return;
    const rows = Array.isArray(wx.getStorageSync(WORKS_TASKS_KEY)) ? wx.getStorageSync(WORKS_TASKS_KEY) : [];
    const next = [task].concat(rows.filter((item) => item && item.task_id !== task.task_id)).slice(0, 50);
    wx.setStorageSync(WORKS_TASKS_KEY, next);
    wx.setStorageSync("lobster_open_super_video", true);
    wx.setStorageSync("lobster_refresh_works", "1");
  },

  showSubmittedModal() {
    wx.showModal({
      title: "提交完成✅",
      content: "视频任务已提交，可以在我的作品的超级视频里等待结果。",
      confirmText: "查看视频",
      cancelText: "继续创作",
      success: (res) => {
        if (res.confirm) {
          wx.setStorageSync("lobster_open_super_video", true);
          wx.switchTab({ url: "/pages/downloads/downloads" });
        }
      }
    });
  },

  submitVideo() {
    if (!this.ensureAuth()) return;
    const prompt = cleanText(this.data.prompt);
    if (!prompt) {
      wx.showToast({ title: "请输入视频创意", icon: "none" });
      return;
    }
    const referenceUrls = (this.data.referenceImages || [])
      .map((item) => cleanText(item.source_url || item.url))
      .filter(Boolean)
      .slice(0, MAX_REFERENCES);
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    const phone = app.globalData.phone || wx.getStorageSync("lobster_phone") || "";
    this.stopPolling();
    this.setData({ submitting: true, resultVideoUrl: "", progressText: referenceUrls.length ? "正在提交视频任务" : "正在生成视频参考图" });
    this.ensureVideoReferenceUrls(prompt, referenceUrls)
      .then((readyReferenceUrls) => this.submitVideoTask(prompt, readyReferenceUrls, deviceId, phone))
      .catch((err) => {
        this.clearPendingTask();
        this.setData({ submitting: false, progressText: "" });
        wx.showToast({ title: api.errorMessage(err) || "提交失败", icon: "none" });
      });
  },

  ensureVideoReferenceUrls(prompt, referenceUrls) {
    if (referenceUrls.length) return Promise.resolve(referenceUrls);
    const imageSize = this.data.ratio === "16:9" ? "16:9" : "9:16";
    const imagePrompt = `${prompt}\n生成一张适合作为图生视频首帧的高清画面，主体清晰，画面干净，构图适合${imageSize}视频，不要文字水印。`;
    return app
      .request({
        method: "POST",
        url: "/api/comfly-proxy/v1/images/generations",
        data: {
          model: IMAGE_MODEL_ID,
          prompt: imagePrompt,
          image_size: imageSize,
          aspect_ratio: imageSize,
          ratio: imageSize,
          num_images: 1,
          n: 1,
          response_format: "url",
          source: "miniprogram_video_inspiration_reference"
        },
        timeout: 240000
      })
      .then((data) => {
        const images = extractGeneratedImages(data);
        const first = images[0];
        const url = first && cleanText(first.source_url || first.url);
        if (!url) throw new Error("参考图生成失败，请上传图片后再生成视频");
        const generatedRef = {
          asset_id: first.asset_id || "",
          url,
          source_url: url,
          preview_url: url,
          media_type: "image",
          generated: true
        };
        this.setData({
          referenceImages: uniqueRows([generatedRef].concat(this.data.referenceImages || [])).slice(0, MAX_REFERENCES),
          progressText: "参考图已生成，正在提交视频任务"
        });
        const recent = Array.isArray(wx.getStorageSync("lobster_recent_image_assets")) ? wx.getStorageSync("lobster_recent_image_assets") : [];
        wx.setStorageSync("lobster_recent_image_assets", uniqueRows([generatedRef].concat(recent)).slice(0, 20));
        wx.setStorageSync("lobster_refresh_works", "1");
        return [url];
      });
  },

  submitVideoTask(prompt, referenceUrls, deviceId, phone) {
    const refs = (referenceUrls || []).filter(Boolean).slice(0, MAX_REFERENCES);
    if (!refs.length) return Promise.reject(new Error("缺少视频参考图"));
    return app
      .request({
        method: "POST",
        url: "/api/comfly-proxy/openmind/v1/videos",
        data: {
          model: MODEL_ID,
          prompt,
          aspect_ratio: this.data.ratio,
          ratio: this.data.ratio,
          duration: this.data.duration,
          seconds: this.data.duration,
          resolution: "720p",
          size: this.data.ratio === "16:9" ? "1280x720" : "720x1280",
          count: this.data.count,
          n: this.data.count,
          images: refs,
          image_url: refs[0] || "",
          image: refs[0] || "",
          reference_image_urls: refs,
          device_id: deviceId,
          phone,
          source: "miniprogram_video_inspiration",
          title: (this.data.selectedIdea && this.data.selectedIdea.title) || "获客灵感"
        },
        timeout: 180000
      })
      .then((data) => {
        const directUrl = extractVideoUrl(data);
        if (directUrl) {
          return this.handleVideoReady(directUrl, data);
        }
        const taskId = extractTaskId(data);
        if (!taskId) throw new Error("任务提交成功但没有返回任务ID");
        const task = this.buildTaskRecord(taskId, data);
        this.upsertWorksTask(task);
        this.clearPendingTask();
        this.activeTaskId = "";
        this.pollCount = 0;
        this.setData({ submitting: false, progressText: "任务已提交，可到超级视频查看进度" });
        this.showSubmittedModal();
        return null;
      });
  },

  savePendingTask(taskId, task) {
    const idea = this.data.selectedIdea || {};
    wx.setStorageSync(TASK_KEY, {
      task_id: taskId,
      prompt: this.data.prompt,
      ratio: this.data.ratio,
      count: this.data.count,
      duration: this.data.duration,
      referenceImages: this.data.referenceImages || [],
      idea_id: idea.id || "",
      created_at: Date.now(),
      works_task: task || null
    });
  },

  clearPendingTask() {
    wx.removeStorageSync(TASK_KEY);
    this.activeTaskId = "";
  },

  resumePendingTask() {
    if (this.data.submitting) return;
    const task = wx.getStorageSync(TASK_KEY);
    if (!task || !task.task_id) return;
    if (Date.now() - Number(task.created_at || 0) > 24 * 60 * 60 * 1000) this.clearPendingTask();
  },

  startPolling(taskId) {
    this.stopPolling();
    this.pollTimer = setInterval(() => this.pollVideo(taskId), POLL_INTERVAL);
    this.pollVideo(taskId);
  },

  pollVideo(taskId) {
    this.pollCount += 1;
    app
      .request({
        url: `/api/comfly-proxy/openmind/v1/videos/${encodeURIComponent(taskId)}`,
        timeout: 60000
      })
      .then((data) => {
        const url = extractVideoUrl(data);
        if (url) {
          this.stopPolling();
          this.handleVideoReady(url, data);
          return;
        }
        if (isTerminalFailure(data)) {
          this.clearPendingTask();
          throw new Error("视频生成失败，请换一个灵感重试");
        }
        if (this.pollCount >= MAX_POLLS) {
          this.stopPolling();
          this.setData({ submitting: false, progressText: "任务还在生成，稍后回到本页会继续查询" });
          wx.showToast({ title: "任务已提交", icon: "success" });
        } else {
          this.setData({ progressText: `视频生成中 ${this.pollCount}/${MAX_POLLS}` });
        }
      })
      .catch((err) => {
        this.stopPolling();
        this.setData({ submitting: false, progressText: "" });
        wx.showToast({ title: api.errorMessage(err) || "生成失败", icon: "none" });
      });
  },

  handleVideoReady(url, payload) {
    this.clearPendingTask();
    this.setData({ resultVideoUrl: url, submitting: false, progressText: "视频已生成" });
    this.saveVideoAsset(url, payload);
    wx.showModal({
      title: "视频已生成",
      content: "结果已展示在当前页面，并会保存到作品页。",
      confirmText: "去作品页",
      cancelText: "继续创作",
      success: (res) => {
        if (res.confirm) {
          wx.setStorageSync("lobster_open_super_video", true);
          wx.switchTab({ url: "/pages/downloads/downloads" });
        }
      }
    });
  },

  saveVideoAsset(url, payload) {
    const idea = this.data.selectedIdea || {};
    app
      .request({
        method: "POST",
        url: "/api/assets/save-url",
        data: {
          url,
          media_type: "video",
          tags: "auto,video_inspiration,miniprogram",
          prompt: this.data.prompt,
          model: MODEL_ID,
          name: `${idea.title || "获客灵感"}-${Date.now()}.mp4`
        },
        timeout: 180000
      })
      .catch((err) => console.warn("[video-inspiration] save video failed", err));
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  goWorks() {
    wx.setStorageSync("lobster_open_super_video", true);
    wx.switchTab({ url: "/pages/downloads/downloads" });
  },

  noop() {},

  onShareAppMessage() {
    const idea = this.data.selectedIdea;
    return share.appShare({
      title: idea ? `${idea.title} - AI视频获客灵感` : "AI视频获客灵感",
      path: idea ? `/pages/video-inspiration/video-inspiration?idea=${encodeURIComponent(idea.id)}` : "/pages/video-inspiration/video-inspiration"
    });
  },

  onShareTimeline() {
    const idea = this.data.selectedIdea;
    return share.timelineShare({
      title: "AI视频获客灵感",
      query: idea ? `idea=${encodeURIComponent(idea.id)}` : ""
    });
  }
});
