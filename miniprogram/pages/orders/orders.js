const app = getApp();
const api = require("../../utils/api");
const { formatDateTime } = require("../../utils/datetime");

const STATUS = {
  pending: "待支付",
  paid: "已支付",
  completed: "已完成",
  failed: "失败",
  refunded: "已退款",
  cancelled: "已取消"
};

Page({
  data: { items: [], total: 0, loading: false },
  onShow() { this.load(); },
  load() {
    if (!app.globalData.token) {
      this.setData({ items: [], total: 0 });
      return;
    }
    this.setData({ loading: true });
    app.request({ url: "/api/recharge/my-orders?limit=50", timeout: 30000 })
      .then((data) => {
        const items = ((data && data.items) || []).map((r) => ({
          id: r.id,
          orderNo: String(r.out_trade_no || ""),
          amount: Number(r.amount_yuan || (Number(r.amount_fen || 0) / 100) || 0).toFixed(2),
          credits: Number(r.credits || 0),
          statusText: STATUS[String(r.status || "")] || String(r.status || ""),
          timeText: formatDateTime(r.created_at_beijing || r.created_at || ""),
          paidText: r.paid_at_beijing ? formatDateTime(r.paid_at_beijing) : ""
        }));
        this.setData({ items, total: Number((data && data.total) || items.length) });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },
  copyOrder(e) {
    const no = String((e.currentTarget.dataset.no) || "");
    if (!no) return;
    wx.setClipboardData({ data: no, success: () => wx.showToast({ title: "已复制订单号", icon: "success" }) });
  },
  goRecharge() {
    wx.navigateTo({ url: "/pages/recharge/recharge" });
  }
});
