const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");
const media = require("../../utils/media");
const share = require("../../utils/share");

const TABS = [
  { key: "all", label: "全部" },
  { key: "video", label: "视频" },
  { key: "image", label: "图片" },
  { key: "article", label: "文章" }
];

const ALL_KEYS = ["video", "image", "article", "wechat_article"];

const KIND_KEYS = {
  video: ["video"],
  image: ["image"],
  article: ["article", "wechat_article"]
};

const KIND_LABEL = {
  video: "视频",
  image: "图片",
  article: "文章",
  wechat_article: "文章",
  ppt: "PPT",
  audio: "音频"
};

const CATEGORY_LABEL = {
  shanjian_digital_human: "数字人",
  montage: "混剪",
  h5_workflow: "热门跟创",
  canvas: "画布",
  online_wechat_article: "公众号",
  ip_daily: "朋友圈",
  ip_moments: "朋友圈",
  douyin_imitation: "热门跟创",
  comfly_image: "图片"
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
    if (typeof this.getTabBar === "function" && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 });
    }
    app.restoreSession();
    this.setData({ phoneBound: Boolean(app.globalData.token && app.globalData.phone) });
    this.refresh();
    this.startInflightTimer();
  },

  onHide() { this.stopInflightTimer(); },

  onUnload() { this.stopInflightTimer(); },

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
    if (offset === 0) {
      const kindParam = this.data.tab === "all" ? "" : "&kind=" + encodeURIComponent(this.data.tab);
      requests.push(
        app.request({
          url: "/api/content-records/inflight?limit=40" + kindParam,
          timeout: 15000
        }).catch(() => ({ items: [], __inflight_error: true }))
      );
    }
    return Promise.all(requests)
      .then((lists) => {
        let hasNext = false;
        let failed = 0;
        const contentLists = offset === 0 ? lists.slice(0, -1) : lists;
        const inflightList = offset === 0 ? lists[lists.length - 1] : null;
        const byKey = {};
        (replace ? [] : this.data.items).forEach((row) => {
          if (!row || !row.key) return;
          byKey[row.key] = row;
        });
        contentLists.forEach((data) => {
          if (data && data.__error) failed += 1;
          const rows = (data && data.items) || [];
          if (data && data.pagination && data.pagination.has_next) hasNext = true;
          rows.forEach((row) => {
            const item = this.mapRow(row);
            if (!item.key) return;
            byKey[item.key] = item;
          });
        });
        if (inflightList) {
          ((inflightList && inflightList.items) || []).forEach((row) => {
            const item = this.mapInflight(row);
            if (!item.key) return;
            byKey[item.key] = item;
          });
        }
        const merged = Object.keys(byKey).map((key) => byKey[key]);
        merged.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
        if (failed && failed === contentLists.length) {
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

  refreshInflight() {
    if (!app.globalData.token) return Promise.resolve();
    const kindParam = this.data.tab === "all" ? "" : "&kind=" + encodeURIComponent(this.data.tab);
    return app.request({
      url: "/api/content-records/inflight?limit=40" + kindParam,
      timeout: 15000
    })
      .then((data) => {
        const hadPending = this.data.items.some((row) => row && row.pending);
        const byKey = {};
        this.data.items.filter((row) => row && !row.pending).forEach((row) => {
          byKey[row.key] = row;
        });
        ((data && data.items) || []).forEach((row) => {
          const item = this.mapInflight(row);
          if (item.key) byKey[item.key] = item;
        });
        const merged = Object.keys(byKey).map((key) => byKey[key]);
        merged.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
        this.setData({ items: merged });
        // 任务刚完成时 inflight 会消失，立即整页刷新把新生成的内容记录拉回来，
        // 避免作品卡片先消失、等用户手动刷新才出现。
        if (hadPending && !merged.some((row) => row && row.pending)) {
          return this.refresh();
        }
      })
      .catch(() => {});
  },

  startInflightTimer() {
    this.stopInflightTimer();
    this.inflightTimer = setInterval(() => {
      if (!this.data.items.some((row) => row && row.pending)) return;
      this.refreshInflight();
    }, 10000);
  },

  stopInflightTimer() {
    if (this.inflightTimer) {
      clearInterval(this.inflightTimer);
      this.inflightTimer = null;
    }
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
      categoryLabel: CATEGORY_LABEL[String(source.source || "")] || "",
      title: String(source.title || "未命名内容"),
      summary: String(source.summary || source.content || "").replace(/\s+/g, " ").slice(0, 90),
      time: fmtTime(source.created_at),
      createdAt: String(source.created_at || ""),
      thumb: thumbSource ? mediaUrl(thumbSource, "thumb") : "",
      isVideo: kind === "video",
      pending: false
    };
  },

  mapInflight(row) {
    const source = row || {};
    const kind = String(source.kind || "article");
    const createdAt = String(source.created_at || "");
    return {
      key: String(source.source || "x") + ":" + String(source.source_id || ""),
      source: String(source.source || ""),
      sourceId: String(source.source_id || ""),
      kind,
      kindLabel: KIND_LABEL[kind] || "内容",
      categoryLabel: CATEGORY_LABEL[String(source.source || "")] || "",
      title: String(source.title || "生成中的作品"),
      summary: String(source.summary || "正在生成，完成后自动出现在作品里"),
      time: fmtTime(createdAt),
      createdAt,
      thumb: "",
      isVideo: kind === "video",
      pending: true,
      status: String(source.status || "running")
    };
  },

  openDetail(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row || row.pending) return;
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
