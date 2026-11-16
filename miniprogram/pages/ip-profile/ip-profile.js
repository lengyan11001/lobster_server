const app = getApp();
const api = require("../../utils/api");

const TABS = [
  { key: "profile", label: "资料" },
  { key: "qa", label: "百问百答" },
  { key: "personal", label: "个人资料" },
  { key: "company", label: "企业资料" },
  { key: "templates", label: "模板" }
];

const ANALYZE_STEPS = [
  "读取录音转写",
  "分段理解内容",
  "归纳 12 个维度",
  "生成百问百答",
  "生成个人/企业资料"
];

function fmtTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

Page({
  data: {
    tabs: TABS,
    activeTab: "profile",
    analysis: null,
    dimensions: [],
    qaItems: [],
    personalDoc: { title: "个人资料", sections: [] },
    companyDoc: { title: "企业资料", sections: [] },
    topics: [],
    missing: [],
    meta: { name: "", industry: "", city: "", interview_date: "" },
    completeness: 0,
    loading: true,
    analyzing: false,
    analyzeStep: 0,
    analyzeSteps: ANALYZE_STEPS,
    analyzeProgress: 0,
    recordingOpen: false,
    recordings: [],
    recordingsLoading: false,
    supplementOpen: false,
    supplementTarget: null,
    supplementTitle: "",
    supplementText: "",
    supplementUrl: "",
    supplementBusy: false,
    editOpen: false,
    editTarget: null,
    editTitle: "",
    editValue: "",
    editBusy: false,
    templates: [],
    templateLoading: false,
    currentTemplateId: 0
  },

  onShow() {
    app.restoreSession();
    this.loadSaved().then(() => this.resumePendingJob());
    if (this.data.activeTab === "templates") this.loadTemplates();
  },

  onPullDownRefresh() {
    this.loadSaved().then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh());
  },

  loadSaved() {
    if (!app.globalData.token) {
      this.setData({ loading: false, analysis: null });
      return Promise.resolve();
    }
    this.setData({ loading: true });
    return app.request({ url: "/api/ip-profile/saved", timeout: 30000 })
      .then((data) => {
        const analysis = data && data.analysis;
        if (analysis && Array.isArray(analysis.dimensions) && analysis.dimensions.length) {
          if (analysis.record_id) this._lastRecordId = Number(analysis.record_id);
          this.applyAnalysis(analysis);
        } else {
          this.setData({ analysis: null, dimensions: [], qaItems: [], completeness: 0 });
        }
      })
      .catch(() => {})
      .then(() => this.setData({ loading: false }));
  },

  applyAnalysis(analysis) {
    const dims = Array.isArray(analysis.dimensions) ? analysis.dimensions : [];
    const total = dims.reduce((sum, row) => sum + (Number(row.score) || 0), 0);
    const completeness = dims.length ? Math.round(total / dims.length) : 0;
    this.setData({
      analysis,
      dimensions: dims,
      qaItems: Array.isArray(analysis.qa) ? analysis.qa : [],
      personalDoc: analysis.personal_doc && typeof analysis.personal_doc === "object" ? analysis.personal_doc : { title: "个人资料", sections: [] },
      companyDoc: analysis.company_doc && typeof analysis.company_doc === "object" ? analysis.company_doc : { title: "企业资料", sections: [] },
      topics: Array.isArray(analysis.topics) ? analysis.topics : [],
      missing: Array.isArray(analysis.missing) ? analysis.missing : [],
      meta: analysis.meta && typeof analysis.meta === "object" ? analysis.meta : { name: "", industry: "", city: "", interview_date: "" },
      completeness
    });
  },

  resumePendingJob() {
    if (this.data.analyzing) return;
    try {
      const saved = wx.getStorageSync("ip_profile_job");
      if (!saved || !saved.id) return;
      const startedAt = Number(saved.startedAt || 0);
      if (!startedAt || Date.now() - startedAt > 15 * 60 * 1000) {
        wx.removeStorageSync("ip_profile_job");
        return;
      }
      this._analyzeStartedAt = startedAt;
      this._analyzeErrors = 0;
      this._jobId = String(saved.id);
      this.setData({ analyzing: true, analyzeProgress: 2, analyzeStep: 0, activeTab: "profile" });
      this.pollAnalyzeJob(this._jobId);
    } catch (err) {}
  },

  saveAnalysis() {
    if (!this.data.analysis) return Promise.resolve();
    return app.request({
      method: "POST",
      url: "/api/ip-profile/saved",
      data: { analysis: this.data.analysis },
      timeout: 60000
    }).catch(() => {});
  },

  noop() {},

  switchTab(e) {
    const key = String(e.currentTarget.dataset.key || "profile");
    this.setData({ activeTab: key });
    if (key === "templates" && !this.data.templates.length) this.loadTemplates();
  },

  startPickRecording() {
    this.setData({ recordingOpen: true, recordingsLoading: true, recordings: [] });
    app.request({ url: "/api/h5/recorder/files?page=1&page_size=50", timeout: 30000 })
      .then((data) => {
        const rows = ((data && (data.items || data.files)) || [])
          .filter((row) => String(row.status || "") === "completed")
          .map((row) => ({
            id: Number(row.id || row.record_id || 0),
            name: String(row.display_name || row.file_name || "录音"),
            time: fmtTime(row.created_at || row.recorded_at)
          }))
          .filter((row) => row.id);
        this.setData({ recordings: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ recordingsLoading: false }));
  },

  closeRecording() {
    this.setData({ recordingOpen: false });
  },

  pickRecording(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.recordings[index];
    if (!row) return;
    this._lastRecordId = row.id;
    this.setData({ recordingOpen: false });
    this.startAnalyze({ record_id: row.id });
  },

  startAnalyze(payload) {
    if (this.data.analyzing) return;
    this.setData({ analyzing: true, analyzeStep: 0, analyzeProgress: 2, activeTab: "profile" });
    clearTimeout(this._analyzePollTimer);
    this._analyzePollTimer = null;
    this._analyzeErrors = 0;
    this._analyzeStartedAt = Date.now();
    app.request({
      method: "POST",
      url: "/api/ip-profile/jobs",
      data: payload || {},
      timeout: 30000
    })
      .then((data) => {
        const jobId = String((data && data.job_id) || "");
        if (!jobId) throw new Error("没有拿到分析任务 ID");
        this._jobId = jobId;
        try { wx.setStorageSync("ip_profile_job", { id: jobId, startedAt: this._analyzeStartedAt }); } catch (err) {}
        this.pollAnalyzeJob(jobId);
      })
      .catch((err) => {
        this.stopAnalyzeAnim();
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  pollAnalyzeJob(jobId) {
    clearTimeout(this._analyzePollTimer);
    this._analyzePollTimer = setTimeout(() => {
      app.request({ url: "/api/ip-profile/jobs/" + encodeURIComponent(jobId), timeout: 30000 })
        .then((data) => {
          this._analyzeErrors = 0;
          const progress = Math.max(2, Math.min(100, Number((data && data.progress) || 0)));
          this.setData({
            analyzeProgress: progress,
            analyzeStep: Math.min(4, Math.floor(progress / 20))
          });
          const status = String((data && data.status) || "");
          if (status === "done") {
            const result = (data && data.result) || {};
            const analysis = result.analysis || {};
            if (!analysis.dimensions || !analysis.dimensions.length) throw new Error("没有生成出档案，请重试");
            try { wx.removeStorageSync("ip_profile_job"); } catch (err) {}
            this.stopAnalyzeAnim();
            this._lastRecordId = Number(result.record_id || analysis.record_id || this._lastRecordId || 0) || this._lastRecordId;
            if (result.record_id) analysis.record_id = result.record_id;
            this.applyAnalysis(analysis);
            this.saveAnalysis();
            wx.showToast({ title: "IP 档案已生成", icon: "success" });
            return;
          }
          if (status === "failed") {
            try { wx.removeStorageSync("ip_profile_job"); } catch (err) {}
            this.stopAnalyzeAnim();
            wx.showToast({ title: String((data && data.error) || "分析失败"), icon: "none" });
            return;
          }
          if (Date.now() - this._analyzeStartedAt > 15 * 60 * 1000) {
            try { wx.removeStorageSync("ip_profile_job"); } catch (err) {}
            this.stopAnalyzeAnim();
            wx.showToast({ title: "分析超时，请重试", icon: "none" });
            return;
          }
          this.pollAnalyzeJob(jobId);
        })
        .catch((err) => {
          this._analyzeErrors = (this._analyzeErrors || 0) + 1;
          if (this._analyzeErrors >= 5) {
            this.stopAnalyzeAnim();
            wx.showToast({ title: api.errorMessage(err), icon: "none" });
            return;
          }
          this.pollAnalyzeJob(jobId);
        });
    }, 1800);
  },

  stopAnalyzeAnim() {
    clearTimeout(this._analyzePollTimer);
    this._analyzePollTimer = null;
    clearInterval(this._analyzeTimer);
    this._analyzeTimer = null;
    this._jobId = "";
    this.setData({ analyzing: false, analyzeProgress: 100 });
  },

  openGlobalSupplement() {
    this.setData({
      supplementOpen: true,
      supplementTarget: { type: "global" },
      supplementTitle: "补充资料 / 官网地址",
      supplementText: "",
      supplementUrl: ""
    });
  },

  openSupplement(e) {
    const ds = e.currentTarget.dataset;
    const type = String(ds.type || "dimension");
    const key = String(ds.key || "");
    const index = Number(ds.index || 0);
    let title = "";
    let current = "";
    if (type === "dimension") {
      const row = this.data.dimensions[index];
      if (!row) return;
      title = row.title;
      current = row.summary;
    } else if (type === "qa") {
      const row = this.data.qaItems[index];
      if (!row) return;
      title = row.question;
      current = row.answer;
    } else {
      const doc = type === "personal" ? this.data.personalDoc : this.data.companyDoc;
      const section = (doc.sections || [])[index];
      if (!section) return;
      title = section.heading;
      current = section.content;
    }
    this.setData({
      supplementOpen: true,
      supplementTarget: { type, key, index },
      supplementTitle: title,
      supplementText: "",
      supplementUrl: ""
    });
  },

  closeSupplement() {
    this.setData({ supplementOpen: false, supplementBusy: false });
  },

  onSupplementInput(e) {
    this.setData({ supplementText: e.detail.value });
  },

  onSupplementUrl(e) {
    this.setData({ supplementUrl: e.detail.value });
  },

  submitSupplement() {
    if (this.data.supplementBusy) return;
    const supplement = String(this.data.supplementText || "").trim();
    const website = String(this.data.supplementUrl || "").trim();
    if (!supplement && !website) {
      wx.showToast({ title: "请填写补充内容或官网地址", icon: "none" });
      return;
    }
    const target = this.data.supplementTarget || {};
    if (target.type === "global") {
      this.setData({ supplementOpen: false });
      if (!this._lastRecordId) {
        wx.showToast({ title: "请先选择一段录音", icon: "none" });
        return;
      }
      this.startAnalyze({ record_id: this._lastRecordId, extra: supplement, website });
      return;
    }
    let current = "";
    let title = "";
    if (target.type === "dimension") {
      const row = this.data.dimensions[target.index];
      if (!row) return;
      current = row.summary;
      title = row.title;
    } else if (target.type === "qa") {
      const row = this.data.qaItems[target.index];
      if (!row) return;
      current = row.answer;
      title = row.question;
    } else {
      const doc = target.type === "personal" ? this.data.personalDoc : this.data.companyDoc;
      const section = (doc.sections || [])[target.index];
      if (!section) return;
      current = section.content;
      title = section.heading;
    }
    this.setData({ supplementBusy: true });
    app.request({
      method: "POST",
      url: "/api/ip-profile/refine",
      data: {
        target_type: target.type === "dimension" ? "dimension" : "text",
        target_key: target.key || "",
        title,
        current,
        supplement,
        website
      },
      timeout: 180000
    })
      .then((data) => {
        const result = (data && data.result) || {};
        this.mergeRefine(target, result);
        this.setData({ supplementOpen: false, supplementBusy: false });
        this.saveAnalysis();
        wx.showToast({ title: "已补充并更新", icon: "success" });
      })
      .catch((err) => {
        this.setData({ supplementBusy: false });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  mergeRefine(target, result) {
    const analysis = this.data.analysis || {};
    if (target.type === "dimension") {
      const dimensions = this.data.dimensions.map((row, i) => i === target.index ? Object.assign({}, row, {
        summary: String(result.summary || row.summary || ""),
        score: Number(result.score || row.score || 0),
        evidence: Array.isArray(result.evidence) && result.evidence.length ? result.evidence : row.evidence,
        quotes: Array.isArray(result.quotes) && result.quotes.length ? result.quotes : row.quotes,
        gaps: Array.isArray(result.gaps) ? result.gaps : row.gaps
      }) : row);
      analysis.dimensions = dimensions;
      this.applyAnalysis(analysis);
    } else if (target.type === "qa") {
      const qaItems = this.data.qaItems.map((row, i) => i === target.index ? Object.assign({}, row, { answer: String(result.text || row.answer || "") }) : row);
      analysis.qa = qaItems;
      this.setData({ qaItems });
    } else {
      const docKey = target.type === "personal" ? "personal_doc" : "company_doc";
      const doc = this.data[target.type === "personal" ? "personalDoc" : "companyDoc"];
      const sections = (doc.sections || []).map((row, i) => i === target.index ? Object.assign({}, row, { content: String(result.text || row.content || "") }) : row);
      analysis[docKey] = Object.assign({}, doc, { sections });
      this.setData({ [target.type === "personal" ? "personalDoc" : "companyDoc"]: analysis[docKey] });
    }
  },

  openEdit(e) {
    const ds = e.currentTarget.dataset;
    const type = String(ds.type || "dimension");
    const index = Number(ds.index || 0);
    let title = "";
    let value = "";
    if (type === "dimension") {
      const row = this.data.dimensions[index];
      if (!row) return;
      title = row.title;
      value = row.summary;
    } else if (type === "qa") {
      const row = this.data.qaItems[index];
      if (!row) return;
      title = row.question;
      value = row.answer;
    } else {
      const doc = type === "personal" ? this.data.personalDoc : this.data.companyDoc;
      const section = (doc.sections || [])[index];
      if (!section) return;
      title = section.heading;
      value = section.content;
    }
    this.setData({ editOpen: true, editTarget: { type, index }, editTitle: title, editValue: value });
  },

  closeEdit() {
    this.setData({ editOpen: false, editBusy: false });
  },

  onEditInput(e) {
    this.setData({ editValue: e.detail.value });
  },

  submitEdit() {
    if (this.data.editBusy) return;
    const target = this.data.editTarget || {};
    const value = String(this.data.editValue || "");
    if (!value.trim()) {
      wx.showToast({ title: "内容不能为空", icon: "none" });
      return;
    }
    this.mergeRefine(target, { text: value, summary: value });
    this.setData({ editOpen: false });
    this.saveAnalysis();
    wx.showToast({ title: "已保存", icon: "success" });
  },

  loadTemplates() {
    this.setData({ templateLoading: true });
    return app.request({ url: "/api/ip-content/schedule-templates", timeout: 30000 })
      .then((data) => {
        const raw = (data && (data.items || data.templates)) || [];
        const rows = raw.map((row) => {
          const meta = row.meta && typeof row.meta === "object" ? row.meta : {};
          const dh = meta.digital_human_template && typeof meta.digital_human_template === "object" ? meta.digital_human_template : {};
          return {
            id: Number(row.id || 0),
            name: String(row.name || "未命名模板"),
            is_personal_default: Boolean(row.is_personal_default || row.name === "个人默认配置"),
            device_name: String(row.device_name || (row.installation_id ? String(row.installation_id).slice(0, 12) : "账号级")),
            device_online: Boolean(row.device_online),
            installation_id: String(row.installation_id || ""),
            styleId: String(dh.style_id || meta.style_id || ""),
            time: String(row.updated_at || "").replace("T", " ").slice(0, 16)
          };
        }).filter((row) => row.id);
        rows.sort((a, b) => String(b.time).localeCompare(String(a.time)));
        const seenDefault = {};
        const deduped = rows.filter((row) => {
          if (!row.is_personal_default) return true;
          const key = row.installation_id || "account";
          if (seenDefault[key]) return false;
          seenDefault[key] = true;
          return true;
        });
        let currentId = 0;
        try {
          const saved = wx.getStorageSync("lobster_current_template") || wx.getStorageSync("lobster_dh_template");
          currentId = saved && saved.id ? Number(saved.id) : 0;
        } catch (err) {
          currentId = 0;
        }
        this.setData({ templates: deduped, currentTemplateId: currentId });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ templateLoading: false }));
  },

  setCurrentTemplate(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.templates[index];
    if (!row) return;
    wx.setStorageSync("lobster_current_template", {
      id: row.id,
      name: row.name,
      device_name: row.device_name,
      installation_id: row.installation_id,
      styleId: row.styleId || ""
    });
    this.setData({ currentTemplateId: row.id });
    wx.showToast({ title: "已设为当前模板", icon: "success" });
  },

  copyTemplate(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.templates[index];
    if (!row) return;
    wx.showLoading({ title: "复制中" });
    app.request({ method: "POST", url: "/api/ip-content/schedule-templates/" + row.id + "/copy", data: {}, timeout: 60000 })
      .then(() => {
        wx.showToast({ title: "已复制为我的模板", icon: "success" });
        this.loadTemplates();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  addTemplate() {
    wx.navigateTo({ url: "/pages/template-edit/template-edit" });
  },

  editTemplate(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.templates[index];
    if (!row) return;
    wx.navigateTo({ url: "/pages/template-edit/template-edit?id=" + row.id });
  },

  importTemplate() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["json"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        try {
          const raw = wx.getFileSystemManager().readFileSync(file.path, "utf8");
          const obj = JSON.parse(raw);
          const name = String(obj.name || "").trim();
          if (!name) throw new Error("模板文件缺少 name 字段");
          wx.showLoading({ title: "导入中" });
          app.request({
            method: "POST",
            url: "/api/ip-content/schedule-templates",
            data: {
              name,
              requirements: obj.requirements && typeof obj.requirements === "object" ? obj.requirements : {},
              meta: obj.meta && typeof obj.meta === "object" ? obj.meta : { source: "miniprogram-import" }
            },
            timeout: 60000
          })
            .then(() => {
              wx.showToast({ title: "已导入", icon: "success" });
              this.loadTemplates();
            })
            .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
            .then(() => wx.hideLoading());
        } catch (err) {
          wx.showToast({ title: String((err && err.message) || "模板文件解析失败"), icon: "none" });
        }
      }
    });
  }
});
