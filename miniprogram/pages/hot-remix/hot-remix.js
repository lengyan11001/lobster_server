const app = getApp();
const api = require("../../utils/api");

const STATUS_TEXT = { SUCCESS: "已完成", RUNNING: "生成中", FAILED: "失败" };

Page({
  data: {
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
    videoUrl: "", videoPreview: "", videoName: "",
    shareText: "", shareStatus: "", resolving: false,
    imageUrl: "", imagePreview: "", uploadingImage: false,
    prompt: "", title: "",
    submitting: false, statusText: "", resultUrl: "",
    history: [], polling: false
  },

  onShow() {
    if (app.globalData.token) this.loadHistory();
  },

  onUnload() { this.stopPolling(); },

  goBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/index/index" }) }); },

  stopPolling() {
    this.polling = false;
    if (this.pollTimer) { clearTimeout(this.pollTimer); this.pollTimer = null; }
    if (this._pollTries) this._pollTries = 0;
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
            this.setData({ videoUrl: url, videoPreview: file.tempFilePath, videoName: "已上传的视频", shareStatus: "" });
            wx.showToast({ title: "视频已就绪", icon: "success" });
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  onShareInput(e) { this.setData({ shareText: e.detail.value }); },

  resolveShare() {
    const text = String(this.data.shareText || "").trim();
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

  pickMode(e) { this.setData({ modeIndex: Number(e.detail.value || 0) }); },
  pickRes(e) { this.setData({ resIndex: Number(e.detail.value || 0) }); },
  pickDur(e) { this.setData({ durIndex: Number(e.detail.value || 0) }); },
  onPrompt(e) { this.setData({ prompt: e.detail.value }); },
  onTitle(e) { this.setData({ title: e.detail.value }); },

  submit() {
    if (!app.globalData.token) {
      wx.showToast({ title: "请先登录", icon: "none" });
      return;
    }
    if (!this.data.videoUrl) return wx.showToast({ title: "先上传视频或解析链接", icon: "none" });
    if (!this.data.imageUrl) return wx.showToast({ title: "先上传一张参考图", icon: "none" });
    if (this.data.submitting) return;
    const mode = this.data.modes[this.data.modeIndex] || this.data.modes[0];
    const duration = this.data.durations[this.data.durIndex] || this.data.durations[0];
    this.setData({ submitting: true, statusText: "正在提交跟创任务…", resultUrl: "" });
    app.request({
      method: "POST",
      url: "/api/douyin/platform-information-desk/imitation",
      data: {
        image_url: this.data.imageUrl,
        video_url: this.data.videoUrl,
        mode: mode.value,
        resolution: this.data.resolutions[this.data.resIndex] || "720P",
        duration_seconds: Number(duration.value || 0),
        prompt: String(this.data.prompt || "").trim(),
        title: String(this.data.title || "").trim()
      },
      timeout: 60000
    })
      .then((data) => {
        const taskId = String((data && data.task_id) || "");
        if (!taskId) throw new Error("没有拿到任务号");
        if (data.video_url) this.setData({ resultUrl: data.video_url });
        this.setData({ statusText: "已提交，正在生成…" });
        this._pollTries = 0;
        this.pollTask(taskId);
        this.loadHistory();
      })
      .catch((err) => {
        this.setData({ statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ submitting: false }));
  },

  pollTask(taskId) {
    this.stopPolling();
    this.polling = true;
    this._pollTries = this._pollTries || 0;
    const tick = () => {
      if (!this.polling) return;
      app.request({ url: `/api/douyin/platform-information-desk/imitation/${encodeURIComponent(taskId)}`, timeout: 30000 })
        .then((data) => {
          const status = String((data && data.status) || "");
          if (status === "SUCCESS") {
            this.stopPolling();
            this.setData({
              statusText: "生成完成",
              resultUrl: String(data.video_url || this.data.resultUrl || "")
            });
            this.loadHistory();
            return;
          }
          if (status === "FAILED") {
            this.stopPolling();
            this.setData({ statusText: String(data.fail_reason || "生成失败") });
            return;
          }
          this._pollTries += 1;
          this.setData({ statusText: `生成中…（${this._pollTries * 5}s）` });
          if (this._pollTries < 120) this.pollTimer = setTimeout(tick, 5000);
          else this.stopPolling();
        })
        .catch(() => {
          this._pollTries += 1;
          if (this._pollTries < 120) this.pollTimer = setTimeout(tick, 5000);
        });
    };
    tick();
  },

  loadHistory() {
    if (!app.globalData.token) return;
    app.request({ url: "/api/douyin/platform-information-desk/imitation/history?limit=20&refresh=0", timeout: 30000 })
      .then((data) => {
        const rows = ((data && (data.items || data.history)) || []).map((row) => ({
          id: row.id,
          taskId: String(row.task_id || ""),
          title: String(row.title || ""),
          desc: String(row.source_desc || ""),
          statusText: STATUS_TEXT[String(row.status || "")] || String(row.status || ""),
          videoUrl: String(row.video_url || ""),
          timeText: String(row.created_at || "").replace("T", " ").slice(0, 16)
        }));
        this.setData({ history: rows });
      })
      .catch(() => {});
  },

  openHistory(e) {
    const row = this.data.history[Number(e.currentTarget.dataset.index || 0)];
    if (!row) return;
    if (row.videoUrl) {
      this.setData({ resultUrl: row.videoUrl, statusText: "历史成片" });
      return;
    }
    if (row.taskId) {
      this.setData({ statusText: "正在查询任务状态…" });
      this.pollTask(row.taskId);
    }
  },

  saveResult() {
    if (!this.data.resultUrl) return;
    wx.showLoading({ title: "下载中", mask: true });
    wx.downloadFile({
      url: this.data.resultUrl,
      success: (res) => {
        if (Number(res.statusCode || 0) >= 400) {
          wx.showToast({ title: "下载失败", icon: "none" });
          return;
        }
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
