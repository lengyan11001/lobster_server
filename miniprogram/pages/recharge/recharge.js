const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");

Page({
  data: { packages: [], selected: 0, mode: "package", customAmount: "", paying: false, statusText: "", balanceText: "--", payAmount: "0.00", iosBlocked: false, payEnabled: true },

  onShow() {
    app.restoreSession();
    if (!config.FEATURE_RECHARGE) {
      this.setData({ payEnabled: false, statusText: "当前版本暂不支持在线充值" });
      return;
    }
    this.setData({ payEnabled: true, iosBlocked: false });
    this.load();
    this.loadBalance();
  },

  goOrders() { wx.navigateTo({ url: "/pages/orders/orders" }); },

  loadBalance() {
    if (!app.globalData.token) { this.setData({ balanceText: "--" }); return; }
    app.request({ url: "/auth/me", timeout: 20000 })
      .then((data) => {
        const credits = Number((data && data.credits) || 0);
        const parts = credits.toFixed(2).split(".");
        this.setData({ balanceText: parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + parts[1] });
      })
      .catch(() => {});
  },

  syncPayAmount() {
    const pkg = this.data.packages[this.data.selected];
    const amount = this.data.mode === "package" ? Number((pkg && pkg.price) || 0) : Number(this.data.customAmount || 0);
    this.setData({ payAmount: amount.toFixed(2) });
  },

  load() {
    app.request({ url: "/api/recharge/packages", timeout: 20000 })
      .then((data) => {
        const rows = (data && (data.packages || data.items)) || [];
        const packages = (Array.isArray(rows) ? rows : []).map((row, i) => {
          const price = Number(row.price_yuan || row.price || 0);
          return {
            index: Number(row.index != null ? row.index : i),
            price,
            credits: price * 100,
            label: price + " 元 = " + (price * 100) + " 算力"
          };
        });
        this.setData({ packages }, () => this.syncPayAmount());
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
  },

  select(e) { this.setData({ selected: Number(e.currentTarget.dataset.index || 0), mode: "package" }, () => this.syncPayAmount()); },
  setMode(e) { this.setData({ mode: String(e.currentTarget.dataset.mode || "package") }, () => this.syncPayAmount()); },
  onCustomAmount(e) { this.setData({ customAmount: e.detail.value }, () => this.syncPayAmount()); },

  pay() {
    if (!this.data.payEnabled) return wx.showToast({ title: "当前版本暂不支持在线充值", icon: "none" });
    const pkg = this.data.packages[this.data.selected];
    if (this.data.mode === "package" && !pkg) return wx.showToast({ title: "请选择套餐", icon: "none" });
    if (this.data.mode === "custom" && !(Number(this.data.customAmount) > 0)) return wx.showToast({ title: "请输入充值金额", icon: "none" });
    if (this.data.paying) return;
    const yuan = Math.max(1, Math.round(this.data.mode === "package" ? Number(pkg.price || 0) : Number(this.data.customAmount || 0)));
    this.setData({ paying: true, statusText: "正在创建订单…" });
    wx.login({
      success: (res) => this.startVirtualPay(yuan, res.code || ""),
      fail: () => this.setData({ paying: false, statusText: "微信登录失败，请重试" })
    });
  },

  startVirtualPay(yuan, code) {
    if (!code) { this.setData({ paying: false, statusText: "微信登录失败，请重试" }); return; }
    app.request({
      method: "POST",
      url: "/api/virtual-pay/prepare",
      data: { code, yuan },
      timeout: 30000
    })
      .then((data) => {
        if (!data || !data.signData || !data.paySig || !data.signature) throw new Error("没有拿到虚拟支付参数");
        this._orderNo = String(data.outTradeNo || "");
        this._buyQuantity = Number(data.buyQuantity || yuan * 100);
        this.setData({ statusText: "正在调起支付…" });
        wx.requestVirtualPayment({
          signData: data.signData,
          paySig: data.paySig,
          signature: data.signature,
          mode: data.mode || "short_series_coin",
          success: () => this.confirmVirtualPay(),
          fail: (err) => {
            const raw = String((err && (err.errMsg || err.message)) || "");
            let tip = raw || "支付取消";
            if (raw.indexOf("COIN_OR_PRODUCT_ID_CREATED_IN_RECENTLY") >= 0) tip = "微信侧代币刚创建、尚未生效，请稍后再试（通常几小时）";
            else if (raw.indexOf("SIGNATURE") >= 0 || raw.indexOf("15005") >= 0) tip = "用户态签名校验失败，请重新登录后再试";
            else if (raw.indexOf("15006") >= 0) tip = "支付签名校验失败，请联系客服";
            this.setData({ paying: false, statusText: tip });
          }
        });
      })
      .catch((err) => {
        this.setData({ paying: false, statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  },

  confirmVirtualPay() {
    app.request({
      method: "POST",
      url: "/api/virtual-pay/confirm",
      data: { out_trade_no: this._orderNo || "", buy_quantity: this._buyQuantity || 0 },
      timeout: 30000
    })
      .then((data) => {
        const credits = Number((data && data.credits) || 0);
        this.setData({ paying: false, statusText: "充值成功，到账 " + credits + " 算力" });
        wx.showToast({ title: "充值成功", icon: "success" });
        this.loadBalance();
      })
      .catch((err) => {
        this.setData({ paying: false, statusText: "" });
        wx.showToast({ title: api.errorMessage(err), icon: "none" });
      });
  }
});
