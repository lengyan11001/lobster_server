const app = getApp();
const api = require("../../utils/api");

Component({
  properties: {
    target: { type: String, value: "digital" }
  },

  data: {
    open: false,
    mode: "",
    phase: "",
    recordList: [],
    recordLoading: false,
    history: [],
    historyLoading: false,
    items: [],
    keywords: [],
    gridCenter: "",
    stageText: "",
    errorText: "",
    generating: false,
    fromHistory: false,
    sheetPx: 0,
    bodyPx: 0
  },

  methods: {
    noop() {},

    open(mode) {
      if (!(app.globalData.token && app.globalData.phone)) {
        wx.showToast({ title: "请先到「我的」登录授权", icon: "none" });
        return;
      }
      let win = {};
      try {
        win = (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()) || {};
      } catch (err) {
        win = {};
      }
      const sheetPx = Math.round((Number(win.windowHeight) || 640) * 0.86);
      const patch = {
        open: true,
        mode,
        sheetPx,
        bodyPx: Math.max(240, sheetPx - 56),
        phase: mode === "recording" ? "record" : "loading",
        items: [],
        keywords: [],
        gridCenter: "",
        errorText: "",
        fromHistory: false,
        stageText: mode === "memory" ? "正在读 IP 记忆…" : ""
      };
      this.setData(patch);
      setTimeout(() => this.measureBody(), 60);
      if (mode === "recording") this.loadRecords();
      else this.generate({ mode: "memory" });
    },

    measureBody() {
      wx.createSelectorQuery().in(this).select(".pf-sheet").boundingClientRect((sheet) => {
        wx.createSelectorQuery().in(this).select(".pf-head").boundingClientRect((head) => {
          if (!sheet || !sheet.height) return;
          const headH = head && head.height ? head.height : 56;
          this.setData({ bodyPx: Math.max(240, Math.round(sheet.height - headH - 8)) });
        }).exec();
      }).exec();
    },

    close() {
      this.setData({ open: false, generating: false });
    },

    backToRecords() {
      this.setData({ phase: "record", items: [], errorText: "" });
    },

    loadRecords() {
      this.setData({ recordLoading: true, recordList: [] });
      app.request({ url: "/api/h5/recorder/files?page=1&page_size=30", timeout: 30000 })
        .then((data) => {
          const rows = ((data && data.items) || [])
            .filter((row) => row.status !== "deleted")
            .map((row) => ({
              id: row.id,
              name: String(row.display_name || row.file_name || "录音"),
              time: String(row.created_at || "").replace("T", " ").slice(0, 16),
              hasTranscript: row.has_transcript === true || row.status === "completed",
              stateText: (row.has_transcript === true || row.status === "completed") ? "已转写 · 直接用文案" : "未转写"
            }));
          this.setData({ recordList: rows });
        })
        .catch((err) => this.setData({ errorText: api.errorMessage(err) || "录音列表加载失败" }))
        .then(() => this.setData({ recordLoading: false }));
    },

    pickRecord(e) {
      const id = Number(e.currentTarget.dataset.id || 0);
      if (!id || this.data.generating) return;
      const row = (this.data.recordList || []).find((item) => item.id === id);
      if (row && !row.hasTranscript) {
        wx.showToast({ title: "这条还没转写，先去 AI 秘书转写一次", icon: "none" });
        return;
      }
      this.generate({ mode: "recording", record_id: id });
    },

    generate(payload) {
      if (this.data.generating) return;
      this.setData({
        generating: true,
        phase: "loading",
        items: [],
        errorText: "",
        fromHistory: false,
        stageText: payload.mode === "recording" ? "正在读录音 → 拆关键词…" : "正在按 IP 记忆拆关键词…"
      });
      const started = Date.now();
      this.stageTimer = setInterval(() => {
        const sec = Math.round((Date.now() - started) / 1000);
        if (sec >= 50) this.setData({ stageText: "正在按九宫格写 5 条选题和文案…" });
        else if (sec >= 22) this.setData({ stageText: "关键词已出，正在搭 5×5 宫格…" });
      }, 3000);
      app.request({
        method: "POST",
        url: "/api/ip-prefill/generate",
        data: Object.assign({ target: this.data.target, count: 5 }, payload),
        timeout: 240000
      })
        .then((data) => {
          const items = ((data && data.items) || []).map((row, i) => Object.assign({}, row, {
            id: row.id || i + 1,
            summary: this.summaryOf(row)
          }));
          const grid = (data && data.grid) || {};
          this.setData({
            phase: "result",
            items,
            keywords: (data && data.keywords) || [],
            gridCenter: String(grid.center || ""),
            errorText: items.length ? "" : "AI 没有返回可用的选题"
          });
        })
        .catch((err) => {
          this.setData({ phase: payload.mode === "recording" ? "record" : "loading", errorText: api.errorMessage(err) || "生成失败" });
        })
        .then(() => {
          if (this.stageTimer) {
            clearInterval(this.stageTimer);
            this.stageTimer = null;
          }
          this.setData({ generating: false });
        });
    },

    summaryOf(row) {
      const params = (row && row.params) || {};
      if (this.data.target === "digital") {
        const parts = [String(params.duration_seconds || 30) + " 秒"];
        parts.push(params.use_materials ? "混剪开" : "不插素材");
        if (params.use_materials && params.material_group) parts.push(params.material_group);
        if (params.template_name) parts.push(params.template_name);
        return parts.join(" · ");
      }
      if (this.data.target === "montage") {
        const parts = [String(params.duration || 30) + " 秒"];
        if (params.material_group) parts.push(params.material_group);
        if (params.template_name) parts.push(params.template_name);
        return parts.join(" · ");
      }
      const shots = (row && row.shots) || [];
      return String(params.duration || 0) + " 秒 · " + shots.length + " 个分镜 · " + String(params.aspect || "9:16");
    },

    openHistory() {
      this.setData({ phase: "history", historyLoading: true, history: [], errorText: "" });
      app.request({
        url: "/api/ip-prefill/records?target=" + encodeURIComponent(this.data.target) + "&limit=20",
        timeout: 30000
      })
        .then((data) => this.setData({ history: ((data && data.items) || []) }))
        .catch((err) => this.setData({ errorText: api.errorMessage(err) || "历史加载失败" }))
        .then(() => this.setData({ historyLoading: false }));
    },

    pickHistory(e) {
      const id = Number(e.currentTarget.dataset.id || 0);
      if (!id) return;
      this.setData({ phase: "loading", stageText: "正在读取历史记录…", errorText: "" });
      app.request({ url: "/api/ip-prefill/records/" + id, timeout: 30000 })
        .then((data) => {
          const items = ((data && data.items) || []).map((row, i) => Object.assign({}, row, {
            id: row.id || i + 1,
            summary: this.summaryOf(row)
          }));
          const grid = (data && data.grid) || {};
          this.setData({
            phase: "result",
            items,
            keywords: (data && data.keywords) || [],
            gridCenter: String(grid.center || ""),
            fromHistory: true
          });
        })
        .catch((err) => this.setData({ phase: "history", errorText: api.errorMessage(err) || "读取失败" }));
    },

    deleteHistory(e) {
      const id = Number(e.currentTarget.dataset.id || 0);
      if (!id) return;
      app.request({ method: "DELETE", url: "/api/ip-prefill/records/" + id, timeout: 20000 })
        .then(() => this.setData({ history: (this.data.history || []).filter((row) => row.id !== id) }))
        .catch((err) => wx.showToast({ title: api.errorMessage(err) || "删除失败", icon: "none" }));
    },

    applyItem(e) {
      const id = Number(e.currentTarget.dataset.id || 0);
      const item = (this.data.items || []).find((row) => row.id === id);
      if (!item) return;
      this.triggerEvent("pick", { item });
      this.close();
    }
  }
});
