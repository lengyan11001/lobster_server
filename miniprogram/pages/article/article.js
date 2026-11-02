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
    return {
      title: (lines[0] || "").replace(/^#+\s*/, "").trim().slice(0, 60),
      summary: "",
      body: text
    };
  }
}

Page({
  data: {
    topic: "",
    points: "",
    audience: "",
    tone: "专业观点",
    tones: ["专业观点", "简洁商业", "故事叙事", "实用教程"],
    loading: false,
    title: "",
    summary: "",
    body: "",
    progressText: "",
    pickerOpen: false,
    pickerItems: [],
    pickerLoading: false,
    extracting: false
  },

  openAudioPicker() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return;
    }
    this.setData({ pickerOpen: true, pickerLoading: true, pickerItems: [] });
    app.request({ url: "/api/h5/recorder/files?page=1&page_size=30", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || [])
          .filter((row) => row.status === "completed")
          .map((row) => ({
            id: row.id,
            name: String(row.display_name || row.file_name || "录音"),
            time: String(row.created_at || "").replace("T", " ").slice(0, 16)
          }));
        this.setData({ pickerItems: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ pickerLoading: false }));
  },

  closePicker() {
    this.setData({ pickerOpen: false });
  },

  noop() {},

  pickRecording(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.pickerItems[index];
    if (!row || this.data.extracting) return;
    this.setData({ extracting: true, pickerOpen: false, progressText: "AI 正在读录音，提取主题和要点…" });
    app.request({
      method: "POST",
      url: `/api/h5/recorder/files/${row.id}/extract-copy-fields`,
      data: { target: "article" },
      timeout: 240000
    })
      .then((data) => {
        const topic = String((data && data.topic) || "").trim();
        if (!topic) throw new Error("没有提取出文章主题");
        const patch = { topic, points: String((data && data.points) || "").trim(), progressText: "" };
        const tone = String((data && data.tone) || "").trim();
        if (this.data.tones.indexOf(tone) >= 0) patch.tone = tone;
        this.setData(patch);
        wx.showToast({ title: "已填好主题和要点", icon: "none" });
      })
      .catch((err) => {
        this.setData({ progressText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      })
      .then(() => this.setData({ extracting: false }));
  },

  onTopicInput(e) {
    this.setData({ topic: e.detail.value });
  },

  onPointsInput(e) {
    this.setData({ points: e.detail.value });
  },

  setTone(e) {
    this.setData({ tone: e.currentTarget.dataset.value || "专业观点" });
  },

  onAudienceInput(e) {
    this.setData({ audience: e.detail.value });
  },

  ensureAuth() {
    if (!(app.globalData.token && app.globalData.phone)) {
      wx.showToast({ title: "请先在「我的」授权手机号", icon: "none" });
      return false;
    }
    return true;
  },

  generate() {
    const topic = String(this.data.topic || "").trim();
    if (!topic) {
      wx.showToast({ title: "先填文章主题", icon: "none" });
      return;
    }
    if (!this.ensureAuth()) return;
    const points = String(this.data.points || "").trim();
    const tone = this.data.tone || "专业观点";
    const audience = String(this.data.audience || "").trim();
    const system = [
      "你是资深公众号主笔，擅长写可直接发布的微信文章。",
      "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
      "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释，不要出现「以下是」。",
      "标题不超过 30 字，要有信息量和点击欲；正文 900-1400 字。"
    ].join("");
    const user = [
      `主题：${topic}`,
      points ? `要点与素材：${points}` : "",
      audience ? `目标读者：${audience}` : "",
      `写作风格：${tone}`,
      "请直接输出 JSON。"
    ].filter(Boolean).join("\n");

    this.setData({ loading: true, progressText: "正在撰写…", title: "", summary: "", body: "" });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/chat/completions",
      data: {
        model: CHAT_MODEL,
        temperature: 0.7,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user }
        ]
      },
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
    wx.setClipboardData({
      data: text,
      success: () => wx.showToast({ title: "已复制", icon: "success" })
    });
  },

  reset() {
    this.setData({ title: "", summary: "", body: "" });
  }
});