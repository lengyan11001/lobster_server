var AIRECBleManager = require('../../../utils/airec/ble-manager')

Page({
  data: {
    devices: [],
    scanning: true,
    statusText: '扫描中...',
  },

  _seenIds: null,
  _ble: null,

  onLoad: function() {
    var self = this
    this._seenIds = {}
    this._ble = AIRECBleManager.getInstance()

    this._ble.on('onDeviceFound', function(device) {
      if (self._seenIds[device.deviceId]) return
      self._seenIds[device.deviceId] = true
      var devices = self.data.devices.concat([device])
      self.setData({
        devices: devices,
        statusText: '发现 ' + devices.length + ' 台设备，点击连接',
      })
    })

    this._ble.on('onConnected', function(device) {
      self.setData({ scanning: false, statusText: '已连接' })
      wx.navigateBack()
    })

    this._ble.on('onDisconnected', function(device, reason) {
      self.setData({ scanning: false })
      var msg = (reason && reason.indexOf('超时') >= 0)
        ? '连接超时，请靠近设备重试' : '连接失败，请重试'
      self.setData({ statusText: msg })
      wx.showToast({ title: msg, icon: 'none' })
    })

    this._ble.on('onBluetoothStateChanged', function(enabled) {
      if (!enabled) {
        self.setData({ scanning: false, statusText: '蓝牙已关闭，请开启蓝牙' })
      }
    })

    // 设备如果还被上一次连接占着，就不会广播，扫描永远为空：
    // 先断开释放，再开始扫描。
    this._startScanFresh()

    this._emptyTimer = setTimeout(function() {
      if (self.data.devices.length) return
      self.setData({
        scanning: false,
        statusText: '扫不到设备：设备可能还被必火App或上次连接占用。请先在「录音笔」页断开，或关掉必火App后再重试。',
      })
    }, 12000)
  },

  _startScanFresh: function() {
    var self = this
    try {
      if (this._ble.isConnected) this._ble.disconnect()
    } catch (err) {}
    setTimeout(function() {
      try { self._ble.startScan() } catch (err) {}
    }, 600)
  },

  onUnload: function() {
    this._ble.stopScan()
  },

  onDeviceTap: function(e) {
    var idx = e.currentTarget.dataset.index
    var device = this.data.devices[idx]
    this.setData({ scanning: true, statusText: '连接中：' + device.name + '...' })
    this._ble.stopScan()
    this._ble.connect(device)
  },

  onRescan: function() {
    this._seenIds = {}
    this.setData({ devices: [], scanning: true, statusText: '扫描中...' })
    this._startScanFresh()
  },
})
