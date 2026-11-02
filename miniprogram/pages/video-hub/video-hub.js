Page({
  goOral() {
    wx.navigateTo({ url: "/pages/digital/history" });
  },
  goStoryboard() {
    wx.navigateTo({ url: "/pages/article/history?feature=storyboard" });
  },
  goMontage() {
    wx.navigateTo({ url: "/pages/article/history?feature=montage" });
  },
  goRecords() {
    wx.navigateTo({ url: "/pages/records/records" });
  },
  goAssets() {
    wx.navigateTo({ url: "/pages/assets/assets" });
  }
});