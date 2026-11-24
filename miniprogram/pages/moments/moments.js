const app = getApp();
const api = require("../../utils/api");
const hist = require("../../utils/gen-history");
const { createPoller } = require("../../utils/task-poll");
const { formatDateTime } = require("../../utils/datetime");

const FEATURE = "moments";
const STALE_MS = 15 * 60 * 1000;

function fmtTime(v) { return formatDateTime(v, true); }

Page({
  data: {
    groups: [],
    loading: false,
    viewMode: "list",
    topic: "",
    copyCount: 10,
    copyRequirement: "",
    generating: false,
    taskRows: []
  },

  onLoad() {
    this.momentPoller = null;
    this.momentActive = "";
    this.recordCount = 0;
  },

  onShow() {
    app.restoreSession();
    if (this.momentPoller) this.momentPoller.resume();
    if (this.data.viewMode === "list") this.load();
    this.resumeMomentJobs();
  },

  onHide() {
    if (this.momentPoller) this.momentPoller.pause();
  },

  onUnload() {
    if (this.momentPoller) this.momentPoller.stop();
  },

  load() {
    if (!app.globalData.token) return Promise.resolve();
    this.setData({ loading: true });
    return app.request({ url: "/api/ip-content/draft-records?task=moments_candidate&limit=200", timeout: 30000 })
      .then((data) => {
        const rows = (data && data.items) || [];
        this.recordCount = rows.length;
        const map = {};
        rows.forEach((row) => {
          const gid = String(row.group_id || (row.meta && row.meta.group_id) || ("single-" + row.id));
          if (!map[gid]) {
            map[gid] = { gid, time: fmtTime(row.created_at), count: 0, title: "", imageCount: 0 };
          }
          const g = map[gid];
          g.count += 1;
          const text = String(row.content || row.body || "").trim();
          if (!g.title && text) g.title = text.split("\n")[0].slice(0, 42);
          const imgs = (row.images && row.images.length) || (row.image_url ? 1 : 0);
          g.imageCount += imgs;
        });
        const groups = Object.keys(map).map((k) => map[k]).sort((a, b) => String(b.time).localeCompare(String(a.time)));
        this.setData({ groups });
        this.refreshTaskRows();
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  refreshTaskRows() {
    const now = Date.now();
    const rows = [];
    (hist.list(FEATURE) || []).forEach((row) => {
      if (!row || row.status === "completed") return;
      if (row.status === "processing" && now - Number(row.at || 0) > STALE_MS) {
        hist.update(row.id, { status: "failed", status_text: "已中断", error: "页面已关闭，任务没有继续执行" });
        row.status = "failed";
        row.status_text = "已中断";
      }
      rows.push(Object.assign({}, row, {
        timeText: fmtTime(row.started_at || row.at),
        statusText: row.status_text || (row.status === "failed" ? "失败" : "生成中")
      }));
    });
    this.setData({ taskRows: rows });
  },

  openCreate() {
    this.setData({ viewMode: "create", topic: "", copyRequirement: "", copyCount: 10, generating: false });
  },

  backToList() {
    this.setData({ viewMode: "list" });
    this.load();
  },

  openTopicRecommend() { wx.navigateTo({ url: "/pages/topic-recommend?target=moments" }); },
  openMemory() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("memory"); },
  openPrefillHistory() { const d = this.selectComponent("#topicPrefill"); if (d) d.openHistory(); },
  openRecording() { const d = this.selectComponent("#topicPrefill"); if (d) d.open("recording"); },

  onPrefillPick(evt) {
    const item = (evt && evt.detail && evt.detail.item) || {};
    const text = String(item.script || item.content || item.body || "");
    this.setData({
      topic: String(item.title || text || "").slice(0, 200),
      copyRequirement: text ? text.slice(0, 200) : this.data.copyRequirement
    });
    wx.showToast({ title: "已填入选题", icon: "none" });
  },

  chooseImages() {
    if (this.data.uploadingImage) return;
    wx.chooseMedia({
      count: 6 - (this.data.images || []).length,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const files = (res.tempFiles || []).slice(0, 6 - (this.data.images || []).length);
        if (!files.length) return;
        this.setData({ uploadingImage: true });
        const rows = (this.data.images || []).slice();
        let done = 0;
        files.reduce((chain, file) => chain.then(() => api.uploadFile({
          url: "/api/assets/upload",
          filePath: file.tempFilePath,
          name: "file",
          formData: { media_type: "image", tags: "moments,miniprogram" },
          token: app.globalData.token || ""
        }).then((data) => {
          const url = String((data && (data.source_url || data.url)) || "").trim();
          if (url) rows.push({ url, local: file.tempFilePath });
        }).catch(() => {}).then(() => {
          done += 1;
          this.setData({ images: rows.slice(0, 6) });
        })), Promise.resolve()).then(() => this.setData({ uploadingImage: false }));
      }
    });
  },

  removeImage(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const rows = (this.data.images || []).slice();
    rows.splice(idx, 1);
    this.setData({ images: rows });
  },

  queueMomentImages(groupId) {
    const gid = String(groupId || "");
    if (!gid) return;
    app.request({ url: "/api/ip-content/draft-records?task=moments_candidate&limit=200", timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || [])
          .filter((row) => String(row.group_id || "") === gid)
          .slice(0, 1);
        const ids = rows.map((row) => row.record_id).filter(Boolean);
        if (!ids.length) return;
        return app.request({
          method: "POST",
          url: "/api/ip-content/moments/images/generate",
          data: { record_ids: ids },
          timeout: 30000
        });
      })
      .catch(() => {});
    // 图片后台生成，稍后刷新两次把配图拉回来
    setTimeout(() => this.load(), 20000);
    setTimeout(() => this.load(), 45000);
  },


  onTopic(e) { this.setData({ topic: e.detail.value }); },
  onReq(e) { this.setData({ copyRequirement: e.detail.value }); },
  setCopyCount(e) { this.setData({ copyCount: Number(e.currentTarget.dataset.value || 10) }); },

  submitCreate() {
    const topic = String(this.data.topic || "").trim();
    if (!topic) return wx.showToast({ title: "先填主题或卖点", icon: "none" });
    if (!app.globalData.token) return wx.showToast({ title: "请先登录", icon: "none" });
    if ((hist.list(FEATURE) || []).some((row) => row.status === "processing")) {
      return wx.showToast({ title: "已有朋友圈任务在生成", icon: "none" });
    }
    const requirement = [topic, String(this.data.copyRequirement || "").trim()].filter(Boolean).join("\n");
    const count = Math.max(1, Math.min(20, Number(this.data.copyCount || 10)));
    const histId = hist.save(FEATURE, {
      title: topic.slice(0, 24),
      topic,
      count,
      baseline: this.recordCount,
      started_at: Date.now(),
      status: "processing",
      status_text: "生成中"
    });
    this.setData({ viewMode: "list", generating: true });
    this.refreshTaskRows();
    this.startMomentPolling(histId);
    app.request({
      method: "POST",
      url: "/api/ip-content/generate/moments-candidates",
      data: { extra_requirements: requirement, count },
      timeout: 300000
    })
      .then((data) => {
        const gid = String((data && data.group_id) || "");
        if (count <= 1) this.queueMomentImages(gid);
        this.finishMomentTask(histId, true, gid ? ("已生成 · " + gid.slice(0, 6)) : "已生成");
      })
      .catch((err) => {
        this.finishMomentTask(histId, false, api.errorMessage(err) || "生成失败");
      });
  },

  startMomentPolling(histId) {
    if (this.momentPoller) this.momentPoller.stop();
    this.momentActive = histId;
    this.momentPoller = createPoller(
      () => app.request({ url: "/api/ip-content/draft-records?task=moments_candidate&limit=200", timeout: 30000 }),
      (data) => {
        const total = ((data && data.items) || []).length;
        const entry = hist.get(histId);
        if (!entry) return true;
        const baseline = Number(entry.baseline || 0);
        const age = Date.now() - Number(entry.started_at || entry.at || 0);
        if (total > baseline && age > 8000) {
          this.finishMomentTask(histId, true, "已生成");
          return true;
        }
        return false;
      },
      () => {}
    );
    this.momentPoller.start(true);
  },

  finishMomentTask(histId, ok, message) {
    if (this.momentPoller) this.momentPoller.stop();
    this.momentPoller = null;
    this.momentActive = "";
    if (ok) {
      hist.remove(histId);
      setTimeout(() => this.load(), 600);
    } else {
      hist.update(histId, { status: "failed", status_text: "失败", error: String(message || "生成失败") });
      this.load();
    }
    this.setData({ generating: false });
    wx.showToast({ title: String(message || (ok ? "已生成" : "生成失败")).slice(0, 24), icon: ok ? "success" : "none" });
  },

  resumeMomentJobs() {
    if (this.momentPoller || this.momentActive) return;
    const pending = (hist.list(FEATURE) || []).find((row) => row.status === "processing");
    if (!pending) return;
    if (Date.now() - Number(pending.at || 0) > STALE_MS) {
      hist.update(pending.id, { status: "failed", status_text: "已中断", error: "页面已关闭，任务没有继续执行" });
      this.refreshTaskRows();
      return;
    }
    this.startMomentPolling(pending.id);
  },

  openGroup(e) {
    const gid = e.currentTarget.dataset.gid;
    if (!gid) return;
    wx.navigateTo({ url: `/pages/moments-detail/moments-detail?gid=${encodeURIComponent(gid)}` });
  }
});
