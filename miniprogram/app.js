const api = require("./utils/api");
const device = require("./utils/device");

App({
  globalData: {
    token: "",
    phone: "",
    deviceId: "",
    userId: 0,
    phoneBound: false
  },

  onLaunch() {
    this.globalData.deviceId = device.getDeviceId();
    this.restoreSession();
  },

  restoreSession() {
    const token = wx.getStorageSync("lobster_token") || "";
    const phone = wx.getStorageSync("lobster_phone") || "";
    const userId = Number(wx.getStorageSync("lobster_user_id") || 0);
    this.globalData.token = token;
    this.globalData.phone = phone;
    this.globalData.userId = userId;
    this.globalData.phoneBound = Boolean(token && phone);
  },

  saveSession(payload) {
    const token = payload.access_token || payload.token || "";
    if (token) {
      wx.setStorageSync("lobster_token", token);
      this.globalData.token = token;
    }
    if (payload.phone) {
      wx.setStorageSync("lobster_phone", payload.phone);
      this.globalData.phone = payload.phone;
    }
    if (payload.user_id) {
      wx.setStorageSync("lobster_user_id", payload.user_id);
      this.globalData.userId = payload.user_id;
    }
    this.globalData.phoneBound = Boolean(this.globalData.token && this.globalData.phone);
  },

  clearSession() {
    wx.removeStorageSync("lobster_token");
    wx.removeStorageSync("lobster_phone");
    wx.removeStorageSync("lobster_user_id");
    this.globalData.token = "";
    this.globalData.phone = "";
    this.globalData.userId = 0;
    this.globalData.phoneBound = false;
  },

  request(options) {
    const token = this.globalData.token || wx.getStorageSync("lobster_token") || "";
    return api.request(Object.assign({}, options, { token }));
  },

  loginWithWechat() {
    const deviceId = this.globalData.deviceId || device.getDeviceId();
    return new Promise((resolve, reject) => {
      wx.login({
        success: (res) => {
          if (!res.code) {
            reject(new Error("微信登录失败，未返回 code"));
            return;
          }
          api.request({
            method: "POST",
            url: "/api/mobile/wechat-login",
            data: {
              code: res.code,
              device_id: deviceId,
              platform: "wechat_miniprogram",
              display_name: "微信小程序"
            }
          })
            .then((data) => {
              this.saveSession(data);
              resolve(data);
            })
            .catch(reject);
        },
        fail: reject
      });
    });
  },

  bindPhone(phoneCode) {
    const deviceId = this.globalData.deviceId || device.getDeviceId();
    return this.request({
      method: "POST",
      url: "/api/mobile/devices/bind",
      data: {
        phone_code: phoneCode,
        device_id: deviceId,
        platform: "wechat_miniprogram",
        display_name: "微信小程序"
      }
    }).then((data) => {
      this.saveSession(data);
      return data;
    });
  }
});

