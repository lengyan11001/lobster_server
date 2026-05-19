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

function normalize(item) {
  const status = item.status || "processing";
  const url = videoUrl(item);
  return Object.assign({}, item, {
    title: item.title || "未命名视频",
    prompt: item.text || item.prompt || "",
    cover_url: coverUrl(item),
    playable_url: url,
    status,
    status_label: statusLabel(status),
    created_at_text: formatTime(item.created_at),
    duration_text: item.duration ? `${item.duration}秒` : "--",
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

Page({
  data: {
    id: "",
    taskId: "",
    loading: false,
    work: null
  },

  pollTimer: null,

  onLoad(query) {
    const cached = wx.getStorageSync("lobster_work_detail");
    if (cached && (String(cached.id || "") === String(query.id || "") || String(cached.task_id || "") === String(query.task_id || ""))) {
      this.setData({ work: normalize(cached) });
    }
    this.setData({ id: query.id || "", taskId: query.task_id || (cached && cached.task_id) || "" });
    this.refreshWork();
  },

  onShow() {
    app.restoreSession();
  },

  onUnload() {
    this.stopPolling();
  },

  onPullDownRefresh() {
    this.refreshWork().finally(() => wx.stopPullDownRefresh());
  },

  refreshWork() {
    app.restoreSession();
    if (!app.globalData.token) return Promise.resolve();
    const taskId = this.data.taskId || (this.data.work && this.data.work.task_id);
    if (taskId && (!this.data.work || this.data.work.is_processing)) {
      return this.pollTask(taskId, true);
    }
    return this.loadFromList();
  },

  loadFromList() {
    this.setData({ loading: true });
    return app
      .request({ url: "/api/hifly/my/video/list?page=1&size=80" })
      .then((data) => {
        const id = String(this.data.id || "");
        const taskId = String(this.data.taskId || "");
        const row = (data.items || []).find((item) => String(item.id || "") === id || String(item.task_id || "") === taskId);
        if (row) this.applyWork(row);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  pollTask(taskId, fallbackToList) {
    this.setData({ loading: !this.data.work });
    return app
      .request({
        method: "POST",
        url: "/api/hifly/my/video/task",
        data: { task_id: taskId },
        timeout: 60000
      })
      .then((data) => this.applyWork(data.item || data))
      .catch((err) => {
        if (fallbackToList) return this.loadFromList();
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
        return null;
      })
      .finally(() => this.setData({ loading: false }));
  },

  applyWork(raw) {
    const work = normalize(raw || {});
    this.setData({
      work,
      id: work.id || this.data.id,
      taskId: work.task_id || this.data.taskId
    });
    wx.setStorageSync("lobster_refresh_works", "1");
    if (work.is_processing && work.task_id) {
      this.startPolling();
      return;
    }
    this.stopPolling();
  },

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      const work = this.data.work || {};
      if (!work.task_id || !work.is_processing) {
        this.stopPolling();
        return;
      }
      this.pollTask(work.task_id, false);
    }, 8000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  goBack() {
    wx.navigateBack({
      fail() {
        wx.switchTab({ url: "/pages/downloads/downloads" });
      }
    });
  },

  copyPrompt() {
    const prompt = this.data.work && this.data.work.prompt;
    if (!prompt) return;
    wx.setClipboardData({ data: prompt });
  },

  copyLink() {
    const url = this.data.work && this.data.work.playable_url;
    if (!url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    media.copyLink(url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  saveVideo() {
    const work = this.data.work;
    if (!work || !work.playable_url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    media
      .saveToAlbum({
        id: work.id,
        title: work.title,
        media_type: "video",
        url: work.playable_url,
        preview_url: work.playable_url,
        download_url: work.playable_url
      })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch(() => media.copyLink(work.playable_url).finally(() => wx.showToast({ title: "保存失败，已复制链接", icon: "none" })));
  },

  regenerate() {
    const work = this.data.work || {};
    wx.setStorageSync("lobster_digital_prefill", {
      title: work.title || "",
      text: work.prompt || ""
    });
    wx.navigateTo({ url: "/pages/digital/digital?mode=create" });
  },

  shareWork() {
    this.copyLink();
  }
});
