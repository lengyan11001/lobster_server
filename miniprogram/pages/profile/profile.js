const app = getApp();
const api = require("../../utils/api");

Page({
  data: {
    phone: "",
    phoneBound: false,
    avatarText: "AI",
    deviceId: "",
    onlineAvailable: false,
    onlineDevices: [],
    smsBindVisible: false,
    smsPhone: "",
    smsCode: "",
    smsSending: false,
    smsBinding: false,
    smsCountdown: 0
  },

  smsTimer: null,

  onShow() {
    app.restoreSession();
    this.refreshState();
    if (app.globalData.token) this.loadDevices();
  },

  onUnload() {
    this.clearSmsCountdown();
  },

  refreshState() {
    const phone = app.globalData.phone || "";
    this.setData({
      phone,
      phoneBound: Boolean(app.globalData.token && phone),
      avatarText: phone ? phone.slice(-2) : "AI",
      deviceId: app.globalData.deviceId
    });
  },

  loadDevices() {
    if (!app.globalData.token) {
      wx.showToast({ title: "请先登录绑定", icon: "none" });
      return;
    }
    wx.showLoading({ title: "刷新中", mask: true });
    app
      .request({ url: "/api/mobile/devices" })
      .then((data) => {
        this.setData({
          onlineAvailable: Boolean(data.online_available),
          onlineDevices: data.online_devices || []
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshState();
        if (data.needs_phone_bind || !app.globalData.phone) {
          wx.showToast({ title: "请授权手机号", icon: "none" });
          return;
        }
        wx.showToast({ title: "登录成功", icon: "success" });
        this.loadDevices();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      this.setData({ smsBindVisible: true });
      wx.showToast({ title: "微信取号失败，可用短信绑定", icon: "none" });
      return;
    }
    const bind = () => this.bindPhone(code);
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind();
  },

  bindPhone(code) {
    wx.showLoading({ title: "绑定中", mask: true });
    app
      .bindPhone(code)
      .then(() => {
        this.refreshState();
        this.setData({ smsBindVisible: false });
        wx.showToast({ title: "绑定成功", icon: "success" });
        this.loadDevices();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  showSmsBind() {
    this.setData({ smsBindVisible: true });
  },

  onSmsPhoneInput(evt) {
    this.setData({ smsPhone: evt.detail.value || "" });
  },

  onSmsCodeInput(evt) {
    this.setData({ smsCode: evt.detail.value || "" });
  },

  sendSmsCode() {
    if (this.data.smsSending || this.data.smsCountdown > 0) return;
    const phone = (this.data.smsPhone || "").trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      wx.showToast({ title: "手机号格式不对", icon: "none" });
      return;
    }
    const send = () => {
      this.setData({ smsSending: true });
      app
        .request({ method: "POST", url: "/api/mobile/sms/send", data: { phone } })
        .then(() => {
          wx.showToast({ title: "验证码已发送", icon: "success" });
          this.startSmsCountdown();
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
        .finally(() => this.setData({ smsSending: false }));
    };
    if (!app.globalData.token) {
      app.loginWithWechat().then(send).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    send();
  },

  startSmsCountdown() {
    this.clearSmsCountdown();
    this.setData({ smsCountdown: 60 });
    this.smsTimer = setInterval(() => {
      const next = Math.max(0, Number(this.data.smsCountdown || 0) - 1);
      this.setData({ smsCountdown: next });
      if (next <= 0) this.clearSmsCountdown();
    }, 1000);
  },

  clearSmsCountdown() {
    if (this.smsTimer) {
      clearInterval(this.smsTimer);
      this.smsTimer = null;
    }
  },

  bindBySms() {
    const phone = (this.data.smsPhone || "").trim();
    const code = (this.data.smsCode || "").trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      wx.showToast({ title: "手机号格式不对", icon: "none" });
      return;
    }
    if (!code) {
      wx.showToast({ title: "请输入短信验证码", icon: "none" });
      return;
    }
    const bind = () => {
      this.setData({ smsBinding: true });
      app
        .request({
          method: "POST",
          url: "/api/mobile/devices/bind",
          data: {
            phone,
            sms_code: code,
            device_id: app.globalData.deviceId,
            platform: "wechat_miniprogram",
            display_name: "微信小程序"
          }
        })
        .then((data) => {
          app.saveSession(data);
          this.refreshState();
          this.setData({ smsBindVisible: false, smsCode: "" });
          wx.showToast({ title: "绑定成功", icon: "success" });
          this.loadDevices();
        })
        .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
        .finally(() => this.setData({ smsBinding: false }));
    };
    if (!app.globalData.token) {
      app.loginWithWechat().then(bind).catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      return;
    }
    bind();
  },

  goDownloads() {
    wx.switchTab({ url: "/pages/downloads/downloads" });
  },

  logout() {
    wx.showModal({
      title: "退出登录",
      content: "退出后需要重新微信登录并授权手机号。",
      success: (res) => {
        if (!res.confirm) return;
        app.clearSession();
        this.refreshState();
        this.clearSmsCountdown();
        this.setData({
          onlineAvailable: false,
          onlineDevices: [],
          smsBindVisible: false,
          smsPhone: "",
          smsCode: "",
          smsCountdown: 0
        });
        wx.switchTab({ url: "/pages/index/index" });
      }
    });
  }
});
