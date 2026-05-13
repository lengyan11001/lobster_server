const app = getApp();
const api = require("../../utils/api");
const media = require("../../utils/media");

Page({
  data: {
    phoneBound: false,
    loading: false,
    mediaType: "media",
    items: []
  },

  onShow() {
    app.restoreSession();
    this.setData({ phoneBound: Boolean(app.globalData.token && app.globalData.phone) });
    if (this.data.phoneBound) this.loadDownloads();
  },

  onPullDownRefresh() {
    this.loadDownloads().finally(() => wx.stopPullDownRefresh());
  },

  switchType(evt) {
    const mediaType = evt.currentTarget.dataset.type || "media";
    this.setData({ mediaType });
    this.loadDownloads();
  },

  loadDownloads() {
    if (!app.globalData.token) {
      this.setData({ phoneBound: false, loading: false, items: [] });
      return Promise.resolve();
    }
    const deviceId = app.globalData.deviceId;
    const type = this.data.mediaType || "media";
    this.setData({ loading: true });
    return app
      .request({
        url: `/api/mobile/downloads?device_id=${encodeURIComponent(deviceId)}&media_type=${encodeURIComponent(type)}&limit=80`
      })
      .then((data) => {
        this.setData({ items: data.items || [], phoneBound: true });
      })
      .catch((err) => {
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
        if (/未绑定|401|403/.test(api.errorMessage(err))) this.setData({ phoneBound: false });
      })
      .finally(() => this.setData({ loading: false }));
  },

  previewImage(evt) {
    const url = evt.currentTarget.dataset.url;
    if (!url) return;
    wx.previewImage({ urls: [url], current: url });
  },

  copyLink(evt) {
    const item = this.data.items[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    media
      .copyLink(item.url || item.preview_url || "")
      .then(() => wx.showToast({ title: "链接已复制", icon: "success" }))
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  saveMedia(evt) {
    const item = this.data.items[Number(evt.currentTarget.dataset.index || 0)];
    if (!item) return;
    if (item.media_type !== "image" && item.media_type !== "video") {
      this.copyLink(evt);
      return;
    }
    media
      .saveToAlbum(item)
      .then(() => wx.showToast({ title: "已保存", icon: "success" }))
      .catch((err) => {
        const msg = api.errorMessage(err);
        if (/auth deny|authorize|permission|scope/i.test(msg)) {
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
        media.copyLink(item.url || item.preview_url || "").finally(() => {
          wx.showToast({ title: "保存失败，已复制链接", icon: "none" });
        });
      });
  },

  goHome() {
    wx.switchTab({ url: "/pages/index/index" });
  }
});

