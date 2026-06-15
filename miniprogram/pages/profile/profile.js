const app = getApp();
const api = require("../../utils/api");
const share = require("../../utils/share");

Page({
  data: {
    phone: "",
    phoneBound: false,
    wechatLoggedIn: false,
    avatarText: "AI",
    deviceId: "",
    onlineAvailable: false,
    onlineDevices: [],
    smsBindVisible: false,
    smsPhone: "",
    smsCode: "",
    smsSending: false,
    smsBinding: false,
    smsCountdown: 0,
    loginBusy: false,
    loginDebug: "",
    lastError: "",
    scheduledRuns: [],
    runsLoading: false
  },

  smsTimer: null,

  onShow() {
    share.showShareMenu();
    app.restoreSession();
    this.refreshState();
    if (app.globalData.token) {
      this.loadDevices();
      this.loadScheduledRuns();
    }
  },

  onUnload() {
    this.clearSmsCountdown();
  },

  refreshState() {
    const phone = app.globalData.phone || "";
    this.setData({
      phone,
      phoneBound: Boolean(app.globalData.token && phone),
      wechatLoggedIn: Boolean(app.globalData.token && !phone),
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
        const onlineDevices = (data.online_devices || []).filter((item) => item && item.online);
        this.setData({
          onlineAvailable: onlineDevices.length > 0,
          onlineDevices
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  loadScheduledRuns() {
    if (!app.globalData.token || this.data.runsLoading) return Promise.resolve();
    this.setData({ runsLoading: true });
    return app
      .request({ url: "/api/scheduled-tasks/runs?limit=30" })
      .then((data) => {
        const rows = (data.runs || []).map((row) => ({
          id: row.id,
          title: row.title || "定时任务",
          status: row.status || "",
          status_text: row.status === "completed" ? "完成" : (row.status === "failed" ? "失败" : "执行中"),
          time: String(row.created_at || "").replace("T", " ").slice(0, 16),
          summary: row.error || row.result_text || ""
        }));
        this.setData({ scheduledRuns: rows });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ runsLoading: false }));
  },

  openScheduledRun(evt) {
    const id = evt.currentTarget.dataset.id || "";
    if (!id) return;
    wx.navigateTo({ url: `/pages/work-detail/work-detail?run_id=${encodeURIComponent(id)}` });
  },

  setLoginDebug(text) {
    this.setData({ loginDebug: text || "" });
  },

  showLoginError(title, err) {
    const message = api.errorMessage(err);
    this.setData({ lastError: message });
    wx.showModal({
      title: title || "登录失败",
      content: message,
      showCancel: false
    });
  },

  login() {
    if (this.data.loginBusy) return;
    const deviceId = app.globalData.deviceId || wx.getStorageSync("lobster_device_id") || "";
    this.setData({ loginBusy: true, lastError: "" });
    this.setLoginDebug("1/3 正在调用微信登录...");
    wx.showLoading({ title: "登录中", mask: true });
    wx.login({
      success: (res) => {
        if (!res.code) {
          wx.hideLoading();
          this.setData({ loginBusy: false });
          this.setLoginDebug("微信未返回登录 code");
          this.showLoginError("微信登录失败", res.errMsg || "wx.login 未返回 code");
          return;
        }
        this.setLoginDebug("2/3 已拿到微信 code，正在请求 bhzn.top...");
        api
          .request({
            method: "POST",
            url: "/api/mobile/wechat-login",
            data: {
              code: res.code,
              device_id: deviceId,
              platform: "wechat_miniprogram",
              display_name: "微信小程序",
              ...app.refAgentPayload()
            }
          })
          .then((data) => {
            app.saveSession(data);
            this.refreshState();
            wx.hideLoading();
            this.setLoginDebug(data.needs_phone_bind || !app.globalData.phone ? "3/3 微信登录成功，还需要授权手机号。" : "3/3 微信登录成功。");
            if (data.needs_phone_bind || !app.globalData.phone) {
              wx.showToast({ title: "继续授权手机号", icon: "none" });
              return;
            }
            wx.showToast({ title: "登录成功", icon: "success" });
            this.loadDevices();
          })
          .catch((err) => {
            wx.hideLoading();
            this.setLoginDebug("请求登录接口失败");
            this.showLoginError("登录接口失败", err);
          })
          .finally(() => this.setData({ loginBusy: false }));
      },
      fail: (err) => {
        wx.hideLoading();
        this.setData({ loginBusy: false });
        this.setLoginDebug("wx.login 调用失败");
        this.showLoginError("微信登录失败", err);
      }
    });
  },

  onGetPhoneNumber(evt) {
    const code = evt.detail && evt.detail.code;
    if (!code) {
      this.setData({ smsBindVisible: true });
      this.showLoginError("手机号授权失败", (evt.detail && evt.detail.errMsg) || "微信取号失败，可用短信绑定");
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
        wx.hideLoading();
        wx.showToast({ title: "绑定成功", icon: "success" });
        this.loadDevices();
      })
      .catch((err) => {
        wx.hideLoading();
        this.showLoginError("绑定手机号失败", err);
      });
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
            display_name: "微信小程序",
            ...app.refAgentPayload()
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

  copyDiagnostics() {
    const lines = [
      `loginDebug=${this.data.loginDebug || ""}`,
      `lastError=${this.data.lastError || ""}`,
      `token=${app.globalData.token ? "yes" : "no"}`,
      `phone=${app.globalData.phone || ""}`,
      `deviceId=${app.globalData.deviceId || ""}`,
      `apiBase=https://bhzn.top`
    ];
    wx.setClipboardData({
      data: lines.join("\n"),
      success: () => wx.showToast({ title: "诊断已复制", icon: "success" })
    });
  },

  logout() {
    wx.showModal({
      title: "退出登录",
      content: "退出后需要重新微信登录并验证手机号。",
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
  },

  onShareAppMessage() {
    return share.appShare({
      title: "必火AI员工 - 绑定手机连接 OpenClaw",
      path: "/pages/index/index"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "必火AI员工 - 数字人和AI视频创作"
    });
  }
});
