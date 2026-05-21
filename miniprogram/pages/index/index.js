const app = getApp();
const api = require("../../utils/api");
const avatarTemplates = require("../../utils/digital_avatar_templates");
const staticAssets = require("../../utils/static_assets");

const CAPABILITIES = [
  { id: "goal.video.pipeline", name: "创意成片" },
  { id: "hifly.video.create_by_tts", name: "必火数字人" }
];

function uniqRows(rows, keyName) {
  const out = [];
  const seen = {};
  (rows || []).forEach((row) => {
    const value = String((row && row[keyName]) || "").trim();
    if (!value || seen[value]) return;
    seen[value] = true;
    out.push(row);
  });
  return out;
}

function normalizeAvatars(...groups) {
  return uniqRows(groups.flat().map((row) => ({
    avatar: String((row && row.avatar) || "").trim(),
    title: (row && (row.title || row.name || row.avatar)) || ""
  })), "avatar");
}

function normalizeVoices(...groups) {
  const rows = [];
  groups.flat().forEach((row) => {
    const styles = Array.isArray(row && row.styles) && row.styles.length ? row.styles : [row];
    styles.forEach((style) => {
      const voice = String((style && style.voice) || (row && row.voice) || "").trim();
      if (!voice) return;
      const base = (row && (row.title || row.name || row.voice)) || voice;
      const label = style && style.label && style.label !== "默认风格" ? `${base} - ${style.label}` : base;
      rows.push({ voice, title: label });
    });
  });
  return uniqRows(rows, "voice");
}

function capabilityName(id) {
  const row = CAPABILITIES.find((item) => item.id === id);
  return row ? row.name : "定时任务";
}

