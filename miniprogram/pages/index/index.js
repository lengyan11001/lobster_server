const app = getApp();
const api = require("../../utils/api");

Page({
  data: {
    phoneBound: false,
    phone: "",
    phoneInput: "",
    phoneCheckText: "",
    statusText: "正在检查登录状态..."
  },

  onShow() {
    app.restoreSession();
    this.refreshState();
    if (!app.globalData.token) this.login();
  },

  refreshState() {
    const phone = app.globalData.phone || "";
    const bound = Boolean(app.globalData.token && phone);
    this.setData({
      phone,
      phoneBound: bound,
      statusText: bound ? `已绑定 ${phone}` : "未绑定 online 手机号"
    });
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshState();
        if (data.needs_phone_bind) {
          wx.showToast({ title: "请授权手机号", icon: "none" });
        } else {
          wx.showToast({ title: "登录成功", icon: "success" });
        }
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      wx.showToast({ title: "需要授权手机号才能绑定", icon: "none" });
      return;
    }
    if (!app.globalData.token) {
      app.loginWithWechat().then(() => this.bindPhone(code)).catch((err) => {
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
      return;
    }
    this.bindPhone(code);
  },

  bindPhone(code) {
    wx.showLoading({ title: "绑定中", mask: true });
    app
      .bindPhone(code)
      .then(() => {
        this.refreshState();
        wx.showToast({ title: "绑定成功", icon: "success" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onPhoneInput(evt) {
    this.setData({ phoneInput: evt.detail.value || "" });
  },

  checkPhone() {
    const phone = (this.data.phoneInput || "").trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      wx.showToast({ title: "手机号格式不对", icon: "none" });
      return;
    }
    wx.showLoading({ title: "检查中", mask: true });
    api
      .request({ url: `/api/mobile/phone/status?phone=${encodeURIComponent(phone)}` })
      .then((data) => {
        this.setData({ phoneCheckText: data.message || (data.registered ? "已开通 online" : "没有 online 版本") });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  goDownloads() {
    wx.switchTab({ url: "/pages/downloads/downloads" });
  }
});

