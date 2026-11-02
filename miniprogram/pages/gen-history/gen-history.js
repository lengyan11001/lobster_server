const app = getApp();
const hist = require("../../utils/gen-history");

const CONFIG = {
  article: { title: "公众号文章", create: "/pages/article/article" },
  montage: { title: "素材混剪", create: "/pages/montage/montage" }
};

function fmt(v) { const d = new Date(Number(v || 0)); const p = (n) => (n < 10 ? "0" + n : "" + n); return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; }

Page({
  data: { feature: "article", title: "", createUrl: "", items: [], openId: "" },

  onLoad(query) {
    const feature = String((query && query.feature) || "article");
    const conf = CONFIG[feature] || CONFIG.article;
    this.setData({ feature, title: conf.title, createUrl: conf.create });
    wx.setNavigationBarTitle({ title: conf.title });
  },

  onShow() {
    app.restoreSession();
    this.refresh();
  },

  refresh() {
    const items = hist.list(this.data.feature).map((x) => Object.assign({}, x, { timeText: fmt(x.at) }));
    this.setData({ items });
  },

  goCreate() {
    wx.navigateTo({ url: this.data.createUrl });
  },

  toggle(e) {
    const id = String(e.currentTarget.dataset.id || "");
    this.setData({ openId: this.data.openId === id ? "" : id });
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url;
    const item = this.data.items.find((x) => x.id === e.currentTarget.dataset.id);
    if (!item) return;
    wx.previewImage({ current: url, urls: item.images || [url] });
  },

  copyText(e) {
    const item = this.data.items.find((x) => x.id === e.currentTarget.dataset.id);
    if (!item) return;
    wx.setClipboardData({ data: String(item.content || ""), success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  },

  remove(e) {
    const id = String(e.currentTarget.dataset.id || "");
    wx.showModal({ title: "删除记录", content: "删除后不可恢复，确定？", success: (r) => { if (!r.confirm) return; hist.remove(id); this.refresh(); } });
  }
});
