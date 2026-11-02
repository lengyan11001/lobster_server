const app = getApp();
const hist = require("../../utils/gen-history");

const CONFIG = {
  article: { title: "公众号文章", create: "/pages/article/article" },
  montage: { title: "素材混剪", create: "/pages/montage/montage" },
  storyboard: { title: "分镜头视频", create: "/pages/video-inspiration/video-inspiration" }
};

function fmt(v) { const d = new Date(Number(v || 0)); const p = (n) => (n < 10 ? "0" + n : "" + n); return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; }

Page({
  data: { feature: "article", title: "", createUrl: "", items: [], openId: "", modalOpen: false, topic: "", points: "", tone: "专业观点", audience: "", generating: false },

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
    if (this.data.feature === "article") {
      this.setData({ modalOpen: true, topic: "", points: "", audience: "", tone: "专业观点" });
      return;
    }
    wx.navigateTo({ url: this.data.createUrl });
  },

  closeModal() { this.setData({ modalOpen: false }); },
  noop() {},
  onTopic(e) { this.setData({ topic: e.detail.value }); },
  onPoints(e) { this.setData({ points: e.detail.value }); },
  onAudience(e) { this.setData({ audience: e.detail.value }); },

  submitArticle() {
    const topic = String(this.data.topic || "").trim();
    if (!topic) return wx.showToast({ title: "先填文章主题", icon: "none" });
    const points = String(this.data.points || "").trim();
    const audience = String(this.data.audience || "").trim();
    const system = [
      "你是资深公众号主笔，擅长写可直接发布的微信文章。",
      "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
      "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释。"
    ].join("");
    const user = [`主题：${topic}`, points ? `要点与素材：${points}` : "", audience ? `目标读者：${audience}` : "", "请直接输出 JSON。"].filter(Boolean).join("\n");
    this.setData({ generating: true });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/chat/completions",
      data: { model: "gpt-4o-mini", temperature: 0.7, messages: [{ role: "system", content: system }, { role: "user", content: user }] },
      timeout: 300000
    })
      .then((data) => {
        const raw = String(((data.choices || [])[0] || {}).message?.content || "").trim();
        if (!raw) throw new Error("模型没有返回内容");
        const jsonText = raw.replace(/^```json\s*/i, "").replace(/^```/, "").replace(/```$/, "").trim();
        let article = {};
        try { article = JSON.parse(jsonText); } catch (e) { article = { title: topic, summary: "", body: raw }; }
        hist.save("article", { title: article.title || topic, summary: article.summary || "", content: article.body || raw, status: "completed" });
        this.setData({ modalOpen: false });
        wx.showToast({ title: "已生成并加入记录", icon: "success" });
        this.refresh();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ generating: false }));
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

  refreshStatus(e) {
    const item = this.data.items.find((x) => x.id === e.currentTarget.dataset.id);
    if (!item || !item.taskId) return;
    app.request({ method: "POST", url: "/api/shanjian-smart-clip/task", data: { task_id: item.taskId }, timeout: 60000 })
      .then((data) => {
        const status = String((data && data.status) || "processing");
        const videoUrl = String((data && data.video_url) || "");
        hist.update(item.id, { status: status === "succeed" ? "completed" : (status === "failed" ? "failed" : "processing"), videoUrl });
        this.refresh();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  remove(e) {
    const id = String(e.currentTarget.dataset.id || "");
    wx.showModal({ title: "删除记录", content: "删除后不可恢复，确定？", success: (r) => { if (!r.confirm) return; hist.remove(id); this.refresh(); } });
  }
});
