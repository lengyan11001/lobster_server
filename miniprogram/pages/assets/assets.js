const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");

const TABS = ["图片", "视频", "音频", "形象分身", "声音分身"];
const UPLOAD_TYPES = ["image", "video", "audio", "avatar", "voice"];
const READY = ["success", "succeed", "ready", "completed", "done"];

function base() {
  return String(config.API_BASE || "").replace(/\/+$/, "");
}

function mediaUrl(url, mode) {
  const u = String(url || "").trim();
  if (!u) return "";
  if (/^data:/i.test(u)) return u;
  const abs = /^https?:\/\//i.test(u) ? u : base() + (u.charAt(0) === "/" ? u : "/" + u);
  return `${base()}/api/media/${mode}?url=${encodeURIComponent(abs)}`;
}

function askTitle(title, placeholder) {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      editable: true,
      placeholderText: placeholder,
      success: (res) => resolve(res.confirm ? String(res.content || "").trim() : ""),
      fail: () => resolve("")
    });
  });
}

Page({
  data: {
    viewMode: "home",
    scope: "",
    pageTitle: "图片",
    showUploads: true,
    showAvatars: false,
    showVoices: false,
    tabs: TABS,
    tabIndex: 0,
    isDigital: false,
    keyword: "",
    all: [],
    items: [],
    avatars: [],
    voices: [],
    loading: false,
    tabCount: 0,
    currentCount: 0,
    labelOpen: false,
    labelAssetId: "",
    labelGroup: "",
    labelTags: "",
    labelSaving: false,
    previewOpen: false,
    previewType: "",
    previewUrl: "",
    previewTitle: ""
  },

  onLoad(options) {
    const opened = this.applyWantedTab((options && options.tab) || "");
    if (opened) this.setData({ viewMode: "list" });
  },

  goEntry(e) {
    const key = String(e.currentTarget.dataset.key || "");
    if (key === "ip") {
      wx.navigateTo({ url: "/pages/ip-profile/ip-profile" });
      return;
    }
    if (key === "digital") {
      // 只展示素材库里的形象分身 + 声音分身，不再给 tab，也不进生成记录
      this.setData({
        viewMode: "list", scope: "digital", pageTitle: "数字人分身",
        isDigital: true, tabIndex: 3,
        showUploads: false, showAvatars: true, showVoices: true,
        tabCount: 0, currentCount: 0
      });
      this.loadDigital("avatar");
      this.loadDigital("voice");
      return;
    }
    if (key === "image" || key === "video") {
      const index = key === "image" ? 0 : 1;
      this.setData({
        viewMode: "list", scope: key, pageTitle: key === "image" ? "图片" : "视频",
        tabIndex: index, isDigital: false,
        showUploads: true, showAvatars: false, showVoices: false,
        tabCount: 0, currentCount: 0
      });
      this.loadUploads();
      return;
    }
  },

  backHome() {
    this.setData({
      viewMode: "home", scope: "", keyword: "",
      showUploads: true, showAvatars: false, showVoices: false
    });
  },

  applyWantedTab(raw) {
    const map = { image: 0, video: 1, audio: 2, avatar: 3, voice: 4 };
    const wanted = String(raw || "").toLowerCase();
    const index = map[wanted];
    if (index == null) return false;
    this.setData({
      tabIndex: index, isDigital: index >= 3, scope: "",
      pageTitle: TABS[index],
      showUploads: index < 3, showAvatars: index === 3, showVoices: index === 4
    });
    return true;
  },

  onShow() {
    if (typeof this.getTabBar === "function" && this.getTabBar()) {
      this.getTabBar().setData({ selected: 1 });
    }
    const wanted = wx.getStorageSync("lobster_assets_tab");
    if (wanted) {
      wx.removeStorageSync("lobster_assets_tab");
      if (this.applyWantedTab(wanted)) this.setData({ viewMode: "list" });
    }
    if (this.data.viewMode !== "list") return;
    if (this.data.scope === "digital") {
      this.loadDigital("avatar");
      this.loadDigital("voice");
      return;
    }
    if (this.data.isDigital) {
      this.loadDigital(this.data.tabIndex === 3 ? "avatar" : "voice");
      return;
    }
    this.loadUploads();
  },

  onPullDownRefresh() {
    if (this.data.viewMode !== "list") {
      wx.stopPullDownRefresh();
      return;
    }
    let job;
    if (this.data.scope === "digital") {
      job = Promise.all([this.loadDigital("avatar"), this.loadDigital("voice")]);
    } else if (this.data.isDigital) {
      job = this.loadDigital(this.data.tabIndex === 3 ? "avatar" : "voice");
    } else {
      job = this.loadUploads();
    }
    job.then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh());
  },

  loadUploads() {
    this.setData({ loading: true });
    return app.request({ url: "/api/assets?limit=120", timeout: 30000 })
      .then((data) => {
        const rows = ((data && (data.assets || data.items)) || [])
          .map((row) => {
            const id = String(row.asset_id || row.id || "");
            if (!id) return null;
            const type = String(row.media_type || "").toLowerCase();
            if (["image", "video", "audio"].indexOf(type) < 0) return null;
            const src = String(row.source_url || row.url || row.open_url || "");
            return {
              asset_id: id,
              media_type: type,
              group: String(row.creative_candidate_group || ""),
              tags: String(row.tags || ""),
              name: String(row.filename || row.name || id),
              url: src,
              thumb: mediaUrl(src, "thumb")
            };
          })
          .filter(Boolean);
        this.setData({ all: rows });
        this.applyFilter(rows, this.data.tabIndex, this.data.keyword);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  loadDigital(kindOverride) {
    const kind = kindOverride || (this.data.tabIndex === 3 ? "avatar" : "voice");
    this.setData({ loading: true });
    return app.request({ url: `/api/h5/assets/digital-library?kind=${kind}&page=1&size=100&include_pending=1`, timeout: 30000 })
      .then((data) => {
        const rows = (data && data.items) || [];
        if (kind === "avatar") {
          const avatars = rows.filter((row) => String(row.source || "") === "shanjian").map((row, i) => {
            const status = String(row.status || "").toLowerCase();
            const image = String(row.image_url || row.cover_url || "");
            const video = String(row.video_url || row.media_url || row.detail_url || "");
            const isVideo = String(row.source_type || "").toLowerCase().indexOf("video") >= 0;
            const thumbSource = image || video;
            return {
              id: String(row.id || row.avatar || i),
              recordId: Number(row.source_record_id || 0) || 0,
              source: String(row.source || ""),
              profileId: Number(row.source_record_id || 0) || 0,
              title: String(row.title || row.avatar || "形象分身"),
              thumb: thumbSource ? mediaUrl(thumbSource, "thumb") : "",
              videoUrl: isVideo && video ? video : "",
              isVideo,
              ok: READY.indexOf(status) >= 0,
              status,
              statusText: String(row.status_text || status || "未知"),
              message: String(row.message || "")
            };
          });
          const withVoices = this.data.scope === "digital";
          this.setData({
            avatars,
            tabCount: withVoices ? avatars.length + (this.data.voices || []).length : avatars.length,
            currentCount: withVoices ? avatars.length + (this.data.voices || []).length : avatars.length
          });
          this.refreshPendingAvatars(avatars);
        } else {
          const voices = rows.map((row, i) => {
            const status = String(row.status || "").toLowerCase();
            return {
              id: String(row.id || row.voice || i),
              recordId: Number(row.id || 0) || 0,
              title: String(row.title || row.voice || "声音分身"),
              demo: mediaUrl(row.demo_url || "", "proxy"),
              provider: String(row.provider || "hifly"),
              ok: READY.indexOf(status) >= 0,
              statusText: String(row.status_text || status || "未知")
            };
          });
          const withAvatars = this.data.scope === "digital";
          this.setData({
            voices,
            tabCount: withAvatars ? (this.data.avatars || []).length + voices.length : voices.length,
            currentCount: withAvatars ? (this.data.avatars || []).length + voices.length : voices.length
          });
        }
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  applyFilter(all, tabIndex, keyword) {
    const type = UPLOAD_TYPES[tabIndex] || "image";
    const kw = String(keyword || "").trim().toLowerCase();
    const items = (all || [])
      .filter((row) => row.media_type === type)
      .filter((row) => !kw || row.name.toLowerCase().indexOf(kw) >= 0);
    this.setData({ items, tabCount: (all || []).filter((r) => r.media_type === type).length, currentCount: items.length });
  },

  switchTab(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const isDigital = index >= 3;
    this.setData({
      tabIndex: index, isDigital, scope: "", pageTitle: TABS[index],
      showUploads: !isDigital, showAvatars: index === 3, showVoices: index === 4,
      tabCount: 0, currentCount: 0
    });
    if (isDigital) this.loadDigital(index === 3 ? "avatar" : "voice");
    else this.applyFilter(this.data.all, index, this.data.keyword);
  },

  onKeyword(e) {
    const keyword = e.detail.value;
    this.setData({ keyword });
    this.applyFilter(this.data.all, this.data.tabIndex, keyword);
  },

  preview(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    if (row.media_type === "audio") {
      if (!this.audio) this.audio = wx.createInnerAudioContext();
      this.audio.src = mediaUrl(row.url, "proxy");
      this.audio.play();
      return;
    }
    if (row.media_type === "video" || row.media_type === "image") {
      this.setData({
        previewOpen: true,
        previewType: row.media_type,
        previewUrl: mediaUrl(row.url, "proxy"),
        previewTitle: row.name || "素材预览"
      });
    }
  },

  closePreview() {
    this.setData({ previewOpen: false, previewType: "", previewUrl: "", previewTitle: "" });
  },

  refreshPendingAvatars(avatars) {
    if (this._avatarPolling) return;
    const pending = (avatars || []).filter((row) => row && row.profileId && row.status === "processing").slice(0, 3);
    if (!pending.length) return;
    this._avatarPolling = true;
    Promise.all(pending.map((row) => app.request({
      method: "POST",
      url: "/api/shanjian-digital-human/profile/task",
      data: { profile_id: row.profileId },
      timeout: 30000
    }).catch(() => null)))
      .then((rows) => {
        this._avatarPolling = false;
        const changed = rows.some((res) => {
          const status = String((res && res.profile && res.profile.status) || "").toLowerCase();
          return status && status !== "processing";
        });
        if (changed) this.loadDigital();
      })
      .catch(() => { this._avatarPolling = false; });
  },

  removeAvatar(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.avatars[index];
    if (!row) return;
    const recordId = Number(row.recordId || row.profileId || 0);
    if (!recordId) {
      wx.showToast({ title: "这条记录没有可删除的 ID", icon: "none" });
      return;
    }
    wx.showModal({
      title: "删除形象分身",
      content: "确认删除「" + row.title + "」？删除后模板里将不能再选它。",
      confirmColor: "#dc2626",
      success: (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: "删除中", mask: true });
        const url = row.source === "hifly"
          ? "/api/hifly/my/avatar/" + recordId
          : "/api/shanjian-digital-human/profiles/" + recordId;
        app.request({ method: "DELETE", url, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadDigital();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  removeVoice(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.voices[index];
    if (!row) return;
    const recordId = Number(row.recordId || 0);
    if (!recordId) {
      wx.showToast({ title: "这条记录没有可删除的 ID", icon: "none" });
      return;
    }
    wx.showModal({
      title: "删除声音分身",
      content: "确认删除「" + row.title + "」？删除后模板里将不能再选它。",
      confirmColor: "#dc2626",
      success: (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: "删除中", mask: true });
        app.request({ method: "DELETE", url: "/api/hifly/my/voice/" + recordId, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadDigital();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  previewAvatar(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.avatars[index];
    if (!row) return;
    if (!row.ok) {
      wx.showModal({
        title: row.status === "failed" ? "训练失败" : "训练中",
        content: row.status === "failed"
          ? (row.message || "上游拒绝了这次训练，请换一张 2000x2000 以内的清晰正面照片后重新提交。")
          : "数字人还在训练中，完成后会自动出现在这里。",
        showCancel: false
      });
      return;
    }
    if (row.videoUrl) {
      wx.previewMedia({ sources: [{ url: mediaUrl(row.videoUrl, "proxy"), type: "video" }] });
      return;
    }
    if (row.thumb) wx.previewImage({ current: row.thumb, urls: [row.thumb] });
  },

  playVoice(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.voices[index];
    if (!row || !row.demo) return wx.showToast({ title: "该声音没有试听音频", icon: "none" });
    if (!this.audio) this.audio = wx.createInnerAudioContext();
    this.audio.src = row.demo;
    this.audio.play();
  },

  upload() {
    const index = this.data.tabIndex;
    if (index === 3) {
      wx.navigateTo({ url: "/pages/avatar-create/avatar-create" });
      return;
    }
    if (index === 4) {
      wx.navigateTo({ url: "/pages/voice-create/voice-create" });
      return;
    }
    const type = UPLOAD_TYPES[index] || "image";
    if (type === "audio") {
      wx.chooseMessageFile({
        count: 1, type: "file", extension: ["mp3", "wav", "m4a", "aac"],
        success: (res) => {
          const file = (res.tempFiles || [])[0];
          if (file) this.doUpload(file.path, "audio");
        }
      });
      return;
    }
    wx.chooseMedia({
      count: 1,
      mediaType: [type],
      maxDuration: 60,
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (file) this.doUpload(file.tempFilePath, type);
      }
    });
  },



  doUpload(filePath, mediaType) {
    askTitle("素材分组", "输入分组名（可留空，如：产品A素材）").then((group) => {
      askTitle("素材标签", "逗号分隔（可留空，如：产品,口播,横屏）").then((tags) => {
        this.uploadWithMeta(filePath, mediaType, group, tags);
      });
    });
  },

  uploadWithMeta(filePath, mediaType, group, tags) {
    wx.showLoading({ title: "上传中" });
    const formData = { media_type: mediaType, tags: tags || "miniprogram,asset" };
    if (group) formData.creative_candidate_group = group;
    api.uploadFile({
      url: "/api/assets/upload",
      filePath,
      name: "file",
      formData,
      token: app.globalData.token || ""
    })
      .then(() => {
        wx.showToast({ title: "已上传", icon: "success" });
        this.loadUploads();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  onAssetMenu(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    const isVideo = row.media_type === "video";
    const isAudio = row.media_type === "audio";
    const list = isVideo
      ? ["预览", "编辑分组/标签", "加入分组", "AI 打标签", "删除素材"]
      : (isAudio
        ? ["播放", "编辑分组/标签", "加入分组", "删除素材"]
        : ["预览", "编辑分组/标签", "加入分组", "删除素材"]);
    wx.showActionSheet({
      itemList: list,
      success: (res) => {
        const action = list[res.tapIndex];
        if (action === "预览" || action === "播放") this.preview({ currentTarget: { dataset: { index } } });
        else if (action === "编辑分组/标签") this.editLabels(row);
        else if (action === "加入分组") this.addToGroup(row);
        else if (action === "AI 打标签") this.aiTags(row);
        else if (action === "删除素材") this.confirmRemove(row);
      }
    });
  },

  addToGroup(row) {
    wx.showModal({
      title: "加入分组",
      editable: true,
      placeholderText: "输入分组名，例如：产品A素材",
      content: row.group || "",
      success: (res) => {
        if (!res.confirm) return;
        const group = String(res.content || "").trim();
        if (!group) return wx.showToast({ title: "分组名不能为空", icon: "none" });
        wx.showLoading({ title: "保存中" });
        app.request({
          method: "POST",
          url: `/api/assets/${encodeURIComponent(row.asset_id)}/labels`,
          data: { creative_candidate_group: group, tags: row.tags || "" },
          timeout: 30000
        })
          .then(() => { wx.showToast({ title: "已加入分组", icon: "success" }); this.loadUploads(); })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  aiTags(row) {
    if (row.media_type !== "video") {
      wx.showToast({ title: "AI 打标签只支持视频", icon: "none" });
      return;
    }
    wx.showLoading({ title: "下发中" });
    app.request({
      method: "POST",
      url: `/api/assets/${encodeURIComponent(row.asset_id)}/ai-tags`,
      data: {},
      timeout: 60000
    })
      .then(() => {
        wx.hideLoading();
        wx.showModal({
          title: "已下发",
          content: "已交给 Online 设备理解视频并写标签，完成后重新打开列表刷新即可。",
          showCancel: false
        });
      })
      .catch((err) => {
        wx.hideLoading();
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  confirmRemove(row) {
    wx.showModal({
      title: "删除素材",
      content: `确认删除「${row.name}」？`,
      success: (res) => {
        if (!res.confirm) return;
        app.request({ method: "DELETE", url: `/api/assets/${encodeURIComponent(row.asset_id)}`, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadUploads();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  },

  remove(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    wx.showModal({
      title: "删除素材",
      content: `确认删除「${row.name}」？`,
      success: (res) => {
        if (!res.confirm) return;
        app.request({ method: "DELETE", url: `/api/assets/${encodeURIComponent(row.asset_id)}`, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadUploads();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  }
});