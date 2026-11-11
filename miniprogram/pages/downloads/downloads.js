const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");
const media = require("../../utils/media");
const share = require("../../utils/share");

const TABS = [
  { key: "all", label: "全部" },
  { key: "video", label: "视频" },
  { key: "image", label: "图片" },
  { key: "article", label: "文章" },
  { key: "ppt", label: "PPT" },
  { key: "audio", label: "音频" }
];

const ALL_KEYS = ["video", "image", "article", "wechat_article", "ppt", "audio"];

const KIND_KEYS = {
  video: ["video"],
  image: ["image"],
  article: ["article", "wechat_article"],
  ppt: ["ppt"],
  audio: ["audio"]
};

const KIND_LABEL = {
  video: "视频",
  image: "图片",
  article: "文章",
  wechat_article: "文章",
  ppt: "PPT",
  audio: "音频"
};

const PAGE_SIZE = 20;

function base() {
  return String(config.API_BASE || "").replace(/\/+$/, "");
}

function mediaUrl(url, mode) {
  const u = String(url || "").trim();
  if (!u) return "";
  if (/^data:/i.test(u)) return u;
  const abs = /^https?:\/\//i.test(u) ? u : base() + (u.charAt(0) === "/" ? u : "/" + u);
  return base() + "/api/media/" + mode + "?url=" + encodeURIComponent(abs);
}

function fmtTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

