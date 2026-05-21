const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const staticAssets = require("../../utils/static_assets");

function assetUrl(path) {
  const value = String(path || "").trim();
  if (!value) return "";
  let decoded = value;
  try {
    decoded = decodeURIComponent(value);
  } catch (e) {
    decoded = value;
  }
  if (/hfcdn\.lingverse\.co/i.test(value) || /hfcdn\.lingverse\.co/i.test(decoded)) {
    const match = decoded.match(/https?:\/\/hfcdn\.lingverse\.co\/[^"'&\s]+/i);
    const clean = match ? match[0].split("?")[0].split("#")[0] : "";
    const filename = clean.split("/").pop();
    return filename ? staticAssets.hiflyAvatarUrl(`${filename}.jpg`) : "";
  }
  const staticMatch = decoded.match(/\/static\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (staticMatch && staticMatch[1]) {
    return staticAssets.hiflyAvatarUrl(staticMatch[1]);
  }
  const hostedMatch = decoded.match(/\/client\/miniprogram\/hifly_avatars\/([^"'&?\s#]+)/i);
  if (hostedMatch && hostedMatch[1]) {
    return staticAssets.hiflyAvatarUrl(hostedMatch[1]);
  }
  if (/^https?:\/\//i.test(value)) return value;
  if (/^\/\//.test(value)) return `https:${value}`;
  if (value.indexOf("/client/miniprogram/") === 0) return staticAssets.staticAssetUrl(value.replace("/client/miniprogram/", ""));
  if (value.charAt(0) === "/") return api.buildUrl(value);
  return api.buildUrl(value);
}

function uniqueBy(rows, keyName) {
  const out = [];
  const seen = {};
  (rows || []).forEach((row) => {
    const key = String((row && row[keyName]) || "").trim();
    if (!key || seen[key]) return;
    seen[key] = true;
    out.push(row);
  });
  return out;
}

function normalizeAvatar(row, source) {
  const avatar = String((row && row.avatar) || "").trim();
  if (!avatar) return null;
  const rawImage = (row && (row.image_url || row.cover_url || row.detail_url || row.thumb || row.avatar_url || row.poster_url || row.thumbnail_url || row.preview_url)) || "";
  return {
    avatar,
    id: `${source}:${avatar}`,
    title: (row && (row.title || row.name || row.avatar)) || avatar,
    image_url: assetUrl(rawImage),
    section: source,
    section_label: source === "mine" ? "我的数字人" : "公共数字人",
    status: (row && row.status) || "success",
    raw: row || {}
  };
}

function normalizeVoice(row, source) {
  const styles = Array.isArray(row && row.styles) && row.styles.length ? row.styles : [row || {}];
  const baseTitle = (row && (row.title || row.name || row.voice)) || "";
  const rows = [];
  styles.forEach((style) => {
    const voice = String((style && style.voice) || (row && row.voice) || "").trim();
    if (!voice) return;
    const label = (style && style.label && style.label !== "默认风格") ? `${baseTitle} - ${style.label}` : (baseTitle || voice);
    rows.push({
      voice,
      id: `${source}:${voice}`,
      title: label,
      demo_url: assetUrl((style && style.demo_url) || (row && (row.demo_url || row.audio_url || row.preview_url)) || ""),
      section: source,
      section_label: source === "mine" ? "我的声音" : "公共声音",
      status: (row && row.status) || "success",
      rate: (style && style.rate) || (row && row.rate) || "",
      volume: (style && style.volume) || (row && row.volume) || "",
      pitch: (style && style.pitch) || (row && row.pitch) || "",
      raw: row || {}
    });
  });
  return rows;
}

function videoItemForSave(item) {
  const url = item.video_url || item.asset_video_url || item.source_video_url || "";
  return {
    id: item.id,
    title: item.title || `digital_${item.id}.mp4`,
    media_type: "video",
    url,
    preview_url: url,
    download_url: url
  };
}

function normalizeVoiceList(rows, source) {
  const out = [];
  (rows || []).forEach((row) => {
    normalizeVoice(row, source).forEach((item) => out.push(item));
  });
  return out;
}

function selectSource(mine, publicRows) {
  return mine && mine.length ? "mine" : "public";
}

function voiceParamText(value, fallback, min, max) {
  const raw = value === undefined || value === null || value === "" ? fallback : value;
  const num = Number(raw);
  const safeNum = Math.max(min, Math.min(max, Number.isNaN(num) ? fallback : num));
  return safeNum.toFixed(2);
}

Page({
  data: {
    phoneBound: false,
    authPanelVisible: false,
    pageMode: "select",
    manageTab: "avatar",
    activeAssetTab: "avatar",
    avatarTab: "public",
    voiceTab: "public",
    avatarsMine: [],
    avatarsPublic: [],
    voicesMine: [],
    voicesPublic: [],
    displayAvatars: [],
    displayAvatarSource: "public",
    displayVoices: [],
    displayVoiceSource: "public",
    videos: [],
    selectedAvatar: null,
    selectedVoice: null,
    selectedVideo: null,
    title: "数字人口播",
    text: "",
    stShow: true,
    speechRate: 1,
    speechRateText: "1.00x",
    loadingAssets: false,
    loadingVideos: false,
    submitting: false,
    pollingTaskId: "",
    progressText: "",
    audioPlayingId: "",
    previewVisible: false,
    previewVideoUrl: "",
    onlineDevices: [],
    selectedInstallationId: "",
    onlineText: "",
    authHint: "使用数字人前需要微信登录并绑定手机号。"
  },

  audio: null,
  pollTimer: null,

  onShow() {
    app.restoreSession();
    this.applyPrefill();
    this.refreshAuthState();
    if (this.data.phoneBound) {
      this.loadAll();
    }
  },

  onUnload() {
    this.stopAudio();
    this.stopPolling();
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh());
  },

  refreshAuthState() {
    const phoneBound = Boolean(app.globalData.token && app.globalData.phone);
    this.setData({ phoneBound });
    return phoneBound;
  },

  applyPrefill() {
    const prefill = wx.getStorageSync("lobster_digital_prefill");
    if (!prefill) return;
    wx.removeStorageSync("lobster_digital_prefill");
    const selectedAvatar = prefill.avatar ? normalizeAvatar(prefill.avatar, prefill.avatar.section || "public") : null;
    const data = {
      pageMode: "create",
      title: prefill.title || this.data.title,
      text: prefill.text || this.data.text
    };
    if (selectedAvatar) {
      data.selectedAvatar = selectedAvatar;
      data.avatarTab = selectedAvatar.section || "public";
      data.displayAvatarSource = selectedAvatar.section || "public";
    }
    this.setData(data);
  },

  showAuthPanel(hint) {
    this.refreshAuthState();
    if (this.data.phoneBound) return false;
    this.setData({
      authPanelVisible: true,
      authHint: hint || "使用数字人前需要微信登录并绑定手机号。"
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
        this.loadAll();
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
        this.loadAll();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  loadAll() {
    if (!this.refreshAuthState()) return Promise.resolve();
    return Promise.all([this.loadAssets(), this.loadVideos(), this.loadOnlineStatus()]);
  },

  loadOnlineStatus(showToast) {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ onlineText: "正在检查 online..." });
    return app
      .request({ url: "/api/h5-chat/devices/status" })
      .then((data) => {
        const devices = Array.isArray(data.devices) ? data.devices : [];
        const selected = devices.find((d) => d.online && d.installation_id) || devices.find((d) => d.installation_id) || {};
        const onlineCount = devices.filter((d) => d.online).length;
        this.setData({
          onlineDevices: devices,
          selectedInstallationId: selected.installation_id || "",
          onlineText: data.online ? `online 已连接 ${onlineCount}/${devices.length || onlineCount}` : "online 暂未在线"
        });
      })
      .catch((err) => {
        this.setData({ onlineText: "online 状态获取失败" });
        if (showToast) wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  loadAssets() {
    this.setData({ loadingAssets: true });
    return Promise.all([
      app.request({ url: "/api/hifly/my/avatar/list?page=1&size=100&status=success" }).catch(() => ({ items: [] })),
      app.request({ method: "POST", url: "/api/hifly/avatar/library", data: {} }).catch(() => ({ public: [] })),
      app.request({ url: "/api/hifly/my/voice/list?page=1&size=100&status=success" }).catch(() => ({ items: [] })),
      app.request({ method: "POST", url: "/api/hifly/voice/library", data: {} }).catch(() => ({ public: [] }))
    ])
      .then(([myAvatars, publicAvatars, myVoices, publicVoices]) => {
        const avatarsMine = uniqueBy((myAvatars.items || []).map((row) => normalizeAvatar(row, "mine")).filter(Boolean), "avatar");
        const avatarsPublic = uniqueBy((publicAvatars.public || []).map((row) => normalizeAvatar(row, "public")).filter(Boolean), "avatar");
        const voicesMine = uniqueBy(normalizeVoiceList(myVoices.items || [], "mine"), "voice");
        const voicesPublic = uniqueBy(normalizeVoiceList(publicVoices.public || [], "public"), "voice");
        const selectedAvatar = this.data.selectedAvatar || avatarsMine[0] || avatarsPublic[0] || null;
        const selectedVoice = this.data.selectedVoice || voicesMine[0] || voicesPublic[0] || null;
        const displayAvatarSource = selectedAvatar && selectedAvatar.section ? selectedAvatar.section : selectSource(avatarsMine, avatarsPublic);
        const displayVoiceSource = selectSource(voicesMine, voicesPublic);
        this.setData({
          avatarsMine,
          avatarsPublic,
          voicesMine,
          voicesPublic,
          displayAvatars: displayAvatarSource === "mine" ? avatarsMine : avatarsPublic,
          displayAvatarSource,
          displayVoices: displayVoiceSource === "mine" ? voicesMine : voicesPublic,
          displayVoiceSource,
          selectedAvatar,
          selectedVoice,
          avatarTab: displayAvatarSource,
          voiceTab: voicesMine.length ? "mine" : "public"
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loadingAssets: false }));
  },

  loadVideos() {
    this.setData({ loadingVideos: true });
    return app
      .request({ url: "/api/hifly/my/video/list?page=1&size=30" })
      .then((data) => this.setData({ videos: data.items || [] }))
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ loadingVideos: false }));
  },

  setAssetTab(evt) {
    this.setData({ activeAssetTab: evt.currentTarget.dataset.tab || "avatar" });
  },

  setPageMode(evt) {
    const mode = evt.currentTarget.dataset.mode || "select";
    this.setData({ pageMode: mode });
  },

  goSelect() {
    this.setData({ pageMode: "select" });
  },

  goBack() {
    wx.navigateBack({
      fail() {
        wx.switchTab({ url: "/pages/index/index" });
      }
    });
  },

  goCreate() {
    this.setData({ pageMode: "create" });
  },

  goPickDigital() {
    const text = (this.data.text || "").trim();
    if (!text) {
      wx.showToast({ title: "请输入视频内容", icon: "none" });
      return;
    }
    this.setData({ pageMode: "pick" });
  },

  submitDigitalTask() {
    if (!this.data.selectedAvatar) {
      wx.showToast({ title: "请选择数字人", icon: "none" });
      return;
    }
    if (!this.data.selectedVoice) {
      wx.showToast({ title: "请选择声音", icon: "none" });
      return;
    }
    this.createVideo();
  },

  goManage() {
    this.setData({ pageMode: "manage", manageTab: this.data.activeAssetTab || "avatar" });
  },

  setManageTab(evt) {
    this.setData({ manageTab: evt.currentTarget.dataset.tab || "avatar" });
  },

  setAvatarTab(evt) {
    this.setData({ avatarTab: evt.currentTarget.dataset.tab || "public" });
  },

  setVoiceTab(evt) {
    this.setData({ voiceTab: evt.currentTarget.dataset.tab || "public" });
  },

  selectAvatar(evt) {
    const source = evt.currentTarget.dataset.source;
    const index = Number(evt.currentTarget.dataset.index || 0);
    const list = source === "mine" ? this.data.avatarsMine : this.data.avatarsPublic;
    this.setData({ selectedAvatar: list[index] || null });
  },

  selectVoice(evt) {
    const source = evt.currentTarget.dataset.source;
    const index = Number(evt.currentTarget.dataset.index || 0);
    const list = source === "mine" ? this.data.voicesMine : this.data.voicesPublic;
    this.setData({ selectedVoice: list[index] || null });
  },

  onTitleInput(evt) {
    this.setData({ title: evt.detail.value || "" });
  },

  onTextInput(evt) {
    this.setData({ text: evt.detail.value || "" });
  },

  onSpeechRateChange(evt) {
    const raw = Number(evt.detail.value || 100);
    const rate = Math.max(50, Math.min(200, raw)) / 100;
    this.setData({
      speechRate: rate,
      speechRateText: `${rate.toFixed(2)}x`
    });
  },

  toggleSubtitle(evt) {
    this.setData({ stShow: Boolean(evt.detail.value) });
  },

  fillSample() {
    this.setData({
      title: this.data.title || "数字人口播",
      text: "大家好，我是你的数字人助理。今天为你介绍一个可以快速生成口播视频的工作流。"
    });
  },

  playVoice(evt) {
    const source = evt.currentTarget.dataset.source;
    const index = Number(evt.currentTarget.dataset.index || 0);
    const list = source === "mine" ? this.data.voicesMine : this.data.voicesPublic;
    const item = list[index];
    if (!item || !item.demo_url) {
      wx.showToast({ title: "暂无试听音频", icon: "none" });
      return;
    }
    if (this.data.audioPlayingId === item.id) {
      this.stopAudio();
      return;
    }
    this.stopAudio();
    const audio = wx.createInnerAudioContext();
    this.audio = audio;
    audio.src = item.demo_url;
    audio.obeyMuteSwitch = false;
    audio.onEnded(() => this.setData({ audioPlayingId: "" }));
    audio.onError(() => {
      this.setData({ audioPlayingId: "" });
      wx.showToast({ title: "试听失败", icon: "none" });
    });
    audio.play();
    this.setData({ audioPlayingId: item.id });
  },

  stopAudio() {
    if (this.audio) {
      try {
        this.audio.stop();
        this.audio.destroy();
      } catch (e) {
        // ignore cleanup errors
      }
      this.audio = null;
    }
    this.setData({ audioPlayingId: "" });
  },

  createVideo() {
    if (this.showAuthPanel("生成数字人视频前需要先登录并绑定手机号。")) return;
    const avatar = this.data.selectedAvatar;
    const voice = this.data.selectedVoice;
    const text = (this.data.text || "").trim();
    const title = (this.data.title || "").trim() || "数字人口播";
    if (!avatar || !avatar.avatar) {
      wx.showToast({ title: "请选择数字人", icon: "none" });
      return;
    }
    if (!voice || !voice.voice) {
      wx.showToast({ title: "请选择声音", icon: "none" });
      return;
    }
    if (!text) {
      wx.showToast({ title: "请输入口播文案", icon: "none" });
      return;
    }
    const sendCreateRequest = (installationId) => {
      this.setData({ submitting: true, progressText: "正在创建数字人视频" });
      return app
        .request({
          method: "POST",
          url: "/api/hifly/my/video/create-by-tts",
          header: { "X-Installation-Id": installationId },
          data: {
            title,
            avatar: avatar.avatar,
            avatar_title: avatar.title || "",
            avatar_image_url: avatar.image_url || "",
            voice: voice.voice,
            voice_title: voice.title || "",
            text,
            st_show: this.data.stShow ? 1 : 0,
            rate: voiceParamText(this.data.speechRate || voice.rate, 1, 0.5, 2),
            volume: voiceParamText(voice.volume, 1, 0.1, 2),
            pitch: voiceParamText(voice.pitch, 1, 0.1, 2)
          },
          timeout: 60000
        })
        .then((data) => {
          wx.showToast({ title: "任务已创建", icon: "success" });
          this.setData({
            pollingTaskId: data.task_id || "",
            selectedVideo: data.item || null,
            progressText: "视频生成中，请稍候",
            text: ""
          });
          wx.setStorageSync("lobster_refresh_works", "1");
          this.stopPolling();
          setTimeout(() => {
            wx.switchTab({ url: "/pages/downloads/downloads" });
          }, 500);
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
        .finally(() => this.setData({ submitting: false }));
    };

    const installationId = this.data.selectedInstallationId;
    if (!installationId) {
      this.setData({ progressText: "正在检查本地 online 设备" });
      this.loadOnlineStatus(true).then(() => {
        const refreshedInstallationId = this.data.selectedInstallationId;
        if (!refreshedInstallationId) {
          wx.showToast({ title: "未检测到本地 online 设备", icon: "none" });
          this.setData({ progressText: "" });
          return;
        }
        sendCreateRequest(refreshedInstallationId);
      });
      return;
    }
    sendCreateRequest(installationId);
  },

  startPolling(taskId) {
    this.stopPolling();
    if (!taskId) return;
    const poll = () => this.pollTask(taskId);
    poll();
    this.pollTimer = setInterval(poll, 8000);
  },

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  pollTask(taskId) {
    return app
      .request({
        method: "POST",
        url: "/api/hifly/my/video/task",
        data: { task_id: taskId },
        timeout: 60000
      })
      .then((data) => {
        const item = data.item || {};
        this.setData({ selectedVideo: item, progressText: data.status_text || item.status_text || "处理中" });
        this.loadVideos();
        if (item.status === "success" || item.status === "failed" || Number(data.status) === 3 || Number(data.status) === 4) {
          this.stopPolling();
          this.setData({ pollingTaskId: "" });
          wx.showToast({ title: item.status === "success" ? "生成完成" : "生成失败", icon: "none" });
        }
      })
      .catch((err) => {
        this.stopPolling();
        this.setData({ pollingTaskId: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  pollVideo(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.videos[index];
    if (!item || !item.task_id) return;
    this.setData({ progressText: "正在刷新任务状态", selectedVideo: item });
    this.pollTask(item.task_id);
  },

  previewVideo(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.videos[index];
    const url = item && item.video_url;
    if (!url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    this.setData({ previewVisible: true, previewVideoUrl: url, selectedVideo: item });
  },

  closePreview() {
    this.setData({ previewVisible: false, previewVideoUrl: "" });
  },

  saveVideo(evt) {
    const rawIndex = evt.currentTarget.dataset.index;
    const item = rawIndex === undefined ? this.data.selectedVideo : this.data.videos[Number(rawIndex || 0)];
    if (!item || !item.video_url) {
      wx.showToast({ title: "视频还未生成", icon: "none" });
      return;
    }
    media
      .saveToAlbum(videoItemForSave(item))
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const text = api.errorMessage(err);
        if (/auth deny|authorize|permission|scope/i.test(text)) {
          wx.showModal({
            title: "需要相册权限",
            content: "请允许保存到相册后再试。",
            confirmText: "去设置",
            success(res) {
              if (res.confirm) wx.openSetting({});
            }
          });
          return;
        }
        media.copyLink(item.video_url).finally(() => wx.showToast({ title: "保存失败，已复制链接", icon: "none" }));
      });
  },

  copyVideo(evt) {
    const rawIndex = evt.currentTarget.dataset.index;
    const item = rawIndex === undefined ? this.data.selectedVideo : this.data.videos[Number(rawIndex || 0)];
    const url = item && item.video_url;
    if (!url) {
      wx.showToast({ title: "暂无视频链接", icon: "none" });
      return;
    }
    media.copyLink(url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  deleteVideo(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.videos[index];
    if (!item) return;
    wx.showModal({
      title: "删除作品",
      content: "确定删除这个数字人视频吗？",
      success: (res) => {
        if (!res.confirm) return;
        app
          .request({ method: "DELETE", url: `/api/hifly/my/video/${item.id}` })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadVideos();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  }
});
