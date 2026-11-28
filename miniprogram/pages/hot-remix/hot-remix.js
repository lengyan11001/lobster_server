const app = getApp();
const api = require("../../utils/api");
const { createPoller } = require("../../utils/task-poll");

const STATUS_TEXT = { SUCCESS: "已完成", RUNNING: "生成中", FAILED: "失败" };

function extractShareUrl(raw) {
  const text = String(raw || "").replace(/\\_/g, "_");
  const match = text.match(/https?:\/\/[A-Za-z0-9\-._~:\/?#@!$&'*+,;=%]+/i);
  if (!match) return "";
  return match[0].replace(/[。，,.;)）\]】」》]+$/g, "");
}

function fmtTime(v) {
  return String(v || "").replace("T", " ").slice(0, 16);
}

function mapRows(data) {
  return ((data && data.items) || []).map(function (row) {
    var status = String(row.status || "");
    return {
      id: row.id,
      taskId: String(row.task_id || ""),
      title: String(row.title || row.source_desc || "跟创任务"),
      desc: String(row.source_desc || ""),
      status: status,
      statusText: STATUS_TEXT[status] || status,
      credits: Number(row.credits_charged || 0),
      meta: fmtTime(row.created_at) + " · " + (STATUS_TEXT[status] || status) + (Number(row.credits_charged || 0) > 0 ? (" · 扣 " + Number(row.credits_charged) + " 算力") : ""),
      videoUrl: String(row.video_url || "")
    };
  });
}

