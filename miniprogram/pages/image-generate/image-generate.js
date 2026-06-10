const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const share = require("../../utils/share");

const MODEL_ID = "gpt-image-2";
const MAX_REFERENCES = 4;
const CREDITS_PER_IMAGE = 60;

const EXAMPLES = [
  {
    title: "鹿鹿",
    image: "https://images.unsplash.com/photo-1484406566174-9da000fda645?auto=format&fit=crop&w=320&q=80",
    prompt: "极简线稿鹿头 logo，白色背景，黑色细线，现代品牌感，干净高级。"
  },
  {
    title: "北极光",
    image: "https://images.unsplash.com/photo-1483347756197-71ef80e95f73?auto=format&fit=crop&w=320&q=80",
    prompt: "梦幻北极光品牌海报，发光羽毛与蓝紫绿色光晕，中心构图，现代商业设计。"
  },
  {
    title: "开放式餐厅",
    image: "https://images.unsplash.com/photo-1554995207-c18c203602cb?auto=format&fit=crop&w=320&q=80",
    prompt: "开放式餐厅室内设计效果图，浅色木质、白色墙面、自然采光，现代高级，真实渲染。"
  },
  {
    title: "灯具",
    image: "https://images.unsplash.com/photo-1513506003901-1e6a229e2d15?auto=format&fit=crop&w=320&q=80",
    prompt: "现代吊灯产品海报，暖色灯光，黑色背景，精致金属材质，商业摄影，高级质感。"
  },
  {
    title: "电影感",
    image: "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=320&q=80",
    prompt: "电影感人物肖像，侧逆光，暖色调，高级广告摄影，细腻皮肤质感，浅景深。"
  }
];

function uniqueRows(rows) {
  const seen = {};
  const out = [];
  (rows || []).forEach((item) => {
    const url = String((item && item.url) || "").trim();
    if (!url || seen[url]) return;
    seen[url] = true;
    out.push(item);
  });
  return out;
}

