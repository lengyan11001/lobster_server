const app = getApp();
const api = require("../../utils/api");

const LANGS = ["中文", "英文"];
const LANG_VALUES = ["zh-CN", "en-US"];

Page({
  data: {
    editingId: 0,
    name: "",
    note: "",
    languages: LANGS,
    langIndex: 0,
    keywords: [],
    competitors: [],
    memories: [],
    selectedKeywordIds: [],
    selectedCompetitorIds: [],
    selectedMemoryIds: [],
    saving: false
  },

  onLoad(query) {
    const id = Number((query && query.id) || 0);
    this.setData({ editingId: id });
    if (id) wx.setNavigationBarTitle({ title: "编辑模板" });
    Promise.all([this.loadKeywords(), this.loadCompetitors(), this.loadMemories()])
      .then(() => {
        if (id) return this.loadTemplate(id);
        return null;
      });
  },

  loadKeywords() {
    return app.request({ url: "/api/ip-content/keywords", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((row) => ({
          id: Number(row.id || 0),
          keyword: String(row.keyword || ""),
          display_name: String(row.display_name || ""),
          checked: false
        })).filter((row) => row.id);
        this.setData({ keywords: rows });
      })
      .catch(() => {});
  },

  loadCompetitors() {
    return app.request({ url: "/api/ip-content/competitors", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((row) => ({
          id: Number(row.id || 0),
          account_key: String(row.account_key || ""),
          display_name: String(row.display_name || ""),
          checked: false
        })).filter((row) => row.id);
        this.setData({ competitors: rows });
      })
      .catch(() => {});
  },

  loadMemories() {
    return app.request({ url: "/api/personal-settings/memory-documents/list?include_content=false", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.documents) || []).map((row) => ({
          doc_id: String(row.doc_id || ""),
          title: String(row.title || row.filename || "记忆文件"),
          checked: false
        })).filter((row) => row.doc_id);
        this.setData({ memories: rows });
      })
      .catch(() => {});
  },

  loadTemplate(id) {
    return app.request({ url: "/api/ip-content/schedule-templates", timeout: 30000 })
      .then((data) => {
        const rows = (data && (data.items || data.templates)) || [];
        const row = rows.find((item) => Number(item.id) === Number(id));
        if (!row) return;
        const meta = row.meta && typeof row.meta === "object" ? row.meta : {};
        const langValue = String(meta.language || "zh-CN");
        const langIndex = Math.max(0, LANG_VALUES.indexOf(langValue));
        const keywordIds = (row.keyword_ids || []).map(Number);
        const competitorIds = (row.competitor_ids || []).map(Number);
        const memoryIds = (row.memory_doc_ids || []).map(String);
        const keywords = this.data.keywords.map((item) => Object.assign({}, item, { checked: keywordIds.indexOf(item.id) >= 0 }));
        const competitors = this.data.competitors.map((item) => Object.assign({}, item, { checked: competitorIds.indexOf(item.id) >= 0 }));
        const memories = this.data.memories.map((item) => Object.assign({}, item, { checked: memoryIds.indexOf(item.doc_id) >= 0 }));
        this.setData({
          name: String(row.name || ""),
          note: String((row.requirements && row.requirements.note) || ""),
          langIndex,
          keywords,
          competitors,
          memories,
          selectedKeywordIds: keywords.filter((item) => item.checked).map((item) => item.id),
          selectedCompetitorIds: competitors.filter((item) => item.checked).map((item) => item.id),
          selectedMemoryIds: memories.filter((item) => item.checked).map((item) => item.doc_id)
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  onName(e) { this.setData({ name: e.detail.value }); },
  onNote(e) { this.setData({ note: e.detail.value }); },
  pickLang(e) { this.setData({ langIndex: Number(e.detail.value || 0) }); },

  toggleKeyword(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const keywords = this.data.keywords.map((item, i) => i === index ? Object.assign({}, item, { checked: !item.checked }) : item);
    this.setData({ keywords, selectedKeywordIds: keywords.filter((item) => item.checked).map((item) => item.id) });
  },

  toggleCompetitor(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const competitors = this.data.competitors.map((item, i) => i === index ? Object.assign({}, item, { checked: !item.checked }) : item);
    this.setData({ competitors, selectedCompetitorIds: competitors.filter((item) => item.checked).map((item) => item.id) });
  },

  toggleMemory(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const memories = this.data.memories.map((item, i) => i === index ? Object.assign({}, item, { checked: !item.checked }) : item);
    this.setData({ memories, selectedMemoryIds: memories.filter((item) => item.checked).map((item) => item.doc_id) });
  },

  save() {
    const name = String(this.data.name || "").trim();
    if (!name) return wx.showToast({ title: "填写模板名称", icon: "none" });
    const language = LANG_VALUES[this.data.langIndex] || "zh-CN";
    const payload = {
      name,
      keyword_ids: this.data.selectedKeywordIds,
      competitor_ids: this.data.selectedCompetitorIds,
      memory_doc_ids: this.data.selectedMemoryIds,
      requirements: { note: String(this.data.note || "").trim(), language },
      meta: { source: "miniprogram", language }
    };
    this.setData({ saving: true });
    const url = this.data.editingId
      ? `/api/ip-content/schedule-templates/${this.data.editingId}`
      : "/api/ip-content/schedule-templates";
    app.request({ method: this.data.editingId ? "PATCH" : "POST", url, data: payload, timeout: 60000 })
      .then(() => {
        wx.showToast({ title: "已保存", icon: "success" });
        setTimeout(() => wx.navigateBack(), 600);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ saving: false }));
  },

  remove() {
    if (!this.data.editingId) return;
    wx.showModal({
      title: "删除模板",
      content: "删除后电脑端和手机端都会同步移除，确认？",
      success: (res) => {
        if (!res.confirm) return;
        app.request({ method: "DELETE", url: `/api/ip-content/schedule-templates/${this.data.editingId}`, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            setTimeout(() => wx.navigateBack(), 600);
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  }
});