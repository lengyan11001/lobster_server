const app = getApp();
const api = require("../../utils/api");

Page({
  data: {
    appId: "",
    appSecret: "",
    configured: false,
    appIdMasked: "",
    secretMask: "",
    nickname: "",
    serverIps: [],
    loading: true,
    saving: false,
    statusText: ""
  },

  onShow() {
    this.loadConfig();
  },

  loadConfig() {
    if (!app.globalData.token) {
      this.setData({ loading: false, statusText: "请先登录" });
      return;
    }
    this.setData({ loading: true });
    return app.request({ url: "/api/wechat-oa/config", timeout: 20000 })
      .then((data) => {
        this.setData({
          appId: String((data && data.app_id) || ""),
          configured: Boolean(data && data.configured),
          appIdMasked: String((data && data.app_id_masked) || ""),
          secretMask: String((data && data.secret_mask) || ""),
          nickname: String((data && data.nickname) || ""),
          serverIps: ((data && data.server_ips) || []).map((ip) => String(ip || "")).filter(Boolean),
          statusText: ""
        });
      })
      .catch((err) => {
        this.setData({ statusText: api.errorMessage(err) || "读取配置失败" });
      })
      .then(() => this.setData({ loading: false }));
  },

  onAppId(e) { this.setData({ appId: e.detail.value || "" }); },
  onAppSecret(e) { this.setData({ appSecret: e.detail.value || "" }); },

  copyIp(e) {
    const ip = String((e.currentTarget.dataset && e.currentTarget.dataset.ip) || "").trim();
    if (!ip) return;
    wx.setClipboardData({ data: ip, success: () => wx.showToast({ title: "IP 已复制", icon: "success" }) });
  },

  save() {
    const appId = String(this.data.appId || "").trim();
    const appSecret = String(this.data.appSecret || "").trim();
    if (!appId) return wx.showToast({ title: "先填 AppID", icon: "none" });
    if (!appSecret) return wx.showToast({ title: "先填 AppSecret", icon: "none" });
    if (this.data.saving) return;
    this.setData({ saving: true, statusText: "正在验证公众号配置…" });
    wx.showLoading({ title: "验证中", mask: true });
    app.request({
      method: "POST",
      url: "/api/wechat-oa/config",
      data: { app_id: appId, app_secret: appSecret },
      timeout: 30000
    })
      .then((data) => {
        this.setData({
          configured: true,
          appId: String((data && data.app_id) || appId),
          appIdMasked: String((data && data.app_id_masked) || ""),
          secretMask: String((data && data.secret_mask) || ""),
          nickname: String((data && data.nickname) || ""),
          serverIps: ((data && data.server_ips) || this.data.serverIps || []).map((ip) => String(ip || "")).filter(Boolean),
          appSecret: "",
          statusText: "已保存并验证通过"
        });
        wx.showToast({ title: "公众号已配置", icon: "success" });
      })
      .catch((err) => {
        const msg = api.errorMessage(err) || "配置失败";
        this.setData({ statusText: msg });
        wx.showModal({ title: "配置失败", content: msg, showCancel: false });
      })
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false });
      });
  }
});
