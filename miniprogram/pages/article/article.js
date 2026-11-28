const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");

Page({
  data: {
    topic: "", points: "", audience: "", tone: "专业观点",
    tones: ["专业观点", "简洁商业", "故事叙事", "实用教程"],
    loading: false, progressText: "",
    sourceUrl: "", sourceTitle: "", sourceContent: "", rewriteMode: false, fetchingSource: false
  },

  onShow() {
    let prefill = null;
    try { prefill = wx.getStorageSync("lobster_article_prefill"); } catch (e) {}
    if (prefill && (prefill.title || prefill.text)) {
      this.setData({
        topic: String(prefill.title || prefill.text || "").slice(0, 200),
        points: String(prefill.text || "").slice(0, 800)
      });
      try { wx.removeStorageSync("lobster_article_prefill"); } catch (e) {}
      wx.showToast({ title: "已填入选题", icon: "none" });
    }
  },

  openMemory() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("memory"); },
  openRecording() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("recording"); },
  openPrefillHistory() { const d = this.selectComponent("#topicPrefill"); if (d) d.openHistory(); },

  openPeerLink() {
    const c = this.selectComponent("#peerLink");
    if (c) c.open();
  },

  onPeerApply(e) {
    const d = (e && e.detail) || {};
    const script = String(d.script || "");
    const title = String(d.title || "");
    this.setData({
      topic: (title || script).slice(0, 200),
      points: script.slice(0, 800)
    });
    wx.showToast({ title: "已填入文案", icon: "success" });
  },

  onPrefillPick(evt) {
    const item = (evt && evt.detail && evt.detail.item) || {};
    const text = String(item.script || item.content || item.body || "");
    this.setData({
      topic: String(item.title || text || "").slice(0, 200),
      points: text.slice(0, 800)
    });
    wx.showToast({ title: "已填入选题", icon: "none" });
  },

  onSourceUrl(e) { this.setData({ sourceUrl: e.detail.value }); },

  fetchSource() {
    const url = String(this.data.sourceUrl || "").trim();
    if (!/^https?:\/\//i.test(url)) return wx.showToast({ title: "请粘贴公众号文章链接", icon: "none" });
    if (this.data.fetchingSource) return;
    this.setData({ fetchingSource: true, progressText: "正在抓取原文…" });
    app.request({ method: "POST", url: "/api/wechat-article/fetch", data: { url }, timeout: 60000 })
      .then((data) => {
        const content = String((data && data.content) || "").trim();
        const title = String((data && data.title) || "").trim();
        if (!content) throw new Error("没有抓到正文");
        this.setData({
          sourceTitle: title,
          sourceContent: content,
          rewriteMode: true,
          topic: this.data.topic || title,
          points: this.data.points || content.slice(0, 800),
          progressText: ""
        });
        wx.showToast({ title: "原文已抓取", icon: "success" });
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ fetchingSource: false }));
  },

  onTopicInput(e) { this.setData({ topic: e.detail.value }); },
  onPointsInput(e) { this.setData({ points: e.detail.value }); },
  setTone(e) { this.setData({ tone: e.currentTarget.dataset.value || "专业观点" }); },
  onAudienceInput(e) { this.setData({ audience: e.detail.value }); },

  ensureAuth() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return false;
    }
    return true;
  },

  generate() {
    const topic = String(this.data.topic || "").trim();
    const source = String(this.data.sourceContent || "").trim();
    if (!topic && !source) {
      wx.showToast({ title: "先填文章主题，或粘贴公众号链接复刻", icon: "none" });
      return;
    }
    if (!this.ensureAuth()) return;
    const payload = {
      topic,
      points: String(this.data.points || "").trim(),
      audience: String(this.data.audience || "").trim(),
      tone: this.data.tone || "专业观点",
      sourceContent: source
    };
    hist.save("article", {
      title: (topic || "公众号文章").slice(0, 24),
      summary: "",
      content: "",
      images: [],
      status: "processing",
      status_text: "生成中",
      payload,
      started_at: Date.now()
    });
    wx.showToast({ title: "已创建，正在生成", icon: "none" });
    setTimeout(() => wx.navigateBack({ delta: 1, fail: () => {} }), 300);
  }
});
