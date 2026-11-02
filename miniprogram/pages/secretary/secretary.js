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
    }
  },

  onLoad() {
    this._ble = AIRECBleManager.getInstance();
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

  setDevice(patch) {
    this.setData({ device: Object.assign({}, this.data.device, patch) });
  },

  syncDeviceState() {
    const ble = this._ble;
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
      this.setDevice({ connected: true, name: String((device && device.name) || this.data.device.name || "录音设备") });
      try { ble.fetchAllDeviceInfo(); } catch (err) {}
    });
    ble.on("onDisconnected", () => {
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
    ble.on("onRecordDurationUpdated", (sec) => {
      this._pausedElapsed = 0;
      this._startAt = Date.now() - Number(sec || 0) * 1000;
      this.setDevice({ timeText: this._fmtDuration(sec) });
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