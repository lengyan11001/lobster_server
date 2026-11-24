const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");

const CHAT_MODEL = "gpt-5.5";

function pickContent(data) {
  const choice = data && data.choices && data.choices[0];
  const msg = choice && choice.message;
  const content = msg && msg.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => (typeof part === "string" ? part : (part && part.text) || "")).join("");
  }
  return "";
}

function parseArticle(raw) {
  const text = String(raw || "").trim();
  const cleaned = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  try {
    const obj = JSON.parse(cleaned);
    return {
      title: String(obj.title || "").trim(),
      summary: String(obj.summary || obj.digest || "").trim(),
      body: String(obj.body || obj.content || "").trim()
    };
  } catch (err) {
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    return { title: (lines[0] || "").replace(/^#+\s*/, "").trim().slice(0, 60), summary: "", body: text };
  }
}

Page({
  data: {
    topic: "", points: "", audience: "", tone: "专业观点",
    tones: ["专业观点", "简洁商业", "故事叙事", "实用教程"],
    loading: false, title: "", summary: "", body: "", progressText: "",
    sourceUrl: "", sourceTitle: "", sourceContent: "", rewriteMode: false, fetchingSource: false,
    pickerOpen: false, pickerItems: [], pickerLoading: false, extracting: false
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

  goBackList() {
    wx.navigateBack({ fail: () => wx.navigateTo({ url: "/pages/article/history?feature=article" }) });
  },

  openTopicRecommend() { wx.navigateTo({ url: "/pages/topic-recommend?target=article" }); },
  openMemory() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("memory"); },
  openRecording() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("recording"); },
  openPrefillHistory() { const d = this.selectComponent("#topicPrefill"); if (d) d.openHistory(); },

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

  openAudioPicker() { this.openRecording(); },
  closePicker() { this.setData({ pickerOpen: false }); },
  noop() {},

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
    const points = String(this.data.points || "").trim();
    const tone = this.data.tone || "专业观点";
    const audience = String(this.data.audience || "").trim();
    const system = source
      ? [
          "你是资深公众号主笔，擅长把一篇原文改写成可发布的新文章。",
          "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
          "复刻要求：保留原文的核心信息点、结构和观点，用完全不同的表达重写，不要照抄原句，不要大段近似。",
          "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释。标题不超过 30 字。"
        ].join("")
      : [
          "你是资深公众号主笔，擅长写可直接发布的微信文章。",
          "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
          "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释，不要出现「以下是」。",
          "标题不超过 30 字，要有信息量和点击欲；正文 900-1400 字。"
        ].join("");
    const user = [
      topic ? "主题：" + topic : "",
      points ? "要点与素材：" + points : "",
      audience ? "目标读者：" + audience : "",
      "写作风格：" + tone,
      source ? "参考原文（请改写，不要照抄）：\n" + source.slice(0, 6000) : "",
      "请直接输出 JSON。"
    ].filter(Boolean).join("\n");

    this.setData({ loading: true, progressText: source ? "正在复刻改写…" : "正在撰写…", title: "", summary: "", body: "" });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/chat/completions",
      data: { model: CHAT_MODEL, temperature: 0.7, messages: [{ role: "system", content: system }, { role: "user", content: user }] },
      timeout: 300000
    })
      .then((data) => {
        const raw = pickContent(data);
        if (!raw) throw new Error("模型没有返回内容");
        const article = parseArticle(raw);
        this.setData({ title: article.title, summary: article.summary, body: article.body, progressText: "" });
        hist.save("article", { title: article.title, summary: article.summary, content: article.body, status: "completed" });
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ loading: false }));
  },

  copyAll() {
    const text = [this.data.title, "", this.data.body].join("\n").trim();
    if (!text) return;
    wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  },

  reset() { this.setData({ title: "", summary: "", body: "" }); }
});
