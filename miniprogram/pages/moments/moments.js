const app = getApp();
const api = require("../../utils/api");

function fmtTime(v) { return String(v || "").replace("T", " ").slice(0, 16); }

Page({
  data: { groups: [], loading: false, modalOpen: false, topic: "", copyCount: 10, copyRequirement: "", generating: false },

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
    this.setData({ modalOpen: true, topic: "", copyRequirement: "", copyCount: 10 });
  },

  closeModal() { this.setData({ modalOpen: false }); },
  noop() {},

  onTopic(e) { this.setData({ topic: e.detail.value }); },
  onReq(e) { this.setData({ copyRequirement: e.detail.value }); },
  setCopyCount(e) { this.setData({ copyCount: Number(e.currentTarget.dataset.value || 10) }); },

  submitCreate() {
    const topic = String(this.data.topic || "").trim();
    if (!topic) return wx.showToast({ title: "先填主题或卖点", icon: "none" });
    if (!app.globalData.token) return wx.showToast({ title: "请先登录", icon: "none" });
    this.setData({ generating: true });
    app.request({
      method: "POST",
      url: "/api/ip-content/generate/moments-candidates",
      data: {
        extra_requirements: [topic, String(this.data.copyRequirement || "").trim()].filter(Boolean).join("\n"),
        count: Math.max(1, Math.min(20, Number(this.data.copyCount || 10)))
      },
      timeout: 300000
    })
      .then(() => {
        this.setData({ modalOpen: false });
        wx.showToast({ title: "已提交，记录已生成", icon: "success" });
        this.load();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ generating: false }));
  },

  openGroup(e) {
    const gid = e.currentTarget.dataset.gid;
    if (!gid) return;
    wx.navigateTo({ url: `/pages/moments-detail/moments-detail?gid=${encodeURIComponent(gid)}` });
  }
});
