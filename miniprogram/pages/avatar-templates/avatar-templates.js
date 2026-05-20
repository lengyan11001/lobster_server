const app = getApp();
const api = require("../../utils/api");
const avatarTemplates = require("../../utils/digital_avatar_templates");

Page({
  data: {
    templates: [],
    loading: false,
    errorText: ""
  },

  onLoad() {
    const cached = wx.getStorageSync("lobster_public_avatar_templates") || [];
    if (cached.length) {
      this.setData({ templates: avatarTemplates.pickPublicAvatarTemplates(cached, 20) });
    }
    this.loadTemplates();
  },

  onPullDownRefresh() {
    this.loadTemplates(true).finally(() => wx.stopPullDownRefresh());
  },

  loadTemplates(force) {
    if (this.data.loading && !force) return Promise.resolve();
    this.setData({ loading: true, errorText: "" });
    return app
      .request({ method: "POST", url: "/api/hifly/avatar/library", data: { page: 1, size: 100 } })
      .then((data) => {
        const rows = avatarTemplates.pickPublicAvatarTemplates(data.public || [], 20);
        this.setData({ templates: rows, errorText: rows.length ? "" : "暂无可用数字人模板" });
        wx.setStorageSync("lobster_public_avatar_templates", rows);
      })
      .catch((err) => {
        const cached = wx.getStorageSync("lobster_public_avatar_templates") || [];
        if (cached.length) {
          this.setData({ templates: avatarTemplates.pickPublicAvatarTemplates(cached, 20), errorText: "" });
          return;
        }
        this.setData({ errorText: api.errorMessage(err) });
      })
      .finally(() => this.setData({ loading: false }));
  },

  goBack() {
    wx.navigateBack({
      fail() {
        wx.switchTab({ url: "/pages/index/index" });
      }
    });
  },

  createCustom() {
    wx.navigateTo({ url: "/pages/digital/digital" });
  },

  selectTemplate(evt) {
    const index = Number(evt.currentTarget.dataset.index || 0);
    const item = this.data.templates[index];
    if (!avatarTemplates.storeDigitalAvatarPrefill(item)) {
      wx.showToast({ title: "数字人模板不可用", icon: "none" });
      return;
    }
    wx.navigateTo({ url: "/pages/digital/digital" });
  }
});
