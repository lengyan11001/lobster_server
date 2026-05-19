const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");

function videoUrl(item) {
  return item.video_url || item.asset_video_url || item.source_video_url || "";
}

function coverUrl(item) {
  return item.cover_url || item.image_url || item.avatar_image_url || item.avatar_url || "";
}

function statusLabel(status) {
  if (status === "success") return "已完成";
  if (status === "failed") return "失败";
  if (status === "waiting") return "等待中";
  return "生成中";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 19);
  const pad = (num) => String(num).padStart(2, "0");
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function normalizeVideo(item) {
  const status = item.status || "processing";
  const url = videoUrl(item);
  return Object.assign({}, item, {
    work_type: "digital_video",
    media_type: "video",
    work_type_label: "数字人视频",
    title: item.title || "未命名视频",
    prompt: item.text || item.prompt || "",
    cover_url: coverUrl(item),
    playable_url: url,
    preview_url: url,
    status,
    status_label: statusLabel(status),
    created_at_text: formatTime(item.created_at),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

Page({
  data: {
    phoneBound: false,
    authPanelVisible: false,
    authHint: "查看作品前需要微信登录并绑定手机号。",
    mediaTab: "video",
    videoKind: "digital",
    loading: false,
    polling: false,
    works: [],
    onlineText: ""
  },

  pollTimer: null,

  onShow() {
    app.restoreSession();
    this.refreshAuthState();
    const shouldRefresh = wx.getStorageSync("lobster_refresh_works");
    if (shouldRefresh) wx.removeStorageSync("lobster_refresh_works");
    if (this.data.phoneBound) this.loadWorks();
  },

  onHide() {
    this.stopPolling();
  },

  onUnload() {
    this.stopPolling();
  },

  onPullDownRefresh() {
    this.loadWorks().finally(() => wx.stopPullDownRefresh());
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
      authHint: hint || "查看作品前需要微信登录并绑定手机号。"
    });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshAuthState();
        if (data.needs_phone_bind || !app.globalData.phone) {
          wx.showToast({ title: "请授权手机号", icon: "none" });
          return;
        }
        this.setData({ authPanelVisible: false });
        this.loadWorks();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      wx.showToast({ title: "微信取号失败", icon: "none" });
      return;
    }
    const bind = () => this.bindPhone(code);
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind();
  },

  bindPhone(code) {
    wx.showLoading({ title: "绑定中", mask: true });
    app
      .bindPhone(code)
      .then(() => {
        this.setData({ authPanelVisible: false, phoneBound: true });
        wx.showToast({ title: "绑定成功", icon: "success" });
        this.loadWorks();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  setMediaTab(evt) {
    const tab = evt.currentTarget.dataset.tab || "video";
    this.setData({ mediaTab: tab });
  },

  setVideoKind(evt) {
    const kind = evt.currentTarget.dataset.kind || "digital";
    this.setData({ videoKind: kind });
  },

  noop() {},

  loadWorks() {
    if (!this.refreshAuthState()) {
      this.setData({ works: [], loading: false });
      return Promise.resolve();
    }
    this.setData({ loading: true });
    return app
      .request({ url: "/api/hifly/my/video/list?page=1&size=50" })
      .then((data) => {
        const works = (data.items || []).map(normalizeVideo);
        this.setData({ works, authPanelVisible: false });
        this.refreshPolling();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  refreshPolling() {
    const hasProcessing = this.data.works.some((item) => item.is_processing && item.task_id);
    if (hasProcessing) {
      this.startPolling();
      return;
    }
    this.stopPolling();
  },

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => this.pollProcessingWorks(), 8000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  pollProcessingWorks() {
    const targets = this.data.works.filter((item) => item.is_processing && item.task_id).slice(0, 5);
    if (!targets.length || this.data.polling) return;
    this.setData({ polling: true });
    Promise.all(
      targets.map((item) =>
        app
          .request({
            method: "POST",
            url: "/api/hifly/my/video/task",
            data: { task_id: item.task_id },
            timeout: 60000
          })
          .then((data) => normalizeVideo(data.item || item))
          .catch(() => item)
      )
    )
      .then((updated) => {
        const byId = {};
        updated.forEach((item) => {
          byId[String(item.id)] = item;
        });
        const works = this.data.works.map((item) => byId[String(item.id)] || item);
        this.setData({ works });
        this.refreshPolling();
      })
      .finally(() => this.setData({ polling: false }));
  },

  openWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item) return;
    wx.setStorageSync("lobster_work_detail", item);
    wx.navigateTo({ url: `/pages/work-detail/work-detail?id=${item.id || ""}&task_id=${item.task_id || ""}` });
  },

  copyPrompt(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item || !item.prompt) return;
    wx.setClipboardData({ data: item.prompt });
  },

  deleteWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item || !item.id) return;
    wx.showModal({
      title: "删除作品",
      content: "删除后作品记录不可恢复。",
      confirmText: "删除",
      confirmColor: "#ef4444",
      success: (res) => {
        if (!res.confirm) return;
        app
          .request({ method: "DELETE", url: `/api/hifly/my/video/${item.id}` })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadWorks();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  },

  saveWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.works[index];
    if (!item || !item.playable_url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    media
      .saveToAlbum({
        id: item.id,
        title: item.title,
        media_type: "video",
        url: item.playable_url,
        preview_url: item.playable_url,
        download_url: item.playable_url
      })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch(() => media.copyLink(item.playable_url).finally(() => wx.showToast({ title: "保存失败，已复制链接", icon: "none" })));
  },

  goCreate() {
    wx.navigateTo({ url: "/pages/digital/digital" });
  }
});
