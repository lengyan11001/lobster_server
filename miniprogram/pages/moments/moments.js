const app = getApp();
const api = require("../../utils/api");

const IMAGE_MODEL = "gpt-image-2";

function pickText(item) {
  if (!item) return "";
  return String(item.body || item.text || item.content || item.copy || item.title || "").trim();
}

function extractUrls(data) {
  const out = [];
  const push = (u) => {
    if (typeof u === "string" && /^https?:\/\//i.test(u)) out.push(u);
  };
  const saved = (data && data.saved_assets) || [];
  if (Array.isArray(saved)) saved.forEach((a) => push(a && (a.source_url || a.url)));
  const list = (data && (data.data || data.images || data.results)) || [];
  if (Array.isArray(list)) {
    list.forEach((it) => {
      if (typeof it === "string") push(it);
      else if (it) push(it.url || it.source_url || it.image_url);
    });
  }
  return Array.from(new Set(out));
}

Page({
  data: {
    topic: "",
    count: 3,
    copyCount: 10,
    copyRequirement: "",
    imageRequirement: "",
    candidates: [],
    selectedIndex: -1,
    images: [],
    loadingCopy: false,
    loadingImage: false,
    progressText: "",
    pickerOpen: false,
    pickerItems: [],
    pickerLoading: false,
    extracting: false
  },

  onShow() {
    app.restoreSession();
  },

  openAudioPicker() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return;
    }
    this.setData({ pickerOpen: true, pickerLoading: true, pickerItems: [] });
    app.request({ url: "/api/h5/recorder/files?page=1&page_size=30", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || [])
          .filter((row) => row.status === "completed")
          .map((row) => ({
            id: row.id,
            name: String(row.display_name || row.file_name || "录音"),
            time: String(row.created_at || "").replace("T", " ").slice(0, 16)
          }));
        this.setData({ pickerItems: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ pickerLoading: false }));
  },

  closePicker() {
    this.setData({ pickerOpen: false });
  },

  noop() {},

  pickRecording(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.pickerItems[index];
    if (!row || this.data.extracting) return;
    this.setData({ extracting: true, pickerOpen: false, progressText: "AI 正在读录音，生成朋友圈主题…" });
    app.request({
      method: "POST",
      url: `/api/h5/recorder/files/${row.id}/extract-copy-fields`,
      data: { target: "moments" },
      timeout: 240000
    })
      .then((data) => {
        const topic = String((data && data.topic) || "").trim();
        if (!topic) throw new Error("没有生成出主题");
        const count = Math.max(1, Math.min(9, Number((data && data.count) || 3)));
        this.setData({ topic, count, progressText: "" });
        wx.showToast({ title: "已填好主题：" + topic.slice(0, 12) + "…", icon: "none" });
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ extracting: false }));
  },

  onTopicInput(e) {
    this.setData({ topic: e.detail.value });
  },

  setCount(e) {
    this.setData({ count: Number(e.currentTarget.dataset.value || 3) });
  },

  setCopyCount(e) {
    this.setData({ copyCount: Number(e.currentTarget.dataset.value || 10) });
  },

  onCopyRequirement(e) {
    this.setData({ copyRequirement: e.detail.value });
  },

  onImageRequirement(e) {
    this.setData({ imageRequirement: e.detail.value });
  },

  selectCandidate(e) {
    this.setData({ selectedIndex: Number(e.currentTarget.dataset.index || 0) });
  },

  ensureAuth() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return false;
    }
    return true;
  },

  generateCopy() {
    const topic = String(this.data.topic || "").trim();
    if (!topic) {
      wx.showToast({ title: "先填主题或卖点", icon: "none" });
      return;
    }
    if (!this.ensureAuth()) return;
    this.setData({ loadingCopy: true, progressText: "正在生成文案…" });
    app.request({
      method: "POST",
      url: "/api/ip-content/generate/moments-candidates",
      data: {
        extra_requirements: [topic, String(this.data.copyRequirement || "").trim()].filter(Boolean).join("\n"),
        count: Math.max(1, Math.min(20, Number(this.data.copyCount || 10)))
      },
      timeout: 300000
    })
      .then((data) => {
        const rows = (data && (data.items || data.records)) || [];
        const candidates = rows
          .map((row, i) => ({ key: String(row.id || i), text: pickText(row) }))
          .filter((row) => row.text);
        if (!candidates.length) throw new Error("没有生成出文案，请换个主题再试");
        this.setData({ candidates, selectedIndex: 0, progressText: "" });
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ loadingCopy: false }));
  },

  generateImages() {
    const idx = this.data.selectedIndex;
    const picked = this.data.candidates[idx];
    if (!picked) return;
    if (!this.ensureAuth()) return;
    const topic = String(this.data.topic || "").trim();
    const count = Math.max(1, Math.min(9, Number(this.data.count || 3)));
    const imageReq = String(this.data.imageRequirement || "").trim();
    const prompt = `朋友圈配图：${topic}。文案参考：${picked.text.slice(0, 80)}。`
      + (imageReq ? `额外要求：${imageReq}。` : "")
      + "要求干净构图、真实质感、适合微信朋友圈配图，画面不要出现文字。";
    this.setData({ loadingImage: true, images: [], progressText: "正在生成配图…" });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/images/generations",
      data: {
        model: IMAGE_MODEL,
        prompt,
        image_size: "1:1",
        aspect_ratio: "1:1",
        ratio: "1:1",
        num_images: count,
        n: count,
        response_format: "url"
      },
      timeout: 300000
    })
      .then((data) => {
        const urls = extractUrls(data);
        if (!urls.length) throw new Error("图片生成失败，服务器没有返回图片");
        this.setData({ progressText: "正在存入作品…" });
        return Promise.all(urls.map((url) =>
          app.request({
            method: "POST",
            url: "/api/assets/save-url",
            data: { url, media_type: "image", tags: "moments,miniprogram", prompt, model: IMAGE_MODEL },
            timeout: 180000
          }).catch(() => null)
        )).then(() => urls);
      })
      .then((urls) => {
        this.setData({ images: urls, progressText: "" });
        wx.setStorageSync("lobster_refresh_works", "1");
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ loadingImage: false }));
  },

  copySelected() {
    const picked = this.data.candidates[this.data.selectedIndex];
    if (!picked) return;
    wx.setClipboardData({
      data: picked.text,
      success: () => wx.showToast({ title: "文案已复制", icon: "success" })
    });
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url;
    if (url) wx.previewImage({ current: url, urls: this.data.images });
  },

  goWorks() {
    wx.switchTab({ url: "/pages/downloads/downloads" });
  }
});