const app = getApp();
const api = require("../../utils/api");
const config = require("../../config");
const AIRECBleManager = require("../../utils/airec/ble-manager");

const STATUS_TEXT = { completed: "已完成", failed: "失败", processing: "处理中", pending: "排队中" };

function fmtTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

Page({
  data: {
    id: 0,
    loading: true,
    audioLoading: false,
    extracting: false,
    extractOpen: false,
    extractFields: [],
    extractDocs: [],
    playing: false,
    record: null
  },

  onLoad(query) {
    const id = Number(query && query.id);
    this.setData({ id });
    if (!id) return;
    this.load();
    this.loadAudioInfo();
  },

  onUnload() {
    this.stopAudio();
  },

  load() {
    this.setData({ loading: true });
    return app.request({ url: `/api/h5/recorder/files/${this.data.id}`, timeout: 30000 })
      .then((data) => {
        const row = (data && (data.record || data.item || data)) || {};
        const keyPoints = Array.isArray(row.key_points) ? row.key_points.map((x) => String(x)) : [];
        const record = {
          name: String(row.display_name || row.file_name || "录音"),
          status: String(row.status || ""),
          statusText: STATUS_TEXT[row.status] || row.status || "",
          sourceLabel: String(row.source_label || row.device_name || "音频文件"),
          sourceType: String(row.source_type || ""),
          fileName: String(row.file_name || ""),
          time: fmtTime(row.created_at || row.recorded_at),
          summary: String(row.summary_text || ""),
          keyPoints,
          transcript: String(row.transcript_text || ""),
          error: String(row.error_message || "")
        };
        this.setData({ record });
        wx.setNavigationBarTitle({ title: record.name.slice(0, 16) });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  loadAudioInfo() {
    // 仅记录可播放标记，真正下载在点击时进行
    this.audioReady = false;
  },

  playAudio() {
    if (this.data.playing) {
      this.stopAudio();
      return;
    }
    if (this.localAudioPath) {
      this.playLocal(this.localAudioPath);
      return;
    }
    const token = app.globalData.token || "";
    const base = String(config.API_BASE || "").replace(/\/+$/, "");
    const url = `${base}/api/h5/recorder/files/${this.data.id}/audio?brand=${encodeURIComponent(config.BRAND_MARK || "")}`;
    this.setData({ audioLoading: true });
    wx.downloadFile({
      url,
      header: { Authorization: `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode !== 200) {
          wx.showToast({ title: `下载失败 ${res.statusCode}`, icon: "none" });
          return;
        }
        this.localAudioPath = res.tempFilePath;
        this.playLocal(res.tempFilePath);
      },
      fail: () => wx.showToast({ title: "录音下载失败", icon: "none" }),
      complete: () => this.setData({ audioLoading: false })
    });
  },

  playLocal(path) {
    if (!this.audio) {
      this.audio = wx.createInnerAudioContext();
      this.audio.onEnded(() => this.setData({ playing: false }));
      this.audio.onError(() => {
        this.setData({ playing: false });
        wx.showToast({ title: "播放失败", icon: "none" });
      });
    }
    this.audio.src = path;
    this.audio.play();
    this.setData({ playing: true });
  },

  stopAudio() {
    if (this.audio) {
      this.audio.stop();
      this.audio.destroy();
      this.audio = null;
    }
    this.setData({ playing: false });
  },

  extractProfile() {
    if (this.data.extracting) return;
    this.setData({ extracting: true });
    wx.showLoading({ title: "提取中" });
    app.request({
      method: "POST",
      url: `/api/h5/recorder/files/${this.data.id}/extract-profile?preview=1`,
      data: {},
      timeout: 240000
    })
      .then((data) => {
        wx.hideLoading();
        const profile = (data && data.profile) || {};
        const memory = (data && data.memory) || {};
        const labels = {
          profile_name: "你的名字 / 称呼", gender: "性别", birth_decade: "出生年代",
          province: "现在居住省份", city: "现在居住城市", hometown: "籍贯 / 家乡",
          occupation: "你是做什么的", share_content: "主要想分享什么内容",
          video_style: "希望的视频风格", after_view_action: "希望看完后做什么",
          product: "在做什么产品 / 服务", target_customer: "目标客户", advantages: "优势"
        };
        const fields = Object.keys(labels).map((key) => ({
          key,
          label: labels[key],
          value: String(profile[key] || "").trim()
        }));
        const docTitles = { brand_product_intro: "品牌/产品介绍", product_service_faq: "产品服务问答", short_video_scripts: "短视频脚本" };
        const docs = Object.keys(docTitles)
          .map((key) => ({ key, title: docTitles[key], checked: Boolean(String(memory[key] || "").trim()) }))
          .filter((x) => String(memory[x.key] || "").trim());
        this.setData({ extractOpen: true, extractFields: fields, extractDocs: docs });
      })
      .catch((err) => { wx.hideLoading(); wx.showToast({ title: api.errorMessage(err), icon: "none" }); })
      .then(() => this.setData({ extracting: false }));
  },

  onExtractInput(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const list = this.data.extractFields.slice();
    if (!list[idx]) return;
    list[idx].value = e.detail.value;
    this.setData({ extractFields: list });
  },

  toggleExtractItem(e) {
    const idx = Number(e.currentTarget.dataset.index || 0);
    const list = this.data.extractDocs.slice();
    if (!list[idx]) return;
    list[idx].checked = !list[idx].checked;
    this.setData({ extractDocs: list });
  },

  closeExtract() { this.setData({ extractOpen: false }); },
  extractNoop() {},

  saveExtract() {
    const profile = {};
    this.data.extractFields.forEach((x) => { if (String(x.value || "").trim()) profile[x.key] = String(x.value).trim(); });
    const docs = this.data.extractDocs.filter((x) => x.checked).map((x) => x.key);
    const fields = Object.keys(profile);
    if (!fields.length && !docs.length) return wx.showToast({ title: "至少填一项", icon: "none" });
    wx.showLoading({ title: "写入中" });
    app.request({
      method: "POST",
      url: `/api/h5/recorder/files/${this.data.id}/extract-profile`,
      data: { profile, fields, memory_keys: docs },
      timeout: 240000
    })
      .then((data) => {
        wx.hideLoading();
        this.setData({ extractOpen: false });
        const filled = (data && data.profile_fields) || [];
        const created = (data && data.memory_docs) || [];
        wx.showModal({ title: "已写入", content: `资料 ${filled.length} 项，记忆 ${created.length} 份`, showCancel: false });
      })
      .catch((err) => { wx.hideLoading(); wx.showToast({ title: api.errorMessage(err), icon: "none" }); });
  },

  retry() {
    wx.showLoading({ title: "提交中" });
    app.request({ method: "POST", url: `/api/h5/recorder/files/${this.data.id}/retry`, data: {}, timeout: 60000 })
      .then(() => {
        wx.showToast({ title: "已重新提交", icon: "success" });
        this.load();
        [3000, 8000, 15000].forEach((delay) => {
          setTimeout(() => { if (this.data.id) this.load(); }, delay);
        });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  remove() {
    const record = this.data.record || {};
    const isDevice = String(record.sourceType || "") === "device";
    const fileName = String(record.fileName || "");
    wx.showModal({
      title: "删除录音",
      content: isDevice
        ? "会同时从录音设备里删除这个文件（否则下次同步又会回来），删除后不可恢复。"
        : "确认删除这条录音记录？",
      success: (res) => {
        if (!res.confirm) return;
        this._deleteDeviceFile(isDevice, fileName)
          .then(() => this._deleteServerRecord())
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
      }
    });
  },

  _deleteDeviceFile(isDevice, fileName) {
    if (!isDevice || !fileName) return Promise.resolve();
    const ble = AIRECBleManager.getInstance();
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "设备未连接，只删了记录（重连后可能再同步回来）", icon: "none" });
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      let done = false;
      const prevHandler = ble._callbacks ? ble._callbacks["onFileDeleted"] : null;
      const finish = () => {
        if (done) return;
        done = true;
        try {
          if (prevHandler) ble.on("onFileDeleted", prevHandler);
          else ble.off("onFileDeleted");
        } catch (err) {}
        resolve();
      };
      ble.on("onFileDeleted", (name, success) => {
        if (String(name || "") !== fileName) return;
        if (!success) wx.showToast({ title: "设备删除失败", icon: "none" });
        finish();
      });
      try {
        ble.deleteFile(fileName);
      } catch (err) {
        finish();
        return;
      }
      setTimeout(finish, 3000);
    });
  },

  _deleteServerRecord() {
    return app.request({ method: "DELETE", url: `/api/h5/recorder/files/${this.data.id}`, timeout: 30000 })
      .then(() => {
        wx.showToast({ title: "已删除", icon: "success" });
        setTimeout(() => wx.navigateBack(), 600);
      });
  }
});