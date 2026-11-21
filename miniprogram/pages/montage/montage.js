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
    groups: [],
    groupNames: [],
    groupIndex: -1,
    voices: [],
    voiceNames: [],
    voiceIndex: -1,
    content: "",
    duration: 30,
    materials: [],
    submitting: false,
    assetOpen: false,
    assetLoading: false,
    assetItems: [],
    taskId: "",
    statusText: "",
    videoUrl: "",
    progressText: "",
    voices: [],
    voiceNames: [],
    voiceIndex: -1,
    speechText: "",
    speechAudioUrl: "",
    speechStatus: "",
    ttsLoading: false,
    rendering: false,
    localVideoUrl: "",
    localStatus: ""
  },

  onLoad() {
    this.polls = 0;
    this.timer = null;
    this.loadTemplates("oralMixCutting");
    this.loadMontageVoices();
    this.loadGroups();
  },

  onUnload() {
    this.stopPolling();
  },

  openRecordingPrefill() {
    const drawer = this.selectComponent("#topicPrefill");
    if (drawer) drawer.open("recording");
  },

  openPrefillHistory() {
    const drawer = this.selectComponent("#topicPrefill");
    if (drawer) drawer.openHistory();
  },

  openMemoryPrefill() {
    const drawer = this.selectComponent("#topicPrefill");
    if (drawer) drawer.open("memory");
  },

  onPrefillPick(evt) {
    const item = (evt && evt.detail && evt.detail.item) || {};
    const params = item.params || {};
    const materials = (params.materials || [])
      .map((row) => ({
        type: String(row.type || "image"),
        fileUrl: String(row.fileUrl || row.file_url || ""),
        name: String(row.name || "")
      }))
      .filter((row) => row.fileUrl);
    const patch = {
      content: String(item.script || ""),
      duration: Math.max(10, Math.min(180, Number(params.duration || 30)))
    };
    if (materials.length) patch.materials = materials;
    const name = String(params.template_name || "");
    if (name) {
      const index = (this.data.templates || []).findIndex((row) => String(row.name || "") === name);
      if (index >= 0) patch.templateIndex = index;
    }
    this.setData(patch);
    const bits = ["文案"];
    if (materials.length) bits.push("素材 " + materials.length + " 个");
    wx.showToast({ title: "已填好：" + bits.join(" + "), icon: "none" });
  },

  loadGroups() {
    if (!(app.globalData.token && app.globalData.phone)) return;
    app.request({ url: "/api/assets/creative-candidate-groups", timeout: 30000 })
      .then((data) => {
        const rows = (data && (data.items || data.groups || data.results)) || [];
        const names = rows.map((row) => String(row.name || row.group || row.title || "")).filter(Boolean);
        this.setData({ groups: names, groupNames: names, groupIndex: names.length ? 0 : -1 });
      })
      .catch(() => {});
  },

  selectGroup(e) {
    this.setData({ groupIndex: Number(e.detail.value) });
  },

  loadMontageVoices() {
    if (!(app.globalData.token && app.globalData.phone)) return;
    app.request({ url: "/api/hifly/my/voice/list?page=1&size=100", timeout: 30000 })
      .then((data) => {
        const items = ((data && data.items) || [])
          .map((row) => ({ id: String(row.voice || row.voice_id || ""), name: String(row.title || row.name || "音色") }))
          .filter((row) => !!row.id);
        this.setData({ voices: items, voiceNames: items.map((row) => row.name), voiceIndex: items.length ? 0 : -1 });
      })
      .catch(() => {});
  },

  selectVoice(e) {
    this.setData({ voiceIndex: Number(e.detail.value), speechAudioUrl: "", speechStatus: "" });
  },

  onSpeechInput(e) {
    this.setData({ speechText: e.detail.value || "" });
  },

  callTTS(preview) {
    const text = String(this.data.speechText || "").trim();
    if (!text) return wx.showToast({ title: "先写口播文案", icon: "none" });
    if (this.data.ttsLoading) return Promise.resolve("");
    this.setData({ ttsLoading: true, speechStatus: "正在合成语音…" });
    const voice = (this.data.voices[this.data.voiceIndex] || {}).id || "";
    if (!voice) {
      this.setData({ ttsLoading: false, speechStatus: "还没有自己的音色，先去素材库创建声音" });
      return Promise.resolve("");
    }
    return app.request({ method: "POST", url: "/api/hifly/my/voice/preview-tts", data: { text, voice }, timeout: 120000 })
      .then((data) => {
        const url = String((data && data.audio_url) || "");
        if (!url) throw new Error("没有拿到音频");
        this.setData({ speechAudioUrl: url, speechStatus: "音轨就绪（" + Math.round(Number(data.duration_seconds || 0)) + "s）" });
        if (preview) {
          if (this.audioCtx) { try { this.audioCtx.stop(); this.audioCtx.destroy(); } catch (e) {} }
          this.audioCtx = wx.createInnerAudioContext();
          this.audioCtx.src = url;
          this.audioCtx.play();
        }
        return url;
      })
      .catch((err) => {
        const msg = api.errorMessage(err) || "语音合成失败";
        this.setData({ speechStatus: msg });
        wx.showToast({ title: msg.slice(0, 24), icon: "none" });
        return "";
      })
      .then((url) => { this.setData({ ttsLoading: false }); return url; });
  },

  previewSpeech() { this.callTTS(true); },

  generateSpeech() { this.callTTS(false); },

  startLocalMontage() {
    if (!this.ensureAuth()) return;
    const urls = (this.data.materials || []).filter((m) => m && m.type === "video" && m.fileUrl).map((m) => m.fileUrl);
    const group = (this.data.groups[this.data.groupIndex] || "");
    if (!group && !urls.length) return wx.showToast({ title: "先选素材分组或加视频", icon: "none" });
    const run = (voiceUrl) => {
      if (!voiceUrl) return;
      this.setData({ rendering: true, localVideoUrl: "", localStatus: "正在渲染（裁切+拼接+音轨）…" });
      const started = Date.now();
      app.request({
        method: "POST",
        url: "/api/montage/jobs",
        data: { voice_url: voiceUrl, group: group, asset_urls: urls.slice(0, 12), ratio: "9:16", target_seconds: Number(this.data.duration) || undefined, title: "混剪成片" },
        timeout: 300000
      })
        .then((data) => this.setData({
          localVideoUrl: String((data && data.video_url) || ""),
          localStatus: "完成 " + ((data && data.duration) || 0) + "s · " + Math.round((Date.now() - started) / 1000) + "秒"
        }))
        .catch((err) => {
          const msg = api.errorMessage(err) || "混剪失败";
          this.setData({ localStatus: msg });
          wx.showToast({ title: msg.slice(0, 24), icon: "none" });
        })
        .then(() => this.setData({ rendering: false }));
    };
    if (this.data.speechAudioUrl) return run(this.data.speechAudioUrl);
    this.callTTS(false).then(run);
  },

  saveLocalVideo() {
    const url = this.data.localVideoUrl;
    if (!url) return;
    wx.downloadFile({
      url,
      success: (res) => {
        if (res.statusCode !== 200) return wx.showToast({ title: "下载失败", icon: "none" });
        wx.saveVideoToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => wx.showToast({ title: "已保存", icon: "success" }),
          fail: () => wx.showToast({ title: "请检查相册权限", icon: "none" })
        });
      },
      fail: () => wx.showToast({ title: "下载失败", icon: "none" })
    });
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
      count: 9,
      mediaType: ["image", "video"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const files = res.tempFiles || [];
        if (!files.length) return;
        const materials = this.data.materials.slice();
        let done = 0;
        const uploadOne = (file) => {
          const type = file.fileType === "video" ? "video" : "image";
          this.setData({ progressText: `素材上传中… ${done + 1}/${files.length}` });
          return api.uploadFile({
            url: "/api/assets/upload",
            filePath: file.tempFilePath,
            name: "file",
            formData: { media_type: type, tags: "montage,miniprogram" },
            token: app.globalData.token || ""
          }).then((data) => {
            const url = String((data && (data.source_url || data.url)) || "").trim();
            if (url) materials.push({ type, fileUrl: url });
          }).catch((err) => {
            wx.showToast({ title: api.errorMessage(err), icon: "none" });
          }).then(() => {
            done += 1;
            this.setData({ materials: materials.slice(), progressText: done < files.length ? `素材上传中… ${done}/${files.length}` : "" });
          });
        };
        files.reduce((chain, file) => chain.then(() => uploadOne(file)), Promise.resolve());
      }
    });
  },

  openAssetPicker() {
    this.setData({ assetOpen: true, assetLoading: true, assetItems: [] });
    app.request({ url: "/api/assets?limit=100", timeout: 30000 })
      .then((data) => {
        const rows = (data && (data.assets || data.items || data.data)) || [];
        const items = (Array.isArray(rows) ? rows : []).map((row) => ({
          id: String(row.asset_id || row.id || row.source_url || ""),
          url: String(row.source_url || row.url || ""),
          type: String(row.media_type || (String(row.filename || "").match(/\.(mp4|mov|webm)$/i) ? "video" : "image")),
          name: String(row.filename || row.name || "素材"),
          cover: String(row.preview_url || row.cover_url || row.source_url || row.url || "")
        })).filter((x) => x.url);
        this.setData({ assetItems: items });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ assetLoading: false }));
  },

  closeAssetPicker() { this.setData({ assetOpen: false }); },
  assetNoop() {},

  toggleAsset(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const items = this.data.assetItems.slice();
    if (!items[idx]) return;
    items[idx].checked = !items[idx].checked;
    this.setData({ assetItems: items });
  },

  confirmAssets() {
    const picked = this.data.assetItems.filter((x) => x.checked);
    if (!picked.length) return wx.showToast({ title: "先选素材", icon: "none" });
    const materials = this.data.materials.slice();
    picked.forEach((x) => materials.push({ type: x.type, fileUrl: x.url }));
    this.setData({ materials, assetOpen: false });
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