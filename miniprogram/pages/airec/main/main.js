const AIRECBleManager = require('../../../utils/airec/ble-manager')

const PAGE_SIZE = 10

Page({
  data: {
    connected: false,
    deviceName: '',
    macAddress: '',
    battery: 0,
    isCharging: false,
    storageStr: '--',
    firmwareVersion: '',
    settings: {
      ledSwitch: true,
      noiseSwitch: true,
      usbSwitch: false,
      powerOnRecord: false,
      diskFormatSupport: false,
      segmentDuration: 0,
      idleShutdown: 0,
      micGain: 3,
    },
    // 本地 UI 状态（无对应 SDK 命令）
    decibelEnabled: false,
    // 录音
    isRecording: false,
    isPaused: false,
    recordTimeStr: '00:00:00',
    waveBars: [20, 20, 20, 20, 20],
    // 文件
    currentFilter: 0,
    displayFiles: [],
    hasMore: false,
  },

  _ble: null,
  _allFiles: [],
  _filteredFiles: [],
  _currentPage: 0,
  _recordStartDate: null,
  _pausedElapsed: 0,
  _timerInterval: null,
  _isResumingFromPause: false,
  _appInitiatedPauseResume: false,
  _waveTimer: null,
  _justStarted: false,

  onLoad() {
    this._ble = AIRECBleManager.getInstance()
    this._registerCallbacks()
  },

  onShow() {
    this._registerCallbacks()
    if (this._ble.isConnected) {
      this._showConnectedUI()
      // 从其他页面返回时，重新查询设备信息
      this._ble.fetchAllDeviceInfo()
    }
  },

  _registerCallbacks() {
    const ble = this._ble

    ble.on('onConnected', (device) => {
      this._showConnectedUI()
      // 连接成功后立即开始查询设备信息
      ble.fetchAllDeviceInfo()
      // 延迟查询文件列表（等设备信息查完）
      setTimeout(() => {
        if (!ble.isConnected) return
        ble.fetchFileList()
      }, 5500)
    })

    ble.on('onDisconnected', (device, reason) => {
      this._stopTimer()
      this._stopWave()
      this.setData({
        connected: false,
        isRecording: false,
        isPaused: false,
        recordTimeStr: '00:00:00',
        displayFiles: [],
      })
      if (reason && reason !== 'disconnected') {
        wx.showToast({ title: reason, icon: 'none' })
      }
    })

    ble.on('onDeviceInfoUpdated', (device) => {
      if (!device) return
      this.setData({
        battery: device.battery,
        isCharging: device.isCharging,
        storageStr: ble.formatStorageStr(),
        macAddress: ble.settings.macAddress,
      })
    })

    ble.on('onFirmwareVersionReceived', (ver) => {
      this.setData({ firmwareVersion: ver })
    })

    ble.on('onInitParamUpdated', (settings) => {
      this.setData({ settings: { ...settings } })
    })

    ble.on('onFileListUpdated', (files) => {
      this._allFiles = files.map(f => ({
        ...f,
        downloaded: ble.isDownloaded(f.fileName),
        downloading: false,
        progress: 0,
      }))
      this._applyFilter()
    })

    ble.on('onFileDeleted', (fileName, success) => {
      wx.showToast({ title: success ? '删除成功' : '删除失败', icon: 'none' })
      if (success) setTimeout(() => ble.fetchFileList(), 500)
    })

    ble.on('onRecordStateChanged', (recording, fileName) => {
      if (recording) {
        // 设备确认开始录音，如果本地还没开始则同步
        if (!this.data.isRecording) {
          this._recordStartDate = Date.now()
          this._pausedElapsed = 0
          this.setData({ isRecording: true, isPaused: false })
          this._startTimer()
          if (this.data.decibelEnabled) this._startWave()
        }
      } else {
        // 设备确认录音结束，只有本地也认为在录音时才重置
        // 避免延迟包把刚开始的录音状态误重置
        if (this.data.isRecording && !this._justStarted) {
          this._stopTimer()
          this._stopWave()
          this.setData({ isRecording: false, isPaused: false, recordTimeStr: '00:00:00' })
        }
        this._justStarted = false
      }
    })

    ble.on('onRecordPaused', () => {
      // App 主动发的已在按钮处理，只清标志
      if (this._appInitiatedPauseResume) {
        this._appInitiatedPauseResume = false
        return
      }
      // 设备按键触发
      if (!this.data.isPaused) {
        this._pausedElapsed += Date.now() - this._recordStartDate
        this._stopTimer()
        this._stopWave()
        this.setData({ isPaused: true })
      } else {
        this._isResumingFromPause = true
        this.setData({ isPaused: false })
        this._startTimer()
        if (this.data.decibelEnabled) this._startWave()
      }
    })

    ble.on('onRecordStatusQueried', (recording, paused, fileName) => {
      this.setData({ isRecording: recording, isPaused: paused })
      if (recording && !paused) {
        if (!this._recordStartDate) {
          this._recordStartDate = Date.now()
          this._pausedElapsed = 0
        }
        this._startTimer()
      } else if (recording && paused) {
        // 暂停中，停止计时但保持状态
        this._stopTimer()
      }
    })

    ble.on('onRecordDurationUpdated', (sec) => {
      // 以设备时间为准，重置本地计时起点
      this._pausedElapsed = 0
      this._recordStartDate = Date.now() - sec * 1000
      // 如果计时器没在跑，启动它
      if (this.data.isRecording && !this.data.isPaused && !this._timerInterval) {
        this._startTimer()
      }
      this.setData({ recordTimeStr: this._formatTime(sec) })
    })
    ble.on('onFileDownloadProgress', (file, progress) => {
      const displayFiles = this.data.displayFiles.map(f =>
        f.fileName === file.fileName ? { ...f, downloading: true, progress } : f
      )
      this.setData({ displayFiles })
    })

    ble.on('onFileDownloadComplete', (file, localPath) => {
      wx.showToast({ title: '下载完成', icon: 'success' })
      const displayFiles = this.data.displayFiles.map(f =>
        f.fileName === file.fileName ? { ...f, downloading: false, downloaded: true, progress: 100 } : f
      )
      this._allFiles = this._allFiles.map(f =>
        f.fileName === file.fileName ? { ...f, downloaded: true } : f
      )
      this.setData({ displayFiles })
    })

    ble.on('onFileDownloadFailed', (file, reason) => {
      wx.showToast({ title: reason || '下载失败', icon: 'none' })
      const displayFiles = this.data.displayFiles.map(f =>
        f.fileName === file.fileName ? { ...f, downloading: false, progress: 0 } : f
      )
      this.setData({ displayFiles })
    })

    ble.on('onFormatResult', (success, message) => {
      wx.showToast({ title: message, icon: success ? 'success' : 'none' })
      if (success) setTimeout(() => ble.fetchDeviceInfo(), 1000)
    })

    ble.on('onBluetoothStateChanged', (enabled) => {
      if (!enabled) {
        wx.showToast({ title: '蓝牙已关闭', icon: 'none' })
      }
    })
  },

  _showConnectedUI() {
    const dev = this._ble.connectedDevice
    this.setData({
      connected: true,
      deviceName: dev ? dev.name : 'AIREC',
      battery: dev ? dev.battery : 0,
      isCharging: dev ? dev.isCharging : false,
      storageStr: this._ble.formatStorageStr(),
    })
  },

  // ─── 事件处理 ──────────────────────────────────────────────────────────

  onConnectTap() {
    wx.navigateTo({ url: '/pages/airec/scan/scan' })
  },

  onDisconnectTap() {
    wx.showModal({
      title: '断开连接',
      content: '确定断开蓝牙连接？',
      success: (res) => {
        if (res.confirm) {
          this._ble.disconnect()
          this._stopTimer()
          this._stopWave()
          this.setData({
            connected: false,
            isRecording: false,
            isPaused: false,
            recordTimeStr: '00:00:00',
            displayFiles: [],
          })
        }
      },
    })
  },

  onOtaTap() {
    const dev = this._ble.connectedDevice
    const ver = dev && dev.firmwareVersion ? dev.firmwareVersion : '未知'
    wx.showModal({
      title: 'OTA 固件升级',
      content: '当前固件版本：' + ver + '\n\n升级步骤：\n1. 将固件文件(.bin)放入设备存储根目录\n2. 重命名为 update.bin\n3. 重启设备，设备将自动完成升级',
      showCancel: false,
      confirmText: '知道了',
    })
  },

  // 录音
  onStartRecTap() {
    if (this.data.isRecording) return
    this._recordStartDate = Date.now()
    this._pausedElapsed = 0
    this._justStarted = true
    this.setData({ isRecording: true, isPaused: false })
    this._startTimer()
    // 注册实时音频流日志
    this._ble.startRecord()
    setTimeout(() => { this._justStarted = false }, 3000)
  },

  onPauseRecTap() {
    if (this._appInitiatedPauseResume) return // 防抖
    this._appInitiatedPauseResume = true
    if (this.data.isPaused) {
      // 继续
      this._isResumingFromPause = true
      this.setData({ isPaused: false })
      this._startTimer()
      if (this.data.decibelEnabled) this._startWave()
    } else {
      // 暂停
      this._pausedElapsed += Date.now() - this._recordStartDate
      this._stopTimer()
      this._stopWave()
      this.setData({ isPaused: true })
    }
    this._ble.pauseRecord()
  },

  onStopRecTap() {
    this._justStarted = false
    this._ble.off('onAudioStreamData')
    this._stopTimer()
    this._stopWave()
    this.setData({ isRecording: false, isPaused: false, recordTimeStr: '00:00:00' })
    this._ble.endRecord()
    // 主动刷新文件列表（3秒后设备应已保存完成）
    setTimeout(() => {
      if (this._ble.isConnected) this._ble.fetchFileList()
    }, 3500)
  },

  // 文件
  onRefreshFiles() {
    this._ble.fetchFileList()
  },

  onFilterTap(e) {
    const filter = parseInt(e.currentTarget.dataset.filter)
    this.setData({ currentFilter: filter })
    this._applyFilter()
  },

  onLoadMore() {
    this._currentPage++
    this._updateDisplayFiles()
  },

  onFileTap(e) {
    const idx = e.currentTarget.dataset.index
    const file = this.data.displayFiles[idx]
    if (file.downloaded) {
      const path = this._ble.getLocalPath(file.fileName)
      if (path) {
        wx.navigateTo({
          url: `/pages/airec/player/player?path=${encodeURIComponent(path)}&fileName=${encodeURIComponent(file.fileName)}&fileSize=${encodeURIComponent(file.fileSizeStr || '')}`,
        })
      }
    }
  },

  onDownloadTap(e) {
    const idx = e.currentTarget.dataset.index
    const file = this.data.displayFiles[idx]
    if (this.data.isRecording) {
      wx.showToast({ title: '录音中无法下载', icon: 'none' })
      return
    }
    this._ble.downloadFile(file)
  },

  onDeleteTap(e) {
    const idx = e.currentTarget.dataset.index
    const file = this.data.displayFiles[idx]
    wx.showModal({
      title: '删除文件',
      content: `确定删除 ${file.fileName}？`,
      success: (res) => {
        if (res.confirm) this._ble.deleteFile(file.fileName)
      },
    })
  },

  _playAudio(path, fileName) {
    wx.navigateTo({
      url: `/pages/airec/player/player?path=${encodeURIComponent(path)}&fileName=${encodeURIComponent(fileName)}&fileSize=`,
    })
  },

  // ─── 设备设置 ──────────────────────────────────────────────────────────

  onLedChange(e) { this._ble.setLedSwitch(e.detail.value) },
  onNoiseChange(e) { this._ble.setNoiseSwitch(e.detail.value) },
  onUsbChange(e) { this._ble.setUsbSwitch(e.detail.value) },
  onPowerOnRecChange(e) { this._ble.setPowerOnRecord(e.detail.value) },
  onDecibelChange(e) {
    const on = e.detail.value
    this.setData({ decibelEnabled: on })
    if (on && this.data.isRecording && !this.data.isPaused) {
      this._startWave()
    } else if (!on) {
      this._stopWave()
    }
  },

  onSegmentTap() {
    const items = ['不分段', '5分钟', '10分钟', '15分钟', '30分钟', '60分钟', '2小时', '3小时', '4小时', '8小时']
    const values = [0, 5, 10, 15, 30, 60, 120, 180, 240, 480]
    wx.showActionSheet({
      itemList: items,
      success: (res) => {
        this._ble.setSegmentDuration(values[res.tapIndex])
      },
    })
  },

  onIdleTap() {
    const items = ['不关机', '3分钟', '5分钟', '10分钟', '15分钟', '30分钟', '60分钟', '2小时', '4小时']
    const values = [0, 3, 5, 10, 15, 30, 60, 120, 240]
    wx.showActionSheet({
      itemList: items,
      success: (res) => {
        this._ble.setIdleShutdown(values[res.tapIndex])
      },
    })
  },

  onMicGainTap() {
    const items = ['增益 1', '增益 2', '增益 3', '增益 4', '增益 5', '增益 6', '增益 7']
    wx.showActionSheet({
      itemList: items,
      success: (res) => {
        this._ble.setMicGain(res.tapIndex + 1)
      },
    })
  },

  onSafeCodeTap() {
    wx.showModal({
      title: '安全码',
      editable: true,
      placeholderText: '请输入安全码',
      success: (res) => {
        if (res.confirm && res.content) {
          wx.showToast({ title: '安全码已设置', icon: 'success' })
        }
      },
    })
  },

  onActivateCodeTap() {
    wx.showModal({
      title: '激活码',
      editable: true,
      placeholderText: '请输入激活码',
      success: (res) => {
        if (res.confirm && res.content) {
          wx.showToast({ title: '激活码已设置', icon: 'success' })
        }
      },
    })
  },

  onFormatTap() {
    wx.showModal({
      title: '格式化存储',
      content: '格式化将清除所有录音文件，确定继续？',
      confirmColor: '#E74C3C',
      success: (res) => {
        if (res.confirm) this._ble.formatDisk()
      },
    })
  },

  // ─── 文件过滤 & 分页 ──────────────────────────────────────────────────

  _applyFilter() {
    const filter = this.data.currentFilter
    if (filter === 0) {
      this._filteredFiles = [...this._allFiles]
    } else if (filter === 1) {
      // 设备文件（未下载）
      this._filteredFiles = this._allFiles.filter(f => !f.downloaded)
    } else {
      // 本地文件（已下载）
      this._filteredFiles = this._allFiles.filter(f => f.downloaded)
    }
    this._currentPage = 0
    this._updateDisplayFiles()
  },

  _updateDisplayFiles() {
    const end = (this._currentPage + 1) * PAGE_SIZE
    const displayFiles = this._filteredFiles.slice(0, end)
    this.setData({
      displayFiles,
      hasMore: end < this._filteredFiles.length,
    })
  },

  // ─── 计时器 ────────────────────────────────────────────────────────────

  _startTimer() {
    this._stopTimer()
    if (!this._recordStartDate) this._recordStartDate = Date.now()
    this._timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - this._recordStartDate + this._pausedElapsed) / 1000)
      this.setData({ recordTimeStr: this._formatTime(elapsed) })
    }, 1000)
  },

  _stopTimer() {
    if (this._timerInterval) {
      clearInterval(this._timerInterval)
      this._timerInterval = null
    }
  },

  _formatTime(sec) {
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = sec % 60
    return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
  },

  // ─── 声波动画 ──────────────────────────────────────────────────────────

  _startWave() {
    this._stopWave()
    const bars = [0, 1, 2, 3, 4]
    const animate = () => {
      const waveBars = bars.map(() => Math.floor(20 + Math.random() * 80))
      this.setData({ waveBars })
      this._waveTimer = setTimeout(animate, 150)
    }
    animate()
  },

  _stopWave() {
    if (this._waveTimer) {
      clearTimeout(this._waveTimer)
      this._waveTimer = null
    }
    this.setData({ waveBars: [20, 20, 20, 20, 20] })
  },
})
