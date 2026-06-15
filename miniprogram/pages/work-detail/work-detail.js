const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const share = require("../../utils/share");

function videoUrl(item) {
  return item.video_url || item.asset_video_url || item.source_video_url || "";
}

function coverUrl(item) {
  return item.cover_url || item.image_url || item.avatar_image_url || item.avatar_url || "";
}

function filenameFor(item) {
  const raw = item.title || item.asset_id || item.id || "digital-human-video";
  const base = String(raw).replace(/[\\/:*?"<>|#%&=]+/g, "_").slice(0, 80) || "digital-human-video";
  return /\.mp4$/i.test(base) ? base : `${base}.mp4`;
}

function mediaProxyUrl(url, disposition, filename) {
  const token = app.globalData.token || wx.getStorageSync("lobster_token") || "";
  const params = [
    `url=${encodeURIComponent(url || "")}`,
    `disposition=${encodeURIComponent(disposition || "attachment")}`,
    `filename=${encodeURIComponent(filename || "digital-human-video.mp4")}`
  ];
  if (token) params.push(`token=${encodeURIComponent(token)}`);
  return api.buildUrl(`/api/h5-chat/media?${params.join("&")}`);
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
  const filename = filenameFor(item);
  return Object.assign({}, item, {
    title: item.title || "未命名视频",
    prompt: item.text || item.prompt || "",
    cover_url: coverUrl(item),
    playable_url: url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: statusLabel(status),
    created_at_text: formatTime(item.created_at),
    duration_text: item.duration ? `${item.duration}秒` : "--",
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

function ipTaskLabel(task) {
  return {
    industry_hot_oral: "行业热门口播",
    professional_ip_oral: "专业 IP 口播",
    moments_candidate: "朋友圈文案"
  }[String(task || "")] || task || "文案";
}

function normalizeScheduledRun(run) {
  const payload = run && run.result_payload && typeof run.result_payload === "object" ? run.result_payload : {};
  const groups = Array.isArray(payload.groups) ? payload.groups : [];
  return {
    is_scheduled_run: true,
    title: run.title || "定时任务详情",
    status: run.status || "",
    status_label: run.status === "completed" ? "已完成" : (run.status === "failed" ? "失败" : "执行中"),
    created_at_text: formatTime(run.created_at),
    result_text: run.error || run.result_text || "",
    groups: groups.map((group) => ({
      title: `${ipTaskLabel(group.task)} · ${(group.records || []).length}条`,
      records: (group.records || []).map((rec, idx) => ({
        title: `${idx + 1}. ${rec.title || "未命名文案"}`,
        body: rec.body || rec.content || "",
        prompts: Array.isArray(rec.image_prompts) ? rec.image_prompts : []
      }))
    })),
    raw: run
  };
}

Page({
  data: {
    id: "",
    taskId: "",
    runId: "",
    shareToken: "",
    isSharedView: false,
    sharePath: "",
    loading: false,
    work: null
  },

  pollTimer: null,

  onLoad(query) {
    share.showShareMenu();
    if (query.share) {
      this.setData({ shareToken: query.share || "", isSharedView: true });
      this.loadSharedWork(query.share);
      return;
    }
    if (query.run_id) {
      this.setData({ runId: query.run_id || "" });
      this.loadScheduledRun(query.run_id);
      return;
    }
    const cached = wx.getStorageSync("lobster_work_detail");
    if (cached && (String(cached.id || "") === String(query.id || "") || String(cached.task_id || "") === String(query.task_id || ""))) {
      this.setData({ work: normalize(cached) });
    }
    this.setData({ id: query.id || "", taskId: query.task_id || (cached && cached.task_id) || "" });
    this.refreshWork();
  },

  onShow() {
    share.showShareMenu();
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
    if (this.data.isSharedView) return this.loadSharedWork(this.data.shareToken);
    if (!app.globalData.token) return Promise.resolve();
    if (this.data.runId) return this.loadScheduledRun(this.data.runId);
    const taskId = this.data.taskId || (this.data.work && this.data.work.task_id);
    if (taskId && (!this.data.work || this.data.work.is_processing)) {
      return this.pollTask(taskId, true);
    }
    return this.loadFromList();
  },

  loadScheduledRun(runId) {
    if (!runId || !app.globalData.token) return Promise.resolve();
    this.setData({ loading: true });
    return app
      .request({ url: `/api/scheduled-tasks/runs/${encodeURIComponent(runId)}` })
      .then((data) => {
        this.setData({ work: normalizeScheduledRun(data.run || {}), runId });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
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
    if (!this.data.isSharedView) wx.setStorageSync("lobster_refresh_works", "1");
    if (work.is_processing && work.task_id) {
      this.startPolling();
      return;
    }
    this.stopPolling();
    if (!this.data.isSharedView && work.is_success) this.prepareShareToken();
  },

  loadSharedWork(shareToken) {
    const token = shareToken || this.data.shareToken;
    if (!token) return Promise.resolve();
    this.setData({ loading: true, shareToken: token, isSharedView: true });
    return app
      .request({ url: `/api/hifly/video/share/${encodeURIComponent(token)}` })
      .then((data) => this.applyWork(data.item || data))
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
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
        download_url: work.download_url,
        proxy_download_url: work.proxy_download_url,
        proxy_preview_url: work.proxy_preview_url
      })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const reason = api.errorMessage(err);
        media.copyLink(work.playable_url).finally(() => wx.showToast({ title: `保存失败: ${reason}`.slice(0, 28), icon: "none" }));
      });
  },

  regenerate() {
    const work = this.data.work || {};
    wx.setStorageSync("lobster_digital_prefill", {
      title: work.title || "",
      text: work.prompt || ""
    });
    wx.navigateTo({ url: "/pages/digital/digital?mode=create" });
  },

  ensureShareToken() {
    if (this.data.shareToken) return Promise.resolve(this.data.shareToken);
    const work = this.data.work || {};
    if (!work.id) return Promise.reject(new Error("作品还未加载"));
    if (!work.is_success) return Promise.reject(new Error("作品生成完成后才能分享"));
    return app
      .request({ method: "POST", url: `/api/hifly/my/video/${work.id}/share` })
      .then((data) => {
        const token = data.share_token || "";
        if (!token) throw new Error("分享链接生成失败");
        this.setData({
          shareToken: token,
          sharePath: `/pages/work-detail/work-detail?share=${encodeURIComponent(token)}`
        });
        return token;
      });
  },

  prepareShareToken() {
    if (this.data.shareToken || this.data.loading) return;
    this.ensureShareToken().catch(() => {});
  },

  onShareTap() {
    if (this.data.shareToken || this.data.isSharedView) return;
    wx.showToast({ title: "正在生成分享链接，请稍后再点", icon: "none" });
    this.prepareShareToken();
  },

  onShareAppMessage() {
    const work = this.data.work || {};
    const token = this.data.shareToken || "";
    const path = token
      ? `/pages/work-detail/work-detail?share=${encodeURIComponent(token)}`
      : `/pages/work-detail/work-detail?id=${work.id || this.data.id || ""}&task_id=${work.task_id || this.data.taskId || ""}`;
    return {
      title: work.title ? `${work.title} - 龙虾AI员工` : "我用龙虾AI员工生成了一个数字人视频",
      path,
      imageUrl: work.cover_url || ""
    };
  },

  onShareTimeline() {
    const work = this.data.work || {};
    const token = this.data.shareToken || "";
    const query = token
      ? `share=${encodeURIComponent(token)}`
      : `id=${encodeURIComponent(work.id || this.data.id || "")}&task_id=${encodeURIComponent(work.task_id || this.data.taskId || "")}`;
    return share.timelineShare({
      title: work.title ? `${work.title} - 必火AI员工` : "必火AI数字人视频",
      query,
      imageUrl: work.cover_url || share.DEFAULT_IMAGE
    });
  }
});
