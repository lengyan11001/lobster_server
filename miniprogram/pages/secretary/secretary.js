const app = getApp();
const api = require("../../utils/api");
const AIRECBleManager = require("../../utils/airec/ble-manager");

const TABS = [
  { key: "", label: "全部" },
  { key: "device", label: "设备录音" },
  { key: "local", label: "本地上传" }
];
const STATUS_TEXT = { completed: "已完成", failed: "失败", processing: "处理中", pending: "排队中" };

function fmtTime(value) {
  return String(value || "").replace("T", " ").slice(0, 16);
}

Page({
  data: {
    tabs: TABS,
    tabIndex: 0,
    items: [],
    page: 1,
    hasMore: false,
    loading: false,
    device: {
      connected: false,
      name: "",
      battery: null,
      isCharging: false,
      isRecording: false,
      isPaused: false,
      timeText: "00:00:00"
    },
    syncText: "",
    diagOpen: false,
    diagLines: [],
    diagId: ""
  },

  onLoad() {
    this._ble = AIRECBleManager.getInstance();
    this._logDiag("page", "onLoad");
    this._bindBle();
  },

  onUnload() {
    this._stopTimer();
  },

  onShow() {
    this.reload();
    this.syncDeviceState();
  },

  /* ── 录音设备状态与控制（对齐 H5 AI秘书的录音设备面板）────────── */

  _logDiag(tag, detail) {
    const now = new Date();
    const pad = (n) => (n < 10 ? "0" + n : String(n));
    const stamp = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
    let text = "";
    try {
      text = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch (err) {
      text = String(detail);
    }
    const line = stamp + " [" + tag + "] " + String(text || "").slice(0, 300);
    const lines = (this.data.diagLines || []).concat(line);
    this.setData({ diagLines: lines.slice(-200) });
  },

  toggleDiag() {
    this.setData({ diagOpen: !this.data.diagOpen });
  },

  copyDiag() {
    const text = (this.data.diagLines || []).join("\n");
    if (!text) {
      wx.showToast({ title: "还没有日志", icon: "none" });
      return;
    }
    wx.setClipboardData({ data: text, success: () => wx.showToast({ title: "已复制诊断", icon: "success" }) });
  },

  uploadDiag() {
    const lines = this.data.diagLines || [];
    if (!lines.length) {
      wx.showToast({ title: "还没有日志", icon: "none" });
      return;
    }
    wx.showLoading({ title: "上传诊断" });
    app.request({
      method: "POST",
      url: "/api/h5/recorder/diag",
      data: {
        device: this.data.device,
        items: (this.data.items || []).slice(0, 20).map((x) => ({ id: x.id, name: x.name, fileName: x.fileName, status: x.status })),
        lines
      },
      timeout: 60000
    })
      .then((data) => {
        const id = String((data && data.diag_id) || "");
        this.setData({ diagId: id });
        wx.showToast({ title: id ? "已上传：" + id : "已上传", icon: "none" });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => wx.hideLoading());
  },

  setDevice(patch) {
    this.setData({ device: Object.assign({}, this.data.device, patch) });
  },

  syncDeviceState() {
    const ble = this._ble;
    this._logDiag("device", { connected: !!(ble && ble.isConnected) });
    if (!ble || !ble.isConnected) {
      this.setDevice({ connected: false, isRecording: false, isPaused: false, timeText: "00:00:00" });
      return;
    }
    this.setDevice({ connected: true });
    try {
      ble.fetchAllDeviceInfo();
      ble.fetchDeviceInfo();
    } catch (err) {}
  },

  _bindBle() {
    const ble = this._ble;
    if (!ble) return;
    ble.on("onConnected", (device) => {
      this._logDiag("ble:connected", device);
      this.setDevice({ connected: true, name: String((device && device.name) || this.data.device.name || "录音设备") });
      try { ble.fetchAllDeviceInfo(); } catch (err) {}
    });
    ble.on("onDisconnected", () => {
      this._logDiag("ble:disconnected");
      this._stopTimer();
      this.setDevice({ connected: false, isRecording: false, isPaused: false, timeText: "00:00:00" });
    });
    ble.on("onDeviceInfoUpdated", (device) => {
      if (!device) return;
      this.setDevice({
        name: String(device.name || this.data.device.name || "录音设备"),
        battery: typeof device.battery === "number" ? device.battery : this.data.device.battery,
        isCharging: Boolean(device.isCharging)
      });
    });
    ble.on("onRecordStateChanged", (recording) => {
      if (recording) {
        if (!this.data.device.isRecording) {
          this._startAt = Date.now();
          this._pausedElapsed = 0;
          this.setDevice({ isRecording: true, isPaused: false });
          this._startTimer();
        }
      } else if (this.data.device.isRecording && !this._justStarted) {
        this._stopTimer();
        this.setDevice({ isRecording: false, isPaused: false, timeText: "00:00:00" });
        setTimeout(() => { this.reload(); try { ble.fetchFileList(); } catch (err) {} }, 3000);
      }
      this._justStarted = false;
    });
    ble.on("onRecordPaused", () => {
      if (this.data.device.isPaused) {
        this.setDevice({ isPaused: false });
        this._startTimer();
      } else {
        this._pausedElapsed += Date.now() - (this._startAt || Date.now());
        this._stopTimer();
        this.setDevice({ isPaused: true });
      }
    });
    ble.on("onRecordStatusQueried", (recording, paused) => {
      this.setDevice({ isRecording: Boolean(recording), isPaused: Boolean(paused) });
      if (recording && !paused) {
        if (!this._startAt) { this._startAt = Date.now(); this._pausedElapsed = 0; }
        this._startTimer();
      } else if (recording && paused) {
        this._stopTimer();
      }
    });
    ble.on("onFileListUpdated", (files) => {
      const list = Array.isArray(files) ? files : [];
      this._logDiag("files", { count: list.length, names: list.slice(0, 8).map((f) => String((f && f.fileName) || "")) });
      this._autoSyncDeviceFiles(files);
    });
    ble.on("onFileDownloadComplete", (file, localPath) => {
      this._logDiag("downloaded", { file: String((file && file.fileName) || ""), path: String(localPath || "").slice(-40) });
      this._uploadDeviceFile(file, localPath);
    });
    ble.on("onFileDownloadProgress", (file, progress) => {
      this._logDiag("download", { file: String((file && file.fileName) || ""), progress: Number(progress || 0) });
      this.setData({ syncText: "正在下载 " + String((file && file.fileName) || "录音") + " · " + Number(progress || 0) + "%" });
    });
    ble.on("onFileDownloadFailed", (file, reason) => {
      this._logDiag("download:failed", { file: String((file && file.fileName) || ""), reason: String(reason || "") });
      this.setData({ syncText: "下载失败：" + String(reason || "设备下载失败") });
    });
    ble.on("onRecordDurationUpdated", (sec) => {
      this._pausedElapsed = 0;
      this._startAt = Date.now() - Number(sec || 0) * 1000;
      this.setDevice({ timeText: this._fmtDuration(sec) });
    });
  },

  /* 停录后把设备上的新文件自动拉下来并入库（H5 是原生桥做的，小程序自己补） */
  _autoSyncDeviceFiles(files) {
    const ble = this._ble;
    const list = Array.isArray(files) ? files : [];
    if (!ble || !list.length) return;
    const known = {};
    (this.data.items || []).forEach((row) => {
      if (row.fileName) known[String(row.fileName)] = true;
    });
    const todo = list.filter((file) => {
      const name = String((file && file.fileName) || "");
      if (!name || known[name]) return false;
      if (this._uploaded && this._uploaded[name]) return false;
      if (this._uploading && this._uploading[name]) return false;
      return true;
    });
    this._logDiag("sync:plan", { total: list.length, missing: todo.length, known: (this.data.items || []).length });
    if (!todo.length) return;
    this._syncQueue = (this._syncQueue || []).concat(todo);
    if (this._syncing) return;
    this._runSyncQueue();
  },

  _runSyncQueue() {
    const ble = this._ble;
    const queue = this._syncQueue || [];
    if (!ble || !queue.length) {
      this._syncing = false;
      this.setData({ syncText: "" });
      return;
    }
    this._syncing = true;
    const file = queue.shift();
    const name = String((file && file.fileName) || "");
    const local = ble.getLocalPath ? ble.getLocalPath(name) : "";
    if (local) {
      this._uploadDeviceFile(file, local);
      return;
    }
    this.setData({ syncText: "正在从设备下载 " + (name || "录音") + "…" });
    this._uploading = this._uploading || {};
    this._uploading[name] = true;
    try {
      ble.downloadFile(file);
    } catch (err) {
      this._uploading[name] = false;
      this.setData({ syncText: "下载失败：" + (name || "录音") });
      this._runSyncQueue();
    }
  },

  _uploadDeviceFile(file, localPath) {
    const name = String((file && file.fileName) || "");
    if (!localPath || !name) { this._runSyncQueue(); return; }
    this._uploading = this._uploading || {};
    this._uploading[name] = true;
    this._logDiag("upload:start", { file: name, path: String(localPath).slice(-40) });
    this.setData({ syncText: "正在上传 " + name + "…" });
    api.uploadFile({
      url: "/api/h5/recorder/files",
      filePath: localPath,
      name: "file",
      formData: {
        source_type: "device",
        device_name: String((this.data.device && this.data.device.name) || "录音笔")
      },
      token: app.globalData.token || "",
      timeout: 600000
    })
      .then(() => {
        this._logDiag("upload:ok", { file: name });
        this._uploaded = this._uploaded || {};
        this._uploaded[name] = true;
        wx.showToast({ title: "已上传 " + name, icon: "none" });
        this.reload();
      })
      .catch((err) => {
        this._logDiag("upload:failed", { file: name, error: api.errorMessage(err) });
        this.setData({ syncText: "上传失败：" + name + " · " + api.errorMessage(err) });
      })
      .then(() => {
        this._uploading[name] = false;
        this._runSyncQueue();
      });
  },

  _fmtDuration(sec) {
    const total = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => (n < 10 ? "0" + n : String(n));
    return pad(h) + ":" + pad(m) + ":" + pad(s);
  },

  _startTimer() {
    this._stopTimer();
    const tick = () => {
      if (!this.data.device.isRecording || this.data.device.isPaused) return;
      const elapsed = Math.floor((this._pausedElapsed + (Date.now() - (this._startAt || Date.now()))) / 1000);
      this.setDevice({ timeText: this._fmtDuration(elapsed) });
    };
    tick();
    this._timer = setInterval(tick, 1000);
  },

  _stopTimer() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  },

  startRecording() {
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) return;
    this._justStarted = true;
    this._startAt = Date.now();
    this._pausedElapsed = 0;
    this.setDevice({ isRecording: true, isPaused: false, timeText: "00:00:00" });
    this._startTimer();
    try {
      ble.startRecord();
      wx.showToast({ title: "已开始录音", icon: "none" });
    } catch (err) {
      this._stopTimer();
      this.setDevice({ isRecording: false });
      wx.showToast({ title: "开始录音失败", icon: "none" });
    }
  },

  togglePause() {
    const ble = this._ble;
    if (!ble || !this.data.device.isRecording) return;
    if (this.data.device.isPaused) {
      try { ble.resumeRecord(); } catch (err) {}
      this.setDevice({ isPaused: false });
      this._startTimer();
    } else {
      try { ble.pauseRecord(); } catch (err) {}
      this._pausedElapsed += Date.now() - (this._startAt || Date.now());
      this._stopTimer();
      this.setDevice({ isPaused: true });
    }
  },

  stopRecording() {
    const ble = this._ble;
    if (!ble || !this.data.device.isRecording) return;
    try { ble.endRecord(); } catch (err) {}
    this._stopTimer();
    this.setDevice({ isRecording: false, isPaused: false, timeText: "00:00:00" });
    wx.showToast({ title: "已发送停止指令，设备生成文件后自动出现", icon: "none" });
    setTimeout(() => {
      this.reload();
      try { ble.fetchFileList(); } catch (err) {}
    }, 5000);
  },

  refreshDeviceFiles() {
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) {
      wx.showToast({ title: "录音中，请先停止再刷新", icon: "none" });
      return;
    }
    try { ble.fetchFileList(); } catch (err) {}
    wx.showToast({ title: "已请求设备文件列表", icon: "none" });
  },

  onPullDownRefresh() {
    this.reload().then(() => wx.stopPullDownRefresh()).catch(() => wx.stopPullDownRefresh());
  },

  reload() {
    this.setData({ items: [], page: 1 });
    return this.fetchPage(1);
  },

  switchTab(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    if (index === this.data.tabIndex) return;
    this.setData({ tabIndex: index, items: [], page: 1, hasMore: false });
    this.fetchPage(1);
  },

  fetchPage(page) {
    const type = TABS[this.data.tabIndex].key;
    const suffix = type ? `&source_type=${type}` : "";
    this.setData({ loading: true });
    return app.request({ url: `/api/h5/recorder/files?page=${page}&page_size=20${suffix}`, timeout: 30000 })
      .then((data) => {
        const rows = ((data && data.items) || []).map((row) => ({
          id: row.id,
          name: String(row.display_name || row.file_name || "录音"),
          fileName: String(row.file_name || ""),
          status: String(row.status || ""),
          statusText: STATUS_TEXT[row.status] || row.status || "",
          sourceLabel: String(row.source_label || row.device_name || "音频"),
          summary: String(row.summary_text || "").slice(0, 80),
          time: fmtTime(row.created_at)
        }));
        const merged = page === 1 ? rows : this.data.items.concat(rows);
        this.setData({ items: merged, page, hasMore: Boolean(data && data.has_next) });
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    this.fetchPage(this.data.page + 1);
  },

  openDetail(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.items[index];
    if (!row) return;
    wx.navigateTo({ url: `/pages/secretary-detail/secretary-detail?id=${row.id}` });
  },

  upload() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["mp3", "wav", "m4a", "aac", "opus"],
      success: (res) => {
        const file = (res.tempFiles || [])[0];
        if (!file) return;
        wx.showLoading({ title: "上传中" });
        api.uploadFile({
          url: "/api/h5/recorder/files",
          filePath: file.path,
          name: "file",
          formData: { source_type: "local", device_name: "" },
          token: app.globalData.token || "",
          timeout: 600000
        })
          .then(() => {
            wx.showToast({ title: "已上传，转写中", icon: "success" });
            this.reload();
          })
          .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
          .then(() => wx.hideLoading());
      }
    });
  },

  goDevices() {
    wx.navigateTo({ url: "/pages/airec/scan/scan" });
  }
});