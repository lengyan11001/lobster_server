const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");

const POLL_MS = 6000;
const MAX_POLLS = 60;

function pickTemplateId(item) {
  return String(item.styleId || item.style_id || item.templateId || item.id || "").trim();
}

function pickVoiceId(item) {
  return String(item.voiceId || item.voice_id || item.speakerId || item.id || "").trim();
}

Page({
  data: {
    templates: [],
    templatesLoading: false,
    templateIndex: -1,
    voices: [],
    voiceNames: [],
    voiceIndex: -1,
    content: "",
    duration: 30,
    materials: [],
    submitting: false,
    taskId: "",
    statusText: "",
    videoUrl: "",
    progressText: ""
  },

  onLoad() {
    this.polls = 0;
    this.timer = null;
    this.loadTemplates("oralMixCutting");
    this.loadVoices();
  },

  onUnload() {
    this.stopPolling();
  },

  ensureAuth() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return false;
    }
    return true;
  },

  loadTemplates(scene) {
    this.setData({ templatesLoading: true });
    app.request({
      method: "POST",
      url: "/api/shanjian-smart-clip/templates",
      data: { page_size: 24, scene },
      timeout: 60000
    })
      .then((data) => {
        const rows = (data && data.results) || [];
        const templates = rows
          .map((row, i) => ({
            styleId: pickTemplateId(row),
            name: String(row.name || row.title || `模板 ${i + 1}`).slice(0, 20),
            cover: row.cover || row.coverUrl || row.cover_url || ""
          }))
          .filter((row) => row.styleId);
        if (!templates.length && scene !== "realMan") return this.loadTemplates("realMan");
        this.setData({ templates, templateIndex: templates.length ? 0 : -1 });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ templatesLoading: false }));
  },

  loadVoices() {
    app.request({
      method: "POST",
      url: "/api/shanjian-smart-clip/voices",
      data: {},
      timeout: 60000
    })
      .then((data) => {
        const rows = (data && data.results) || [];
        const voices = rows
          .map((row) => ({ id: pickVoiceId(row), name: String(row.name || row.title || "音色").slice(0, 20) }))
          .filter((row) => row.id);
        this.setData({ voices, voiceNames: voices.map((v) => v.name), voiceIndex: voices.length ? 0 : -1 });
      })
      .catch(() => {});
  },

  selectTemplate(e) {
    this.setData({ templateIndex: Number(e.currentTarget.dataset.index || 0) });
  },

  selectVoice(e) {
    this.setData({ voiceIndex: Number(e.detail.value || 0) });
  },

  onContentInput(e) {
    this.setData({ content: e.detail.value });
  },

  onDurationChange(e) {
    this.setData({ duration: Number(e.detail.value || 30) });
  },

  addMaterial() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image", "video"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        const type = file.fileType === "video" ? "video" : "image";
        this.setData({ progressText: "素材上传中…" });
        api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.tempFilePath,
          name: "file",
          formData: { media_type: type, tags: "montage,miniprogram" },
          token: app.globalData.token || ""
        })
          .then((data) => {
            const url = String((data && (data.source_url || data.url)) || "").trim();
            if (!url) throw new Error("上传成功但没有返回链接");
            const materials = this.data.materials.concat({ type, fileUrl: url });
            this.setData({ materials, progressText: "" });
          })
          .catch((err) => {
            this.setData({ progressText: "" });
            wx.showToast({ title: api.errorMessage(err), icon: "none" });
          });
      }
    });
  },

  removeMaterial(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const materials = this.data.materials.slice();
    materials.splice(index, 1);
    this.setData({ materials });
  },

  submit() {
    if (!this.ensureAuth()) return;
    const tpl = this.data.templates[this.data.templateIndex];
    const voice = this.data.voices[this.data.voiceIndex];
    const content = String(this.data.content || "").trim();
    if (!tpl) {
      wx.showToast({ title: "先选一个模板", icon: "none" });
      return;
    }
    if (!voice) {
      wx.showToast({ title: "先选一个声音", icon: "none" });
      return;
    }
    if (!content) {
      wx.showToast({ title: "先填口播文案", icon: "none" });
      return;
    }
    this.setData({ submitting: true, videoUrl: "", statusText: "提交中…" });
    app.request({
      method: "POST",
      url: "/api/shanjian-smart-clip/submit",
      data: {
        scene: "oralMixCutting",
        style_id: tpl.styleId,
        title: `素材混剪 ${new Date().toLocaleDateString()}`,
        content,
        speaker_id: voice.id,
        materials: this.data.materials,
        video_duration: this.data.duration,
        subtitle_switch: true,
        material_switch: true
      },
      timeout: 120000
    })
      .then((data) => {
        const taskId = String((data && (data.task_id || data.taskId)) || "").trim();
        if (!taskId) throw new Error("没有拿到任务号");
        this.setData({ taskId, statusText: "已提交，排队中…" });
        this._histId = hist.save("montage", { title: "素材混剪", content, taskId, status: "processing" });
        this.startPolling(taskId);
        setTimeout(() => { wx.navigateBack({ fail: () => {} }); }, 800);
      })
      .catch((err) => {
        this.setData({ statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ submitting: false }));
  },

  startPolling(taskId) {
    this.stopPolling();
    this.polls = 0;
    const tick = () => this.poll(taskId);
    tick();
    this.timer = setInterval(tick, POLL_MS);
  },

  stopPolling() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  poll(taskId) {
    this.polls += 1;
    if (this.polls > MAX_POLLS) {
      this.stopPolling();
      this.setData({ statusText: "任务超时，请稍后到作品里查看" });
      return;
    }
    app.request({
      method: "POST",
      url: "/api/shanjian-smart-clip/task",
      data: { task_id: taskId },
      timeout: 60000
    })
      .then((data) => {
        const status = String((data && data.status) || "processing");
        const statusText = String((data && data.status_text) || "处理中");
        const videoUrl = String((data && data.video_url) || "");
        this.setData({ statusText, videoUrl });
        if (status === "succeed" || status === "failed") {
          this.stopPolling();
          if (status === "succeed") {
            wx.setStorageSync("lobster_refresh_works", "1");
            if (this._histId) hist.update(this._histId, { status: "completed", videoUrl });
            wx.showToast({ title: "混剪完成", icon: "success" });
          } else {
            if (this._histId) hist.update(this._histId, { status: "failed" });
            wx.showToast({ title: (data && data.message) || "混剪失败", icon: "none" });
          }
        }
      })
      .catch((err) => {
        this.stopPolling();
        this.setData({ statusText: api.errorMessage(err) });
      });
  },

  copyUrl() {
    if (!this.data.videoUrl) return;
    wx.setClipboardData({ data: this.data.videoUrl });
  }
});