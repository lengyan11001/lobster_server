const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");
const { createPoller } = require("../../utils/task-poll");
const { formatDateTime } = require("../../utils/datetime");

function fmtTime(v) {
  const d = new Date(Number(v || 0));
  if (!Number.isFinite(d.getTime())) return "";
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return (d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

Page({
  data: {
    viewMode: "list",
    target: "",
    jobList: [],
    currentJobTitle: "",
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

  onLoad(query) {
    app.restoreSession();
    this.target = String((query && query.target) || "");
    this.setData({ target: this.target });
    this.topicPoller = null;
    this.topicActive = null;
    this.latestPlan = null;
    this.loadJobs();
    this.loadLatest();
  },

  onShow() {
    if (this.topicPoller) this.topicPoller.resume();
    if (this.data.viewMode === "list") this.loadJobs();
    this.resumeTopicJob();
  },

  onHide() {
    if (this.topicPoller) this.topicPoller.pause();
  },

  onUnload() {
    if (this.topicPoller) this.topicPoller.stop();
  },

  loadJobs() {
    const rows = (hist.list("topics") || []).map((row) => Object.assign({}, row, {
      timeText: fmtTime(row.started_at || row.at),
      statusText: row.status_text || (row.status === "processing" ? "生成中" : (row.status === "failed" ? "失败" : "已完成"))
    }));
    if (!rows.length && this.latestPlan && Array.isArray(this.latestPlan.topics) && this.latestPlan.topics.length) {
      const meta = this.latestPlan.meta || {};
      rows.push({
        id: "latest",
        title: "最近一次推荐选题",
        status: "completed",
        statusText: "已完成",
        timeText: formatDateTime(meta.generated_at, true),
        count: this.latestPlan.topics.length,
        plan: this.latestPlan
      });
    }
    this.setData({ jobList: rows });
  },

  openCreate() {
    this.setData({ viewMode: "create", errorText: "", progress: 0, stage: "", calls: [], metricsNote: "" });
  },

  backToList() {
    this.setData({ viewMode: "list" });
    this.loadJobs();
  },

  openJob(evt) {
    const row = this.data.jobList[Number(evt.currentTarget.dataset.index || 0)];
    if (!row) return;
    if (row.status === "processing") {
      wx.showToast({ title: row.statusText || "生成中", icon: "none" });
      return;
    }
    const apply = (plan) => {
      if (!plan || !Array.isArray(plan.topics) || !plan.topics.length) {
        wx.showToast({ title: "这次结果已过期，请重新生成", icon: "none" });
        return;
      }
      this.latestPlan = plan;
      this.applyPlan(plan);
      this.setData({ viewMode: "result", currentJobTitle: row.title || "推荐选题" });
    };
    if (row.plan && row.plan.topics) {
      apply(row.plan);
      return;
    }
    app.request({ url: "/api/ip-profile/topics/latest", timeout: 30000 })
      .then((data) => apply((data && data.plan) || {}))
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  resumeTopicJob() {
    if (this.topicPoller || this.topicActive) return;
    const pending = (hist.list("topics") || []).find((row) => row.status === "processing" && row.job_id);
    if (!pending) return;
    if (Date.now() - Number(pending.started_at || pending.at || 0) > 2 * 60 * 60 * 1000) {
      hist.update(pending.id, { status: "failed", status_text: "已中断", error: "任务已过期" });
      this.loadJobs();
      return;
    }
    this.startTopicPolling(pending.id, pending.job_id);
  },

  loadLatest() {
    app.request({ url: "/api/ip-profile/topics/latest", timeout: 30000 })
      .then((data) => {
        const plan = (data && data.plan) || {};
        if (plan && Array.isArray(plan.topics) && plan.topics.length) {
          this.latestPlan = plan;
          this.loadJobs();
        }
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
      generatedAt: formatDateTime(meta.generated_at, true),
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
    if ((hist.list("topics") || []).some((row) => row.status === "processing")) {
      wx.showToast({ title: "已有选题任务正在生成", icon: "none" });
      return;
    }
    const histId = hist.save("topics", {
      title: "推荐选题 50 条",
      status: "processing",
      status_text: "提交中",
      started_at: Date.now(),
      count: 50
    });
    this.setData({ running: true, viewMode: "list", errorText: "", progress: 0, stage: "提交任务…", calls: [], topics: [], viewTopics: [], openId: 0, metricsNote: "" });
    this.loadJobs();
    app.request({
      method: "POST",
      url: "/api/ip-profile/topics/jobs",
      data: { count: 50 },
      timeout: 40000
    })
      .then((data) => {
        if (!data || !data.job_id) throw new Error("没有拿到任务号");
        hist.update(histId, { job_id: data.job_id, status_text: "排队中" });
        this.loadJobs();
        this.startTopicPolling(histId, data.job_id);
      })
      .catch((err) => {
        const message = api.errorMessage(err) || "启动失败";
        hist.update(histId, { status: "failed", status_text: "失败", error: message });
        this.setData({ running: false, stage: "", errorText: message });
        this.loadJobs();
        wx.showToast({ title: message.slice(0, 24), icon: "none" });
      });
  },

  startTopicPolling(histId, jobId) {
    if (this.topicPoller) this.topicPoller.stop();
    this.topicActive = { histId, jobId };
    this.topicPoller = createPoller(
      () => app.request({ url: "/api/ip-profile/topics/jobs/" + encodeURIComponent(jobId), timeout: 30000 }),
      (data) => {
        const status = String((data && data.status) || "");
        const patch = {
          progress: Number((data && data.progress) || 0),
          stage: String((data && data.stage) || ""),
          calls: Array.isArray(data && data.calls) ? data.calls : this.data.calls
        };
        if (status === "done") {
          const plan = (data && (data.result || data.partial)) || {};
          hist.update(histId, {
            status: "completed",
            status_text: "已完成",
            progress: 100,
            count: (plan.topics || []).length,
            generated_at: (plan.meta && plan.meta.generated_at) || "",
            plan
          });
          this.latestPlan = plan;
          this.setData({ running: false, progress: 100, stage: "已生成 " + ((plan.topics || []).length) + " 条选题" });
          this.topicActive = null;
          this.loadJobs();
          wx.showToast({ title: "选题已生成", icon: "success" });
          return true;
        }
        if (status === "failed") {
          const message = String((data && data.error) || "生成失败");
          hist.update(histId, { status: "failed", status_text: "失败", error: message });
          this.setData({ running: false, errorText: message });
          this.topicActive = null;
          this.loadJobs();
          wx.showToast({ title: message.slice(0, 24), icon: "none" });
          return true;
        }
        hist.update(histId, { status_text: patch.stage || "生成中", progress: patch.progress });
        this.setData(patch);
        this.loadJobs();
        return false;
      },
      () => {
        hist.update(histId, { status_text: "网络重试中" });
        this.loadJobs();
      }
    );
    this.topicPoller.start(true);
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
    const payload = { title: row.title || "", text: row.script || row.hook || row.title || "" };
    if (this.target === "article" || this.target === "moments") {
      wx.setStorageSync(this.target === "article" ? "lobster_article_prefill" : "lobster_moments_prefill", payload);
      wx.navigateBack({ delta: 1 });
      return;
    }
    wx.setStorageSync("lobster_digital_prefill", payload);
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
