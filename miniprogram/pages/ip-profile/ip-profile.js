const app = getApp();
const api = require("../../utils/api");

const TABS = [
  { key: "profile", label: "资料调查" },
  { key: "memory", label: "个人记忆" },
  { key: "keywords", label: "关键词" },
  { key: "competitors", label: "同行" },
  { key: "templates", label: "模板" }
];

const FIELD_DEFS = [
  { key: "name", label: "你的名字/称呼" },
  { key: "gender", label: "性别（male / female）" },
  { key: "birth_era", label: "出生年代（80后/90后…）" },
  { key: "current_province", label: "现在居住省份" },
  { key: "current_city", label: "现在居住城市" },
  { key: "hometown", label: "籍贯/家乡" },
  { key: "role", label: "职业/对外身份" },
  { key: "share_topic", label: "账号分享方向" },
  { key: "video_style", label: "视频风格" },
  { key: "after_view_action", label: "希望观众看完做什么" },
  { key: "product", label: "产品/服务（卖点、价格、交付）", multiline: true },
  { key: "target_customer", label: "目标客户（身份/年龄/地区/痛点）", multiline: true },
  { key: "advantages", label: "比同行好在哪（资历/案例/服务/价格）", multiline: true }
];

function pageSlice(rows, page, size) {
  const list = Array.isArray(rows) ? rows : [];
  const pages = Math.max(1, Math.ceil(list.length / size));
  const current = Math.min(Math.max(1, Number(page) || 1), pages);
  return { page: current, pages, items: list.slice((current - 1) * size, current * size) };
}

function askInput(title, placeholder, value) {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      editable: true,
      placeholderText: placeholder,
      content: value || "",
      success: (res) => resolve(res.confirm ? String(res.content || "").trim() : null),
      fail: () => resolve(null)
    });
  });
}

