const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const share = require("../../utils/share");

function videoUrl(item) {
  return item.video_url || item.asset_video_url || item.source_video_url || "";
}

function safeUrl(url) {
  const value = String(url || "").trim();
  if (!/^https?:\/\//i.test(value)) return "";
  if (/^https?:\/\/webstatic\/?$/i.test(value)) return "";
  if (/^https?:\/\/[^/?#]+\/?$/i.test(value) && value.indexOf("webstatic") >= 0) return "";
  if (/aihuoke-1409040632\.cos\.ap-shanghai\.myqcloud\.com\/client\/miniprogram\/hifly_avatars\//i.test(value)) return "";
  return value;
}

function decodeSafe(value) {
  try {
    return decodeURIComponent(value);
  } catch (e) {
    return value;
  }
}

function hiflyStaticCover(url) {
  const decoded = decodeSafe(String(url || ""));
  const staticMatch = decoded.match(/\/static\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (staticMatch && staticMatch[1]) return "";
  const hostedMatch = decoded.match(/\/client\/miniprogram\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (hostedMatch && hostedMatch[1]) return "";
  const hfcdnMatch = decoded.match(/https?:\/\/hfcdn\.lingverse\.co\/[^"'&\s]+/i);
  if (hfcdnMatch) {
    return "";
  }
  return "";
}

function safeCoverUrl(url) {
  const value = String(url || "").trim();
  if (/hfcdn\.lingverse\.co/i.test(value)) return "";
  if (/aihuoke-1409040632\.cos\.ap-shanghai\.myqcloud\.com\/client\/miniprogram\/hifly_avatars\//i.test(value)) return "";
  const staticCover = hiflyStaticCover(url);
  if (staticCover) return staticCover;
  return safeUrl(url);
}

function coverUrl(item) {
  return (
    safeCoverUrl(item.cover_url) ||
    safeCoverUrl(item.image_url) ||
    safeCoverUrl(item.avatar_image_url) ||
    safeCoverUrl(item.avatar_url) ||
    safeCoverUrl(item.poster_url) ||
    safeCoverUrl(item.thumbnail_url) ||
    safeCoverUrl(item.preview_image_url) ||
    ""
  );
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

function normalizeVideo(item) {
  const status = item.status || "processing";
  const url = videoUrl(item);
  const filename = filenameFor(item);
  return Object.assign({}, item, {
    work_type: "digital_video",
    media_type: "video",
    work_type_label: "数字人视频",
    title: item.title || "未命名视频",
    prompt: item.text || item.prompt || "",
    cover_url: coverUrl(item),
    playable_url: url,
    preview_url: url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: statusLabel(status),
    created_at_text: formatTime(item.created_at),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed"
  });
}

function normalizeWanRoleTask(item) {
  const status = item.status || "processing";
  const url = safeUrl(item.playable_url) || safeUrl(item.video_result_url) || safeUrl(item.asset_video_url) || safeUrl(item.source_video_url) || "";
  const title = item.title || item.task_type_label || (item.task_type === "mix" ? "角色替换" : "动作迁移");
  const filename = filenameFor(Object.assign({}, item, { title }));
  return Object.assign({}, item, {
    id: `wan-${item.id || item.dashscope_task_id || url}`,
    raw_id: item.id,
    work_type: "wan_role_video",
    media_type: "video",
    work_type_label: item.task_type_label || (item.task_type === "mix" ? "角色替换" : "动作迁移"),
    title,
    prompt: item.task_type === "mix" ? "AI角色替换任务" : "AI动作迁移任务",
    cover_url: safeUrl(item.image_url),
    playable_url: url,
    preview_url: url,
    url,
    download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_download_url: url ? mediaProxyUrl(url, "attachment", filename) : "",
    proxy_preview_url: url ? mediaProxyUrl(url, "inline", filename) : "",
    status,
    status_label: item.status_label || statusLabel(status),
    created_at_text: formatTime(item.created_at),
    is_processing: status === "processing" || status === "waiting",
    is_success: status === "success",
    is_failed: status === "failed",
    filename,
    billing: item.meta && item.meta.billing ? item.meta.billing : null
  });
}

function filenameFromUrl(url) {
  const path = String(url || "").split("?")[0].split("#")[0];
  const name = decodeURIComponent(path.split("/").pop() || "");
  return name || "AI图片";
}

function looksLikeStorageFilename(value) {
  return /^(assets|uploads|temp_assets)\//i.test(String(value || "")) || /^[a-f0-9]{8,}\.(mp4|mov|webm|jpg|png|jpeg)$/i.test(String(value || ""));
}

function normalizeMediaItem(item) {
  const url = safeUrl(item.url) || safeUrl(item.preview_url) || safeUrl(item.download_url) || "";
  const mediaType = item.media_type || "media";
  const rawTitle = item.title || item.prompt || "";
  const rawFilename = item.filename || filenameFromUrl(url);
  const title = rawTitle && !looksLikeStorageFilename(rawTitle) ? rawTitle : (mediaType === "video" ? "AI视频结果" : rawFilename);
  const filename = filenameFromUrl(url || title);
  const draft = item.publish_draft && typeof item.publish_draft === "object" ? item.publish_draft : null;
  const draftStatus = String((draft && draft.status) || "").toLowerCase();
  return Object.assign({}, item, {
    id: item.id || item.asset_id || url,
    run_id: item.run_id || (draft && draft.run_id) || "",
    publish_draft: draft,
    publish_status: draftStatus,
    publish_status_label: publishStatusLabel(draftStatus),
    can_publish: !!draft && !["published", "pending", "processing"].includes(draftStatus),
    publish_target_label: draft ? [draft.platform_name || draft.platform, draft.account_nickname || draft.account_id].filter(Boolean).join(" · ") : "",
    media_type: mediaType,
    work_type: mediaType,
    work_type_label: mediaType === "image" ? "AI图片" : mediaType === "video" ? "AI视频" : mediaType === "audio" ? "AI音频" : "AI素材",
    title,
    prompt: item.prompt || "",
    playable_url: url,
    preview_url: safeUrl(item.preview_url) || url,
    download_url: safeUrl(item.download_url) || url,
    proxy_download_url: safeUrl(item.proxy_download_url) || safeUrl(item.download_url) || url,
    proxy_preview_url: safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || url,
    status: "success",
    status_label: "已完成",
    created_at_text: formatTime(item.created_at),
    filename
  });
}

function publishStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  return {
    ready: "待发布",
    draft: "待发布",
    pending: "等待发布",
    processing: "发布中",
    published: "已发布",
    failed: "发布失败"
  }[s] || "";
}

function normalizeAssetItem(item) {
  const url = safeUrl(item.source_url) || safeUrl(item.url) || "";
  return normalizeMediaItem(Object.assign({}, item, {
    id: item.id || item.asset_id || url,
    title: item.filename || item.title || filenameFromUrl(url),
    url,
    preview_url: safeUrl(item.preview_url) || url,
    download_url: safeUrl(item.download_url) || url,
    proxy_preview_url: safeUrl(item.proxy_preview_url) || url,
    proxy_download_url: safeUrl(item.proxy_download_url) || url,
    media_type: item.media_type || "media"
  }));
}

function isHiflyVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  return tags.indexOf("hifly") >= 0 || tags.indexOf("video_tts") >= 0 || meta.indexOf("hifly") >= 0;
}

function isInputVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  if (tags.indexOf("input") >= 0) return true;
  if (tags.indexOf("role_transfer/input") >= 0) return true;
  if (meta.indexOf("role_transfer/input") >= 0) return true;
  if (meta.indexOf('"input"') >= 0 && meta.indexOf("role_transfer") >= 0) return true;
  return false;
}

function isGeneratedVideoAsset(item) {
  const tags = String((item && item.tags) || "").toLowerCase();
  const meta = item && item.meta ? JSON.stringify(item.meta).toLowerCase() : "";
  if (isInputVideoAsset(item)) return false;
  if (tags.indexOf("role_transfer") >= 0 || meta.indexOf("wan_role_task_id") >= 0) return true;
  if (tags.indexOf("auto") >= 0 || tags.indexOf("generated") >= 0 || tags.indexOf("result") >= 0) return true;
  return false;
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
    mediaWorks: [],
    previewVisible: false,
    previewItem: null,
    previewVideoUrl: "",
    onlineText: ""
  },

  pollTimer: null,

  onShow() {
    share.showShareMenu();
    app.restoreSession();
    this.refreshAuthState();
    const openSuperVideo = wx.getStorageSync("lobster_open_super_video");
    if (openSuperVideo) {
      wx.removeStorageSync("lobster_open_super_video");
      this.setData({ mediaTab: "video", videoKind: "super" });
    }
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
    if (this.data.phoneBound) this.loadWorks();
  },

  setVideoKind(evt) {
    const kind = evt.currentTarget.dataset.kind || "digital";
    this.setData({ videoKind: kind });
    if (this.data.phoneBound) this.loadWorks();
  },

  noop() {},

  loadWorks() {
    if (!this.refreshAuthState()) {
      this.setData({ works: [], mediaWorks: [], loading: false });
      return Promise.resolve();
    }
    if (this.data.mediaTab !== "video") return this.loadMediaWorks(this.data.mediaTab);
    if (this.data.videoKind !== "digital") return this.loadAssetVideos();
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

  loadAssetVideos() {
    this.stopPolling();
    this.setData({ loading: true });
    return Promise.all([
      app.request({ url: "/api/wan/role-transfer/tasks?page=1&size=30&refresh=true" }).catch(() => ({ items: [] })),
      app.request({ url: "/api/assets?media_type=video&limit=80" }).catch(() => ({ assets: [] }))
    ])
      .then(([taskData, assetData]) => {
        const taskRows = (taskData.items || []).map(normalizeWanRoleTask);
        const taskAssetIds = {};
        taskRows.forEach((item) => {
          if (item.asset_id) taskAssetIds[String(item.asset_id)] = true;
        });
        const assetRows = (assetData.assets || [])
          .filter((item) => item && item.source_url && item.media_type === "video" && !isHiflyVideoAsset(item))
          .filter((item) => isGeneratedVideoAsset(item))
          .filter((item) => !item.asset_id || !taskAssetIds[String(item.asset_id)])
          .map(normalizeAssetItem);
        const rows = taskRows.concat(assetRows);
        this.setData({ mediaWorks: rows, authPanelVisible: false });
        this.refreshSuperVideoPolling();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loading: false }));
  },

  loadMediaWorks(mediaType) {
    const type = mediaType || this.data.mediaTab || "image";
    if (type === "text") {
      this.setData({ mediaWorks: [], loading: false });
      return Promise.resolve();
    }
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    if (!deviceId) {
      wx.showToast({ title: "未找到当前手机设备", icon: "none" });
      return Promise.resolve();
    }
    this.stopPolling();
    this.setData({ loading: true });
    return app
      .request({ url: `/api/mobile/downloads?device_id=${encodeURIComponent(deviceId)}&media_type=${encodeURIComponent(type)}&limit=80` })
      .then((data) => {
        const rows = (data.items || []).map(normalizeMediaItem).filter((item) => item.media_type === type);
        this.setData({ mediaWorks: rows, authPanelVisible: false });
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

  refreshSuperVideoPolling() {
    const hasProcessing = this.data.mediaTab === "video" && this.data.videoKind === "super" && this.data.mediaWorks.some((item) => item.work_type === "wan_role_video" && item.is_processing);
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
    if (this.data.mediaTab === "video" && this.data.videoKind === "super") {
      if (this.data.polling) return;
      this.setData({ polling: true });
      this.loadAssetVideos().finally(() => this.setData({ polling: false }));
      return;
    }
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
    if (item.is_processing) {
      wx.showToast({ title: "生成中，预计5-10分钟", icon: "none" });
      return;
    }
    const url = safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || safeUrl(item.playable_url);
    if (!url) {
      wx.showToast({ title: item.is_failed ? "生成失败，请重新提交" : "暂无可播放视频", icon: "none" });
      return;
    }
    this.setData({
      previewVisible: true,
      previewItem: item,
      previewVideoUrl: url
    });
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
    const item = evt.currentTarget.dataset.source === "preview" ? this.data.previewItem : this.data.works[index];
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
        download_url: item.download_url,
        proxy_download_url: item.proxy_download_url,
        proxy_preview_url: item.proxy_preview_url
      })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const reason = api.errorMessage(err);
        media.copyLink(item.playable_url).finally(() => wx.showToast({ title: `保存失败: ${reason}`.slice(0, 28), icon: "none" }));
      });
  },

  saveMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = evt.currentTarget.dataset.source === "preview" ? this.data.previewItem : this.data.mediaWorks[index];
    if (!item || !item.download_url) {
      wx.showToast({ title: item && item.is_processing ? "生成中，预计5-10分钟" : "暂无可保存链接", icon: "none" });
      return;
    }
    media
      .saveToAlbum(item)
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const reason = api.errorMessage(err);
        media.copyLink(item.url || item.download_url || "").finally(() => wx.showToast({ title: `保存失败: ${reason}`.slice(0, 28), icon: "none" }));
      });
  },

  openMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    if (!item) return;
    if (item.media_type === "image") {
      this.previewMediaWork(evt);
      return;
    }
    if (item.media_type !== "video") return;
    if (item.is_processing) {
      wx.showToast({ title: "生成中，预计5-10分钟", icon: "none" });
      return;
    }
    const url = safeUrl(item.proxy_preview_url) || safeUrl(item.preview_url) || safeUrl(item.url) || safeUrl(item.playable_url);
    if (!url) {
      wx.showToast({ title: item.is_failed ? "生成失败，请重新提交" : "暂无可播放视频", icon: "none" });
      return;
    }
    this.setData({
      previewVisible: true,
      previewItem: item,
      previewVideoUrl: url
    });
  },

  closePreview() {
    this.setData({
      previewVisible: false,
      previewItem: null,
      previewVideoUrl: ""
    });
  },

  savePreviewWork() {
    const item = this.data.previewItem;
    if (!item) return;
    if (item.work_type === "digital_video") {
      this.saveWork({ currentTarget: { dataset: { source: "preview" } } });
      return;
    }
    this.saveMediaWork({ currentTarget: { dataset: { source: "preview" } } });
  },

  copyMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const url = item && (item.url || item.download_url || item.preview_url);
    if (!url) {
      wx.showToast({ title: item && item.is_processing ? "生成中，预计5-10分钟" : "暂无链接", icon: "none" });
      return;
    }
    media.copyLink(url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  publishMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const runId = item && (item.run_id || (item.publish_draft && item.publish_draft.run_id));
    if (!runId) {
      wx.showToast({ title: "缺少发布记录", icon: "none" });
      return;
    }
    wx.showLoading({ title: "提交发布", mask: true });
    app
      .request({
        method: "POST",
        url: `/api/scheduled-tasks/runs/${encodeURIComponent(runId)}/publish-request`,
        data: {}
      })
      .then(() => {
        wx.showToast({ title: "已提交发布", icon: "success" });
        this.loadMediaWorks(this.data.mediaTab);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  previewMediaWork(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.mediaWorks[index];
    const url = item && (safeUrl(item.preview_url) || safeUrl(item.url));
    if (!url) return;
    if (item.media_type === "image") {
      wx.previewImage({
        current: url,
        urls: this.data.mediaWorks.filter((row) => row.media_type === "image").map((row) => row.preview_url || row.url).filter(Boolean)
      });
    }
  },

  goCreate() {
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  onShareAppMessage() {
    const item = this.data.previewItem;
    if (this.data.previewVisible && item) {
      return share.appShare({
        title: item.title || "我的AI视频作品",
        path: "/pages/downloads/downloads",
        imageUrl: item.cover_url || ""
      });
    }
    return share.appShare({
      title: "我的AI作品 - 必火AI员工",
      path: "/pages/downloads/downloads"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "必火AI员工 - AI视频作品"
    });
  }
});
