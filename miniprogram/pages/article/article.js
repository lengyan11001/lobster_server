const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");
const articleGen = require("../../utils/article-gen");
const articleText = require("../../utils/article-text");

Page({
  data: {
    step: 1,
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

  nextStep() {
    if (!String(this.data.topic || "").trim() && !String(this.data.sourceUrl || "").trim()) {
      return wx.showToast({ title: "先填文章主题或粘贴公众号链接", icon: "none" });
    }
    this.setData({ step: 2 });
    wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },

  prevStep() {
    this.setData({ step: 1 });
    wx.pageScrollTo({ scrollTop: 0, duration: 200 });
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

  syncContentRecord(record) {
    return app.request({
      method: "POST",
      url: "/api/content-records/sync",
      data: { records: [record] },
      timeout: 30000
    });
  },

  deleteContentRecord(source, sourceId) {
    return app.request({
      method: "DELETE",
      url: "/api/content-records?source=" + encodeURIComponent(source) + "&source_id=" + encodeURIComponent(sourceId),
      timeout: 20000
    }).catch(() => null);
  },

  async generate() {
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
    const histId = hist.save("article", {
      title: (topic || "公众号文章").slice(0, 24),
      summary: "",
      content: "",
      images: [],
      status: "processing",
      status_text: "生成中",
      payload,
      started_at: Date.now()
    });
    const sourceId = "article-" + histId;
    const inflight = {
      source: "online_wechat_article",
      source_id: sourceId,
      kind: "article",
      title: (topic || "公众号文章").slice(0, 60),
      status: "running",
      source_created_at: new Date().toISOString()
    };
    this.syncContentRecord(inflight).catch(() => {});
    this.setData({ loading: true, progressText: "正在生成公众号文章…" });
    wx.showLoading({ title: "生成文章中", mask: true });
    try {
      await articleGen.runTask(histId, payload);
      const row = hist.get(histId) || {};
      if (row.status === "failed") {
        await this.deleteContentRecord(inflight.source, sourceId);
        this.setData({ loading: false, progressText: "" });
        wx.hideLoading();
        wx.showToast({ title: String(row.error || "生成失败").slice(0, 30), icon: "none" });
        return;
      }
      const parsed = articleText.parseArticleContent(row.content || "", (row.images || []).filter(Boolean));
      try {
        await this.syncContentRecord({
          source: inflight.source,
          source_id: sourceId,
          kind: "article",
          title: String(row.title || topic || "公众号文章").slice(0, 200),
          summary: String(row.summary || "").slice(0, 400),
          content: parsed.text,
          cover_url: parsed.images[0] || "",
          status: "completed",
          source_created_at: new Date().toISOString(),
          meta: { images: parsed.images }
        });
      } catch (syncErr) {
        wx.showToast({ title: "文章已生成，但作品同步失败", icon: "none" });
      }
      this.setData({ loading: false, progressText: "" });
      wx.hideLoading();
      wx.showToast({ title: "已生成，去作品页查看", icon: "success" });
      setTimeout(() => wx.switchTab({ url: "/pages/downloads/downloads" }), 900);
    } catch (err) {
      await this.deleteContentRecord(inflight.source, sourceId);
      this.setData({ loading: false, progressText: "" });
      wx.hideLoading();
      wx.showToast({ title: (api.errorMessage(err) || "生成失败").slice(0, 30), icon: "none" });
    }
  }
});
