const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");

const KINDS = [
  { key: "video", label: "视频" },
  { key: "image", label: "图片" },
  { key: "article", label: "文章" },
  { key: "ppt", label: "PPT" },
  { key: "audio", label: "音频" }
];

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

function fmtTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

Page({
  data: {
    kinds: KINDS,
    kindIndex: 0,
    items: [],
    loading: false,
    offset: 0,
    hasMore: false,
    detail: null
  },

  onShow() {
    this.refresh();
  },

  onPullDownRefresh() {
    this.refresh().then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh());
  },

  refresh() {
    this.setData({ offset: 0, items: [] });
    return this.fetchPage(0);
  },

  switchKind(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    if (index === this.data.kindIndex) return;
    this.setData({ kindIndex: index, items: [], offset: 0, hasMore: false });
    this.fetchPage(0);
  },

  fetchPage(offset) {
    const kind = KINDS[this.data.kindIndex].key;
    const kinds = kind === "article" ? ["article", "wechat_article"] : [kind];
    this.setData({ loading: true });
    return Promise.all(kinds.map((k) => app.request({
      url: `/api/content-records?kind=${k}&limit=20&offset=${offset}&compact=false`,
      timeout: 30000
    }).catch(() => ({ items: [], pagination: {} }))))
      .then((lists) => {
        const data = {
          items: lists.reduce((acc, cur) => acc.concat(cur.items || []), []),
          pagination: {
            has_next: lists.some((cur) => cur && cur.pagination && cur.pagination.has_next)
          }
        };
        const rows = ((data && data.items) || []).map((row, i) => {
          const media = String(row.file_url || row.source_url || "");
          const thumbSource = row.kind === "image" ? (row.cover_url || media) : row.cover_url;
          return {
            key: `${row.source || "x"}:${row.source_id || row.id || i}`,
            source: String(row.source || ""),
            sourceId: String(row.source_id || ""),
            kindLabel: KINDS.find((k) => k.key === row.kind)?.label || "内容",
            title: String(row.title || "未命名"),
            summary: String(row.summary || row.content || "").slice(0, 80),
            time: fmtTime(row.created_at),
            thumb: thumbSource ? mediaUrl(thumbSource, "thumb") : "",
            mediaUrl: media
          };
        });
        const pagination = (data && data.pagination) || {};
        const merged = offset === 0 ? rows : this.data.items.concat(rows);
        this.setData({
          items: merged,
          offset: offset + rows.length,
          hasMore: Boolean(pagination.has_next)
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    this.fetchPage(this.data.offset);
  },

  openDetail(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    wx.showLoading({ title: "加载中" });
    app.request({
      url: `/api/content-records/detail?source=${encodeURIComponent(row.source)}&source_id=${encodeURIComponent(row.sourceId)}`,
      timeout: 30000
    })
      .then((data) => {
        const item = (data && data.item) || {};
        const media = String(item.file_url || item.source_url || "");
        const images = (item.image_urls || []).map((u) => mediaUrl(u, "thumb"));
        this.setData({
          detail: {
            source: row.source,
            sourceId: row.sourceId,
            title: String(item.title || row.title),
            content: String(item.content || item.summary || ""),
            images,
            videoUrl: item.kind === "video" && media ? mediaUrl(media, "proxy") : ""
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
    if (url) wx.previewImage({ current: url, urls: this.data.detail.images });
  },

  copyContent() {
    const text = String((this.data.detail && this.data.detail.content) || "");
    if (!text) return;
    wx.setClipboardData({ data: text });
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
          url: `/api/content-records?source=${encodeURIComponent(detail.source)}&source_id=${encodeURIComponent(detail.sourceId)}`,
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
  }
});