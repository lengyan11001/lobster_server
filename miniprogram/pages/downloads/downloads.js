const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");

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

function extractMedia(replyText, events) {
  const urls = [];
  const seen = {};
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
    const text = JSON.stringify((ev && ev.payload) || {});
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
      preview_url: mediaProxyUrl(url, "inline"),
      download_url: mediaProxyUrl(url, "attachment")
    };
  });
}

function normalizeHistory(raw) {
  const row = raw.message || {};
  const events = raw.events || [];
  return Object.assign({}, row, {
    events,
    mediaItems: extractMedia(row.reply_text || "", events)
  });
}

Page({
  data: {
    phoneBound: false,
    loading: false,
    sending: false,
    inputText: "",
    messages: [],
    onlineText: "检查 online 状态中..."
  },

  onShow() {
    app.restoreSession();
    const prefill = wx.getStorageSync("lobster_message_prefill") || "";
    if (prefill) wx.removeStorageSync("lobster_message_prefill");
    this.setData({
      phoneBound: Boolean(app.globalData.token && app.globalData.phone),
      inputText: prefill || this.data.inputText
    });
    if (this.data.phoneBound) {
      this.loadMessages();
      this.loadOnlineStatus();
    }
  },

  onPullDownRefresh() {
    Promise.all([this.loadMessages(), this.loadOnlineStatus()]).finally(() => wx.stopPullDownRefresh());
  },

  loadOnlineStatus() {
    if (!app.globalData.token) return Promise.resolve();
    return app
      .request({ url: "/api/h5-chat/devices/status" })
      .then((data) => {
        this.setData({ onlineText: data.online ? "online 已连接" : "online 暂未在线" });
      })
      .catch(() => this.setData({ onlineText: "online 状态获取失败" }));
  },

  loadMessages() {
    if (!app.globalData.token) {
      this.setData({ phoneBound: false, loading: false, messages: [] });
      return Promise.resolve();
    }
    this.setData({ loading: true });
    return app
      .request({ url: "/api/h5-chat/messages?limit=40" })
      .then((data) => {
        this.setData({ messages: (data.messages || []).map(normalizeHistory), phoneBound: true });
      })
      .catch((err) => {
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
        if (/401|403|未绑定/.test(api.errorMessage(err))) this.setData({ phoneBound: false });
      })
      .finally(() => this.setData({ loading: false }));
  },

  onInput(evt) {
    this.setData({ inputText: evt.detail.value || "" });
  },

  quickFill(evt) {
    const text = evt.currentTarget.dataset.text || "";
    this.setData({ inputText: text });
  },

  sendMessage() {
    const content = (this.data.inputText || "").trim();
    if (!content) {
      wx.showToast({ title: "请输入消息", icon: "none" });
      return;
    }
    if (!app.globalData.token) {
      wx.showToast({ title: "请先登录绑定", icon: "none" });
      return;
    }
    this.setData({ sending: true });
    app
      .request({
        method: "POST",
        url: "/api/h5-chat/messages",
        data: { content }
      })
      .then(() => {
        this.setData({ inputText: "" });
        wx.showToast({ title: "已发送", icon: "success" });
        return this.loadMessages();
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

  goHome() {
    wx.switchTab({ url: "/pages/index/index" });
  }
});
