const app = getApp();
const api = require("../../utils/api");

const POLL_MS = 3000;
const MAX_POLL_FAIL = 8;

Page({
  data: {
    running: false,
    progress: 0,
    stage: "",
    calls: [],
    keywords: [],
    grids: [],
    topics: [],
    viewTopics: [],
    activeKeyword: "",
    openId: 0,
    errorText: "",
    metricsLoading: false,
    metricsNote: "",
    generatedAt: "",
    hasPlan: false
  },

  onLoad() {
    app.restoreSession();
    this.pollTimer = null;
    this.pollFail = 0;
    this.loadLatest();
  },

  onUnload() {
    this.stopPoll();
  },

  loadLatest() {
    app.request({ url: "/api/ip-profile/topics/latest", timeout: 30000 })
      .then((data) => {
        const plan = (data && data.plan) || {};
        if (plan && Array.isArray(plan.topics) && plan.topics.length) this.applyPlan(plan);
      })
      .catch(() => {});
  },

  applyPlan(plan) {
    const keywords = this.normalizeKeywords(plan.keywords);
    const grids = Array.isArray(plan.grids) ? plan.grids : [];
    const topics = (Array.isArray(plan.topics) ? plan.topics : []).map((row, i) => ({
      id: row.id || i + 1,
      title: String(row.title || ""),
      keyword: String(row.keyword || ""),
      inner: String(row.inner || ""),
      outer: String(row.outer || ""),
      hook: String(row.hook || ""),
      script: String(row.script || ""),
      format: String(row.format || "口播"),
      score: Number(row.score || 0)
    }));
    const meta = (plan && plan.meta) || {};
    this.setData({
      keywords,
      grids,
      topics,
      hasPlan: topics.length > 0,
      generatedAt: String(meta.generated_at || "").replace("T", " ").slice(0, 16),
      progress: 100,
      stage: topics.length ? "已生成 " + topics.length + " 条选题" : "",
      errorText: ""
    });
    this.applyFilter();
  },

  normalizeKeywords(rows) {
    return (Array.isArray(rows) ? rows : []).map((row) => {
      const heat = Number(row.heat || 0);
      const competition = Number(row.competition || 0);
      const pros = Array.isArray(row.pros) ? row.pros : [];
      const cons = Array.isArray(row.cons) ? row.cons : [];
      const fmtNum = (n) => {
        const v = Number(n || 0);
        if (v >= 100000000) return (v / 100000000).toFixed(1).replace(/\.0$/, "") + "亿";
        if (v >= 10000) return (v / 10000).toFixed(1).replace(/\.0$/, "") + "万";
        return String(v);
      };
      const liveText = row.source === "douyin_search"
        ? ("抖音实测 · 抓真实搜索结果 " + (row.samples || 0) + " 条")
        : "";
      const verdictClass = row.verdict === "值得做" ? "good" : (row.verdict === "竞争过重" ? "bad" : (row.verdict === "能打，靠差异化" ? "mid" : "none"));
      const top = Array.isArray(row.topTitles) && row.topTitles.length ? row.topTitles[0] : null;
      return Object.assign({}, row, {
        heat,
        competition,
        room: Math.max(0, 100 - competition),
        prosText: pros.join(" · "),
        consText: cons.join(" · "),
        liveText,
        midLikesText: fmtNum(row.midLikes),
        topLikesText: fmtNum(row.topLikes),
        marginText: row.verdict ? ("（需求 " + Number(row.heat || 0) + " - 竞争 " + Number(row.competition || 0) + "）") : "",
        verdictClass,
        sampleTitle: top ? String(top.desc || "").slice(0, 28) : "",
        related: Array.isArray(row.related) ? row.related : []
      });
    }).sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  },

  applyFilter() {
    const key = this.data.activeKeyword;
    const rows = this.data.topics || [];
    const view = key ? rows.filter((row) => row.keyword === key) : rows.slice(0, 50);
    this.setData({ viewTopics: view });
  },

  pickKeyword(e) {
    const word = String(e.currentTarget.dataset.word || "");
    const next = this.data.activeKeyword === word ? "" : word;
    this.setData({ activeKeyword: next, openId: 0 });
    this.applyFilter();
    wx.pageScrollTo({ selector: "#topic-list", duration: 200, offsetTop: -10 });
  },

  start() {
    if (this.data.running) return;
    this.setData({ running: true, errorText: "", progress: 0, stage: "提交任务…", calls: [], topics: [], viewTopics: [], openId: 0, metricsNote: "" });
    app.request({
      method: "POST",
      url: "/api/ip-profile/topics/jobs",
      data: { count: 50 },
      timeout: 40000
    })
      .then((data) => {
        if (!data || !data.job_id) throw new Error("没有拿到任务号");
        this.jobId = data.job_id;
        this.pollFail = 0;
        this.schedulePoll(600);
      })
      .catch((err) => {
        this.setData({ running: false, stage: "", errorText: api.errorMessage(err) || "启动失败" });
        wx.showToast({ title: api.errorMessage(err) || "启动失败", icon: "none" });
      });
  },

  schedulePoll(delay) {
    this.stopPoll();
    this.pollTimer = setTimeout(() => this.poll(), delay || POLL_MS);
  },

  stopPoll() {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  },

  poll() {
    if (!this.jobId) return;
    app.request({ url: "/api/ip-profile/topics/jobs/" + encodeURIComponent(this.jobId), timeout: 30000 })
      .then((data) => {
        this.pollFail = 0;
        const partial = (data && data.partial) || {};
        const patch = {
          progress: Number((data && data.progress) || 0),
          stage: String((data && data.stage) || ""),
          calls: Array.isArray(data && data.calls) ? data.calls : this.data.calls
        };
        if (Array.isArray(partial.keywords) && partial.keywords.length) patch.keywords = this.normalizeKeywords(partial.keywords);
        if (Array.isArray(partial.grids) && partial.grids.length) patch.grids = partial.grids;
        if (Array.isArray(partial.topics) && partial.topics.length) {
          patch.topics = partial.topics.map((row, i) => Object.assign({ id: row.id || i + 1, hook: String(row.hook || ""), script: String(row.script || ""), format: String(row.format || "口播"), score: Number(row.score || 0) }, row, { id: row.id || i + 1 }));
        }
        this.setData(patch);
        if (patch.topics) this.applyFilter();

        const status = String((data && data.status) || "");
        if (status === "done") {
          this.stopPoll();
          this.setData({ running: false, progress: 100, stage: "已生成 " + ((data.result && data.result.topics) || []).length + " 条选题" });
          if (data && data.result) this.applyPlan(data.result);
          wx.showToast({ title: "选题已生成", icon: "success" });
          return;
        }
        if (status === "failed") {
          this.stopPoll();
          this.setData({ running: false, errorText: String((data && data.error) || "生成失败") });
          wx.showToast({ title: String((data && data.error) || "生成失败").slice(0, 24), icon: "none" });
          return;
        }
        this.schedulePoll(POLL_MS);
      })
      .catch((err) => {
        this.pollFail += 1;
        if (this.pollFail >= MAX_POLL_FAIL) {
          this.stopPoll();
          this.setData({ running: false, errorText: api.errorMessage(err) || "任务查询失败" });
          return;
        }
        this.schedulePoll(POLL_MS);
      });
  },

  checkDouyin() {
    if (this.data.metricsLoading) return;
    const words = (this.data.keywords || []).map((row) => row.word).filter(Boolean).slice(0, 6);
    if (!words.length) return wx.showToast({ title: "先生成关键词", icon: "none" });
    this.setData({ metricsLoading: true, metricsNote: "" });
    app.request({
      method: "POST",
      url: "/api/ip-profile/topics/keyword-metrics",
      data: { keywords: words },
      timeout: 180000
    })
      .then((data) => {
        const metrics = (data && data.metrics) || [];
        if (!(data && data.enabled)) {
          this.setData({ metricsLoading: false, metricsNote: String((data && data.note) || "抖音数据未启用") });
          return;
        }
        const byWord = {};
        metrics.forEach((row) => { byWord[row.word] = row; });
        const merged = (this.data.keywords || []).map((row) => {
          const hit = byWord[row.word];
          if (!hit) return row;
          return Object.assign({}, row, {
            heat: hit.heat || row.heat,
            competition: hit.competition || row.competition,
            source: "douyin_search",
            samples: hit.samples || 0,
            midLikes: hit.mid_likes || 0,
            topLikes: hit.top_likes || 0,
            bigRatio: hit.big_ratio || 0,
            freshRatio: hit.fresh_ratio || 0,
            interRatio: hit.inter_ratio || 0,
            margin: hit.margin || 0,
            verdict: hit.verdict || "",
            related: Array.isArray(hit.related) ? hit.related : [],
            topTitles: Array.isArray(hit.top_titles) ? hit.top_titles : []
          });
        });
        const keywords = this.normalizeKeywords(merged);
        this.setData({ keywords, metricsLoading: false, metricsNote: String((data && data.note) || "") });
        wx.showToast({ title: "已用抖音数据校正", icon: "success" });
      })
      .catch((err) => {
        this.setData({ metricsLoading: false, metricsNote: api.errorMessage(err) || "抖音数据校验失败" });
      });
  },

  toggleTopic(e) {
    const id = Number(e.currentTarget.dataset.id || 0);
    this.setData({ openId: this.data.openId === id ? 0 : id });
  },

  copyTopic(e) {
    const id = Number(e.currentTarget.dataset.id || 0);
    const row = (this.data.topics || []).find((item) => item.id === id);
    if (!row) return;
    const lines = [row.title];
    if (row.hook) lines.push("钩子：" + row.hook);
    if (row.script) lines.push("", row.script);
    wx.setClipboardData({ data: lines.join("\n"), success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  },

  toDigital(e) {
    const id = Number(e.currentTarget.dataset.id || 0);
    const row = (this.data.topics || []).find((item) => item.id === id);
    if (!row) return;
    wx.setStorageSync("lobster_digital_prefill", {
      title: row.title || "",
      text: row.script || row.hook || row.title || ""
    });
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  copyAll() {
    const rows = this.data.viewTopics || [];
    if (!rows.length) return;
    const text = rows.map((row, i) => {
      const parts = [(i + 1) + ". " + row.title];
      if (row.hook) parts.push("钩子：" + row.hook);
      if (row.script) parts.push(row.script);
      return parts.join("\n");
    }).join("\n\n");
    wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制 " + rows.length + " 条", icon: "success" }) });
  }
});
