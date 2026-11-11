const app = getApp();
const api = require("../../utils/api");

Page({
  data: { packages: [], selected: 0, paying: false, statusText: "" },

  onShow() {
    app.restoreSession();
    this.load();
  },

  load() {
    app.request({ url: "/api/recharge/packages", timeout: 20000 })
      .then((data) => {
        const rows = (data && (data.packages || data.items)) || [];
        const packages = (Array.isArray(rows) ? rows : []).map((row, i) => ({
          index: Number(row.index != null ? row.index : i),
          price: Number(row.price_yuan || row.price || 0),
          credits: Number(row.credits || 0),
          label: String(row.label || row.name || (row.price_yuan ? row.price_yuan + " 元" : "套餐"))
        }));
        this.setData({ packages });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  select(e) { this.setData({ selected: Number(e.currentTarget.dataset.index || 0) }); },

  pay() {
    const pkg = this.data.packages[this.data.selected];
    if (!pkg) return wx.showToast({ title: "请选择套餐", icon: "none" });
    if (this.data.paying) return;
    this.setData({ paying: true, statusText: "正在创建订单…" });
    app.request({
      method: "POST",
      url: "/api/recharge/fuiou-jsapi",
      data: { package_index: pkg.index, price_yuan: pkg.price || undefined },
      timeout: 60000
    })
      .then((data) => {
        const pay = (data && data.pay_params) || {};
        if (!pay.paySign || !pay.package) throw new Error("没有拿到支付参数");
        this._orderNo = String(data.out_trade_no || "");
        this.setData({ statusText: "正在调起支付…" });
        wx.requestPayment({
          timeStamp: pay.timeStamp,
          nonceStr: pay.nonceStr,
          package: pay.package,
          signType: pay.signType || "MD5",
          paySign: pay.paySign,
          success: () => this.poll(),
          fail: (err) => this.setData({ paying: false, statusText: (err && err.errMsg) || "支付取消" })
        });
      })
      .catch((err) => { this.setData({ paying: false, statusText: "" }); wx.showToast({ title: api.errorMessage(err), icon: "none" }); });
  },

  poll() {
    const tries = 8;
    let n = 0;
    const tick = () => {
      n += 1;
      app.request({ url: `/api/recharge/fuiou-query?out_trade_no=${encodeURIComponent(this._orderNo)}`, timeout: 20000 })
        .then((data) => {
          const status = String((data && data.status) || "");
          if (status === "paid" || status === "completed") {
            this.setData({ paying: false, statusText: "充值成功" });
            wx.showToast({ title: "充值成功", icon: "success" });
            return;
          }
          if (n < tries) setTimeout(tick, 2000);
          else this.setData({ paying: false, statusText: "支付结果确认中，稍后到账" });
        })
        .catch(() => { if (n < tries) setTimeout(tick, 2000); else this.setData({ paying: false }); });
    };
    tick();
  }
});