Page({
  data: {
    step: 1,
    viewMode: "create",
    jobList: [],
    jobsLoading: false,
    pendingTask: null,
    resultUrl: "",
    resultTitle: "",
    previewOpen: false,
    previewUrl: "",
    previewTitle: "",
    modes: [
      { label: "复刻人物（换人）", value: "person_swap" },
      { label: "复刻特效", value: "effect_copy" },
      { label: "复刻单人动作", value: "action_copy" }
    ],
    modeLabels: ["复刻人物（换人）", "复刻特效", "复刻单人动作"],
    modeIndex: 0,
    resolutions: ["720P", "1080P"],
    resIndex: 0,
    durations: [
      { label: "跟原视频", value: 0 },
      { label: "5 秒", value: 5 },
      { label: "10 秒", value: 10 },
      { label: "15 秒", value: 15 }
    ],
    durationLabels: ["跟原视频", "5 秒", "10 秒", "15 秒"],
    durIndex: 0,
    videoUrl: "", videoPreview: "", videoName: "", videoSeconds: 0,
    shareText: "", shareStatus: "", resolving: false,
    imageUrl: "", imagePreview: "", uploadingImage: false,
    prompt: "", title: "",
    voiceOptions: [], voiceNames: [], voiceIndex: 0, voicesLoading: false,
    submitting: false, statusText: ""
  },

  onShow() {
    if (!app.globalData.token) return;
    if (this.data.viewMode === "list") this.loadJobs();
    this.loadVoices();
    this.resumePending();
  },

  onHide() { this.pausePollers(); },
  onUnload() { this.stopPollers(); },

  pausePollers() {
    if (this.jobPoller) this.jobPoller.pause();
    if (this.taskPoller) this.taskPoller.pause();
  },

  stopPollers() {
    if (this.jobPoller) this.jobPoller.stop();
    if (this.taskPoller) this.taskPoller.stop();
    this.jobPoller = null;
    this.taskPoller = null;
  },

  goBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/index/index" }) }); },

  openCreate() {
    this.setData({ viewMode: "create", step: 1, statusText: "", resultUrl: "", resultTitle: "" });
    this.loadVoices();
  },

  nextStep() {
    if (!String(this.data.videoUrl || "").trim() && !String(this.data.shareText || "").trim()) {
      return wx.showToast({ title: "请先上传原视频或粘贴链接", icon: "none" });
    }
    this.setData({ step: 2 });
    wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },

  prevStep() {
    this.setData({ step: 1 });
    wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },

  backToList() {
    this.setData({ viewMode: "list", statusText: "" });
    this.loadJobs();
  },

  loadJobs() {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ jobsLoading: true });
    return app.request({ url: "/api/douyin/platform-information-desk/imitation/history?limit=20&refresh=1", timeout: 30000 })
      .then((data) => {
        const rows = mapRows(data);
        this.setData({ jobList: rows, jobsLoading: false });
        if (rows.some((r) => r.status === "RUNNING")) this.startListPoll();
        else if (this.jobPoller) { this.jobPoller.stop(); this.jobPoller = null; }
      })
      .catch(() => this.setData({ jobsLoading: false }));
  },

  startListPoll() {
    if (this.jobPoller) return;
    this.jobPoller = createPoller(
      () => app.request({ url: "/api/douyin/platform-information-desk/imitation/history?limit=20&refresh=1", timeout: 30000 }),
      (data) => {
        const rows = mapRows(data);
        this.setData({ jobList: rows });
        return !rows.some((r) => r.status === "RUNNING");
      },
      () => {}
    );
    this.jobPoller.start(false);
  },

  resumePending() {
    let saved = null;
    try { saved = wx.getStorageSync("lobster_hot_remix_task"); } catch (e) {}
    if (!saved || !saved.taskId) return;
    if (saved.created_at && Date.now() - Number(saved.created_at) > 45 * 60 * 1000) {
      try { wx.removeStorageSync("lobster_hot_remix_task"); } catch (e) {}
      return;
    }
    if (this.taskPoller) return;
    this.setData({ pendingTask: { taskId: String(saved.taskId), statusText: "生成中…" } });
    this.startTaskPoll(String(saved.taskId));
  },

  startTaskPoll(taskId) {
    if (this.taskPoller) this.taskPoller.stop();
    this.taskPoller = createPoller(
      () => app.request({ url: "/api/douyin/platform-information-desk/imitation/" + encodeURIComponent(taskId), timeout: 30000 }),
      (data) => {
        const status = String((data && data.status) || "");
        const label = STATUS_TEXT[status] || status || "生成中";
        if (status === "SUCCESS") {
          this.setData({ pendingTask: null, resultUrl: String((data && data.video_url) || ""), resultTitle: "跟创成片", statusText: "" });
          try { wx.removeStorageSync("lobster_hot_remix_task"); } catch (e) {}
          this.loadJobs();
          return true;
        }
        if (status === "FAILED") {
          this.setData({ pendingTask: null, statusText: String((data && data.fail_reason) || "生成失败") });
          try { wx.removeStorageSync("lobster_hot_remix_task"); } catch (e) {}
          this.loadJobs();
          return true;
        }
        this.setData({ pendingTask: { taskId: String(taskId), statusText: label + "…" } });
        return false;
      },
      () => {}
    );
    this.taskPoller.start(true);
  },

  chooseVideo() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["video"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        wx.showLoading({ title: "上传中", mask: true });
        api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.tempFilePath,
          name: "file",
          formData: { media_type: "video", upload_source: "montage", tags: "hot-remix" },
          token: app.globalData.token || ""
        })
          .then((data) => {
            const url = String((data && (data.source_url || data.url)) || "").trim();
            if (!url) throw new Error("上传失败：没有拿到视频地址");
            this.setData({ videoUrl: url, videoPreview: file.tempFilePath, videoName: "已上传的视频", videoSeconds: Number(file.duration || 0), shareStatus: "" });
            wx.showToast({ title: "视频已就绪", icon: "success" });
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  onShareInput(e) {
    const raw = String((e.detail && e.detail.value) || "");
    const url = extractShareUrl(raw);
    this.setData({ shareText: url || raw });
  },

  resolveShare() {
    const text = extractShareUrl(this.data.shareText) || String(this.data.shareText || "").trim();
    if (!text) return wx.showToast({ title: "先粘贴分享文案或链接", icon: "none" });
    if (this.data.resolving) return;
    this.setData({ resolving: true, shareStatus: "正在解析并下载视频…" });
    app.request({
      method: "POST",
      url: "/api/viral-video-remix/share-video/resolve",
      data: { share_text: text },
      timeout: 120000
    })
      .then((data) => {
        const url = String((data && (data.source_url || data.direct_video_url || data.url)) || "").trim();
        if (!url) throw new Error("解析成功但没有视频地址");
        this.setData({
          videoUrl: url,
          videoPreview: url,
          videoSeconds: 0,
          videoName: String((data && data.title) || "分享视频"),
          shareStatus: "视频已就绪，可以开始跟创"
        });
      })
      .catch((err) => this.setData({ shareStatus: api.errorMessage(err) || "解析失败" }))
      .then(() => this.setData({ resolving: false }));
  },

  chooseImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        this.setData({ uploadingImage: true });
        api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.tempFilePath,
          name: "file",
          formData: { media_type: "image", tags: "hot-remix" },
          token: app.globalData.token || ""
        })
          .then((data) => {
            const url = String((data && (data.source_url || data.url)) || "").trim();
            if (!url) throw new Error("图片上传失败");
            this.setData({ imageUrl: url, imagePreview: file.tempFilePath });
            wx.showToast({ title: "图片已就绪", icon: "success" });
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => this.setData({ uploadingImage: false }));
      }
    });
  },

  loadVoices() {
    if (!app.globalData.token || this.data.voicesLoading) return Promise.resolve();
    this.setData({ voicesLoading: true });
    return app.request({ url: "/api/hifly/voice/options", timeout: 45000 })
      .then((data) => {
        const rows = [{ id: "", name: "原声（保留视频原声）", source: "original", provider: "" }];
        ((data && (data.items || data.mine || [])) || []).forEach((row) => {
          const id = String(row.voice || row.id || "").trim();
          if (!id) return;
          const source = String(row.source || "").toLowerCase() === "system" ? "system" : "mine";
          const label = String(row.title || row.name || "声音");
          rows.push({
            id,
            name: (source === "system" ? "系统声音 · " : "我的声音 · ") + label,
            source,
            provider: String(row.provider || (source === "system" ? "minimax" : ""))
          });
        });
        const selectedId = String(((this.data.voiceOptions || [])[this.data.voiceIndex] || {}).id || "");
        let index = rows.findIndex((row) => row.id === selectedId);
        if (index < 0) index = 0;
        this.setData({ voiceOptions: rows, voiceNames: rows.map((row) => row.name), voiceIndex: index });
      })
      .catch(() => {})
      .then(() => this.setData({ voicesLoading: false }));
  },

  pickVoice(e) { this.setData({ voiceIndex: Number((e.detail && e.detail.value) || 0) }); },

  pickMode(e) { this.setData({ modeIndex: Number((e.detail && e.detail.value) || 0) }); },
  pickRes(e) { this.setData({ resIndex: Number((e.detail && e.detail.value) || 0) }); },
  pickDur(e) { this.setData({ durIndex: Number((e.detail && e.detail.value) || 0) }); },
  onPrompt(e) { this.setData({ prompt: e.detail.value }); },
  onTitle(e) { this.setData({ title: e.detail.value }); },

  async submit() {
    if (!app.globalData.token) return wx.showToast({ title: "请先登录", icon: "none" });
    if (!this.data.videoUrl) return wx.showToast({ title: "先上传视频或解析链接", icon: "none" });
    if (!this.data.imageUrl) return wx.showToast({ title: "先上传一张参考图", icon: "none" });
    if (this.data.submitting) return;
    const mode = this.data.modes[this.data.modeIndex] || this.data.modes[0];
    const duration = this.data.durations[this.data.durIndex] || this.data.durations[0];
    const voice = (this.data.voiceOptions || [])[this.data.voiceIndex] || { source: "original" };
    const useVoice = voice.source !== "original" && !!voice.id;
    this.setData({ submitting: true, statusText: "正在提交跟创任务…" });
    try {
      if (useVoice) {
        this.setData({ statusText: "正在提取原视频文案并生成配音，约 1-3 分钟…" });
      }
      const data = await app.request({
        method: "POST",
        url: "/api/douyin/platform-information-desk/imitation",
        data: {
          image_url: this.data.imageUrl,
          video_url: this.data.videoUrl,
          mode: mode.value,
          resolution: this.data.resolutions[this.data.resIndex] || "720P",
          duration_seconds: Number(duration.value || 0),
          video_seconds: Number(this.data.videoSeconds || 0),
          prompt: String(this.data.prompt || "").trim(),
          title: String(this.data.title || "").trim(),
          voice_mode: useVoice ? "clone" : "original",
          voice_id: useVoice ? voice.id : "",
          voice_source: useVoice ? voice.source : ""
        },
        timeout: useVoice ? 600000 : 60000
      });
      const taskId = String((data && data.task_id) || "");
      if (!taskId) throw new Error("没有拿到任务号");
      try { wx.setStorageSync("lobster_hot_remix_task", { taskId: taskId, created_at: Date.now() }); } catch (e) {}
      this.setData({
        viewMode: "create",
        submitting: false,
        statusText: "",
        pendingTask: { taskId: taskId, statusText: "生成中…" },
        resultUrl: "",
        resultTitle: "",
        videoUrl: "", videoPreview: "", videoName: "",
        shareText: "", shareStatus: "",
        imageUrl: "", imagePreview: "",
        prompt: "", title: ""
      });
      this.startTaskPoll(taskId);
      this.loadJobs();
      wx.showToast({ title: "已提交，去作品页查看", icon: "success" });
      setTimeout(() => wx.switchTab({ url: "/pages/downloads/downloads" }), 900);
    } catch (err) {
      this.setData({ submitting: false, statusText: "" });
      wx.showToast({ title: api.errorMessage(err), icon: "none" });
    }
  },

  openJob(e) {
    const row = this.data.jobList[Number(e.currentTarget.dataset.index || 0)];
    if (!row) return;
    if (row.videoUrl) {
      this.setData({ previewOpen: true, previewUrl: row.videoUrl, previewTitle: row.title || "跟创成片" });
      return;
    }
    if (row.taskId) {
      this.setData({ pendingTask: { taskId: row.taskId, statusText: "查询中…" } });
      this.startTaskPoll(row.taskId);
    } else {
      wx.showToast({ title: row.statusText || "生成中", icon: "none" });
    }
  },

  downloadJob(e) {
    const row = this.data.jobList[Number(e.currentTarget.dataset.index || 0)];
    if (!row || !row.videoUrl) return;
    wx.showLoading({ title: "下载中", mask: true });
    wx.downloadFile({
      url: row.videoUrl,
      success: (res) => {
        if (Number(res.statusCode || 0) >= 400) { wx.showToast({ title: "下载失败", icon: "none" }); return; }
        wx.saveVideoToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => wx.showToast({ title: "已保存到相册", icon: "success" }),
          fail: () => wx.showToast({ title: "保存失败，请检查相册权限", icon: "none" })
        });
      },
      fail: () => wx.showToast({ title: "下载失败", icon: "none" }),
      complete: () => wx.hideLoading()
    });
  },

  closePreview() { this.setData({ previewOpen: false, previewUrl: "", previewTitle: "" }); },

  savePreview() {
    const url = String(this.data.previewUrl || "");
    if (!url) return;
    wx.showLoading({ title: "下载中", mask: true });
    wx.downloadFile({
      url,
      success: (res) => {
        if (Number(res.statusCode || 0) >= 400) { wx.showToast({ title: "下载失败", icon: "none" }); return; }
        wx.saveVideoToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => wx.showToast({ title: "已保存到相册", icon: "success" }),
          fail: () => wx.showToast({ title: "保存失败，请检查相册权限", icon: "none" })
        });
      },
      fail: () => wx.showToast({ title: "下载失败", icon: "none" }),
      complete: () => wx.hideLoading()
    });
  },

  saveResult() {
    const url = String(this.data.resultUrl || "");
    if (!url) return;
    wx.showLoading({ title: "下载中", mask: true });
    wx.downloadFile({
      url: url,
      success: (res) => {
        if (Number(res.statusCode || 0) >= 400) { wx.showToast({ title: "下载失败", icon: "none" }); return; }
        wx.saveVideoToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => wx.showToast({ title: "已保存到相册", icon: "success" }),
          fail: () => wx.showToast({ title: "保存失败，请检查相册权限", icon: "none" })
        });
      },
      fail: () => wx.showToast({ title: "下载失败", icon: "none" }),
      complete: () => wx.hideLoading()
    });
  }
});