Page({
  data: {
    heroBgUrl: staticAssets.staticAssetUrl("openclaw-hero-bg.jpg"),
    lobsterUrl: staticAssets.staticAssetUrl("openclaw-lobster.png"),
    aiImageBgUrl: staticAssets.staticAssetUrl("ai-image-bg.jpg"),
    aiVideoBgUrl: staticAssets.staticAssetUrl("ai-video-bg.jpg"),
    phoneBound: false,
    phone: "",
    accountText: "点击功能时授权",
    authPanelVisible: false,
    smsBindVisible: false,
    authHint: "使用前需要微信登录并绑定手机号，用来关联你的电脑端 online。",
    smsPhone: "",
    smsCode: "",
    smsSending: false,
    smsBinding: false,
    smsCountdown: 0,
    onlineText: "点击开始后检查 online",
    onlineDevices: [],
    selectedInstallationId: "",
    taskExpanded: false,
    capabilities: CAPABILITIES,
    taskAbility: "goal.video.pipeline",
    scheduleType: "once",
    intervalMinutes: "60",
    taskTitle: "",
    submittingTask: false,
    avatarRows: [],
    voiceRows: [],
    avatarIndex: 0,
    voiceIndex: 0,
    selectedAvatarTitle: "",
    selectedVoiceTitle: "",
    hiflyLoading: false,
    hiflyLoaded: false,
    publicAvatarTemplates: [],
    avatarTemplatesLoading: false,
    quickMessages: [
      { mark: "图", title: "生成图片", text: "帮我生成一张图片，画面内容是：" },
      { mark: "视", title: "生成视频", text: "帮我生成一个6秒宣传视频，画面内容是：" },
      { mark: "传", title: "图生视频", text: "用这个图片，提示词：" },
      { mark: "T", title: "爆款TVC", text: "用爆款TVC生成一个视频" },
      { mark: "发", title: "发布素材", text: "把某素材发布到 平台 账号" },
      { mark: "文", title: "朋友圈文案", text: "根据我的记忆，写一条50字以内的朋友圈文案" }
    ]
  },

  smsTimer: null,

  onShow() {
    app.restoreSession();
    this.refreshState();
    if (app.globalData.token && app.globalData.phone) {
      this.loadOnlineStatus(false);
    }
    this.loadPublicAvatarTemplates();
  },

  onUnload() {
    this.clearSmsCountdown();
  },

  refreshState() {
    const phone = app.globalData.phone || "";
    const bound = Boolean(app.globalData.token && phone);
    this.setData({
      phone,
      phoneBound: bound,
      accountText: bound ? `已绑定 ${phone}` : "点击功能时授权"
    });
  },

  showAuthPanel(hint) {
    this.refreshState();
    if (this.data.phoneBound) return false;
    this.setData({
      authPanelVisible: true,
      smsBindVisible: this.data.smsBindVisible,
      authHint: hint || "使用前需要微信登录并绑定手机号，用来关联你的电脑端 online。"
    });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    return app
      .loginWithWechat()
      .then((data) => {
        this.refreshState();
        if (!data.needs_phone_bind && app.globalData.phone) {
          this.setData({ authPanelVisible: false });
          this.loadOnlineStatus(false);
          wx.showToast({ title: "登录成功", icon: "success" });
        } else {
          wx.showToast({ title: "请授权手机号", icon: "none" });
        }
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
        this.setData({ authPanelVisible: false, smsBindVisible: false });
        this.loadOnlineStatus(false);
        wx.showToast({ title: "绑定成功", icon: "success" });
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
          this.setData({ authPanelVisible: false, smsBindVisible: false, smsCode: "" });
          this.loadOnlineStatus(false);
          wx.showToast({ title: "绑定成功", icon: "success" });
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

  toggleTaskPanel() {
    const next = !this.data.taskExpanded;
    this.setData({ taskExpanded: next });
    if (!next) return;
    if (this.showAuthPanel("下发定时任务前需要绑定手机号，用来找到你的电脑端 online。")) return;
    this.loadOnlineStatus(true);
    if (this.data.taskAbility === "hifly.video.create_by_tts") this.loadHiflyLibraries();
  },

  setTaskAbility(evt) {
    const ability = evt.currentTarget.dataset.ability || "goal.video.pipeline";
    this.setData({ taskAbility: ability });
    if (ability === "hifly.video.create_by_tts" && this.data.phoneBound) {
      this.loadHiflyLibraries();
    }
  },

  setScheduleType(evt) {
    this.setData({ scheduleType: evt.currentTarget.dataset.type || "once" });
  },

  onTaskTitleInput(evt) {
    this.setData({ taskTitle: evt.detail.value || "" });
  },

  onIntervalInput(evt) {
    this.setData({ intervalMinutes: evt.detail.value || "" });
  },

  onAvatarChange(evt) {
    const index = Number(evt.detail.value || 0);
    const row = this.data.avatarRows[index] || {};
    this.setData({ avatarIndex: index, selectedAvatarTitle: row.title || "" });
  },

  onVoiceChange(evt) {
    const index = Number(evt.detail.value || 0);
    const row = this.data.voiceRows[index] || {};
    this.setData({ voiceIndex: index, selectedVoiceTitle: row.title || "" });
  },

  autoPickDigital() {
    this.setData({
      avatarIndex: 0,
      voiceIndex: 0,
      selectedAvatarTitle: (this.data.avatarRows[0] && this.data.avatarRows[0].title) || "",
      selectedVoiceTitle: (this.data.voiceRows[0] && this.data.voiceRows[0].title) || ""
    });
    if (!this.data.hiflyLoaded) this.loadHiflyLibraries();
  },

  loadOnlineStatus(showToast) {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ onlineText: "正在检查 online..." });
    return app
      .request({ url: "/api/h5-chat/devices/status" })
      .then((data) => {
        const devices = Array.isArray(data.devices) ? data.devices : [];
        const selected = devices.find((d) => d.online && d.installation_id) || devices.find((d) => d.installation_id) || {};
        const onlineCount = devices.filter((d) => d.online).length;
        this.setData({
          onlineDevices: devices,
          selectedInstallationId: selected.installation_id || "",
          onlineText: data.online ? `online 已连接 ${onlineCount}/${devices.length || onlineCount}` : "online 暂未在线"
        });
      })
      .catch((err) => {
        this.setData({ onlineText: "online 状态获取失败" });
        if (showToast) wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  loadHiflyLibraries() {
    if (this.data.hiflyLoaded || this.data.hiflyLoading || !app.globalData.token) return Promise.resolve();
    this.setData({ hiflyLoading: true });
    return Promise.all([
      app.request({ url: "/api/hifly/my/avatar/list?page=1&size=100" }).catch(() => ({ items: [] })),
      app.request({ method: "POST", url: "/api/hifly/avatar/library", data: { page: 1, size: 100, include_mine: true } }).catch(() => ({ public: [] })),
      app.request({ url: "/api/hifly/my/voice/list?page=1&size=100" }).catch(() => ({ items: [] })),
      app.request({ method: "POST", url: "/api/hifly/voice/library", data: {} }).catch(() => ({ public: [] }))
    ])
      .then(([myAvatar, publicAvatar, myVoice, publicVoice]) => {
        const avatarRows = normalizeAvatars(myAvatar.items || [], publicAvatar.public || []);
        const voiceRows = normalizeVoices(myVoice.items || [], publicVoice.public || []);
        this.setData({
          avatarRows,
          voiceRows,
          avatarIndex: 0,
          voiceIndex: 0,
          selectedAvatarTitle: (avatarRows[0] && avatarRows[0].title) || "",
          selectedVoiceTitle: (voiceRows[0] && voiceRows[0].title) || "",
          hiflyLoaded: true
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ hiflyLoading: false }));
  },

  createScheduledTask() {
    if (this.showAuthPanel("下发定时任务前需要绑定手机号，用来找到你的电脑端 online。")) return;
    const installationId = this.data.selectedInstallationId;
    if (!installationId) {
      this.loadOnlineStatus(true).then(() => {
        if (!this.data.selectedInstallationId) wx.showToast({ title: "未检测到本地 online 设备", icon: "none" });
      });
      return;
    }

    const ability = this.data.taskAbility;
    const capPayload = {};
    if (ability === "hifly.video.create_by_tts") {
      const avatar = this.data.avatarRows[this.data.avatarIndex];
      const voice = this.data.voiceRows[this.data.voiceIndex];
      if (!avatar || !avatar.avatar) {
        wx.showToast({ title: "请选择数字人", icon: "none" });
        return;
      }
      if (!voice || !voice.voice) {
        wx.showToast({ title: "请选择声音", icon: "none" });
        return;
      }
      capPayload.avatar = avatar.avatar;
      capPayload.voice = voice.voice;
    }

    const interval = parseInt(this.data.intervalMinutes || "60", 10);
    const body = {
      title: (this.data.taskTitle || "").trim() || capabilityName(ability),
      task_kind: "capability",
      content: `定时调用能力 ${ability}`,
      payload: { capability_id: ability, payload: capPayload },
      schedule_type: this.data.scheduleType || "once",
      interval_seconds: Math.max(60, (Number.isNaN(interval) ? 60 : interval) * 60),
      installation_ids: [installationId]
    };

    this.setData({ submittingTask: true });
    app
      .request({
        method: "POST",
        url: "/api/scheduled-tasks/tasks",
        data: body,
        header: { "X-Installation-Id": installationId }
      })
      .then(() => {
        wx.showToast({ title: "任务已下发", icon: "success" });
        this.setData({ taskExpanded: false });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ submittingTask: false }));
  },

  goMessages() {
    wx.navigateTo({ url: "/pages/assistant/assistant" });
  },

  loadPublicAvatarTemplates() {
    if (this.data.avatarTemplatesLoading || this.data.publicAvatarTemplates.length) return Promise.resolve();
    this.setData({ avatarTemplatesLoading: true });
    return app
      .request({ method: "POST", url: "/api/hifly/avatar/library", data: { page: 1, size: 100 } })
      .then((data) => {
        const rows = avatarTemplates.pickPublicAvatarTemplates(data.public || [], 20);
        this.setData({ publicAvatarTemplates: rows });
        wx.setStorageSync("lobster_public_avatar_templates", rows);
      })
      .catch(() => {
        const cached = wx.getStorageSync("lobster_public_avatar_templates") || [];
        if (cached.length) this.setData({ publicAvatarTemplates: avatarTemplates.pickPublicAvatarTemplates(cached, 20) });
      })
      .finally(() => this.setData({ avatarTemplatesLoading: false }));
  },

  goAssistant() {
    wx.navigateTo({ url: "/pages/assistant/assistant" });
  },

  goFeature(evt) {
    const prompt = evt.currentTarget.dataset.prompt || "";
    if (prompt) wx.setStorageSync("lobster_message_prefill", prompt);
    wx.navigateTo({ url: "/pages/assistant/assistant" });
  },

  goDigital() {
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  goAvatarTemplates() {
    wx.navigateTo({ url: "/pages/avatar-templates/avatar-templates" });
  },

  selectPublicAvatarTemplate(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.publicAvatarTemplates[index];
    if (!avatarTemplates.storeDigitalAvatarPrefill(item)) {
      wx.showToast({ title: "数字人模板不可用", icon: "none" });
      return;
    }
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  goProfile() {
    wx.switchTab({ url: "/pages/profile/profile" });
  },

  quickMessage(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.quickMessages[index];
    if (item && item.text) wx.setStorageSync("lobster_message_prefill", item.text);
    this.goMessages();
  }
});
