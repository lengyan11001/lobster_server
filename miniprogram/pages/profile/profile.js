const app = getApp();
const api = require("../../utils/api");

Page({
  data: {
    phone: "",
    phoneBound: false,
    avatarText: "AI",
    deviceId: "",
    onlineAvailable: false,
    onlineDevices: []
  },

  onShow() {
    app.restoreSession();
    this.refreshState();
    if (app.globalData.token) this.loadDevices();
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
        this.setData({ onlineAvailable: false, onlineDevices: [] });
        wx.switchTab({ url: "/pages/index/index" });
      }
    });
  }
});

