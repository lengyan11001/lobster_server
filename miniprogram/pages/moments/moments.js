const app = getApp();
const api = require("../../utils/api");

function fmtTime(v) { return String(v || "").replace("T", " ").slice(0, 16); }

Page({
  data: { groups: [], loading: false },

  onShow() {
    app.restoreSession();
    this.load();
  },

  load() {
    if (!app.globalData.token) return;
    this.setData({ loading: true });
    app.request({ url: "/api/ip-content/draft-records?task=moments_candidate&limit=200", timeout: 30000 })
      .then((data) => {
        const rows = (data && data.items) || [];
        const map = {};
        rows.forEach((row) => {
          const gid = String(row.group_id || (row.meta && row.meta.group_id) || ("single-" + row.id));
          if (!map[gid]) {
            map[gid] = { gid, time: fmtTime(row.created_at), count: 0, title: "", imageCount: 0 };
          }
          const g = map[gid];
          g.count += 1;
          const text = String(row.content || row.body || "").trim();
          if (!g.title && text) g.title = text.split("\n")[0].slice(0, 42);
          const imgs = (row.images && row.images.length) || (row.image_url ? 1 : 0);
          g.imageCount += imgs;
        });
        const groups = Object.keys(map).map((k) => map[k]).sort((a, b) => String(b.time).localeCompare(String(a.time)));
        this.setData({ groups });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  goCreate() {
    wx.navigateTo({ url: "/pages/moments-create/moments-create" });
  },

  openGroup(e) {
    const gid = e.currentTarget.dataset.gid;
    if (!gid) return;
    wx.navigateTo({ url: `/pages/moments-detail/moments-detail?gid=${encodeURIComponent(gid)}` });
  }
});