Page({
  data: {
    tabs: TABS,
    tab: "all",
    skeletons: [1, 2, 3],
    items: [],
    offset: 0,
    loading: false,
    loadingMore: false,
    hasMore: true,
    phoneBound: false,
    detail: null
  },

  onShow() {
    app.restoreSession();
    this.setData({ phoneBound: Boolean(app.globalData.token && app.globalData.phone) });
    this.refresh();
  },

  onPullDownRefresh() {
    this.setData({ phoneBound: Boolean(app.globalData.token && app.globalData.phone) });
    this.refresh()
      .then(() => wx.stopPullDownRefresh())
      .catch(() => wx.stopPullDownRefresh());
  },

  switchTab(e) {
    const key = String(e.currentTarget.dataset.key || "all");
    if (key === this.data.tab) return;
    this.setData({ tab: key, items: [], offset: 0, hasMore: true });
    this.refresh();
  },

  keys() {
    if (this.data.tab === "all") return ALL_KEYS;
    return KIND_KEYS[this.data.tab] || [this.data.tab];
  },

  refresh() {
    if (!app.globalData.token) {
      this.setData({ items: [], loading: false, hasMore: false });
      return Promise.resolve();
    }
    this.setData({ items: [], offset: 0, hasMore: true });
    return this.fetchPage(0, true);
  },

  fetchPage(offset, replace) {
    const keys = this.keys();
    if (replace) this.setData({ loading: true });
    else this.setData({ loadingMore: true });
    const requests = keys.map((kind) =>
      app.request({
        url: "/api/content-records?kind=" + encodeURIComponent(kind) +
          "&limit=" + PAGE_SIZE + "&offset=" + offset + "&compact=false",
        timeout: 30000
      }).catch(() => ({ items: [], pagination: {}, __error: true }))
    );
    return Promise.all(requests)
      .then((lists) => {
        let hasNext = false;
        let failed = 0;
        const seen = {};
        const merged = [];
        (replace ? [] : this.data.items).forEach((row) => {
          if (!row || !row.key) return;
          seen[row.key] = true;
          merged.push(row);
        });
        lists.forEach((data) => {
          if (data && data.__error) failed += 1;
          const rows = (data && data.items) || [];
          if (data && data.pagination && data.pagination.has_next) hasNext = true;
          rows.forEach((row) => {
            const item = this.mapRow(row);
            if (!item.key || seen[item.key]) return;
            seen[item.key] = true;
            merged.push(item);
          });
        });
        merged.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
        if (failed && failed === lists.length) {
          wx.showToast({ title: "加载失败，请稍后重试", icon: "none" });
          hasNext = false;
        }
        this.setData({ items: merged, offset: offset + PAGE_SIZE, hasMore: hasNext });
      })
      .catch((err) => {
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ loading: false, loadingMore: false }));
  },

  loadMore() {
    if (this.data.loading || this.data.loadingMore || !this.data.hasMore) return;
    this.fetchPage(Number(this.data.offset || 0), false);
  },

  mapRow(row) {
    const source = row || {};
    const kind = String(source.kind || "article");
    const raw = String(source.file_url || source.source_url || "");
    const thumbSource = kind === "image" ? (source.cover_url || raw) : source.cover_url;
    return {
      key: String(source.source || "x") + ":" + String(source.source_id || source.id || ""),
      source: String(source.source || ""),
      sourceId: String(source.source_id || source.id || ""),
      kind,
      kindLabel: KIND_LABEL[kind] || "内容",
      title: String(source.title || "未命名内容"),
      summary: String(source.summary || source.content || "").replace(/\s+/g, " ").slice(0, 90),
      time: fmtTime(source.created_at),
      createdAt: String(source.created_at || ""),
      thumb: thumbSource ? mediaUrl(thumbSource, "thumb") : "",
      isVideo: kind === "video"
    };
  },

  openDetail(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    wx.showLoading({ title: "加载中", mask: true });
    app.request({
      url: "/api/content-records/detail?source=" + encodeURIComponent(row.source) +
        "&source_id=" + encodeURIComponent(row.sourceId),
      timeout: 30000
    })
      .then((data) => {
        const item = (data && data.item) || {};
        const kind = String(item.kind || row.kind);
        const raw = String(item.file_url || item.source_url || "");
        const images = [];
        (item.image_urls || []).forEach((u) => {
          if (u) images.push(mediaUrl(u, "thumb"));
        });
        if (kind === "image" && raw && images.length === 0) images.push(mediaUrl(raw, "thumb"));
        this.setData({
          detail: {
            source: row.source,
            sourceId: row.sourceId,
            kind,
            kindLabel: KIND_LABEL[kind] || row.kindLabel,
            title: String(item.title || row.title),
            time: fmtTime(item.created_at) || row.time,
            content: String(item.content || item.summary || ""),
            images,
            videoUrl: kind === "video" && raw ? mediaUrl(raw, "proxy") : "",
            saveable: Boolean(raw && (kind === "video" || kind === "image")),
            saveUrl: raw ? mediaUrl(raw, "proxy") : "",
            saveType: kind === "video" ? "video" : (kind === "image" ? "image" : "")
          }
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  closeDetail() {
    this.setData({ detail: null });
  },

  noop() {},

  previewImage(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return;
    wx.previewImage({ current: url, urls: this.data.detail.images });
  },

  copyContent() {
    const text = String((this.data.detail && this.data.detail.content) || "");
    if (!text) {
      wx.showToast({ title: "没有可复制的文字", icon: "none" });
      return;
    }
    wx.setClipboardData({
      data: text,
      success: () => wx.showToast({ title: "已复制", icon: "success" })
    });
  },

  saveDetail() {
    const detail = this.data.detail;
    if (!detail) return;
    if (!detail.saveable || !detail.saveUrl) {
      this.copyContent();
      return;
    }
    const ext = detail.saveType === "video" ? ".mp4" : ".jpg";
    media
      .saveToAlbum({
        title: String(detail.title || "lobster-content") + ext,
        media_type: detail.saveType,
        url: detail.saveUrl,
        download_url: detail.saveUrl
      })
      .then(() => wx.showToast({ title: "已保存到相册", icon: "success" }))
      .catch((err) => {
        media.copyLink(detail.saveUrl).finally(() => {
          wx.showToast({ title: ("保存失败: " + api.errorMessage(err)).slice(0, 30), icon: "none" });
        });
      });
  },

  deleteDetail() {
    const detail = this.data.detail;
    if (!detail) return;
    wx.showModal({
      title: "删除记录",
      content: "确认删除这条内容记录？",
      success: (res) => {
        if (!res.confirm) return;
        app.request({
          method: "DELETE",
          url: "/api/content-records?source=" + encodeURIComponent(detail.source) +
            "&source_id=" + encodeURIComponent(detail.sourceId),
          timeout: 30000
        })
          .then(() => {
            this.setData({ detail: null });
            wx.showToast({ title: "已删除", icon: "success" });
            this.refresh();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  },

  goLogin() {
    wx.switchTab({ url: "/pages/profile/profile" });
  },

  goCreate() {
    wx.switchTab({ url: "/pages/index/index" });
  },

  onShareAppMessage() {
    return share.appShare({
      title: "我的内容记录 - 必火AI员工",
      path: "/pages/downloads/downloads"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "必火AI员工 - 内容记录"
    });
  }
});
