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
    loading: true,
    saving: false,
    fields: [],
    memoryLoading: false,
    memoryDocs: [],
    keywordLoading: false,
    keywords: [],
    competitorLoading: false,
    competitors: [],
    templateLoading: false,
    templates: [],
    doc: null,
    currentTemplateId: 0
  },

  onShow() {
    this.loadInstallationId();
    this.reloadCurrent();
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
  onPullDownRefresh() { this.reloadCurrent().then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh()); },

  reloadCurrent() {
    const index = this.data.tabIndex;
    if (index === 0) return this.loadProfile();
    if (index === 1) return this.loadMemory();
    if (index === 2) return this.loadKeywords();
    if (index === 3) return this.loadCompetitors();
    return this.loadTemplates();
  },

  switchTab(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    this.setData({ tabIndex: index });
    this.reloadCurrent();
  },
  loadProfile() {
    this.setData({ loading: true });
    return app.request({ url: "/api/mastra-chat/personal-profile", timeout: 30000 })
      .then((data) => {
        const values = ((data && data.profile) || {}).fields || {};
        const fields = FIELD_DEFS.map((def) => Object.assign({}, def, { value: String(values[def.key] || "") }));
        this.setData({ fields });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  onField(e) {
    const key = e.currentTarget.dataset.key;
    const value = e.detail.value;
    const fields = this.data.fields.map((item) => item.key === key ? Object.assign({}, item, { value }) : item);
    this.setData({ fields });
  },

  save() {
    const values = {};
    this.data.fields.forEach((item) => {
      const text = String(item.value || "").trim();
      if (text) values[item.key] = text;
    });
    const basic = {};
    ["gender", "birth_era", "current_province", "current_city", "hometown", "role", "share_topic", "video_style", "after_view_action"].forEach((key) => {
      if (values[key]) basic[key] = values[key];
    });
    const requirements = { basic_profile: basic };
    if (values.name) requirements.profile_name = values.name;
    if (values.product) requirements.product = values.product;
    if (values.target_customer) requirements.target_customer = values.target_customer;
    if (values.advantages) requirements.advantages = values.advantages;
    this.setData({ saving: true });
    app.request({
      method: "POST",
      url: "/api/ip-content/schedule-templates",
      data: { name: "个人默认配置", requirements, meta: { source: "miniprogram" } },
      timeout: 60000
    })
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ saving: false }));
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
    const row = this.data.memoryDocs[index];
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
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ keywordLoading: false }));
  },

  addKeyword() {
    askInput("新增关键词", "例如：小户型收纳").then((text) => {
      if (!text) return;
      wx.showLoading({ title: "保存中" });
      app.request({ method: "POST", url: "/api/ip-content/keywords", data: { keyword: text, display_name: text }, timeout: 30000 })
        .then(() => {
          wx.showToast({ title: "已添加", icon: "success" });
          this.loadKeywords();
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
        .then(() => wx.hideLoading());
    });
  },

  keywordMenu(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.keywords[index];
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
          status: String(row.status || "")
        })).filter((row) => row.id);
        this.setData({ competitors: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ competitorLoading: false }));
  },

  addCompetitor() {
    wx.showActionSheet({
      itemList: ["抖音", "视频号"],
      success: (res) => {
        const platform = res.tapIndex === 1 ? "wechat_channels" : "douyin";
        askInput("新增同行", platform === "douyin" ? "输入抖音号或主页链接" : "输入视频号 ID").then((key) => {
          if (!key) return;
          wx.showLoading({ title: "保存中" });
          app.request({
            method: "POST",
            url: "/api/ip-content/competitors",
            data: { platform, account_key: key, display_name: key },
            timeout: 30000
          })
            .then(() => {
              wx.showToast({ title: "已添加", icon: "success" });
              this.loadCompetitors();
            })
            .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
            .then(() => wx.hideLoading());
        });
      }
    });
  },

  competitorMenu(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.competitors[index];
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
        let currentId = 0;
        try {
          const saved = wx.getStorageSync("lobster_current_template") || wx.getStorageSync("lobster_dh_template");
          currentId = saved && saved.id ? Number(saved.id) : 0;
        } catch (err) {
          currentId = 0;
        }
        this.setData({ templates: rows, currentTemplateId: currentId });
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