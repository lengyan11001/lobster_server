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
      storageUsed: 0,
      storageTotal: 0,
      storageText: "",
      isCharging: false,
      isRecording: false,
      isPaused: false,
      timeText: "00:00:00"
    },
    syncText: "",
    syncing: false,
    segmentLabel: "分段设置",
    powerOnRecord: null,
    formatting: false,
    deviceFiles: [],
    listRows: [],
    diagOpen: false,
    diagLines: [],
    diagId: ""
  },

  onLoad() {
    this._ble = AIRECBleManager.getInstance();
    let envInfo = "";
    try {
      const acc = wx.getAccountInfoSync ? wx.getAccountInfoSync() : {};
      const mini = (acc && acc.miniProgram) || {};
      envInfo = " env=" + String(mini.envVersion || "") + " ver=" + String(mini.version || "");
    } catch (err) {}
    this._bleHandlers = [];
    this._bleBound = false;
    this._syncedNames = {};
    this._logDiag("page", "onLoad v2026-11-02-rec-fix13" + envInfo);
    this._bindBle();
  },

  onUnload() {
    this._stopTimer();
    try {
      (this._bleHandlers || []).forEach((h) => this._ble.off(h.event, h.fn));
    } catch (err) {}
    this._bleHandlers = [];
    this._bleBound = false;
  },

  onShow() {
    this.reload();
    this.syncDeviceState();
    this._loadSyncedNames();
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
    this.setData({ diagLines: lines.slice(-60) });
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
      this._logDiag("files:request", "onShow");
      // 连上后设备还在回设备信息，隔 1.5s 再要文件列表，避免写队列堆积
      clearTimeout(this._firstListTimer);
      this._firstListTimer = setTimeout(() => this._requestFileList("onShow+1.5s"), 1500);
    } catch (err) {
      this._logDiag("files:request:error", String((err && err.message) || err));
    }
  },

  // 设备上报的存储单位是 KB（SDK getStorageStr 也是 /1024 显示 MB）
  _sizeText(kb) {
    const bytes = Number(kb || 0) * 1024;
    if (!bytes) return "0";
    if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(1) + "G";
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + "M";
    return Math.round(bytes / 1024) + "K";
  },

  _onBle(event, fn) {
    this._ble.on(event, fn);
    (this._bleHandlers = this._bleHandlers || []).push({ event, fn });
  },

  _bindBle() {
    const ble = this._ble;
    if (!ble || this._bleBound) return;
    this._bleBound = true;
    this._onBle("onConnected", (device) => {
      this._logDiag("ble:connected", device);
      this.setDevice({ connected: true, name: String((device && device.name) || this.data.device.name || "录音设备") });
      try { ble.fetchAllDeviceInfo(); } catch (err) {}
    });
    this._onBle("onDisconnected", () => {
      this._logDiag("ble:disconnected");
      if (this.data.formatting) {
        clearTimeout(this._formatTimer);
        this.setData({ formatting: false });
        wx.showModal({
          title: "设备已断开",
          content: "格式化时设备会重启并断开蓝牙，这是正常的。请等 30 秒左右再点「连接设备」重新连接。",
          showCancel: false
        });
      }
      this._stopTimer();
      this.setDevice({ connected: false, isRecording: false, isPaused: false, timeText: "00:00:00" });
    });
    this._onBle("onFormatResult", (success, message) => {
      clearTimeout(this._formatTimer);
      this.setData({ formatting: false });
      this._logDiag("format:result", { success: Boolean(success), message: String(message || "") });
      wx.showToast({ title: success ? "格式化完成" : String(message || "格式化失败"), icon: "none" });
      if (success) {
        setTimeout(() => {
          try { ble.fetchAllDeviceInfo(); } catch (err) {}
          this._emptyRetryLeft = 3;
          this._requestFileList("after-format");
        }, 3000);
      }
    });
    this._onBle("onInitParamUpdated", (settings) => {
      const minutes = Number((settings && settings.segmentDuration) || 0);
      this._logDiag("init:param", {
        segmentDuration: minutes,
        powerOnRecord: settings ? Boolean(settings.powerOnRecord) : null,
        micGain: settings ? settings.micGain : null,
        usbSwitch: settings ? Boolean(settings.usbSwitch) : null,
        diskFormatSupport: settings ? Boolean(settings.diskFormatSupport) : null
      });
      this.setData({
        segmentLabel: minutes > 0 ? "分段 " + minutes + "分钟" : "不分段",
        powerOnRecord: settings && typeof settings.powerOnRecord !== "undefined" ? Boolean(settings.powerOnRecord) : this.data.powerOnRecord
      });
    });
    this._onBle("onDeviceInfoUpdated", (device) => {
      if (!device) return;
      const used = Number(device.storageUsed || 0);
      const total = Number(device.storageTotal || 0);
      if (this._formatCheckAt && Date.now() - this._formatCheckAt > 4000 && this._formatUsedBefore > 0) {
        const before = this._formatUsedBefore;
        this._formatCheckAt = 0;
        this._formatUsedBefore = 0;
        if (used >= before * 0.9) {
          wx.showModal({
            title: "格式化没有生效",
            content: "存储仍是 " + this._sizeText(used) + "/" + this._sizeText(total) + "，设备的蓝牙格式化没有释放空间。请在录音笔上直接格式化，或换卡/升级固件。",
            showCancel: false
          });
        }
      }
      this.setDevice({
        name: String(device.name || this.data.device.name || "录音设备"),
        battery: typeof device.battery === "number" ? device.battery : this.data.device.battery,
        isCharging: Boolean(device.isCharging),
        storageUsed: used,
        storageTotal: total,
        storageText: total > 0 ? this._sizeText(used) + "/" + this._sizeText(total) : ""
      });
    });
    this._onBle("onRecordStateChanged", (recording) => {
      this._logDiag("record:state", { recording: Boolean(recording) });
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
        this._emptyRetryLeft = 3;
        setTimeout(() => { this.reload(); try { ble.fetchFileList(); } catch (err) {} }, 3000);
      }
      this._justStarted = false;
    });
    this._onBle("onRecordPaused", () => {
      this._logDiag("record:paused");
      if (this.data.device.isPaused) {
        this.setDevice({ isPaused: false });
        this._startTimer();
      } else {
        this._pausedElapsed += Date.now() - (this._startAt || Date.now());
        this._stopTimer();
        this.setDevice({ isPaused: true });
      }
    });
    this._onBle("onRecordStatusQueried", (recording, paused) => {
      this._logDiag("record:queried", { recording: Boolean(recording), paused: Boolean(paused) });
      this.setDevice({ isRecording: Boolean(recording), isPaused: Boolean(paused) });
      if (recording && !paused) {
        if (!this._startAt) { this._startAt = Date.now(); this._pausedElapsed = 0; }
        this._startTimer();
      } else if (recording && paused) {
        this._stopTimer();
      }
    });
    this._onBle("onFileListUpdated", (files) => {
      this._listInFlightAt = 0;
      const list = Array.isArray(files) ? files : [];
      this._logDiag("files", { count: list.length, names: list.slice(0, 8).map((f) => String((f && f.fileName) || "")) });
      if (this._ble && this._ble.lastFilesMeta) this._logDiag("files:meta", this._ble.lastFilesMeta);
      this._renderDeviceFiles(list);
    });
    this._onBle("onFileDownloadComplete", (file, localPath) => {
      this._logDiag("downloaded", { file: String((file && file.fileName) || ""), path: String(localPath || "").slice(-40) });
      this._uploadDeviceFile(file, localPath);
    });
    this._onBle("onFileDownloadProgress", (file, progress) => {
      const name = String((file && file.fileName) || "录音");
      const pct = Math.max(0, Math.min(100, Number(progress || 0)));
      this._lastProgress = this._lastProgress || {};
      const prev = this._lastProgress[name];
      const percentStep = pct >= 100 || !prev || Math.abs(pct - prev.pct) >= 10;
      const timeStep = !prev || Date.now() - prev.at >= 1200;
      this._lastProgress[name] = { pct, at: prev ? prev.at : 0 };
      if (percentStep) {
        this._logDiag("download", { file: name, progress: pct });
      }
      if (percentStep || timeStep) {
        this._lastProgress[name].at = Date.now();
        this.setData({ syncText: "正在下载 " + name + " · " + pct + "%" });
      }
    });
    this._onBle("onFileDownloadFailed", (file, reason) => {
      this._logDiag("download:failed", { file: String((file && file.fileName) || ""), reason: String(reason || "") });
      this.setData({ syncText: "下载失败：" + String(reason || "设备下载失败") });
    });
    this._onBle("onRecordDurationUpdated", (sec) => {
      this._pausedElapsed = 0;
      this._startAt = Date.now() - Number(sec || 0) * 1000;
      this.setDevice({ timeText: this._fmtDuration(sec) });
    });
  },

  /* 停录后把设备上的新文件自动拉下来并入库（H5 是原生桥做的，小程序自己补） */
  _normalizeName(name) {
    return String(name || "")
      .trim()
      .toLowerCase()
      .replace(/\.(opus|atw|wav|mp3|m4a|aac|ogg|amr|silk)$/i, "");
  },

  /* 只列设备文件，由用户自己点“同步”（H5 就是这个逻辑） */
  _renderDeviceFiles(files) {
    const list = Array.isArray(files) ? files : [];
    const synced = this._syncedNames || {};
    (this.data.items || []).forEach((row) => {
      if (!row.fileName) return;
      synced[this._normalizeName(row.fileName)] = row.id;
    });
    const rows = list
      .slice()
      .sort((a, b) => String((b && b.fileName) || "").localeCompare(String((a && a.fileName) || "")))
      .map((file) => {
        const name = String((file && file.fileName) || "");
        const key = this._normalizeName(name);
        return {
          fileName: name,
          sizeText: String((file && (file.fileSizeStr || file.fileSize)) || ""),
          durationText: String((file && file.durationStr) || ""),
          synced: Boolean(synced[key]),
          recordId: synced[key] || 0
        };
      });
    this._logDiag("files:render", { total: rows.length, synced: rows.filter((x) => x.synced).length });
    this._lastDeviceList = list;
    this.setData({ deviceFiles: rows }, () => this._rebuildList());
    if (rows.length) {
      this._emptyRetryLeft = 0;
    } else if ((this._emptyRetryLeft || 0) > 0) {
      this._emptyRetryLeft -= 1;
      this._logDiag("files:retry", { left: this._emptyRetryLeft });
      setTimeout(() => this._requestFileList("empty-retry"), 2500);
    }
  },

  /* 设备文件不再单独一块列表，和“设备录音”列表合成同一个列表 */
  _rebuildList() {
    const type = TABS[this.data.tabIndex].key;
    const showDevice = type === "" || type === "device";
    const deviceRows = showDevice
      ? (this.data.deviceFiles || [])
          .filter((f) => f && f.fileName && !f.synced)
          .map((f) => ({
            kind: "device",
            key: "dev:" + f.fileName,
            fileName: f.fileName,
            name: f.fileName,
            meta: [f.durationText, f.sizeText, "未同步"].filter(Boolean).join(" · ")
          }))
      : [];
    const recRows = (this.data.items || []).map((r) => ({
      kind: "record",
      key: "rec:" + r.id,
      id: r.id,
      name: r.name,
      status: r.status,
      statusText: r.statusText,
      summary: r.summary,
      meta: [r.sourceLabel, r.time].filter(Boolean).join(" · ")
    }));
    this.setData({ listRows: deviceRows.concat(recRows) });
  },

  /* 拉全设备录音的已同步名单，避免把已入库的老文件又显示成“未同步” */
  _loadSyncedNames() {
    const map = {};
    const walk = (page) => app.request({
      url: `/api/h5/recorder/files?page=${page}&page_size=50&source_type=device`,
      timeout: 30000
    }).then((data) => {
      ((data && data.items) || []).forEach((row) => {
        if (row && row.file_name) map[this._normalizeName(row.file_name)] = row.id;
      });
      if (data && data.has_next && page < 10) return walk(page + 1);
    });
    return walk(1).then(() => {
      this._syncedNames = map;
      if (this._lastDeviceList) this._renderDeviceFiles(this._lastDeviceList);
    }).catch(() => {});
  },

  syncDeviceFile(e) {
    const name = String(e.currentTarget.dataset.name || "");
    const row = (this.data.deviceFiles || []).find((f) => f.fileName === name);
    if (!row) return;
    if (row.synced) {
      wx.showToast({ title: "这条已经同步过了", icon: "none" });
      return;
    }
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) {
      wx.showToast({ title: "录音中，请先停止", icon: "none" });
      return;
    }
    this._logDiag("sync:manual", { file: row.fileName });
    this._syncQueue = [{ fileName: row.fileName }];
    this._runSyncQueue();
  },

  deleteDeviceFile(e) {
    const name = String(e.currentTarget.dataset.name || "");
    const row = (this.data.deviceFiles || []).find((f) => f.fileName === name);
    if (!row) return;
    const ble = this._ble;
    const content = row.synced
      ? "同时删除服务器记录和设备里的文件（不可恢复）？"
      : "从录音设备删除这个文件（不可恢复）？";
    wx.showModal({
      title: "删除设备录音",
      content,
      success: (res) => {
        if (!res.confirm) return;
        const finish = () => {
          if (row.recordId) {
            app.request({ method: "DELETE", url: `/api/h5/recorder/files/${row.recordId}`, timeout: 30000 })
              .then(() => { this.reload(); this._requestFileList("after-delete"); })
              .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }));
          } else {
            this._requestFileList("after-delete");
          }
        };
        if (!ble || !ble.isConnected) {
          wx.showToast({ title: "设备未连接，只删服务器记录", icon: "none" });
          finish();
          return;
        }
        let done = false;
        const handler = (name, success) => {
          if (String(name || "") !== row.fileName) return;
          settle(Boolean(success));
        };
        const settle = (success) => {
          if (done) return;
          done = true;
          try { ble.off("onFileDeleted", handler); } catch (err) {}
          this._bleHandlers = (this._bleHandlers || []).filter((h) => h.fn !== handler);
          if (!success) wx.showToast({ title: "设备删除失败", icon: "none" });
          finish();
        };
        this._onBle("onFileDeleted", handler);
        try { ble.deleteFile(row.fileName); } catch (err) { settle(false); return; }
        setTimeout(() => settle(true), 3000);
      }
    });
  },

  _runSyncQueue() {
    const ble = this._ble;
    const queue = this._syncQueue || [];
    if (!ble || !queue.length) {
      this._syncing = false;
      this.setData({ syncing: false, syncText: "" });
      return;
    }
    this._syncing = true;
    this.setData({ syncing: true });
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

  _readHeader(path) {
    try {
      const fs = wx.getFileSystemManager();
      const fd = fs.openSync(path, "r");
      const buf = new ArrayBuffer(12);
      fs.readSync(fd, buf, 0, 12, 0);
      fs.closeSync(fd);
      return new Uint8Array(buf);
    } catch (err) {
      return null;
    }
  },

  /* 设备下载下来的可能是 ATW（私有格式），必须转成 Opus 才能上传转写 */
  _prepareUploadFile(name, localPath) {
    const rawName = String(name || "").replace(/^ble_/i, "");
    const head = this._readHeader(localPath);
    if (!head) return { ok: false, reason: "read_header_failed" };
    const isAtw = head[0] === 0x5B && head[1] === 0x50;
    const isKa = head[0] === 0x4B && head[1] === 0x41;
    let path = localPath;
    let ext = ".opus";
    if (isAtw || isKa) {
      try {
        const converter = require("../../utils/airec/atw-opus-converter");
        const converted = converter.convert(localPath);
        if (!converted) return { ok: false, reason: "atw_convert_failed" };
        path = converted;
      } catch (err) {
        return { ok: false, reason: "atw_convert_error" };
      }
    } else if (head[0] === 0x52 && head[1] === 0x49 && head[2] === 0x46 && head[3] === 0x46) {
      ext = ".wav";
    } else if (head[0] === 0x49 && head[1] === 0x44 && head[2] === 0x33) {
      ext = ".mp3";
    } else if (head[4] === 0x66 && head[5] === 0x74 && head[6] === 0x79 && head[7] === 0x70) {
      ext = ".m4a";
    } else if (!(head[0] === 0x4F && head[1] === 0x67 && head[2] === 0x67 && head[3] === 0x53)) {
      return { ok: false, reason: "unknown_container" };
    }
    try {
      const fs = wx.getFileSystemManager();
      const target = `${wx.env.USER_DATA_PATH}/${rawName}${ext}`;
      fs.copyFileSync(path, target);
      return { ok: true, path: target, name: rawName + ext };
    } catch (err) {
      return { ok: true, path, name: rawName + ext };
    }
  },

  _uploadDeviceFile(file, localPath) {
    const name = String((file && file.fileName) || "");
    if (!localPath || !name) { this._runSyncQueue(); return; }
    const prepared = this._prepareUploadFile(name, localPath);
    if (!prepared.ok) {
      this._logDiag("upload:skip", { file: name, reason: prepared.reason });
      this.setData({ syncText: "跳过 " + name + "（" + prepared.reason + "）" });
      this._runSyncQueue();
      return;
    }
    this._logDiag("upload:start", { file: prepared.name, from: name });
    this.setData({ syncText: "正在上传 " + prepared.name + "…" });
    api.uploadFile({
      url: "/api/h5/recorder/files",
      filePath: prepared.path,
      name: "file",
      formData: {
        source_type: "device",
        device_name: String((this.data.device && this.data.device.name) || "录音笔")
      },
      token: app.globalData.token || "",
      timeout: 600000
    })
      .then(() => {
        this._logDiag("upload:ok", { file: prepared.name });
        this._uploaded = this._uploaded || {};
        this._uploaded[name] = true;
        this._uploaded[this._normalizeName(name)] = true;
        wx.showToast({ title: "已上传 " + prepared.name, icon: "none" });
        this.reload();
        if (this._lastDeviceList) this._renderDeviceFiles(this._lastDeviceList);
      })
      .catch((err) => {
        this._logDiag("upload:failed", { file: prepared.name, error: api.errorMessage(err) });
        this.setData({ syncText: "上传失败：" + prepared.name + " · " + api.errorMessage(err) });
      })
      .then(() => {
        this._uploading = this._uploading || {};
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
    if (this.data.formatting) {
      wx.showToast({ title: "设备正在格式化，请稍后", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) return;
    this._logDiag("action:startRecord");
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
    this._logDiag("action:endRecord");
    try { ble.endRecord(); } catch (err) { this._logDiag("action:endRecord:error", String((err && err.message) || err)); }
    this._stopTimer();
    this.setDevice({ isRecording: false, isPaused: false, timeText: "00:00:00" });
    wx.showToast({ title: "已发送停止指令，设备生成文件后自动出现", icon: "none" });
    [3000, 8000, 15000].forEach((delay, idx) => {
      setTimeout(() => {
        this.reload();
        this._requestFileList("after-stop#" + (idx + 1));
      }, delay);
    });
  },

  _requestFileList(tag) {
    const ble = this._ble;
    if (!ble || !ble.isConnected) return;
    const now = Date.now();
    if (this._listInFlightAt && now - this._listInFlightAt < 8000) {
      this._logDiag("files:skip", { tag, reason: "in_flight" });
      return;
    }
    this._listInFlightAt = now;
    this._logDiag("files:request", tag);
    try {
      ble.fetchFileList();
    } catch (err) {
      this._logDiag("files:request:error", String((err && err.message) || err));
    }
  },

  stopSync() {
    this._syncQueue = [];
    this._queuedNames = {};
    this._syncing = false;
    this._listInFlightAt = 0;
    this.setData({ syncing: false, syncText: "已停止同步（设备上未同步的文件保持原样）" });
  },

  formatDeviceStorage() {
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) {
      wx.showToast({ title: "请先停止录音", icon: "none" });
      return;
    }
    wx.showModal({
      title: "格式化录音笔存储",
      content: "会清空录音笔内的全部录音，不可恢复。当前文件列表是 0 条且存储几乎占满，通常需要格式化才能恢复写入。确定继续？",
      confirmText: "格式化",
      confirmColor: "#dc2626",
      success: (res) => {
        if (!res.confirm) return;
        try { ble.formatDisk(); } catch (err) {}
        this._logDiag("format:request", "");
        this._formatUsedBefore = Number(this.data.device.storageUsed || 0);
        this._formatCheckAt = Date.now();
        this.setData({ formatting: true });
        wx.showLoading({ title: "格式化中" });
        setTimeout(() => wx.hideLoading(), 3000);
        clearTimeout(this._formatTimer);
        this._formatTimer = setTimeout(() => {
          if (!this.data.formatting) return;
          this.setData({ formatting: false });
          wx.showModal({
            title: "没收到格式化结果",
            content: "设备可能还在重启。请把录音笔关机再开机，然后重新连接查看存储占用。",
            showCancel: false
          });
        }, 45000);
      }
    });
  },

  togglePowerOnRecord() {
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.powerOnRecord === null) {
      try { ble.fetchInitParam(); } catch (err) {}
      wx.showToast({ title: "正在读取设备设置，请稍后再点", icon: "none" });
      return;
    }
    // 不用“取反”：显示值可能过期，取反会把“关”点成“开”。这里明确选要写入的值。
    wx.showActionSheet({
      itemList: ["关闭开机自动录音", "开启开机自动录音"],
      success: (res) => {
        const next = res.tapIndex === 1;
        try { ble.setPowerOnRecord(next); } catch (err) {}
        this._logDiag("poweron:set", { enabled: next });
        this.setData({ powerOnRecord: next });
        wx.showToast({ title: next ? "已设为开启" : "已设为关闭", icon: "none" });
        setTimeout(() => {
          try { ble.fetchInitParam(); } catch (err) {}
        }, 1200);
      }
    });
  },

  changeSegment() {
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    const options = [0, 5, 10, 15, 30, 60];
    const labels = ["不分段", "5分钟", "10分钟", "15分钟", "30分钟", "60分钟"];
    wx.showActionSheet({
      itemList: labels,
      success: (res) => {
        const minutes = options[res.tapIndex];
        try { ble.setSegmentDuration(minutes); } catch (err) {}
        this._logDiag("segment:set", { minutes });
        this.setData({ segmentLabel: minutes > 0 ? "分段 " + minutes + "分钟" : "不分段" });
        wx.showToast({ title: minutes > 0 ? "已设为每段" + minutes + "分钟" : "已设为不分段", icon: "none" });
      }
    });
  },

  refreshDeviceFiles() {
    this._emptyRetryLeft = 2;
    const ble = this._ble;
    if (!ble || !ble.isConnected) {
      wx.showToast({ title: "请先连接录音设备", icon: "none" });
      return;
    }
    if (this.data.formatting) {
      wx.showToast({ title: "设备正在格式化，请稍后", icon: "none" });
      return;
    }
    if (this.data.device.isRecording) {
      wx.showToast({ title: "设备正在录音，请先停止再刷新", icon: "none" });
      return;
    }
    this._listInFlightAt = 0;
    this._requestFileList("manual");
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
    this.setData({ tabIndex: index, items: [], listRows: [], page: 1, hasMore: false });
    this.fetchPage(1);
    if (TABS[index].key === "device") this._loadSyncedNames();
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
        this.setData({ items: merged, page, hasMore: Boolean(data && data.has_next) }, () => this._rebuildList());
      })
      .catch((err) => wx.showToast({ title: api.errorMessage(err), icon: "none" }))
      .then(() => this.setData({ loading: false }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    this.fetchPage(this.data.page + 1);
  },

  openRow(e) {
    const index = Number(e.currentTarget.dataset.index || 0);
    const row = this.data.listRows[index];
    if (!row || row.kind !== "record" || !row.id) return;
    wx.navigateTo({ url: `/pages/secretary-detail/secretary-detail?id=${row.id}` });
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