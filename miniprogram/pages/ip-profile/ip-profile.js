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
    return this.loadInstallationId()
      .then(() => app.request({
        url: "/api/ip-content/personal-default",
        header: this.iidHeader(),
        timeout: 30000
      }))
      .then((data) => {
        const item = (data && data.item) || {};
        const requirements = (item && item.requirements) || {};
        const basic = (requirements.basic_profile && typeof requirements.basic_profile === "object") ? requirements.basic_profile : {};
        const business = (requirements.business_description && typeof requirements.business_description === "object") ? requirements.business_description : {};
        this._defaultItem = item;
        this._surveyId = Number(item.survey_id || (item.survey && item.survey.id) || 0) || null;
        const fields = FIELD_DEFS.map((def) => {
          let value = "";
          if (def.key === "name") value = requirements.profile_name || basic.name || "";
          else if (def.key === "product" || def.key === "target_customer" || def.key === "advantages") {
            value = requirements[def.key] || business[def.key] || "";
          } else {
            value = basic[def.key] || requirements[def.key] || "";
          }
          return Object.assign({}, def, { value: String(value || "") });
        });
        this.setData({ fields });
      })
      .then(() => {
        if (this._surveyId) return null;
        // 老数据可能只有资料调查记录、没有绑定到个人默认配置：
        // 复用最新一条，避免下一次保存又新增一条。
        return app.request({ url: "/api/ip-content/profile-surveys", timeout: 20000 })
          .then((data) => {
            const items = (data && data.items) || [];
            if (items.length) this._surveyId = Number(items[0].id) || null;
          })
          .catch(() => null);
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

  collectRequirements() {
    const basic = {};
    const business = {};
    this.data.fields.forEach((item) => {
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

  save() {
    if (this.data.saving) return;
    const requirements = this.collectRequirements();
    const hasAny = this.data.fields.some((item) => String(item.value || "").trim());
    if (!hasAny) {
      wx.showToast({ title: "请先填写资料", icon: "none" });
      return;
    }
    const existing = this._defaultItem || {};
    const surveyId = Number(this._surveyId || 0) || 0;
    const surveyName = "个人资料调查";
    const header = this.iidHeader();
    this.setData({ saving: true });
    const surveyBody = { name: surveyName, requirements, meta: { source: "h5_personal_profile" } };
    const createSurvey = () => app.request({
      method: "POST",
      url: "/api/ip-content/profile-surveys",
      header,
      data: surveyBody,
      timeout: 60000
    });
    const saveSurvey = surveyId
      ? app.request({
          method: "PATCH",
          url: "/api/ip-content/profile-surveys/" + surveyId,
          header,
          data: surveyBody,
          timeout: 60000
        }).catch((err) => {
          // 记录被删过就重新建一条，避免保存卡死
          if (String(api.errorMessage(err)).indexOf("不存在") >= 0) return createSurvey();
          throw err;
        })
      : createSurvey();
    saveSurvey
      .then((data) => {
        const item = (data && data.item) || {};
        const savedSurveyId = Number(item.id || surveyId || 0) || null;
        this._surveyId = savedSurveyId;
        // 与 H5/online 一致：资料调查只保留一条，再更新个人默认配置，不新建模板
        return app.request({
          method: "PUT",
          url: "/api/ip-content/personal-default",
          header,
          data: {
            name: existing.name || "个人默认配置",
            keyword_ids: Array.isArray(existing.keyword_ids) ? existing.keyword_ids : [],
            competitor_ids: Array.isArray(existing.competitor_ids) ? existing.competitor_ids : [],
            memory_doc_ids: Array.isArray(existing.memory_doc_ids) ? existing.memory_doc_ids : [],
            memory_docs: Array.isArray(existing.memory_docs) ? existing.memory_docs : [],
            requirements,
            survey_id: savedSurveyId,
            meta: Object.assign({}, existing.meta || {}, { source: "h5_personal_profile", survey_id: savedSurveyId })
          },
          timeout: 60000
        });
      })
      .then((data) => {
        this._defaultItem = (data && data.item) || this._defaultItem;
        wx.showToast({ title: "已保存", icon: "success" });
        this.loadProfile();
      })
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