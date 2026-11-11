var AIRECBleManager = require('../../../utils/airec/ble-manager')

Page({
  data: {
    devices: [],
    scanning: true,
    statusText: '扫描中...',
    lastName: '',
    lastDeviceId: '',
    autoConnecting: false,
  },

  _seenIds: null,
  _ble: null,
  _handlers: null,
  _retried: false,
  _emptyTimer: null,
  _lastDevice: null,
  _autoConnectEnabled: false,
  _firstShow: true,
  _alive: true,
  _suppressDisconnect: false,

  onLoad: function() {
    var self = this
    this._seenIds = {}
    this._ble = AIRECBleManager.getInstance()
    this._handlers = []
    this._retried = false
    try { this._lastDevice = wx.getStorageSync('recorder_last_device') || null } catch (err) { this._lastDevice = null }
    // 已经在连着的时候进「更换设备」，不要再自动连回同一台
    this._wasConnectedOnLoad = this._ble.isConnected
    this._autoConnectEnabled = !this._wasConnectedOnLoad && !!(this._lastDevice && (this._lastDevice.deviceId || this._lastDevice.name))
    this.setData({
      lastName: (this._lastDevice && this._lastDevice.name) || '',
      lastDeviceId: (this._lastDevice && this._lastDevice.deviceId) || '',
    })

    this._onBle('onDeviceFound', function(device) {
      if (self._seenIds[device.deviceId]) return
      self._seenIds[device.deviceId] = true
      var devices = self.data.devices.concat([device])
      self.setData({
        devices: devices,
        scanning: true,
        statusText: self.data.autoConnecting
          ? self.data.statusText
          : ('发现 ' + devices.length + ' 台设备，点击连接'),
      })
      self._maybeAutoConnect(device)
    })

    this._onBle('onConnected', function(device) {
      try {
        wx.setStorageSync('recorder_last_device', { deviceId: device.deviceId, name: device.name })
      } catch (err) {}
      self.setData({ scanning: false, statusText: '已连接', autoConnecting: false })
      wx.navigateBack()
    })

    this._onBle('onDisconnected', function(device, reason) {
      if (!self._alive) return
      if (self._suppressDisconnect) {
        self._suppressDisconnect = false
        self.setData({ scanning: false, autoConnecting: false })
        return
      }
      self.setData({ scanning: false, autoConnecting: false })
      var msg = (reason && reason.indexOf('超时') >= 0)
        ? '连接超时，请靠近设备重试' : '连接失败，请重试'
      self.setData({ statusText: msg })
      wx.showToast({ title: msg, icon: 'none' })
      // 连接失败后扫描已经被停掉，重启一次让用户能继续选设备
      clearTimeout(self._retryTimer)
      self._retryTimer = setTimeout(function() {
        if (!self._alive || self._ble.isConnected) return
        self._startScanFresh()
        self._armEmptyTimer()
      }, 900)
    })

    this._onBle('onBluetoothStateChanged', function(enabled) {
      if (!enabled) {
        self.setData({ scanning: false, autoConnecting: false, statusText: '蓝牙已关闭，请开启蓝牙' })
      }
    })

    this._onBle('onScanFailed', function(errMsg) {
      self.setData({
        scanning: false,
        autoConnecting: false,
        statusText: '无法开始扫描：' + String(errMsg || '蓝牙不可用') + '。请打开手机蓝牙和定位权限后重试。',
      })
    })

    // 设备如果还被上一次连接占着，就不会广播：先断开释放，再开始扫描。
    this._startScanFresh()
    this._armEmptyTimer()
  },

  onShow: function() {
    if (this._firstShow) {
      this._firstShow = false
      return
    }
    if (!this._ble || this.data.devices.length) return
    if (this._ble.isConnected) return
    this._startScanFresh()
    this._armEmptyTimer()
  },

  onUnload: function() {
    this._alive = false
    if (this._emptyTimer) clearTimeout(this._emptyTimer)
    clearTimeout(this._retryTimer)
    clearTimeout(this._autoTimer)
    clearTimeout(this._suppressTimer)
    var ble = this._ble
    try {
      (this._handlers || []).forEach(function(h) { ble.off(h.event, h.fn) })
    } catch (err) {}
    this._handlers = []
    try { ble.stopScan() } catch (err) {}
  },

  _onBle: function(event, fn) {
    this._ble.on(event, fn)
    this._handlers.push({ event: event, fn: fn })
  },

  _matchesLast: function(device) {
    var saved = this._lastDevice || {}
    if (!saved.deviceId && !saved.name) return false
    if (saved.deviceId && device.deviceId === saved.deviceId) return true
    return Boolean(saved.name) && device.name === saved.name
  },

  _maybeAutoConnect: function(device) {
    if (!this._autoConnectEnabled || this.data.autoConnecting || !this._alive) return
    if (!this._matchesLast(device)) return
    var self = this
    var saved = this._lastDevice || {}
    this._autoConnectEnabled = false
    if (saved.deviceId !== device.deviceId) {
      // 设备 ID 变了（换机/重装常见），按名字认领并更新缓存
      this._lastDevice = { deviceId: device.deviceId, name: device.name }
      this.setData({ lastDeviceId: device.deviceId })
      try { wx.setStorageSync('recorder_last_device', this._lastDevice) } catch (err) {}
    }
    // 留 0.9s 给用户点别的设备；点了别的设备就取消这次自动连接
    clearTimeout(this._autoTimer)
    this._autoTimer = setTimeout(function() {
      if (!self._alive) return
      self.setData({
        autoConnecting: true,
        statusText: '正在连接上次设备：' + device.name + '...',
      })
      try { self._ble.stopScan() } catch (err) {}
      self._ble.connect(device)
    }, 900)
  },

  cancelAutoConnect: function() {
    this._autoConnectEnabled = false
    clearTimeout(this._autoTimer)
    this.setData({ autoConnecting: false, statusText: '已取消自动连接，请选择要连接的设备' })
  },

  _startScanFresh: function() {
    var self = this
    try {
      if (this._ble.isConnected) {
        // 主动断开旧连接是正常换设备，不要弹“连接失败”
        this._suppressDisconnect = true
        clearTimeout(this._suppressTimer)
        this._suppressTimer = setTimeout(function() { self._suppressDisconnect = false }, 2500)
        this._ble.disconnect()
      }
    } catch (err) {}
    try { this._ble.stopScan() } catch (err) {}
    // 上次连接可能残留在系统 GATT 里（页面退出/设备重启后常见），
    // 先关适配器再重开，确保广播能被重新发现。
    try {
      wx.closeBluetoothAdapter({
        complete: function() {
          setTimeout(function() {
            if (!self._alive) return
            try { self._ble.startScan() } catch (err) {}
          }, 800)
        }
      })
    } catch (err) {
      setTimeout(function() {
        if (!self._alive) return
        try { self._ble.startScan() } catch (err) {}
      }, 600)
    }
  },

  _armEmptyTimer: function() {
    var self = this
    if (this._emptyTimer) clearTimeout(this._emptyTimer)
    this._emptyTimer = setTimeout(function() {
      if (!self._alive) return
      if (self.data.devices.length) return
      if (!self._retried) {
        self._retried = true
        self.setData({ scanning: true, statusText: '暂未发现设备，正在重新扫描...' })
        self._startScanFresh()
        self._armEmptyTimer()
        return
      }
      self.setData({
        scanning: false,
        autoConnecting: false,
        statusText: '扫不到设备：设备可能还被必火App或上次连接占用。请先在「录音笔」页断开，或关掉必火App后再重试。',
      })
    }, 12000)
  },

  onDeviceTap: function(e) {
    var idx = e.currentTarget.dataset.index
    var device = this.data.devices[idx]
    if (!device) return
    this._autoConnectEnabled = false
    clearTimeout(this._autoTimer)
    this.setData({ scanning: true, autoConnecting: false, statusText: '连接中：' + device.name + '...' })
    this._ble.stopScan()
    this._ble.connect(device)
  },

  onRescan: function() {
    this._seenIds = {}
    this._retried = false
    this.setData({ devices: [], scanning: true, statusText: '扫描中...' })
    this._startScanFresh()
    this._armEmptyTimer()
  },
})
