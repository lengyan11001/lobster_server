const app = getApp();
const api = require("../../utils/api");
const IMAGE_MODEL = "gpt-image-2";

function extractUrls(data) {
  const out = [];
  const push = (u) => { if (typeof u === "string" && /^https?:\/\//i.test(u)) out.push(u); };
  const saved = (data && data.saved_assets) || [];
  if (Array.isArray(saved)) saved.forEach((a) => push(a && (a.source_url || a.url)));
  const list = (data && (data.data || data.images || data.results)) || [];
  if (Array.isArray(list)) list.forEach((it) => { if (typeof it === "string") push(it); else if (it) push(it.url || it.source_url || it.image_url); });
  return Array.from(new Set(out));
}

Page({
  data: { gid: "", items: [], loading: true, generatingIndex: -1 },

  onLoad(query) {
    const gid = decodeURIComponent(String((query && query.gid) || ""));
    this.setData({ gid });
    this.load();
  },

  load() {
    if (!app.globalData.token) return;
    this.setData({ loading: true });
    app.request({ url: "/api/ip-content/draft-records?task=moments_candidate&limit=200", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).filter((row) => String(row.group_id || (row.meta && row.meta.group_id) || ("single-" + row.id)) === this.data.gid);
        const items = rows.map((row) => ({
          recordId: String(row.record_id || ""),
          content: String(row.content || row.body || ""),
          images: ((row.images || []).map((x) => x.image_url || x.url).filter(Boolean)).concat(row.image_url && !(row.images || []).length ? [row.image_url] : []),
          time: String(row.created_at || "").replace("T", " ").slice(0, 16)
        }));
        this.setData({ items });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  copyText(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const count = Math.max(1, Math.min(9, Number(e.currentTarget.dataset.count || 1)));
    const item = this.data.items[idx];
    if (!item) return;
    wx.setClipboardData({ data: item.content, success: () => wx.showToast({ title: "已复制", icon: "success" }) });
  },

  generateImages(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const count = Math.max(1, Math.min(9, Number(e.currentTarget.dataset.count || 1)));
    const item = this.data.items[idx];
    if (!item || !item.recordId || this.data.generatingIndex >= 0) return;
    const prompt = `朋友圈配图：${item.content.slice(0, 120)}。要求真实质感、干净构图，适合微信朋友圈，画面不要出现文字。`;
    this.setData({ generatingIndex: idx });
    wx.showLoading({ title: "生成配图中" });
    app.request({
      method: "POST",
      url: "/api/comfly-proxy/v1/images/generations",
      data: { model: IMAGE_MODEL, prompt, image_size: "1:1", aspect_ratio: "1:1", ratio: "1:1", num_images: count, n: count, response_format: "url" },
      timeout: 300000
    })
      .then((data) => {
        const urls = extractUrls(data);
        if (!urls.length) throw new Error("图片生成失败，服务器没有返回图片");
        return urls;
      })
      .then((urls) => Promise.all(urls.map((url) =>
        app.request({ method: "POST", url: "/api/assets/save-url", data: { url, media_type: "image", tags: "moments,miniprogram", prompt, model: IMAGE_MODEL }, timeout: 180000 }).catch(() => null)
      )).then(() => urls))
      .then((urls) => {
        const metaImages = urls.map((u, i) => ({ image_url: u, image_prompt: prompt, index: i + 1 }));
        return app.request({
          method: "POST",
          url: `/api/ip-content/draft-records/${encodeURIComponent(item.recordId)}/image`,
          data: { image_url: urls[0], image_prompt: prompt, selected: true, meta: { images: metaImages } },
          timeout: 60000
        }).then(() => urls);
      })
      .then((urls) => {
        const items = this.data.items.slice();
        items[idx] = Object.assign({}, items[idx], { images: (items[idx].images || []).concat(urls) });
        this.setData({ items });
        wx.showToast({ title: "配图已生成", icon: "success" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => { wx.hideLoading(); this.setData({ generatingIndex: -1 }); });
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url;
    if (url) wx.previewImage({ current: url, urls: [url] });
  }
});
