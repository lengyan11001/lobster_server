Page({
  data: { type: "", email: "support@bhzn.top", text: "" },
  onLoad(options) {
    const type = String((options && options.type) || "");
    this.setData({ type });
    wx.setNavigationBarTitle({ title: type === "delete" ? "注销账号" : "举报与投诉" });
  },
  copyEmail() {
    wx.setClipboardData({ data: this.data.email, success: () => wx.showToast({ title: "已复制邮箱", icon: "success" }) });
  },
  onInput(e) { this.setData({ text: e.detail.value }); },
  openContactTip() {
    wx.showModal({
      title: "如何提交",
      content: "请点击下方「联系客服」直接提交，或复制邮箱发送相关链接与说明。我们会在 24 小时内响应，3 个工作日内处理。",
      showCancel: false
    });
  }
});
