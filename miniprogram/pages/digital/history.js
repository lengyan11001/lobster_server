const app = getApp();
const api = require("../../utils/api");

function asset(u) {
  const url = String(u || "");
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  const base = String((require("../../config") || {}).API_BASE || "").replace(/\/+$/, "");
  return base + (url.charAt(0) === "/" ? url : "/" + url);
}

Page({
  data: { items: [], loading: false, openId: "" },

  onShow() { app.restoreSession(); this.load(); },

  load() {
    if (!app.globalData.token) return;
    this.setData({ loading: true });
    app.request({ url: "/api/shanjian-digital-human/videos", timeout: 30000 })
      .then((data) => {
        const rows = (data && (data.items || data.records || data.videos || data)) || [];
        const items = (Array.isArray(rows) ? rows : []).map((row) => ({
          id: String(row.id || row.task_id || row.video_url || ""),
          title: String(row.title || row.task_name || "数字人视频"),
          statusText: String(row.status_text || row.status || ""),
          time: String(row.created_at || row.updated_at || "").replace("T", " ").slice(0, 16),
          videoUrl: asset(row.video_url),
          coverUrl: asset(row.cover_url || row.cover)
        })).filter((x) => x.id);
        this.setData({ items });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  goCreate() { wx.navigateTo({ url: "/pages/digital/digital" }); },

  toggle(e) { const id = String(e.currentTarget.dataset.id || ""); this.setData({ openId: this.data.openId === id ? "" : id }); },

  copyUrl(e) {
    const item = this.data.items.find((x) => x.id === e.currentTarget.dataset.id);
    if (!item || !item.videoUrl) return;
    wx.setClipboardData({ data: item.videoUrl, success: () => wx.showToast({ title: "链接已复制", icon: "success" }) });
  }
});