Page({
  data: {
    tabs: TABS,
    tabIndex: 0,
    profileFields: [],
    profileName: "",
    profileSaving: false,
    keywordModalOpen: false,
    keywordInput: "",
    keywordSuggesting: false,
    keywordSaving: false,
    suggestItems: [],
    loading: true,
    surveys: [],
    currentSurveyId: 0,
    editorVisible: false,
    editorId: 0,
    editorName: "",
    editorFields: [],
    editorSaving: false,
    memoryLoading: false,
    memoryDocs: [],
    memoryItems: [],
    memoryPage: 1,
    memoryPages: 1,
    keywordLoading: false,
    keywords: [],
    keywordItems: [],
    keywordPage: 1,
    keywordPages: 1,
    competitorLoading: false,
    compOpen: false,
    compPlatform: "douyin",
    compKeyword: "",
    compTags: "",
    compSearching: false,
    compAdding: false,
    compSearched: false,
    compResults: [],
    competitors: [],
    competitorItems: [],
    competitorPage: 1,
    competitorPages: 1,
    templateLoading: false,
    templates: [],
    doc: null,
    currentTemplateId: 0
  },

  onLoad() {
    this.setData({ profileFields: FIELD_DEFS.map((def) => Object.assign({}, def, { value: "" })) });
  },

  onShow() {
    this.loadInstallationId();
    this.loadAll();
  },

  loadAll() {
    return Promise.all([
      this.loadProfile(),
      this.loadMemory(),
      this.loadKeywords(),
      this.loadCompetitors()
    ]);
  },

  loadInstallationId() {
    if (!app.globalData.token) return Promise.resolve("");
    if (this.installationId) return Promise.resolve(this.installationId);
    return app.request({ url: "/api/mobile/devices", timeout: 20000 })
      .then((data) => {
        const rows = (data && data.online_devices) || [];
        const picked = rows.find((row) => row.online) || rows[0];
        this.installationId = picked ? String(picked.installation_id || "") : "";
        return this.installationId;
      })
      .catch(() => "");
  },

  iidHeader() {
    return this.installationId ? { "X-Installation-Id": this.installationId } : {};
  },
  onPullDownRefresh() {
    this.loadAll().then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh());
  },
  loadProfile() {
    this.setData({ loading: true });
    return this.loadInstallationId()
      .then(() => Promise.all([
        app.request({ url: "/api/ip-content/personal-default", header: this.iidHeader(), timeout: 30000 }).catch(() => null),
        app.request({ url: "/api/ip-content/profile-surveys", timeout: 30000 }).catch(() => null)
      ]))
      .then((results) => {
        const defData = results[0];
        const listData = results[1];
        const item = (defData && defData.item) || this._defaultItem || {};
        this._defaultItem = item;
        const currentSurveyId = Number(item.survey_id || (item.survey && item.survey.id) || 0) || 0;
        if (listData && Array.isArray(listData.items)) {
          const rows = listData.items.map((row) => ({
            id: Number(row.id || 0),
            name: String(row.name || "资料调查"),
            time: String(row.updated_at || "").replace("T", " ").slice(0, 16),
            requirements: row.requirements && typeof row.requirements === "object" ? row.requirements : {}
          })).filter((row) => row.id);
          this._surveys = rows;
          this.setData({ surveys: rows.map((row) => ({ id: row.id, name: row.name, time: row.time })) });
        }
        const surveys = this._surveys || [];
        const current = surveys.find((row) => Number(row.id) === Number(currentSurveyId));
        const requirements = current ? current.requirements : ((item && item.requirements) || {});
        this.setData({
          currentSurveyId,
          profileName: current ? current.name : "个人资料",
          profileFields: this.fieldsFromRequirements(requirements)
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  fieldsFromRequirements(requirements) {
    const req = requirements || {};
    const basic = req.basic_profile && typeof req.basic_profile === "object" ? req.basic_profile : {};
    const business = req.business_description && typeof req.business_description === "object" ? req.business_description : {};
    return FIELD_DEFS.map((def) => {
      let value = "";
      if (def.key === "name") value = req.profile_name || basic.name || "";
      else if (def.key === "product" || def.key === "target_customer" || def.key === "advantages") {
        value = req[def.key] || business[def.key] || "";
      } else {
        value = basic[def.key] || req[def.key] || "";
      }
      return Object.assign({}, def, { value: String(value || "") });
    });
  },

  applyPaging(kind, page) {
    if (kind === "memory") {
      const res = pageSlice(this.data.memoryDocs, page || this.data.memoryPage, 5);
      this.setData({ memoryItems: res.items, memoryPage: res.page, memoryPages: res.pages });
      return;
    }
    if (kind === "keyword") {
      const res = pageSlice(this.data.keywords, page || this.data.keywordPage, 5);
      this.setData({ keywordItems: res.items, keywordPage: res.page, keywordPages: res.pages });
      return;
    }
    if (kind === "competitor") {
      const res = pageSlice(this.data.competitors, page || this.data.competitorPage, 5);
      this.setData({ competitorItems: res.items, competitorPage: res.page, competitorPages: res.pages });
    }
  },

  changePage(e) {
    const kind = String(e.currentTarget.dataset.kind || "");
    const dir = Number(e.currentTarget.dataset.dir || 0);
    const map = { memory: "memoryPage", keyword: "keywordPage", competitor: "competitorPage" };
    const key = map[kind];
    if (!key) return;
    this.applyPaging(kind, Number(this.data[key] || 1) + dir);
  },

  onProfileField(e) {
    const key = e.currentTarget.dataset.key;
    const value = e.detail.value || "";
    const profileFields = this.data.profileFields.map((item) => item.key === key ? Object.assign({}, item, { value }) : item);
    this.setData({ profileFields });
  },

  saveProfileInline() {
    if (this.data.profileSaving) return;
    if (!app.globalData.token) return wx.showToast({ title: "请先登录", icon: "none" });
    const requirements = this.collectRequirements(this.data.profileFields);
    const editingId = Number(this.data.currentSurveyId || 0) || 0;
    const name = String(this.data.profileName || "").trim() || "个人资料";
    const header = this.iidHeader();
    const body = { name, requirements, meta: { source: "online_personal_profile" } };
    this.setData({ profileSaving: true });
    const saveRequest = editingId
      ? app.request({ method: "PATCH", url: "/api/ip-content/profile-surveys/" + editingId, header, data: body, timeout: 60000 })
      : app.request({ method: "POST", url: "/api/ip-content/profile-surveys", header, data: body, timeout: 60000 });
    saveRequest
      .then((data) => {
        const item = (data && data.item) || {};
        const savedId = Number(item.id || editingId || 0) || 0;
        return this.syncDefaultSurvey(savedId, requirements)
          .then(() => {
            this.setData({ profileSaving: false, currentSurveyId: savedId });
            wx.showToast({ title: "已保存", icon: "success" });
          })
          .catch(() => {
            this.setData({ profileSaving: false, currentSurveyId: savedId });
            wx.showToast({ title: "已保存，同步默认配置失败", icon: "none" });
          });
      })
      .catch((err) => {
        this.setData({ profileSaving: false });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  newSurvey() {
    this.setData({
      editorVisible: true,
      editorId: 0,
      editorName: "默认资料",
      editorFields: FIELD_DEFS.map((def) => Object.assign({}, def, { value: "" }))
    });
  },

  editSurvey(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = (this._surveys || [])[index];
    if (!row) return;
    this.setData({
      editorVisible: true,
      editorId: row.id,
      editorName: row.name || "默认资料",
      editorFields: this.fieldsFromRequirements(row.requirements)
    });
  },

  closeEditor() {
    this.setData({ editorVisible: false });
  },

  onEditorName(e) {
    this.setData({ editorName: e.detail.value || "" });
  },

  onEditorField(e) {
    const key = e.currentTarget.dataset.key;
    const value = e.detail.value || "";
    const editorFields = this.data.editorFields.map((item) => item.key === key ? Object.assign({}, item, { value }) : item);
    this.setData({ editorFields });
  },

  collectRequirements(fields) {
    const basic = {};
    const business = {};
    (fields || []).forEach((item) => {
      const text = String(item.value || "").trim();
      if (item.key === "product" || item.key === "target_customer" || item.key === "advantages") {
        business[item.key] = text;
      } else {
        basic[item.key] = text;
      }
    });
    const requirements = {
      basic_profile: basic,
      business_description: business,
      profile_name: basic.name || ""
    };
    Object.keys(basic).forEach((key) => {
      if (key !== "name") requirements[key] = basic[key];
    });
    Object.keys(business).forEach((key) => {
      requirements[key] = business[key];
    });
    return requirements;
  },

  syncDefaultSurvey(surveyId, requirements) {
    const existing = this._defaultItem || {};
    return app.request({
      method: "PUT",
      url: "/api/ip-content/personal-default",
      header: this.iidHeader(),
      data: {
        name: existing.name || "个人默认配置",
        keyword_ids: Array.isArray(existing.keyword_ids) ? existing.keyword_ids : [],
        competitor_ids: Array.isArray(existing.competitor_ids) ? existing.competitor_ids : [],
        memory_doc_ids: Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : [],
        memory_docs: Array.isArray(existing.memory_docs) ? existing.memory_docs : [],
        requirements,
        survey_id: surveyId,
        meta: Object.assign({}, existing.meta || {}, { source: "online_personal_profile", survey_id: surveyId })
      },
      timeout: 60000
    }).then((data) => {
      this._defaultItem = (data && data.item) || this._defaultItem;
      this.setData({ currentSurveyId: Number(surveyId || 0) || 0 });
    });
  },

  saveSurvey() {
    if (this.data.editorSaving) return;
    const name = String(this.data.editorName || "").trim() || "默认资料";
    const requirements = this.collectRequirements(this.data.editorFields);
    const editingId = Number(this.data.editorId || 0) || 0;
    const header = this.iidHeader();
    const body = { name, requirements, meta: { source: "online_personal_profile" } };
    this.setData({ editorSaving: true });
    const saveRequest = editingId
      ? app.request({ method: "PATCH", url: "/api/ip-content/profile-surveys/" + editingId, header, data: body, timeout: 60000 })
      : app.request({ method: "POST", url: "/api/ip-content/profile-surveys", header, data: body, timeout: 60000 });
    saveRequest
      .then((data) => {
        const item = (data && data.item) || {};
        const savedId = Number(item.id || editingId || 0) || null;
        // 与 online 一致：记录先落库，再同步个人默认配置，最后刷新列表
        this.setData({ editorVisible: false, editorSaving: false });
        return this.syncDefaultSurvey(savedId, requirements)
          .then(() => {
            this.loadProfile();
            wx.showToast({ title: "已保存", icon: "success" });
          })
          .catch(() => {
            this.loadProfile();
            wx.showToast({ title: "已保存，同步默认配置失败", icon: "none" });
          });
      })
      .catch((err) => {
        this.setData({ editorSaving: false });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  deleteSurvey(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = (this._surveys || [])[index];
    if (!row) return;
    wx.showModal({
      title: "删除资料调查",
      content: "确认删除「" + row.name + "」？",
      success: (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: "删除中", mask: true });
        app.request({ method: "DELETE", url: "/api/ip-content/profile-surveys/" + row.id, timeout: 30000 })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.loadProfile();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  loadMemory() {
    this.setData({ memoryLoading: true });
    return this.loadInstallationId()
      .then(() => app.request({ url: "/api/personal-settings/memory-documents/list?include_content=false", header: this.iidHeader(), timeout: 30000 }))
      .then((data) => {
        const docs = ((data && data.documents) || []).map((row) => ({
          doc_id: String(row.doc_id || ""),
          title: String(row.title || row.filename || "记忆文件"),
          notes: String(row.notes || "")
        })).filter((row) => row.doc_id);
        this.setData({ memoryDocs: docs });
        this.applyPaging("memory", 1);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ memoryLoading: false }));
  },

  addMemoryText() {
    askInput("新增记忆", "记忆标题，例如：产品介绍").then((title) => {
      if (!title) return;
      askInput("记忆正文", "粘贴或输入正文内容").then((content) => {
        if (!content) return;
        wx.showLoading({ title: "保存中" });
        app.request({
          method: "POST",
          url: "/api/personal-settings/memory-documents/save-raw",
          data: { title, content, notes: "", mode: "new" },
          header: this.iidHeader(),
          timeout: 60000
        })
          .then(() => {
            wx.showToast({ title: "已保存", icon: "success" });
            this.loadMemory();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      });
    });
  },

  uploadMemory() {
    wx.chooseMessageFile({
      count: 5,
      type: "file",
      extension: ["txt", "md", "pdf", "docx", "doc"],
      success: (res) => {
        const files = res.tempFiles || [];
        if (!files.length) return;
        wx.showLoading({ title: "上传中" });
        const jobs = files.map((file) => api.uploadFile({
          url: "/api/personal-settings/memory-documents/save-upload",
          filePath: file.path,
          name: "files",
          formData: { title: String(file.name || ""), notes: "", mode: "new" },
          header: this.iidHeader(),
          token: app.globalData.token || "",
          timeout: 600000
        }).catch(() => null));
        Promise.all(jobs)
          .then(() => {
            wx.showToast({ title: "已上传", icon: "success" });
            this.loadMemory();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  addTemplate() {
    wx.navigateTo({ url: "/pages/template-edit/template-edit" });
  },

  editTemplate(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.templates[index];
    if (!row) return;
    wx.navigateTo({ url: `/pages/template-edit/template-edit?id=${row.id}` });
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
  },

  openDoc(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.memoryItems[index];
    if (!row) return;
    wx.showLoading({ title: "加载中" });
    app.request({ url: `/api/personal-settings/memory-documents/${encodeURIComponent(row.doc_id)}/preview`, header: this.iidHeader(), timeout: 30000 })
      .then((data) => {
        const content = String((data && (data.content_text || (data.document && data.document.content_text))) || "");
        this.setData({ doc: { title: row.title, content: content || "（无内容）" } });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  closeDoc() { this.setData({ doc: null }); },
  noop() {},
  loadKeywords() {
    this.setData({ keywordLoading: true });
    return app.request({ url: "/api/ip-content/keywords", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((row) => ({
          id: Number(row.id || 0),
          keyword: String(row.keyword || ""),
          display_name: String(row.display_name || ""),
          status: String(row.status || "")
        })).filter((row) => row.id);
        this.setData({ keywords: rows });
        this.applyPaging("keyword", 1);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ keywordLoading: false }));
  },

  openKeywordModal() {
    this.setData({ keywordModalOpen: true, keywordInput: "", suggestItems: [], keywordSuggesting: false, keywordSaving: false });
  },

  closeKeywordModal() {
    this.setData({ keywordModalOpen: false });
  },

  onKeywordInput(e) {
    this.setData({ keywordInput: e.detail.value || "" });
  },

  suggestKeywords() {
    const seed = String(this.data.keywordInput || "").trim();
    if (!seed) return wx.showToast({ title: "先输入关键词", icon: "none" });
    if (this.data.keywordSuggesting) return;
    this.setData({ keywordSuggesting: true });
    app.request({ method: "POST", url: "/api/ip-content/keywords/suggest", data: { keyword: seed }, timeout: 120000 })
      .then((data) => {
        const items = [{ text: seed, checked: true }];
        ((data && data.items) || []).forEach((raw) => {
          const text = String(raw || "").trim();
          if (text && !items.some((row) => row.text === text)) items.push({ text, checked: false });
        });
        this.setData({ suggestItems: items });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err) || "推荐失败", icon: "none" }))
      .then(() => this.setData({ keywordSuggesting: false }));
  },

  toggleSuggest(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const suggestItems = this.data.suggestItems.map((row, idx) => idx === index ? Object.assign({}, row, { checked: !row.checked }) : row);
    this.setData({ suggestItems });
  },

  confirmKeywords() {
    if (this.data.keywordSaving) return;
    const picked = (this.data.suggestItems || []).filter((row) => row.checked).map((row) => row.text);
    if (!picked.length) return wx.showToast({ title: "至少选一个关键词", icon: "none" });
    const existing = new Set((this.data.keywords || []).map((row) => row.keyword));
    const todo = picked.filter((text) => !existing.has(text));
    if (!todo.length) {
      this.setData({ keywordModalOpen: false });
      return wx.showToast({ title: "这些关键词已存在", icon: "none" });
    }
    this.setData({ keywordSaving: true });
    Promise.all(todo.map((text) => app.request({
      method: "POST",
      url: "/api/ip-content/keywords",
      data: { keyword: text, display_name: text },
      timeout: 30000
    }).catch(() => null)))
      .then(() => {
        this.setData({ keywordSaving: false, keywordModalOpen: false });
        wx.showToast({ title: "已添加 " + todo.length + " 个关键词", icon: "success" });
        this.loadKeywords();
      })
      .catch(() => this.setData({ keywordSaving: false }));
  },

  keywordMenu(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.keywordItems[index];
    if (!row) return;
    wx.showActionSheet({
      itemList: ["立即同步", "删除"],
      success: (res) => {
        if (res.tapIndex === 0) {
          wx.showLoading({ title: "同步中" });
          app.request({ method: "POST", url: `/api/ip-content/keywords/${row.id}/sync`, data: {}, timeout: 120000 })
            .then(() => wx.showToast({ title: "已同步", icon: "success" }))
            .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
            .then(() => wx.hideLoading());
          return;
        }
        if (res.tapIndex === 1) {
          wx.showModal({
            title: "删除关键词",
            content: `确认删除「${row.keyword}」？`,
            success: (r) => {
              if (!r.confirm) return;
              app.request({ method: "DELETE", url: `/api/ip-content/keywords/${row.id}`, timeout: 30000 })
                .then(() => {
                  wx.showToast({ title: "已删除", icon: "success" });
                  this.loadKeywords();
                })
                .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
            }
          });
        }
      }
    });
  },
  loadCompetitors() {
    this.setData({ competitorLoading: true });
    return app.request({ url: "/api/ip-content/competitors", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((row) => ({
          id: Number(row.id || 0),
          platform: String(row.platform || ""),
          account_key: String(row.account_key || ""),
          display_name: String(row.display_name || ""),
          status: String(row.status || ""),
          industry_tags: String(row.industry_tags || ""),
          last_fetch_at: String(row.last_fetch_at || "").replace("T", " ").slice(0, 16)
        })).filter((row) => row.id);
        this.setData({ competitors: rows });
        this.applyPaging("competitor", 1);
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ competitorLoading: false }));
  },

  openCompetitorModal() {
    this.setData({ compOpen: true, compKeyword: "", compTags: "", compResults: [], compSearched: false, compSearching: false, compAdding: false });
  },

  closeCompetitorModal() {
    this.setData({ compOpen: false });
  },

  setCompPlatform(e) {
    const p = String(e.currentTarget.dataset.p || "douyin");
    if (p === this.data.compPlatform) return;
    this.setData({ compPlatform: p, compResults: [], compSearched: false });
  },

  onCompKeyword(e) { this.setData({ compKeyword: e.detail.value || "" }); },
  onCompTags(e) { this.setData({ compTags: e.detail.value || "" }); },

  searchCompetitor() {
    const q = String(this.data.compKeyword || "").trim();
    if (!q) return wx.showToast({ title: "先输入昵称或账号", icon: "none" });
    if (this.data.compSearching) return;
    const platform = this.data.compPlatform;
    const url = platform === "wechat_channels"
      ? "/api/ip-content/wechat-channels/users/search?q=" + encodeURIComponent(q)
      : "/api/ip-content/douyin/users/search?q=" + encodeURIComponent(q);
    this.setData({ compSearching: true, compSearched: false, compResults: [] });
    app.request({ url, timeout: 120000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((item, idx) => this.normalizeCandidate(item, platform, idx));
        this.setData({ compResults: rows, compSearched: true });
      })
      .catch((err) => {
        this.setData({ compSearched: true });
        wx.showToast({ title: api.errorMessage(err) || "搜索失败", icon: "none" });
      })
      .then(() => this.setData({ compSearching: false }));
  },

  normalizeCandidate(item, platform, idx) {
    const raw = item || {};
    const name = String(raw.display_name || raw.nickname || raw.unique_id || raw.username || "账号").trim();
    const bits = [];
    if (platform === "wechat_channels") {
      const u = String(raw.username || raw.finder_username || "").trim();
      if (u) bits.push("username：" + u);
    } else {
      const uid = String(raw.unique_id || "").trim();
      if (uid) bits.push("抖音号：" + uid);
    }
    if (raw.follower_count) bits.push("粉丝：" + raw.follower_count);
    if (raw.aweme_count) bits.push("作品：" + raw.aweme_count);
    if (raw.verify_info) bits.push(String(raw.verify_info));
    return {
      key: String(raw.sec_user_id || raw.sec_uid || raw.username || raw.finder_username || raw.id || idx),
      name,
      initial: name.slice(0, 1),
      avatar: String(raw.avatar_url || raw.avatar || ""),
      meta: bits.join(" · "),
      raw
    };
  },

  addCompetitorCandidate(e) {
    if (this.data.compAdding) return;
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.compResults[index];
    if (!row) return;
    const platform = this.data.compPlatform;
    const c = row.raw || {};
    const accountKey = String(platform === "wechat_channels"
      ? (c.username || c.finder_username || c.id || "")
      : (c.sec_user_id || c.sec_uid || c.id || "")).trim();
    if (!accountKey) return wx.showToast({ title: "这个账号缺少可用的账号 ID", icon: "none" });
    this.setData({ compAdding: true });
    app.request({
      method: "POST",
      url: "/api/ip-content/competitors",
      data: {
        platform,
        account_key: accountKey,
        display_name: row.name,
        homepage_url: String(c.homepage_url || ""),
        industry_tags: String(this.data.compTags || "").trim(),
        meta: {
          source: platform === "wechat_channels" ? "wechat_channels_user_search" : "douyin_user_search",
          unique_id: c.unique_id || "",
          username: c.username || c.finder_username || "",
          uid: c.uid || "",
          nickname: c.nickname || row.name,
          follower_count: c.follower_count || 0,
          aweme_count: c.aweme_count || 0,
          like_count: c.like_count || 0,
          avatar_url: row.avatar
        }
      },
      timeout: 60000
    })
      .then(() => {
        wx.showToast({ title: "已添加", icon: "success" });
        this.setData({ compOpen: false });
        this.loadCompetitors();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ compAdding: false }));
  },

  addCompetitorByChannel() {
    const value = String(this.data.compKeyword || "").trim();
    if (!value) return wx.showToast({ title: "先粘贴 ID 或作品链接", icon: "none" });
    if (this.data.compAdding) return;
    this.setData({ compAdding: true });
    app.request({
      method: "POST",
      url: "/api/ip-content/wechat-channels/competitors/by-channel-id",
      data: { channel_id: value, industry_tags: String(this.data.compTags || "").trim() },
      timeout: 120000
    })
      .then(() => {
        wx.showToast({ title: "已添加", icon: "success" });
        this.setData({ compOpen: false });
        this.loadCompetitors();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ compAdding: false }));
  },

  syncCompetitorRow(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.competitorItems[index];
    if (!row || !row.id) return;
    wx.showLoading({ title: "同步中" });
    app.request({ method: "POST", url: `/api/ip-content/competitors/${row.id}/sync`, data: { count: 20 }, timeout: 180000 })
      .then(() => {
        wx.showToast({ title: "已同步", icon: "success" });
        this.loadCompetitors();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  competitorMenu(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.competitorItems[index];
    if (!row) return;
    wx.showActionSheet({
      itemList: ["立即同步", "删除"],
      success: (res) => {
        if (res.tapIndex === 0) {
          wx.showLoading({ title: "同步中" });
          app.request({ method: "POST", url: `/api/ip-content/competitors/${row.id}/sync`, data: { count: 20 }, timeout: 180000 })
            .then(() => wx.showToast({ title: "已同步", icon: "success" }))
            .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
            .then(() => wx.hideLoading());
          return;
        }
        if (res.tapIndex === 1) {
          wx.showModal({
            title: "删除同行",
            content: `确认删除「${row.display_name || row.account_key}」？`,
            success: (r) => {
              if (!r.confirm) return;
              app.request({ method: "DELETE", url: `/api/ip-content/competitors/${row.id}`, timeout: 30000 })
                .then(() => {
                  wx.showToast({ title: "已删除", icon: "success" });
                  this.loadCompetitors();
                })
                .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
            }
          });
        }
      }
    });
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
        // 旧的错误保存会留下多条同名「个人默认配置」，列表里每个槽位只展示最新一条
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
    app.request({ method: "POST", url: `/api/ip-content/schedule-templates/${row.id}/copy`, data: {}, timeout: 60000 })
      .then(() => {
        wx.showToast({ title: "已复制为我的模板", icon: "success" });
        this.loadTemplates();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  }
});