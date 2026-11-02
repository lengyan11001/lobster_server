var AIRECBleManager = require('../../../utils/airec/ble-manager')

Page({
  data: {
    devices: [],
    scanning: true,
    statusText: '扫描中...',
  },

  _seenIds: null,
  _ble: null,
  _handlers: null,
  _retried: false,
  _emptyTimer: null,

  onLoad: function() {
    var self = this
    this._seenIds = {}
    this._ble = AIRECBleManager.getInstance()
    this._handlers = []
    this._retried = false

    this._onBle('onDeviceFound', function(device) {
      if (self._seenIds[device.deviceId]) return
      self._seenIds[device.deviceId] = true
      var devices = self.data.devices.concat([device])
      self.setData({
        devices: devices,
        scanning: true,
        statusText: '发现 ' + devices.length + ' 台设备，点击连接',
      })
    })

    this._onBle('onConnected', function(device) {
      self.setData({ scanning: false, statusText: '已连接' })
      wx.navigateBack()
    })

    this._onBle('onDisconnected', function(device, reason) {
      self.setData({ scanning: false })
      var msg = (reason && reason.indexOf('超时') >= 0)
        ? '连接超时，请靠近设备重试' : '连接失败，请重试'
      self.setData({ statusText: msg })
      wx.showToast({ title: msg, icon: 'none' })
    })

    this._onBle('onBluetoothStateChanged', function(enabled) {
      if (!enabled) {
        self.setData({ scanning: false, statusText: '蓝牙已关闭，请开启蓝牙' })
      }
    })

    this._onBle('onScanFailed', function(errMsg) {
      self.setData({
        scanning: false,
        statusText: '无法开始扫描：' + String(errMsg || '蓝牙不可用') + '。请打开手机蓝牙和定位权限后重试。',
      })
    })

    // 设备如果还被上一次连接占着，就不会广播，扫描永远为空：
    // 先断开释放，再开始扫描。
    this._startScanFresh()
    this._armEmptyTimer()
  },

  onShow: function() {
    // 从后台/必火App切回来时，微信可能已经停掉了 discovery，重新拉一次
    if (!this._ble || this.data.devices.length) return
    if (this._ble.isConnected) return
    this._startScanFresh()
  },

  onUnload: function() {
    if (this._emptyTimer) clearTimeout(this._emptyTimer)
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

  _startScanFresh: function() {
    var self = this
    try {
      if (this._ble.isConnected) this._ble.disconnect()
    } catch (err) {}
    try { this._ble.stopScan() } catch (err) {}
    // 上次连接可能残留在系统 GATT 里（页面退出/设备重启后常见），
    // 先关适配器再重开，确保广播能被重新发现。
    try {
      wx.closeBluetoothAdapter({
        complete: function() {
          setTimeout(function() {
            try { self._ble.startScan() } catch (err) {}
          }, 800)
        }
      })
    } catch (err) {
      setTimeout(function() {
        try { self._ble.startScan() } catch (err) {}
      }, 600)
    }
  },

  _armEmptyTimer: function() {
    var self = this
    if (this._emptyTimer) clearTimeout(this._emptyTimer)
    this._emptyTimer = setTimeout(function() {
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
        statusText: '扫不到设备：设备可能还被必火App或上次连接占用。请先在「录音笔」页断开，或关掉必火App后再重试。',
      })
    }, 12000)
  },

  onDeviceTap: function(e) {
    var idx = e.currentTarget.dataset.index
    var device = this.data.devices[idx]
    if (!device) return
    this.setData({ scanning: true, statusText: '连接中：' + device.name + '...' })
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
