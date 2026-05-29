const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");
const share = require("../../utils/share");

const URL_RE = /https?:\/\/[^\s<>"']+/gi;
const MEDIA_EXTS = {
  ".mp4": "video",
  ".webm": "video",
  ".mov": "video",
  ".m4v": "video",
  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".webp": "image",
  ".gif": "image",
  ".mp3": "audio",
  ".wav": "audio",
  ".m4a": "audio"
};
const MOBILE_UPLOAD_TITLE = "【手机上传素材】";
const MOBILE_UPLOAD_BLOCK_RE = /\n*【手机上传素材】\n[\s\S]*$/;
const MAX_ATTACH_IMAGES = 3;

function mediaTypeFromUrl(url) {
  const clean = String(url || "").split("?")[0].toLowerCase();
  const match = clean.match(/\.[a-z0-9]+$/);
  return (match && MEDIA_EXTS[match[0]]) || "media";
}

function filenameFromUrl(url) {
  const path = String(url || "").split("?")[0].split("#")[0];
  return decodeURIComponent(path.split("/").pop() || "生成素材");
}

function mediaProxyUrl(url, disposition) {
  const token = app.globalData.token || wx.getStorageSync("lobster_token") || "";
  const params = [
    `url=${encodeURIComponent(url)}`,
    `disposition=${encodeURIComponent(disposition || "inline")}`,
    `filename=${encodeURIComponent(filenameFromUrl(url))}`
  ];
  if (token) params.push(`token=${encodeURIComponent(token)}`);
  return api.buildUrl(`/api/h5-chat/media?${params.join("&")}`);
}

function directDownloadUrl(url) {
  const clean = String(url || "").trim();
  return /^https:\/\//i.test(clean) ? clean : "";
}

function stripUploadBlock(text) {
  const raw = String(text || "");
  if (raw.indexOf(MOBILE_UPLOAD_TITLE) < 0) return raw;
  return raw.replace(MOBILE_UPLOAD_BLOCK_RE, "").trim() || "已上传图片";
}

function extractUploadedMediaFromContent(content) {
  const raw = String(content || "");
  const idx = raw.indexOf(MOBILE_UPLOAD_TITLE);
  if (idx < 0) return [];
  const block = raw.slice(idx);
  const seen = {};
  const out = [];
  block.split(/\n+/).forEach((line, index) => {
    const urlMatch = String(line || "").match(/URL:\s*(https?:\/\/\S+)/i);
    if (!urlMatch) return;
    const url = urlMatch[1].replace(/[，。；;)]+$/, "");
    if (!url || seen[url]) return;
    seen[url] = true;
    out.push({
      id: `upload_${index}_${url}`,
      title: "上传图片",
      media_type: "image",
      url,
      preview_url: directDownloadUrl(url) || mediaProxyUrl(url, "inline"),
      download_url: directDownloadUrl(url) || mediaProxyUrl(url, "attachment"),
      proxy_preview_url: mediaProxyUrl(url, "inline"),
      proxy_download_url: mediaProxyUrl(url, "attachment")
    });
  });
  return out;
}

function extractMedia(replyText, events) {
  const urls = [];
  const seen = {};
  let publishDraft = null;
  function add(url) {
    const clean = String(url || "").trim().replace(/[，。；;)]+$/, "");
    if (!/^https?:\/\//i.test(clean) || seen[clean]) return;
    seen[clean] = true;
    urls.push(clean);
  }
  String(replyText || "").replace(URL_RE, (url) => {
    add(url);
    return url;
  });
  (events || []).forEach((ev) => {
    const payload = (ev && ev.payload) || {};
    if (payload.publish_draft && typeof payload.publish_draft === "object") {
      publishDraft = Object.assign({}, payload.publish_draft, {
        run_id: payload.publish_draft.run_id || payload.run_id || ""
      });
    }
    const text = JSON.stringify(payload);
    text.replace(URL_RE, (url) => {
      add(url);
      return url;
    });
  });
  return urls.map((url, index) => {
    const mediaType = mediaTypeFromUrl(url);
    const filename = filenameFromUrl(url);
    return {
      id: `${index}_${url}`,
      title: filename,
      media_type: mediaType,
      url,
      publish_draft: publishDraft,
      run_id: publishDraft && publishDraft.run_id,
      publish_status: publishDraft ? String(publishDraft.status || "").toLowerCase() : "",
      publish_status_label: publishStatusLabel(publishDraft && publishDraft.status),
      can_publish: !!publishDraft && !["published", "pending", "processing"].includes(String(publishDraft.status || "").toLowerCase()),
      publish_target_label: publishDraft ? [publishDraft.platform_name || publishDraft.platform, publishDraft.account_nickname || publishDraft.account_id].filter(Boolean).join(" · ") : "",
      preview_url: directDownloadUrl(url) || mediaProxyUrl(url, "inline"),
      download_url: directDownloadUrl(url) || mediaProxyUrl(url, "attachment"),
      proxy_preview_url: mediaProxyUrl(url, "inline"),
      proxy_download_url: mediaProxyUrl(url, "attachment")
    };
  });
}

function publishStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  return {
    ready: "待发布",
    draft: "待发布",
    pending: "等待发布",
    processing: "发布中",
    published: "已发布",
    failed: "发布失败"
  }[s] || "";
}

function normalizeHistory(raw) {
  const row = raw.message || {};
  const events = raw.events || [];
  return Object.assign({}, row, {
    content: stripUploadBlock(row.content || ""),
    events,
    mediaItems: extractUploadedMediaFromContent(row.content || "").concat(extractMedia(row.reply_text || "", events))
  });
}

Page({
  data: {
    phoneBound: false,
    authPanelVisible: false,
    smsBindVisible: false,
    authHint: "发送消息前需要微信登录并绑定手机号，用来关联你的电脑端 online。",
    smsPhone: "",
    smsCode: "",
    smsSending: false,
    smsBinding: false,
    smsCountdown: 0,
    loading: false,
    sending: false,
    uploadingImage: false,
    inputText: "",
    attachedImages: [],
    messages: [],
    onlineAvailable: false,
    onlineText: "检查 online 状态中...",
    composerBottomPadding: 180
  },

  smsTimer: null,
  scrollTimer: null,
  messagePollTimer: null,

  onShow() {
    share.showShareMenu();
    app.restoreSession();
    const prefill = wx.getStorageSync("lobster_message_prefill") || "";
    if (prefill) wx.removeStorageSync("lobster_message_prefill");
    this.setData({
      phoneBound: Boolean(app.globalData.token && app.globalData.phone),
      authPanelVisible: false,
      inputText: prefill || this.data.inputText
    });
    if (this.data.phoneBound) {
      this.loadMessages({ forceScroll: true });
      this.loadOnlineStatus();
      this.startMessagePolling();
    } else {
      this.stopMessagePolling();
    }
    this.updateComposerPadding();
  },

  onPullDownRefresh() {
    Promise.all([this.loadMessages({ forceScroll: true }), this.loadOnlineStatus()]).finally(() => wx.stopPullDownRefresh());
  },

  onHide() {
    this.stopMessagePolling();
  },

  onUnload() {
    this.clearSmsCountdown();
    this.stopMessagePolling();
    if (this.scrollTimer) {
      clearTimeout(this.scrollTimer);
      this.scrollTimer = null;
    }
  },

  updateComposerPadding(callback) {
    wx.nextTick(() => {
      const query = wx.createSelectorQuery().in(this);
      query
        .select(".composer")
        .boundingClientRect((rect) => {
          const height = rect && rect.height ? Math.ceil(rect.height) : 180;
          const nextPadding = height + 28;
          if (Math.abs(Number(this.data.composerBottomPadding || 0) - nextPadding) > 2) {
            this.setData({ composerBottomPadding: nextPadding }, () => {
              if (typeof callback === "function") callback();
            });
            return;
          }
          if (typeof callback === "function") callback();
        })
        .exec();
    });
  },

  scrollToBottom(delay) {
    if (this.scrollTimer) clearTimeout(this.scrollTimer);
    this.scrollTimer = setTimeout(() => {
      wx.pageScrollTo({
        selector: "#message-bottom",
        duration: 220,
        fail: () => wx.pageScrollTo({ scrollTop: 999999, duration: 220 })
      });
    }, typeof delay === "number" ? delay : 80);
  },

  refreshLayoutToBottom(delay) {
    this.updateComposerPadding(() => this.scrollToBottom(delay));
  },

  startMessagePolling() {
    this.stopMessagePolling();
    if (!this.data.phoneBound) return;
    this.messagePollTimer = setInterval(() => {
      if (!this.data.phoneBound || this.data.sending) return;
      this.loadMessages({ silent: true, scrollOnChange: true });
    }, 5000);
  },

  stopMessagePolling() {
    if (this.messagePollTimer) {
      clearInterval(this.messagePollTimer);
      this.messagePollTimer = null;
    }
  },

  messagesDigest(messages) {
    return (messages || [])
      .map((item) =>
        [
          item.id,
          item.status,
          item.reply_text || "",
          item.error || "",
          (item.events || []).length,
          (item.mediaItems || []).length
        ].join("|")
      )
      .join("||");
  },

  loadOnlineStatus() {
    if (!app.globalData.token) return Promise.resolve();
    return app
      .request({ url: "/api/h5-chat/devices/status" })
      .then((data) => {
        const online = Boolean(data.online);
        this.setData({
          onlineAvailable: online,
          onlineText: online ? "online 已连接" : "online 暂未在线"
        });
      })
      .catch(() => this.setData({ onlineAvailable: false, onlineText: "online 状态获取失败" }));
  },

  loadMessages(options) {
    const opts = options || {};
    if (!app.globalData.token) {
      this.setData({ phoneBound: false, loading: false, messages: [] });
      this.stopMessagePolling();
      return Promise.resolve();
    }
    const beforeDigest = this.messagesDigest(this.data.messages);
    if (!opts.silent) this.setData({ loading: true });
    return app
      .request({ url: "/api/h5-chat/messages?limit=40" })
      .then((data) => {
        const messages = (data.messages || []).map(normalizeHistory);
        const changed = beforeDigest !== this.messagesDigest(messages);
        this.setData({ messages, phoneBound: true }, () => {
          if (opts.forceScroll || (changed && opts.scrollOnChange !== false)) {
            this.refreshLayoutToBottom(80);
          } else {
            this.updateComposerPadding();
          }
        });
      })
      .catch((err) => {
        const message = api.errorMessage(err);
        if (/401|403|未绑定/.test(message)) {
          this.setData({ phoneBound: false });
          this.stopMessagePolling();
        }
        if (!opts.silent) wx.showToast({ title: message, icon: "none" });
      })
      .finally(() => {
        if (!opts.silent) this.setData({ loading: false });
      });
  },

  onInput(evt) {
    this.setData({ inputText: evt.detail.value || "" }, () => this.updateComposerPadding());
  },

  chooseUploadImage() {
    if (this.showAuthPanel("上传图片前需要微信登录并绑定手机号，用来关联你的电脑端 online。")) return;
    const remain = MAX_ATTACH_IMAGES - (this.data.attachedImages || []).length;
    if (remain <= 0) {
      wx.showToast({ title: `最多上传${MAX_ATTACH_IMAGES}张`, icon: "none" });
      return;
    }
    wx.chooseMedia({
      count: remain,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      sizeType: ["compressed"],
      success: (res) => {
        const files = (res.tempFiles || [])
          .map((item) => item.tempFilePath || item.path || "")
          .filter(Boolean);
        if (files.length) this.uploadImages(files);
      }
    });
  },

  uploadImages(filePaths) {
    if (!filePaths || !filePaths.length) return;
    this.setData({ uploadingImage: true });
    const uploaded = [];
    let chain = Promise.resolve();
    filePaths.forEach((filePath) => {
      chain = chain.then(() => this.uploadOneImage(filePath).then((item) => uploaded.push(item)));
    });
    chain
      .then(() => {
        const attachedImages = (this.data.attachedImages || []).concat(uploaded).slice(0, MAX_ATTACH_IMAGES);
        this.setData({ attachedImages }, () => this.refreshLayoutToBottom());
        wx.showToast({ title: "图片已上传", icon: "success" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ uploadingImage: false }));
  },

  uploadOneImage(filePath) {
    return api
      .uploadFile({
        url: "/api/assets/upload",
        filePath,
        name: "file",
        token: app.globalData.token || wx.getStorageSync("lobster_token") || ""
      })
      .then((data) => {
        const url = data.source_url || data.url || "";
        if (!url) throw new Error("上传成功但没有返回图片链接");
        return {
          asset_id: data.asset_id || "",
          title: data.filename || "上传图片",
          media_type: "image",
          url,
          source_url: url,
          preview_url: filePath
        };
      });
  },

  removeAttachedImage(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const attachedImages = (this.data.attachedImages || []).filter((_, i) => i !== index);
    this.setData({ attachedImages }, () => this.refreshLayoutToBottom());
  },

  buildMessageContent(content, attachments) {
    const clean = String(content || "").trim();
    const images = (attachments || []).filter((item) => item && item.url);
    if (!images.length) return clean;
    const lines = images.map((item) => {
      const assetId = item.asset_id || "";
      return `- asset_id: ${assetId}  media_type: image  URL: ${item.url}`;
    });
    return `${clean || "请根据上传图片继续处理。"}\n\n${MOBILE_UPLOAD_TITLE}\n${lines.join("\n")}`;
  },

  refreshAuthState() {
    const phoneBound = Boolean(app.globalData.token && app.globalData.phone);
    this.setData({ phoneBound });
    return phoneBound;
  },

  showAuthPanel(hint) {
    this.refreshAuthState();
    if (this.data.phoneBound) return false;
    this.setData({
      authPanelVisible: true,
      authHint: hint || "发送消息前需要微信登录并绑定手机号，用来关联你的电脑端 online。"
    });
    return true;
  },

  login() {
    wx.showLoading({ title: "登录中", mask: true });
    app
      .loginWithWechat()
      .then((data) => {
        this.refreshAuthState();
        if (data.needs_phone_bind || !app.globalData.phone) {
          wx.showToast({ title: "请授权手机号", icon: "none" });
          return;
        }
        this.setData({ authPanelVisible: false });
        wx.showToast({ title: "登录成功", icon: "success" });
        this.loadMessages({ forceScroll: true });
        this.loadOnlineStatus();
        this.startMessagePolling();
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
        this.setData({ authPanelVisible: false, smsBindVisible: false, phoneBound: true });
        wx.showToast({ title: "登录成功", icon: "success" });
        this.loadMessages({ forceScroll: true });
        this.loadOnlineStatus();
        this.startMessagePolling();
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
            display_name: "微信小程序",
            ...app.refAgentPayload()
          }
        })
        .then((data) => {
          app.saveSession(data);
          this.setData({ authPanelVisible: false, smsBindVisible: false, phoneBound: true, smsCode: "" });
          wx.showToast({ title: "登录成功", icon: "success" });
          this.loadMessages({ forceScroll: true });
          this.loadOnlineStatus();
          this.startMessagePolling();
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

  quickFill(evt) {
    const text = evt.currentTarget.dataset.text || "";
    this.setData({ inputText: text });
  },

  sendMessage() {
    const inputText = (this.data.inputText || "").trim();
    const attachedImages = this.data.attachedImages || [];
    if (!inputText && attachedImages.length === 0) {
      wx.showToast({ title: "请输入消息或上传图片", icon: "none" });
      return;
    }
    if (this.showAuthPanel("发送消息前需要微信登录并绑定手机号，用来关联你的电脑端 online。")) return;
    const content = this.buildMessageContent(inputText, attachedImages);
    this.setData({ sending: true });
    this.loadOnlineStatus()
      .then(() => {
        if (!this.data.onlineAvailable) {
          wx.showToast({ title: "online 未在线，启动电脑端后再发送", icon: "none" });
          return Promise.resolve();
        }
        return app
          .request({
            method: "POST",
            url: "/api/h5-chat/messages",
            data: { content }
          })
          .then(() => {
            this.setData({ inputText: "", attachedImages: [] }, () => this.refreshLayoutToBottom());
            wx.showToast({ title: "已发送", icon: "success" });
            return this.loadMessages({ forceScroll: true });
          });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => this.setData({ sending: false }));
  },

  previewImage(evt) {
    const url = evt.currentTarget.dataset.url;
    if (!url) return;
    wx.previewImage({ urls: [url], current: url });
  },

  copyMedia(evt) {
    const item = this.data.messages[Number(evt.currentTarget.dataset.msg || 0)].mediaItems[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    media.copyLink(item.url).then(() => wx.showToast({ title: "链接已复制", icon: "success" }));
  },

  saveMedia(evt) {
    const msg = this.data.messages[Number(evt.currentTarget.dataset.msg || 0)];
    const item = msg && msg.mediaItems[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    if (item.media_type !== "image" && item.media_type !== "video") {
      this.copyMedia(evt);
      return;
    }
    media
      .saveToAlbum(item)
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const text = api.errorMessage(err);
        if (/auth deny|authorize|permission|scope/i.test(text)) {
          wx.showModal({
            title: "需要相册权限",
            content: "请允许保存到相册后再试。",
            confirmText: "去设置",
            success(res) {
              if (res.confirm) wx.openSetting({});
            }
          });
          return;
        }
        media.copyLink(item.url).finally(() => wx.showToast({ title: "保存失败，已复制链接", icon: "none" }));
      });
  },

  publishMedia(evt) {
    const msg = this.data.messages[Number(evt.currentTarget.dataset.msg || 0)];
    const item = msg && msg.mediaItems[Number(evt.currentTarget.dataset.index || 0)];
    const runId = item && (item.run_id || (item.publish_draft && item.publish_draft.run_id));
    if (!runId) {
      wx.showToast({ title: "缺少发布记录", icon: "none" });
      return;
    }
    wx.showLoading({ title: "提交发布", mask: true });
    app
      .request({
        method: "POST",
        url: `/api/scheduled-tasks/runs/${encodeURIComponent(runId)}/publish-request`,
        data: {}
      })
      .then(() => {
        wx.showToast({ title: "已提交发布", icon: "success" });
        this.loadMessages({ silent: true, scrollOnChange: false });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .finally(() => wx.hideLoading());
  },

  goHome() {
    wx.switchTab({ url: "/pages/index/index" });
  },

  onShareAppMessage() {
    return share.appShare({
      title: "打开必火AI员工，对话安排创作任务",
      path: "/pages/assistant/assistant"
    });
  },

  onShareTimeline() {
    return share.timelineShare({
      title: "必火AI员工 - 对话安排AI任务"
    });
  }
});