function parseJsonMaybe(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  const text = String(value || "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

function extractErrorMessage(data) {
  const raw = JSON.stringify(data || {});
  const objects = [data, data && data.error];
  for (let i = 0; i < objects.length; i += 1) {
    const obj = objects[i];
    if (!obj || typeof obj !== "object") continue;
    const msg = obj.message || obj.detail || (obj.error && (obj.error.message || obj.error.detail));
    if (msg) return String(msg);
  }
  const match = raw.match(/(?:参数错误|调用失败|请求失败|生成失败|未配置|积分不足|余额不足)[^。；\n]{0,160}/);
  return match ? match[0] : "";
}

function normalizeSavedAsset(item) {
  const url = String((item && (item.url || item.source_url || item.file_url || item.image_url || item.path || item.b64_json)) || "").trim();
  if (!url) return null;
  return {
    asset_id: String((item && item.asset_id) || ""),
    url,
    media_type: String((item && item.media_type) || "image").toLowerCase()
  };
}

function extractSavedAssets(data) {
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
    if (Array.isArray(saved)) {
      saved.forEach((item) => {
        const normalized = normalizeSavedAsset(item);
        if (normalized) out.push(normalized);
      });
    }
    const images = obj.output && Array.isArray(obj.output.images) ? obj.output.images : [];
    images.forEach((item) => {
      const normalized = normalizeSavedAsset(item);
      if (normalized) out.push(normalized);
    });
    const mediaUrls = Array.isArray(obj.media_urls) ? obj.media_urls : [];
    mediaUrls.forEach((url) => {
      const normalized = normalizeSavedAsset({ url, media_type: "image" });
      if (normalized) out.push(normalized);
    });
    const dataImages = Array.isArray(obj.data) ? obj.data : [];
    dataImages.forEach((item) => {
      const normalized = normalizeSavedAsset(item);
      if (normalized) out.push(normalized);
    });
    const single = normalizeSavedAsset(obj);
    if (single && (obj.url || obj.image_url || obj.file_url || obj.b64_json)) out.push(single);
  };
  visit(data, 0);
  return uniqueRows(out).filter((item) => item.media_type === "image" || !item.media_type);
}

function saveGeneratedAssets(rows, prompt) {
  const tasks = uniqueRows(rows || []).map((item) => {
    if (!/^https?:\/\//i.test(item.url || "")) return Promise.resolve(item);
    return app.request({
      method: "POST",
      url: "/api/assets/save-url",
      data: {
        url: item.url,
        media_type: "image",
        tags: "auto,image_generate,miniprogram",
        prompt,
        model: MODEL_ID
      },
      timeout: 180000
    }).then((saved) => ({
      asset_id: String((saved && saved.asset_id) || ""),
      url: String((saved && (saved.source_url || saved.url)) || item.url || ""),
      media_type: "image",
      saved: true
    })).catch((err) => {
      console.warn("[image-generate] save generated image failed", err);
      return Object.assign({}, item, { saved: false });
    });
  });
  return Promise.all(tasks).then((items) => uniqueRows(items).filter((item) => item.url));
}

function cacheRecentGeneratedImages(rows) {
  const current = wx.getStorageSync("lobster_recent_image_assets") || [];
  const merged = uniqueRows((rows || []).concat(Array.isArray(current) ? current : []))
    .filter((item) => item && item.url)
    .slice(0, 20);
  wx.setStorageSync("lobster_recent_image_assets", merged);
}

function normalizePickerAsset(item) {
  const url = String((item && (item.source_url || item.url || item.preview_url || item.download_url)) || "").trim();
  if (!url) return null;
  return {
    asset_id: String((item && (item.asset_id || item.id)) || url),
    url,
    source_url: url,
    preview_url: String((item && (item.preview_url || item.source_url || item.url)) || url),
    title: String((item && (item.prompt || item.title || item.filename)) || "素材图片"),
    media_type: "image",
    selected: false
  };
}

function imageSizeFor(ratio) {
  const allowed = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"];
  const value = String(ratio || "9:16").trim();
  return allowed.indexOf(value) >= 0 ? value : "1:1";
}

function costHintFor(count) {
  const n = Math.max(1, Number(count || 1));
  return `消耗算力 ${n * CREDITS_PER_IMAGE}`;
}

Page({
  data: {
    phoneBound: false,
    authPanelVisible: false,
    authHint: "图片生成前需要微信登录并绑定手机号。",
    prompt: "",
    promptLength: 0,
    referenceImages: [],
    uploadingReference: false,
    assetPickerVisible: false,
    assetPickerLoading: false,
    assetImages: [],
    selectedAssetIds: [],
    resolutions: [
      { label: "1K", value: "1k" },
      { label: "2K", value: "2k" },
      { label: "4K", value: "4k" }
    ],
    resolution: "2k",
    ratios: [
      { label: "1:1", value: "1:1" },
      { label: "16:9", value: "16:9" },
      { label: "9:16", value: "9:16" },
      { label: "4:3", value: "4:3" },
      { label: "3:4", value: "3:4" }
    ],
    ratio: "9:16",
    counts: [
      { label: "1张", value: 1 },
      { label: "3张", value: 3 },
      { label: "5张", value: 5 },
      { label: "9张", value: 9 }
    ],
    count: 1,
    visibleExamples: EXAMPLES,
    submitting: false,
    progressText: "正在生成",
    resultImages: [],
    costHint: costHintFor(1)
  },

  onLoad() {
    share.showShareMenu();
    app.restoreSession();
    this.refreshAuthState();
  },

  onUnload() {
  },

  refreshAuthState() {
    const phoneBound = Boolean(app.globalData.token && app.globalData.phone);
    this.setData({ phoneBound });
    return phoneBound;
  },

  showAuthPanel(hint) {
    this.refreshAuthState();
    if (this.data.phoneBound) return false;
    this.setData({
      authPanelVisible: true,
      authHint: hint || "图片生成前需要微信登录并绑定手机号。"
    });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshAuthState();
        if (!data.needs_phone_bind && app.globalData.phone) this.setData({ authPanelVisible: false });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      wx.showToast({ title: "手机号授权失败", icon: "none" });
      return;
    }
    const bind = () => app.bindPhone(code).then(() => {
      this.refreshAuthState();
      this.setData({ authPanelVisible: false });
      wx.showToast({ title: "登录成功", icon: "success" });
    });
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind().catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  goBack() {
    if (getCurrentPages().length > 1) {
      wx.navigateBack();
      return;
    }
    wx.switchTab({ url: "/pages/index/index" });
  },

  noop() {},

  onPromptInput(evt) {
    const prompt = evt.detail.value || "";
    this.setData({ prompt, promptLength: prompt.length });
  },

  smartPrompt() {
    const base = (this.data.prompt || "").trim();
    const next = base
      ? `${base}，高级商业设计，干净构图，真实光影，细节精致，适合移动端竖屏展示。`
      : "一张高级商业海报图，主体清晰，现代设计语言，真实光影，干净背景，精致材质，适合移动端竖屏展示。";
    this.setData({ prompt: next, promptLength: next.length });
  },

  chooseReferenceImage() {
    if (this.showAuthPanel("上传参考图前需要先登录。")) return;
    const remain = MAX_REFERENCES - (this.data.referenceImages || []).length;
    if (remain <= 0) {
      wx.showToast({ title: `最多上传${MAX_REFERENCES}张`, icon: "none" });
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
          tags: "input,image_generate,reference"
        },
        token: app.globalData.token || wx.getStorageSync("lobster_token") || "",
        timeout: 180000
      }).then((data) => {
        const url = data.source_url || data.url || "";
        if (!url) throw new Error("上传成功但没有返回图片链接");
        uploaded.push({
          asset_id: data.asset_id || "",
          url,
          source_url: url,
          preview_url: filePath,
          media_type: "image"
        });
      }));
    });
    chain
      .then(() => {
        this.setData({ referenceImages: (this.data.referenceImages || []).concat(uploaded).slice(0, MAX_REFERENCES) });
        wx.showToast({ title: "参考图已上传", icon: "success" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ uploadingReference: false }));
  },

  openAssetPicker() {
    if (this.showAuthPanel("选择素材库图片前需要先登录。")) return;
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
    const assetId = String(evt.currentTarget.dataset.assetId || "");
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
    const selected = this.data.selectedAssetIds || [];
    const remain = MAX_REFERENCES - (this.data.referenceImages || []).length;
    if (remain <= 0) {
      wx.showToast({ title: `最多上传${MAX_REFERENCES}张`, icon: "none" });
      return;
    }
    const selectedMap = {};
    selected.forEach((id) => { selectedMap[id] = true; });
    const picked = (this.data.assetImages || [])
      .filter((item) => selectedMap[item.asset_id])
      .slice(0, remain);
    if (!picked.length) {
      wx.showToast({ title: "请选择图片", icon: "none" });
      return;
    }
    const existing = {};
    (this.data.referenceImages || []).forEach((item) => {
      const key = String(item.asset_id || item.url || item.source_url || "");
      if (key) existing[key] = true;
    });
    const next = picked
      .filter((item) => !existing[String(item.asset_id || item.url || item.source_url || "")])
      .map((item) => ({
        asset_id: item.asset_id,
        url: item.url,
        source_url: item.source_url || item.url,
        preview_url: item.preview_url || item.url,
        media_type: "image"
      }));
    this.setData({
      referenceImages: (this.data.referenceImages || []).concat(next).slice(0, MAX_REFERENCES),
      assetPickerVisible: false,
      selectedAssetIds: [],
      assetImages: (this.data.assetImages || []).map((item) => Object.assign({}, item, { selected: false }))
    });
  },

  removeReferenceImage(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    this.setData({ referenceImages: (this.data.referenceImages || []).filter((_, i) => i !== index) });
  },

  shuffleExamples() {
    const rows = EXAMPLES.slice().sort(() => Math.random() - 0.5);
    this.setData({ visibleExamples: rows });
  },

  useExample(evt) {
    const item = this.data.visibleExamples[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    this.setData({ prompt: item.prompt, promptLength: item.prompt.length });
  },

  setResolution(evt) {
    this.setData({ resolution: evt.currentTarget.dataset.value || "2k" });
  },

  setRatio(evt) {
    this.setData({ ratio: evt.currentTarget.dataset.value || "9:16" });
  },

  setCount(evt) {
    const count = Number(evt.currentTarget.dataset.value || 1);
    this.setData({ count, costHint: costHintFor(count) });
  },

  submitGenerate() {
    if (this.showAuthPanel("图片生成前需要先登录。")) return;
    const prompt = (this.data.prompt || "").trim();
    if (!prompt) {
      wx.showToast({ title: "请输入图片描述", icon: "none" });
      return;
    }
    const refs = uniqueRows((this.data.referenceImages || [])
      .map((item) => ({ url: item.source_url || item.url }))
      .filter((item) => item.url))
      .map((item) => item.url);
    const imageSize = imageSizeFor(this.data.ratio);
    const payload = {
      model: MODEL_ID,
      prompt,
      image_size: imageSize,
      aspect_ratio: imageSize,
      ratio: imageSize,
      num_images: this.data.count,
      n: this.data.count,
      response_format: "url"
    };
    if (refs.length) {
      payload.image_url = refs[0];
      payload.image_urls = refs;
    }
    this.setData({ submitting: true, progressText: "正在生成", resultImages: [] });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/images/generations",
      data: payload,
      timeout: 240000
    })
      .then((data) => {
        const assets = extractSavedAssets(data);
        if (assets.length) {
          this.setData({ resultImages: assets, progressText: "正在保存" });
          return saveGeneratedAssets(assets, prompt);
        }
        throw new Error(extractErrorMessage(data) || "图片生成失败，服务器未返回图片结果");
      })
      .then((assets) => {
        cacheRecentGeneratedImages(assets);
        this.setData({ resultImages: assets, submitting: false, progressText: "生成完成" });
        wx.setStorageSync("lobster_refresh_works", "1");
        wx.setStorageSync("lobster_open_media_tab", "image");
        const savedCount = assets.filter((item) => item.saved !== false && item.asset_id).length;
        wx.showToast({ title: savedCount ? "生成完成" : "生成完成，保存稍后重试", icon: "none" });
      })
      .catch((err) => {
        this.setData({ submitting: false, progressText: "生成失败" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  previewResult(evt) {
    const url = evt.currentTarget.dataset.url;
    const urls = (this.data.resultImages || []).map((item) => item.url).filter(Boolean);
    if (url) wx.previewImage({ urls, current: url });
  },

  saveResult(evt) {
    const item = this.data.resultImages[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    media.saveToAlbum(item)
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        media.copyLink(item.url).finally(() => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      });
  },

  copyResult(evt) {
    const item = this.data.resultImages[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    media.copyLink(item.url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  onShareAppMessage() {
    return share.appShare({
      title: "AI图片生成",
      path: "/pages/image-generate/image-generate"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "AI图片生成"
    });
  }
});
