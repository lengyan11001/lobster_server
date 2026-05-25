const api = require("./utils/api");
const device = require("./utils/device");

App({
  globalData: {
    token: "",
    phone: "",
    deviceId: "",
    userId: 0,
    phoneBound: false,
    refAgentUserId: 0
  },

  onLaunch(options) {
    this.globalData.deviceId = device.getDeviceId();
    this.captureShareRef(options);
    this.restoreSession();
  },

  onShow(options) {
    this.captureShareRef(options);
    if (this.globalData.token) this.syncShareRefBinding();
  },

  restoreSession() {
    const token = wx.getStorageSync("lobster_token") || "";
    const phone = wx.getStorageSync("lobster_phone") || "";
    const userId = Number(wx.getStorageSync("lobster_user_id") || 0);
    const refAgentUserId = Number(wx.getStorageSync("lobster_ref_agent_user_id") || 0);
    this.globalData.token = token;
    this.globalData.phone = phone;
    this.globalData.userId = userId;
    this.globalData.refAgentUserId = refAgentUserId;
    this.globalData.phoneBound = Boolean(token && phone);
  },

  captureShareRef(options) {
    const query = (options && options.query) || {};
    const sceneRaw = query.scene ? decodeURIComponent(query.scene) : "";
    const sceneParts = {};
    if (sceneRaw) {
      sceneRaw.split("&").forEach((part) => {
        const pair = part.split("=");
        if (pair[0]) sceneParts[pair[0]] = pair.slice(1).join("=");
      });
    }
    const raw = query.ref_agent || query.agent_id || query.parent_user_id || sceneParts.ref_agent || sceneParts.agent_id || sceneParts.parent_user_id || "";
    const refId = parseInt(raw, 10);
    if (!refId || refId <= 0) return;
    const ownId = Number(this.globalData.userId || wx.getStorageSync("lobster_user_id") || 0);
    if (ownId && ownId === refId) return;
    wx.setStorageSync("lobster_ref_agent_user_id", refId);
    this.globalData.refAgentUserId = refId;
  },

  shareQuery(extra) {
    const parts = [];
    const userId = Number(this.globalData.userId || wx.getStorageSync("lobster_user_id") || 0);
    if (userId > 0) parts.push(`ref_agent=${encodeURIComponent(userId)}`);
    const opts = extra || {};
    Object.keys(opts).forEach((key) => {
      const value = opts[key];
      if (value === undefined || value === null || value === "") return;
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
    });
    return parts.join("&");
  },

  sharePath(path, extra) {
    const base = path || "/pages/index/index";
    const query = this.shareQuery(extra);
    if (!query) return base;
    return base.indexOf("?") >= 0 ? `${base}&${query}` : `${base}?${query}`;
  },

  refAgentPayload() {
    const refId = Number(this.globalData.refAgentUserId || wx.getStorageSync("lobster_ref_agent_user_id") || 0);
    const ownId = Number(this.globalData.userId || wx.getStorageSync("lobster_user_id") || 0);
    if (!refId || refId <= 0 || (ownId && ownId === refId)) return {};
    return { ref_agent_user_id: refId };
  },

  syncShareRefBinding() {
    const payload = this.refAgentPayload();
    if (!payload.ref_agent_user_id || !this.globalData.token) return Promise.resolve({ ok: false });
    return this.request({
      method: "POST",
      url: "/api/mobile/share-bind",
      data: payload
    }).then((data) => {
      if (data && data.ok) {
        wx.removeStorageSync("lobster_ref_agent_user_id");
        this.globalData.refAgentUserId = 0;
      }
      return data;
    }).catch(() => ({ ok: false }));
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
              display_name: "微信小程序",
              ...this.refAgentPayload()
            }
          })
            .then((data) => {
              this.saveSession(data);
              this.syncShareRefBinding();
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
        display_name: "微信小程序",
        ...this.refAgentPayload()
      }
    }).then((data) => {
      this.saveSession(data);
      this.syncShareRefBinding();
      return data;
    });
  }
});
