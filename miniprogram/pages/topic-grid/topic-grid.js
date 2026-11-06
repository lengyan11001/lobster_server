const app = getApp();
const api = require("../../utils/api");

Page({
  data: {
    keyword: "",
    loading: false,
    grid: new Array(25).fill(null).map((_, i) => ({ text: "", ring: i === 12 ? "center" : ([6,7,8,11,13,16,17,18].indexOf(i) >= 0 ? "inner" : "outer") })),
    centerIndex: 12,
    filled: 0,
    topics: [],
    statusText: "",
    chips: [],
    activeTopic: -1,
    hits: new Array(25).fill(false)
  },

  onLoad() {
    app.restoreSession();
    app.request({ url: "/api/ip-content/keywords", timeout: 20000 })
      .then((data) => {
        const rows = (data && (data.items || data.keywords)) || [];
        const chips = (Array.isArray(rows) ? rows : [])
          .map((row) => String((row && row.keyword) || row || "").trim())
          .filter(Boolean)
          .slice(0, 8);
        this.setData({ chips });
      })
      .catch(() => {});
  },

  pickChip(e) { this.setData({ keyword: e.currentTarget.dataset.word || "" }); },

  onKeyword(e) { this.setData({ keyword: e.detail.value }); },

  generate() {
    const keyword = String(this.data.keyword || "").trim();
    if (!keyword) return wx.showToast({ title: "先填一个关键词", icon: "none" });
    const emptyGrid = new Array(25).fill(null).map((_, i) => ({ text: "", ring: i === 12 ? "center" : ([6,7,8,11,13,16,17,18].indexOf(i) >= 0 ? "inner" : "outer") }));
    this.setData({ loading: true, topics: [], grid: emptyGrid, filled: 0, statusText: "AI 正在拆解 5x5 宫格…" });
    app.request({
      method: "POST",
      url: "/api/ip-content/grid-topics",
      data: { keyword, count: 10 },
      timeout: 240000
    })
      .then((data) => {
        const center = String((data && data.center) || keyword);
        const inner = (data && data.inner) || [];
        const outer = (data && data.outer) || [];
        this.fillGrid(center, inner, outer, (data && data.topics) || []);
      })
      .catch((err) => {
        this.setData({ loading: false, statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  fillGrid(center, inner, outer, topics) {
    const grid = new Array(25).fill(null).map((_, i) => ({ text: "", ring: i === 12 ? "center" : ([6,7,8,11,13,16,17,18].indexOf(i) >= 0 ? "inner" : "outer") }));
    grid[12].text = center;
    this.setData({ grid: grid.slice(), filled: 1, statusText: "中心词已填入，正在生成内圈…" });
    const innerIdx = [6,7,8,11,13,16,17,18];
    let step = 0;
    const innerTimer = setInterval(() => {
      if (step >= inner.length || step >= innerIdx.length) {
        clearInterval(innerTimer);
        this.fillOuter(grid, outer, topics);
        return;
      }
      grid[innerIdx[step]].text = inner[step];
      step += 1;
      this.setData({ grid: grid.slice(), filled: 1 + step, statusText: "内圈关键词 " + step + "/8" });
    }, 220);
  },

  fillOuter(grid, outer, topics) {
    const outerIdx = [0,1,2,3,4,5,9,10,14,15,19,20,21,22,23,24];
    let step = 0;
    this.setData({ statusText: "外圈人群/场景 0/16" });
    const timer = setInterval(() => {
      if (step >= outer.length || step >= outerIdx.length) {
        clearInterval(timer);
        this.setData({ loading: false, topics, statusText: topics.length ? "已生成 " + topics.length + " 条选题" : "没有生成出选题" });
        return;
      }
      grid[outerIdx[step]].text = outer[step];
      step += 1;
      this.setData({ grid: grid.slice(), filled: 9 + step, statusText: "外圈人群/场景 " + step + "/16" });
    }, 160);
  },

  selectTopic(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    if (idx === this.data.activeTopic) {
      this.setData({ activeTopic: -1, hits: new Array(25).fill(false) });
      return;
    }
    const topic = this.data.topics[idx];
    if (!topic) return;
    const hits = new Array(25).fill(false);
    this.data.grid.forEach((cell, i) => {
      const text = String((cell && cell.text) || "").trim();
      if (!text) return;
      if (text === String(topic.inner || "").trim() || text === String(topic.outer || "").trim()) hits[i] = true;
    });
    this.setData({ activeTopic: idx, hits });
  },

  copyTopic(e) {
    const text = e.currentTarget.dataset.text || "";
    if (text) wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  }
});
