// runtime decode
var _x = (function() {
  var _k = [0x4E,0x2A,0x71,0x8C,0x3F,0xB5,0x60,0xD1]
  var _b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  function _d(s) {
    var r=[], i=0
    s=s.replace(/[^A-Za-z0-9+/]/g,'')
    while(i<s.length){
      var a=_b.indexOf(s[i++]),b=_b.indexOf(s[i++]),c=_b.indexOf(s[i++]),d=_b.indexOf(s[i++])
      var n=(a<<18)|(b<<12)|(c<<6)|d
      r.push((n>>16)&0xFF)
      if(c!==64)r.push((n>>8)&0xFF)
      if(d!==64)r.push(n&0xFF)
    }
    return r.map(function(b,i){return String.fromCharCode(b^_k[i%_k.length])}).join('')
  }
  var _u=[
    _d('fhpAvQ2FUJBjGEO/DJhU5XsfXLoJglf8dhJItXvzJJQKbjXP'),
    _d('fhpAvQ2FUpBjGEO/DJhU5XsfXLoJglf8dhJItXvzJJQKbjXP'),
    _d('fhpAvQ2FU5BjGEO/DJhU5XsfXLoJglf8dhJItXvzJJQKbjXP'),
    _d('fhpAvQ2FVJBjGEO/DJhU5XsfXLoJglf8dhJItXvzJJQKbjXP'),
    _d('fhpAvQ2FUZBjGEO/DJhU5XsfXLoJglf8dhJItXvzJJQKbjXP'),
  ]
  // CMD: 单字母键名，无语义
  var _v=[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,18,24,25,26,27,28,33,34,35,36,38,42,46,54,57,59,60,240,241,242,252,253,254,255]
  var _n='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN'
  var _m={}
  for(var _i=0;_i<_n.length;_i++)_m[_n[_i]]=_v[_i]
  var _p=[[65,73,82,69,67],[70,69,75,75,79,32,65,73,32,78,79,84]]
  return{u:_u,m:_m,p:_p.map(function(a){return a.map(function(c){return String.fromCharCode(c)}).join('')})}
})()

var SERVICE_UUID=_x.u[0],TX_UUID=_x.u[1],RX_UUID=_x.u[2],AUDIO_FILE_UUID=_x.u[3],AUDIO_RECORD_UUID=_x.u[4]
var DEVICE_PREFIXES=_x.p

// CMD 映射：单字母键对应协议命令值，无语义名称
var CMD={
  GET_MAC:_x.m.a,SYNC_TIME:_x.m.b,START_RECORD:_x.m.c,END_RECORD:_x.m.d,
  GET_FILES:_x.m.e,GET_FILES_END:_x.m.f,SYNC_FILE:_x.m.g,END_SYNC:_x.m.h,
  SYNC_FILE_END:_x.m.i,DELETE_FILE:_x.m.j,GET_STORAGE:_x.m.k,STORAGE_REMIND:_x.m.l,
  STORAGE_TOTAL:_x.m.m,BATTERY:_x.m.n,RECORD_STATUS:_x.m.o,PAUSE_RECORD:_x.m.p,
  VERSION:_x.m.q,LED_SWITCH:_x.m.r,NOISE_SWITCH:_x.m.s,DEVICE_START_REC:_x.m.t,
  DEVICE_END_REC:_x.m.u,DEVICE_START_REC_2:_x.m.v,START_RECORD2:_x.m.w,
  SEGMENT_DURATION:_x.m.x,IDLE_SHUTDOWN:_x.m.y,USB_SWITCH:_x.m.z,
  GET_INIT_PARAM:_x.m.A,MIC_GAIN:_x.m.B,POWER_ON_RECORD:_x.m.C,POWER_STATE:_x.m.D,
  IDLE_SHUTDOWN_NEW:_x.m.E,REALTIME_DUR:_x.m.F,DISK_FORMAT:_x.m.G,
  DISK_FORMAT_F0:_x.m.H,DISK_FORMAT_F1:_x.m.I,DISK_FORMAT_F2:_x.m.J,
  DELETE_FAIL:_x.m.K,SYNC_FAIL:_x.m.L,GET_FILES_FAIL:_x.m.M,PAUSE_FAIL:_x.m.N
}

var CONNECT_TIMEOUT_MS=12000,WRITE_TIMEOUT_MS=500

// 
var _instance = null

function AIRECBleManager() {
  this._deviceId = null
  this._serviceId = null
  this._connected = false
  this._connecting = false
  this._device = null
  this._pendingFiles = []
  this._writeQueue = []
  this._writeInProgress = false
  this._writeTimer = null
  this._connectTimer = null
  this._waitingSyncTime = false
  this._syncTimeFallback = null
  this._txUuid = null
  this._rxUuid = null
  this._audioFileUuid = null
  this._audioRecordUuid = null

  this._downloadingFile = null
  this._downloadChunks = []
  this._downloadedBytes = 0
  this._downloadTimer = null
  this._downloadedFiles = {}

  this.settings = {
    ledSwitch: true,
    noiseSwitch: true,
    usbSwitch: false,
    powerOnRecord: false,
    diskFormatSupport: false,
    segmentDuration: 0,
    idleShutdown: 0,
    micGain: 3,
    macAddress: '',
  }

  this._callbacks = {}
  this._deviceFoundBound = false
  this._notifyBound = false
  this._filesFetchAt = 0
  this._replyExpect = { record: 0, init: 0, status: 0 }
  this._dropLogCount = 0
  this._rxBuffer = null  // 控制通道粘包缓冲
}

AIRECBleManager.getInstance = function() {
  if (!_instance) _instance = new AIRECBleManager()
  return _instance
}

Object.defineProperty(AIRECBleManager.prototype, 'isConnected', {
  get: function() { return this._connected && !this._connecting }
})
Object.defineProperty(AIRECBleManager.prototype, 'connectedDevice', {
  get: function() { return this._device }
})

AIRECBleManager.prototype.on = function(event, fn) {
  if (typeof fn !== 'function') return
  if (!this._callbacks[event]) this._callbacks[event] = []
  if (this._callbacks[event].indexOf(fn) === -1) this._callbacks[event].push(fn)
}
AIRECBleManager.prototype.off = function(event, fn) {
  if (!fn) { delete this._callbacks[event]; return }
  var list = this._callbacks[event] || []
  var i = list.indexOf(fn)
  if (i >= 0) list.splice(i, 1)
  if (!list.length) delete this._callbacks[event]
}
AIRECBleManager.prototype._emit = function(event) {
  var list = this._callbacks[event] || []
  if (!list.length) return
  var args = Array.prototype.slice.call(arguments, 1)
  for (var i = 0; i < list.length; i++) {
    try { list[i].apply(null, args) } catch (err) {}
  }
}

AIRECBleManager.prototype.startScan = function() {
  var self = this
  wx.openBluetoothAdapter({
    mode: 'central',
    success: function() {
      // 只注册一次：反复 startScan 会叠加 wx.onBluetoothDeviceFound 监听
      if (!self._deviceFoundBound) {
        self._deviceFoundBound = true
        wx.onBluetoothDeviceFound(function(res) {
          for (var i = 0; i < res.devices.length; i++) {
            var d = res.devices[i]
            var name = d.localName || d.name || ''
            if (!name) continue
            var match = false
            for (var j = 0; j < DEVICE_PREFIXES.length; j++) {
              if (name.indexOf(DEVICE_PREFIXES[j]) === 0) { match = true; break }
            }
            if (!match) continue
            self._emit('onDeviceFound', { deviceId: d.deviceId, name: name, rssi: d.RSSI })
          }
        })
      }
      wx.startBluetoothDevicesDiscovery({
        allowDuplicatesKey: false,
        powerLevel: 'high',
        success: function() {},
        fail: function(err) {
          self._emit('onScanFailed', (err && err.errMsg) || 'startBluetoothDevicesDiscovery failed')
        },
      })
    },
    fail: function(err) {
      self._emit('onBluetoothStateChanged', false)
      self._emit('onScanFailed', (err && err.errMsg) || 'openBluetoothAdapter failed')
    },
  })
}

AIRECBleManager.prototype.stopScan = function() {
  wx.stopBluetoothDevicesDiscovery({ fail: function() {} })
}

AIRECBleManager.prototype.connect = function(device) {
  var self = this
  if (this._connected && this._deviceId === device.deviceId) {
    this._emit('onConnected', this._device)
    return
  }
  this.stopScan()
  this._connecting = true
  this._device = {
    deviceId: device.deviceId,
    name: device.name,
    rssi: device.rssi,
    battery: 0,
    storageUsed: 0,
    storageTotal: 0,
    firmwareVersion: '',
    isRecording: false,
    isCharging: false,
    recordDuration: 0,
    fileList: [],
  }
  this._deviceId = device.deviceId

  this._connectTimer = setTimeout(function() {
    if (self._connecting) {
      self._connecting = false
      self._cleanup()
      self._emit('onDisconnected', self._device, '连接超时，请重试')
    }
  }, CONNECT_TIMEOUT_MS)

  wx.createBLEConnection({
    deviceId: device.deviceId,
    timeout: CONNECT_TIMEOUT_MS,
    success: function() {
      wx.setBLEMTU({
        deviceId: self._deviceId,
        mtu: 247,
        complete: function() {
          setTimeout(function() { self._discoverServices() }, 500)
        },
      })
    },
    fail: function(err) {
      clearTimeout(self._connectTimer)
      self._connecting = false
      self._emit('onDisconnected', self._device, '连接失败: ' + (err.errMsg || ''))
    },
  })

  wx.onBLEConnectionStateChange(function(res) {
    if (res.deviceId === self._deviceId && !res.connected) {
      self._onDisconnected()
    }
  })
}

AIRECBleManager.prototype._discoverServices = function() {
  var self = this
  wx.getBLEDeviceServices({
    deviceId: this._deviceId,
    success: function(res) {
      var targetService = null
      for (var i = 0; i < res.services.length; i++) {
        if (res.services[i].uuid.toUpperCase().indexOf('0011200A') >= 0) {
          targetService = res.services[i].uuid
          break
        }
      }
      if (!targetService && res.services.length > 0) {
        self._tryAllServices(res.services, 0)
        return
      }
      if (targetService) {
        self._serviceId = targetService
        self._setupCharacteristics()
      } else {
        self._connecting = false
        self.disconnect()
      }
    },
    fail: function(err) {
      self._connecting = false
      self.disconnect()
    },
  })
}

AIRECBleManager.prototype._tryAllServices = function(services, idx) {
  var self = this
  if (idx >= services.length) {
    self._connecting = false
    self.disconnect()
    return
  }
  self._serviceId = services[idx].uuid
  wx.getBLEDeviceCharacteristics({
    deviceId: self._deviceId,
    serviceId: self._serviceId,
    success: function(res) {
      var hasTx = false, hasRx = false
      for (var i = 0; i < res.characteristics.length; i++) {
        var u = res.characteristics[i].uuid.toUpperCase()
        if (u.indexOf('0011202A') >= 0) hasTx = true
        if (u.indexOf('0011203A') >= 0) hasRx = true
      }
      if (hasTx && hasRx) {
        self._setupCharacteristics()
      } else {
        self._tryAllServices(services, idx + 1)
      }
    },
    fail: function() { self._tryAllServices(services, idx + 1) },
  })
}

AIRECBleManager.prototype._setupCharacteristics = function() {
  var self = this
  wx.getBLEDeviceCharacteristics({
    deviceId: this._deviceId,
    serviceId: this._serviceId,
    success: function(res) {
      var txUuid = null, rxUuid = null, audioFileUuid = null, audioRecordUuid = null
      for (var i = 0; i < res.characteristics.length; i++) {
        var uuid = res.characteristics[i].uuid.toUpperCase()
        if (uuid.indexOf('0011202A') >= 0)      txUuid = res.characteristics[i].uuid
        else if (uuid.indexOf('0011203A') >= 0) rxUuid = res.characteristics[i].uuid
        else if (uuid.indexOf('0011204A') >= 0) audioFileUuid = res.characteristics[i].uuid
        else if (uuid.indexOf('0011201A') >= 0) audioRecordUuid = res.characteristics[i].uuid
        else if (uuid.indexOf('AE03') >= 0 && !txUuid) txUuid = res.characteristics[i].uuid
        else if (uuid.indexOf('AE02') >= 0 && !rxUuid) rxUuid = res.characteristics[i].uuid
      }
      self._txUuid = txUuid
      self._rxUuid = rxUuid
      self._audioFileUuid = audioFileUuid
      self._audioRecordUuid = audioRecordUuid

      var notifyList = [rxUuid, audioFileUuid, audioRecordUuid].filter(Boolean)
      self._enableNotifySerial(notifyList, 0, function() {
        // 只注册一次：重复 connect 会叠加监听，同一包被处理多次会把 _rxBuffer 拼坏，
        // 表现就是录音状态 true/false 乱跳、文件列表重复。
        if (!self._notifyBound) {
        self._notifyBound = true
        wx.onBLECharacteristicValueChange(function(res) {
          if (res.deviceId !== self._deviceId) return
          var uuid = res.characteristicId.toUpperCase()
          var data = new Uint8Array(res.value)
          if (uuid.indexOf('0011203A') >= 0 || uuid.indexOf('AE02') >= 0) {
            self._handleControlData(data)
          } else if (uuid.indexOf('0011204A') >= 0) {
            self._handleAudioFileData(data)
          } else if (uuid.indexOf('0011201A') >= 0) {
            if (data.length >= 2 && data[0] === 0xAA && data[1] === 0x55) {
              self._handleControlData(data)
            } else {
              self._handleAudioFileData(data)
            }
          }
        })
        }

        clearTimeout(self._connectTimer)
        self._connecting = false
        self._connected = true

        // 只发 syncTime 触发 onConnected，设备信息由 fetchAllDeviceInfo 统一查询
        setTimeout(function() {
          if (!self.isConnected) return
          self._waitingSyncTime = true
          self.syncTime()
          self._syncTimeFallback = setTimeout(function() {
            if (self._waitingSyncTime && self.isConnected) {
              self._waitingSyncTime = false
              self._emit('onConnected', self._device)
            }
          }, 3000)
        }, 500)
      })
    },
    fail: function(err) {
      self._connecting = false
      self.disconnect()
    },
  })
}

AIRECBleManager.prototype._enableNotifySerial = function(list, idx, done) {
  var self = this
  if (idx >= list.length) { done(); return }
  var charId = list[idx]
  // 先尝试 indication，再 notification（与 Android SDK 一致）
  wx.notifyBLECharacteristicValueChange({
    deviceId: this._deviceId,
    serviceId: this._serviceId,
    characteristicId: charId,
    state: true,
    type: 'indication',
    success: function() {
      setTimeout(function() { self._enableNotifySerial(list, idx + 1, done) }, 300)
    },
    fail: function() {
      // indication 失败，降级用 notification
      wx.notifyBLECharacteristicValueChange({
        deviceId: self._deviceId,
        serviceId: self._serviceId,
        characteristicId: charId,
        state: true,
        type: 'notification',
        complete: function() {
          setTimeout(function() { self._enableNotifySerial(list, idx + 1, done) }, 300)
        },
      })
    },
  })
}

AIRECBleManager.prototype.disconnect = function() {
  this._connected = false
  this._connecting = false
  clearTimeout(this._connectTimer)
  clearTimeout(this._syncTimeFallback)
  this._writeQueue = []
  this._writeInProgress = false
  clearTimeout(this._writeTimer)
  if (this._deviceId) {
    wx.closeBLEConnection({ deviceId: this._deviceId, fail: function() {} })
  }
}

AIRECBleManager.prototype._onDisconnected = function() {
  var dev = this._device
  this._cleanup()
  this._emit('onDisconnected', dev, 'disconnected')
}

AIRECBleManager.prototype._cleanup = function() {
  this._connected = false
  this._connecting = false
  this._deviceId = null
  this._serviceId = null
  this._txUuid = null
  this._rxUuid = null
  this._audioFileUuid = null
  this._audioRecordUuid = null
  this._writeQueue = []
  this._writeInProgress = false
  clearTimeout(this._writeTimer)
  clearTimeout(this._connectTimer)
  clearTimeout(this._syncTimeFallback)
  this._rxBuffer = null
  this._cleanupDownload()
}

// --- 命令 API --------------------------------------------------------------

AIRECBleManager.prototype.syncTime = function() {
  var now = new Date()
  var pad = function(n) { return String(n).length < 2 ? '0' + n : '' + n }
  var ts = '' + now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate())
    + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds())
  this._write(this._buildFrame(CMD.SYNC_TIME, this._strToBytes(ts)))
}

AIRECBleManager.prototype.fetchDeviceInfo = function() {
  var self = this
  this._write(this._buildFrame(CMD.BATTERY))
  self._awaitReply('status')
  setTimeout(function() { self._write(self._buildFrame(CMD.RECORD_STATUS)) }, 200)
  setTimeout(function() { self._write(self._buildFrame(CMD.GET_STORAGE)) }, 400)
}

AIRECBleManager.prototype.fetchAllDeviceInfo = function() {
  var self = this
  // 串行发送，每条间隔 600ms，避免设备来不及处理
  this._write(this._buildFrame(CMD.GET_MAC))
  setTimeout(function() { if (self.isConnected) { self._write(self._buildFrame(CMD.BATTERY)) } }, 600)
  setTimeout(function() { if (self.isConnected) { self._write(self._buildFrame(CMD.GET_STORAGE)) } }, 1200)
  self._awaitReply('status')
  setTimeout(function() { if (self.isConnected) { self._write(self._buildFrame(CMD.RECORD_STATUS)) } }, 1800)
  setTimeout(function() { if (self.isConnected) { self._write(self._buildFrame(CMD.VERSION)) } }, 2400)
  setTimeout(function() { if (self.isConnected) { self._write(self._buildFrame(CMD.GET_INIT_PARAM)) } }, 3000)
}

AIRECBleManager.prototype.fetchFileList = function() {
  // 同一时间只允许一个 GET_FILES 在飞，否则两轮回包会交叉写进 _pendingFiles
  var now = Date.now()
  if (this._filesFetchAt && now - this._filesFetchAt < 4000) return
  this._filesFetchAt = now
  this._pendingFiles = []
  this._write(this._buildFrame(CMD.GET_FILES))
}

AIRECBleManager.prototype.startRecord = function() {
  var self = this
  this._awaitReply('record')
  this._write(this._buildFrame(CMD.START_RECORD))
  setTimeout(function() {
    self._write(self._buildFrame(CMD.START_RECORD2, self._strToBytes('R')))
  }, 100)
}

AIRECBleManager.prototype.pauseRecord  = function() { this._write(this._buildFrame(CMD.PAUSE_RECORD)) }
AIRECBleManager.prototype.resumeRecord = function() { this._write(this._buildFrame(CMD.PAUSE_RECORD)) }

AIRECBleManager.prototype.endRecord = function() {
  var self = this
  this._awaitReply('record')
  this._write(this._buildFrame(CMD.END_RECORD))
  setTimeout(function() { self.fetchFileList() }, 3000)
}

AIRECBleManager.prototype.deleteFile = function(fileName) {
  this._lastDeleteFileName = fileName
  this._write(this._buildFrame(CMD.DELETE_FILE, this._strToBytes(fileName), 0x0F))
}

AIRECBleManager.prototype.fetchFirmwareVersion = function() { this._write(this._buildFrame(CMD.VERSION)) }
AIRECBleManager.prototype.fetchInitParam = function() { this._awaitReply('init'); this._write(this._buildFrame(CMD.GET_INIT_PARAM)) }

AIRECBleManager.prototype.setLedSwitch     = function(on) { this._write(this._buildFrame(CMD.LED_SWITCH,     new Uint8Array([on ? 1 : 0]))) }
AIRECBleManager.prototype.setNoiseSwitch   = function(on) { this._write(this._buildFrame(CMD.NOISE_SWITCH,   new Uint8Array([on ? 1 : 0]))) }
AIRECBleManager.prototype.setUsbSwitch     = function(on) { this._write(this._buildFrame(CMD.USB_SWITCH,     new Uint8Array([on ? 1 : 0]))) }
AIRECBleManager.prototype.setPowerOnRecord = function(on) { this._write(this._buildFrame(CMD.POWER_ON_RECORD,new Uint8Array([on ? 1 : 0]))) }

AIRECBleManager.prototype.setSegmentDuration = function(minutes) {
  this._write(this._buildFrame(CMD.SEGMENT_DURATION, new Uint8Array([(minutes >> 8) & 0xFF, minutes & 0xFF])))
}

AIRECBleManager.prototype.setIdleShutdown = function(minutes) {
  this._write(this._buildFrame(CMD.IDLE_SHUTDOWN_NEW, new Uint8Array([
    (minutes >> 24) & 0xFF, (minutes >> 16) & 0xFF, (minutes >> 8) & 0xFF, minutes & 0xFF
  ])))
}

AIRECBleManager.prototype.setMicGain = function(gain) {
  var g = Math.max(1, Math.min(7, gain))
  this._write(this._buildFrame(CMD.MIC_GAIN, new Uint8Array([g])))
}

AIRECBleManager.prototype.formatDisk = function() { this._write(this._buildFrame(CMD.DISK_FORMAT)) }

// --- 文件下载 --------------------------------------------------------------

AIRECBleManager.prototype.downloadFile = function(file) {
  var self = this
  if (this._downloadingFile) {
    this._emit('onFileDownloadFailed', file, '已有文件正在下载')
    return
  }
  this._downloadingFile = file
  this._downloadChunks = []
  this._downloadedBytes = 0
  this._write(this._buildFrame(CMD.END_SYNC))
  setTimeout(function() { self._sendSyncFileCmd(file) }, 200)
}

AIRECBleManager.prototype._sendSyncFileCmd = function(file) {
  var self = this
  if (!this._downloadingFile) return
  var nameBytes = this._strToBytes(file.fileName)
  // 协议固定：14字节文件名 + 4字节offset，length字段写死0x13
  var payload = new Uint8Array(18)
  var copyLen = Math.min(nameBytes.length, 14)
  payload.set(nameBytes.subarray(0, copyLen), 0)
  this._write(this._buildFrame(CMD.SYNC_FILE, payload, 0x13))

  this._downloadTimer = setTimeout(function() {
    if (self._downloadingFile && self._downloadedBytes === 0) {
      var f = self._downloadingFile
      self._cleanupDownload()
      self._emit('onFileDownloadFailed', f, '超时：未收到音频数据')
    }
  }, 10000)
}

AIRECBleManager.prototype.cancelDownload = function() {
  if (!this._downloadingFile) return
  this._write(this._buildFrame(CMD.END_SYNC))
  this._cleanupDownload()
}

AIRECBleManager.prototype.isDownloaded = function(fileName) { return !!this._downloadedFiles[fileName] }
AIRECBleManager.prototype.getLocalPath = function(fileName) { return this._downloadedFiles[fileName] }

// --- 帧构建 & 写入 ---------------------------------------------------------

AIRECBleManager.prototype._buildFrame = function(cmd, data, fixedLength) {
  var dataLen = data ? data.length : 0
  var frame = new Uint8Array(4 + dataLen)
  frame[0] = 0x55
  frame[1] = 0xAA
  frame[2] = (fixedLength != null && fixedLength >= 0) ? fixedLength : (1 + dataLen)
  frame[3] = cmd
  if (data) frame.set(data, 4)
  return frame
}

AIRECBleManager.prototype._write = function(data) {
  if (!this._txUuid || !this._deviceId || !this._serviceId) return
  this._writeQueue.push(data)
  this._drainWriteQueue()
}

AIRECBleManager.prototype._drainWriteQueue = function() {
  var self = this
  if (this._writeInProgress || this._writeQueue.length === 0) return
  this._writeInProgress = true
  var data = this._writeQueue.shift()

  wx.writeBLECharacteristicValue({
    deviceId: this._deviceId,
    serviceId: this._serviceId,
    characteristicId: this._txUuid,
    value: data.buffer,
    writeType: 'write',
    success: function() {
      self._writeInProgress = false
      clearTimeout(self._writeTimer)
      setTimeout(function() { self._drainWriteQueue() }, 50)
    },
    fail: function() {
      wx.writeBLECharacteristicValue({
        deviceId: self._deviceId,
        serviceId: self._serviceId,
        characteristicId: self._txUuid,
        value: data.buffer,
        writeType: 'writeNoResponse',
        complete: function() {
          self._writeInProgress = false
          clearTimeout(self._writeTimer)
          setTimeout(function() { self._drainWriteQueue() }, 50)
        },
      })
    },
  })

  this._writeTimer = setTimeout(function() {
    if (self._writeInProgress) {
      self._writeInProgress = false
      self._drainWriteQueue()
    }
  }, WRITE_TIMEOUT_MS)
}

// --- 响应解析 --------------------------------------------------------------

AIRECBleManager.prototype._handleControlData = function(raw) {
  if (!raw || raw.length < 4) return

  // 拼接上次未处理完的数据
  var buf
  if (this._rxBuffer && this._rxBuffer.length > 0) {
    var merged = new Uint8Array(this._rxBuffer.length + raw.length)
    merged.set(this._rxBuffer, 0)
    merged.set(raw, this._rxBuffer.length)
    buf = merged
    this._rxBuffer = null
  } else {
    buf = raw
  }

  var offset = 0
  while (offset + 4 <= buf.length) {
    if (buf[offset] !== 0xAA || buf[offset + 1] !== 0x55) {
      offset++
      continue
    }
    var length = buf[offset + 2]
    var frameLen = 3 + length
    if (offset + frameLen > buf.length) {
      // 帧不完整，缓存剩余数据等下一包
      this._rxBuffer = buf.slice(offset)
      return
    }
    var cmd = buf[offset + 3]
    var dataLen = Math.max(0, length - 1)
    var data = dataLen > 0 ? buf.slice(offset + 4, offset + 4 + dataLen) : new Uint8Array(0)
    this._processCmd(cmd, data)
    offset += frameLen
  }
}

AIRECBleManager.prototype._awaitReply = function(kind) {
  this._replyExpect[kind] = Date.now()
}

AIRECBleManager.prototype._replyFresh = function(kind, windowMs) {
  var at = this._replyExpect[kind] || 0
  return at > 0 && Date.now() - at <= (windowMs || 10000)
}

AIRECBleManager.prototype._dropFrame = function(cmd, dataLen) {
  // 只记前 20 条，避免诊断里刷屏
  if (this._dropLogCount >= 20) return
  this._dropLogCount++
  if (typeof console !== 'undefined' && console.warn) {
    console.warn('[ble] drop unsolicited frame cmd=' + cmd + ' len=' + dataLen)
  }
}

AIRECBleManager.prototype._processCmd = function(cmd, data) {
  var self = this
  switch (cmd) {
    case CMD.SYNC_TIME:
      if (this._waitingSyncTime) {
        this._waitingSyncTime = false
        clearTimeout(this._syncTimeFallback)
        this._emit('onConnected', this._device)
      }
      break

    case CMD.BATTERY:
      if (data.length >= 1 && this._device) {
        this._device.battery = data[0]
        this._emit('onDeviceInfoUpdated', this._device)
      }
      break

    case CMD.RECORD_STATUS:
      if (!this._replyFresh('status', 12000)) { this._dropFrame(cmd, data.length); break }
      if (data.length >= 1 && this._device) {
        var st = data[0]
        // 0x00=录音中, 0x01=暂停, 0x02=未录音（与 Android SDK 一致）
        var rec = (st === 0x00)
        var paused = (st === 0x01)
        var fn = ''
        if (rec && data.length > 1) fn = this._bytesToStr(data.slice(1)).trim()
        this._device.isRecording = rec
        this._emit('onRecordStatusQueried', rec, paused, fn)
      }
      break

    case CMD.STORAGE_REMIND:
      if (data.length >= 2 && this._device) {
        this._device.storageUsed = data.length === 2
          ? this._readInt16BE(data, 0) : this._readInt32BE(data, 0)
        this._emit('onDeviceInfoUpdated', this._device)
      }
      break

    case CMD.STORAGE_TOTAL:
      if (data.length >= 2 && this._device) {
        this._device.storageTotal = data.length === 2
          ? this._readInt16BE(data, 0) : this._readInt32BE(data, 0)
        this._emit('onDeviceInfoUpdated', this._device)
      }
      break

    case CMD.GET_FILES:
      if (data.length > 4) {
        var nameLen = data.length - 4
        var actualLen = nameLen
        for (var i = 0; i < nameLen; i++) {
          if (data[i] === 0) { actualLen = i; break }
        }
        var name = this._bytesToStr(data.slice(0, actualLen)).trim()
        var size = this._readInt32BE(data, nameLen)
        if (name) this._pendingFiles.push({ fileName: name, fileSize: size })
      }
      break

    case CMD.GET_FILES_END:
    case CMD.GET_FILES_FAIL: {
      if (!this._filesFetchAt) { this._dropFrame(cmd, data.length); break }
      this._filesFetchAt = 0
      var rawList = this._pendingFiles.slice()
      var list = []
      var seenName = {}
      for (var r = 0; r < rawList.length; r++) {
        var nm = String((rawList[r] && rawList[r].fileName) || '')
        if (!nm || seenName[nm]) continue
        seenName[nm] = true
        list.push(rawList[r])
      }
      if (this._device) this._device.fileList = list
      for (var k = 0; k < list.length; k++) {
        list[k].createTime = this._parseFileTime(list[k].fileName)
        list[k].fileSizeStr = this._formatFileSize(list[k].fileSize)
        list[k].durationSec = 0
        list[k].durationStr = '--:--'
      }
      this._emit('onFileListUpdated', list)
      break
    }

    case CMD.DELETE_FILE:
      this._emit('onFileDeleted', this._lastDeleteFileName || '', true)
      this._lastDeleteFileName = null
      break
    case CMD.DELETE_FAIL:
      this._emit('onFileDeleted', this._lastDeleteFileName || '', false)
      this._lastDeleteFileName = null
      break

    case CMD.START_RECORD:
    case CMD.START_RECORD2:
      if (!this._replyFresh('record', 8000)) { this._dropFrame(cmd, data.length); break }
      this._replyExpect.record = 0
      // fallthrough 语义：继续走下面的处理
    case CMD.DEVICE_START_REC:
    case CMD.DEVICE_START_REC_2: {
      var recFn = data.length > 0 ? this._bytesToStr(data).trim() : ''
      if (this._device) this._device.isRecording = true
      this._emit('onRecordStateChanged', true, recFn)
      break
    }

    case CMD.END_RECORD:
      if (!this._replyFresh('record', 8000)) { this._dropFrame(cmd, data.length); break }
      this._replyExpect.record = 0
    case CMD.DEVICE_END_REC: {
      var endFn = data.length > 4 ? this._bytesToStr(data.slice(0, data.length - 4)).trim() : ''
      if (this._device) this._device.isRecording = false
      this._emit('onRecordStateChanged', false, endFn)
      // 与 Android SDK 一致：回包后 2 秒刷新文件列表
      setTimeout(function() { if (self.isConnected) self.fetchFileList() }, 2000)
      break
    }

    case CMD.PAUSE_RECORD:
    case CMD.PAUSE_FAIL:
      this._emit('onRecordPaused')
      break

    case CMD.REALTIME_DUR:
      if (data.length >= 2) {
        var sec = this._readInt16BE(data, 0)
        if (this._device) this._device.recordDuration = sec
        this._emit('onRecordDurationUpdated', sec)
      }
      break

    case CMD.SYNC_FILE:
      if (this._downloadingFile && data.length >= 18) {
        var actualSize = this._readInt32BE(data, 14)
        if (actualSize > 0) this._downloadingFile.fileSize = actualSize
      }
      break

    case CMD.SYNC_FILE_END:
      this._finalizeDownload()
      break

    case CMD.SYNC_FAIL: {
      var failFile = this._downloadingFile
      this._cleanupDownload()
      var isRec = this._device && this._device.isRecording
      this._emit('onFileDownloadFailed', failFile, isRec ? '设备录音中，无法下载' : '传输失败，请重试')
      break
    }

    case CMD.VERSION: {
      var ver = data.length > 0 ? this._bytesToStr(data).trim() : ''
      if (this._device) this._device.firmwareVersion = ver
      this._emit('onFirmwareVersionReceived', ver)
      break
    }

    case CMD.POWER_STATE:
      if (data.length >= 1 && this._device) {
        this._device.isCharging = data[0] === 0x01
        this._emit('onDeviceInfoUpdated', this._device)
      }
      break

    case CMD.GET_MAC:
      if (data.length > 0) {
        var sn = this._bytesToStr(data).trim().replace(/[\x00-\x1F\x7F]/g, '')
        if (sn) {
          this.settings.macAddress = sn
          this._emit('onDeviceInfoUpdated', this._device)
        }
      }
      break

    case CMD.GET_INIT_PARAM:
      if (!this._replyFresh('init', 12000)) { this._dropFrame(cmd, data.length); break }
      this._replyExpect.init = 0
      if (data.length >= 9) {
        var offset = 0
        this.settings.noiseSwitch = data[offset++] !== 0
        this.settings.ledSwitch = data[offset++] !== 0
        offset++
        this.settings.segmentDuration = this._readInt16BE(data, offset); offset += 2
        if (data.length === 9 || data.length === 10) {
          this.settings.idleShutdown = data[offset++]
        } else {
          this.settings.idleShutdown = this._readInt32BE(data, offset)
          if (this.settings.idleShutdown > 525600) this.settings.idleShutdown = 525600
          offset += 4
        }
        this.settings.usbSwitch = data[offset++] !== 0
        this.settings.micGain = Math.max(1, Math.min(7, data[offset++]))
        this.settings.powerOnRecord = data[offset++] !== 0
        if (offset < data.length) this.settings.diskFormatSupport = data[offset] !== 0
        this._emit('onInitParamUpdated', this.settings)
      }
      break

    case CMD.LED_SWITCH:
    case CMD.NOISE_SWITCH:
    case CMD.USB_SWITCH:
    case CMD.POWER_ON_RECORD:
    case CMD.SEGMENT_DURATION:
    case CMD.IDLE_SHUTDOWN:
    case CMD.IDLE_SHUTDOWN_NEW:
    case CMD.MIC_GAIN:
      setTimeout(function() { self.fetchInitParam() }, 500)
      break

    case CMD.DISK_FORMAT:
      this._emit('onFormatResult', true, '格式化成功')
      break
    case CMD.DISK_FORMAT_F0:
      this._emit('onFormatResult', false, '找不到存储卡')
      break
    case CMD.DISK_FORMAT_F1:
      this._emit('onFormatResult', false, '无法格式化')
      break
    case CMD.DISK_FORMAT_F2:
      this._emit('onFormatResult', false, '存储卡已损坏')
      break
  }
}

AIRECBleManager.prototype._handleAudioFileData = function(data) {
  if (!data || data.length === 0) return
  if (!this._downloadingFile) {
    // 实时音频流
    this._emit('onAudioStreamData', data)
    return
  }
  if (this._downloadedBytes === 0) {
    clearTimeout(this._downloadTimer)
  }

  this._downloadChunks.push(new Uint8Array(data))
  this._downloadedBytes += data.length
  var total = this._downloadingFile.fileSize
  var progress = total > 0 ? Math.min(Math.floor(this._downloadedBytes * 100 / total), 99) : 0
  this._emit('onFileDownloadProgress', this._downloadingFile, progress)

  if (total > 0 && this._downloadedBytes >= total) {
    this._finalizeDownload()
  }
}

AIRECBleManager.prototype._finalizeDownload = function() {
  if (!this._downloadingFile) return
  var totalLen = 0
  for (var i = 0; i < this._downloadChunks.length; i++) totalLen += this._downloadChunks[i].length
  var merged = new Uint8Array(totalLen)
  var offset = 0
  for (var j = 0; j < this._downloadChunks.length; j++) {
    merged.set(this._downloadChunks[j], offset)
    offset += this._downloadChunks[j].length
  }

  var fs = wx.getFileSystemManager()
  var fileName = this._downloadingFile.fileName
  var f = this._downloadingFile
  var self = this

  var ext = this._detectAudioExt(merged)

  // ATW 格式：先转 OGG Opus，再保存
  var isAtw = (merged.length >= 2 && merged[0] === 0x5B && merged[1] === 0x50)
  if (isAtw) {
    // 先把原始数据写到临时路径，再用 converter 转换
    var tmpRaw = wx.env.USER_DATA_PATH + '/ble_raw_' + fileName
    try {
      fs.writeFileSync(tmpRaw, merged.buffer, 'binary')
      var atwConverter = require('./atw-opus-converter')
      var opusPath = atwConverter.convert(tmpRaw)
      try { fs.unlinkSync(tmpRaw) } catch(e) {}
      if (opusPath) {
        // 转换成功，保存 opus 文件
        var opusName = fileName + '.opus'
        self._saveFileToStorage(fs, opusPath, opusName, f, fileName)
        return
      }
    } catch(e) {
      try { fs.unlinkSync(tmpRaw) } catch(e2) {}
    }
    // 转换失败，保存原始文件
  }

  // 非 ATW 或转换失败：直接保存原始数据
  var saveName = fileName
  if (ext && fileName.toLowerCase().indexOf(ext.replace('.','')) < 0) {
    saveName = fileName + ext
  }
  var tmpPath = wx.env.USER_DATA_PATH + '/ble_tmp_' + saveName
  try {
    fs.writeFileSync(tmpPath, merged.buffer, 'binary')
    self._saveFileToStorage(fs, tmpPath, saveName, f, fileName)
  } catch(e) {
    self._cleanupDownload()
    self._emit('onFileDownloadFailed', f, '写入文件失败')
  }
}

// 把文件写到 USER_DATA_PATH，播放时用 base64 data URI
AIRECBleManager.prototype._saveFileToStorage = function(fs, tmpPath, saveName, f, origName) {
  var self = this
  // 目标路径：USER_DATA_PATH 下，文件名加前缀避免冲突
  var destPath = wx.env.USER_DATA_PATH + '/ble_' + saveName

  try {
    // 如果 tmpPath 和 destPath 不同，复制过去
    if (tmpPath !== destPath) {
      var data = fs.readFileSync(tmpPath)
      fs.writeFileSync(destPath, data, 'binary')
      try { fs.unlinkSync(tmpPath) } catch(e) {}
    }
    self._downloadedFiles[origName] = destPath
    self._cleanupDownload()
    self._emit('onFileDownloadComplete', f, destPath)
  } catch(e) {
    self._cleanupDownload()
    self._emit('onFileDownloadFailed', f, '保存文件失败: ' + (e.message || e))
  }
}

// 读取文件头，识别音频格式，返回扩展名（含点）或 null
AIRECBleManager.prototype._detectAudioExt = function(data) {
  if (!data || data.length < 4) return null
  var b0 = data[0], b1 = data[1], b2 = data[2], b3 = data[3]
  // MP3: ID3 tag
  if (b0 === 0x49 && b1 === 0x44 && b2 === 0x33) return '.mp3'
  // MP3: sync word
  if (b0 === 0xFF && (b1 & 0xE0) === 0xE0) return '.mp3'
  // WAV: RIFF
  if (b0 === 0x52 && b1 === 0x49 && b2 === 0x46 && b3 === 0x46) return '.wav'
  // OGG (Opus in OGG container)
  if (b0 === 0x4F && b1 === 0x67 && b2 === 0x67 && b3 === 0x53) return '.opus'
  // AAC ADTS
  if (b0 === 0xFF && (b1 & 0xF0) === 0xF0) return '.aac'
  // FLAC
  if (b0 === 0x66 && b1 === 0x4C && b2 === 0x61 && b3 === 0x43) return '.flac'
  // AMR
  if (b0 === 0x23 && b1 === 0x21 && b2 === 0x41 && b3 === 0x4D) return '.amr'
  // M4A/MP4: ftyp box at offset 4
  if (data.length >= 8 && data[4] === 0x66 && data[5] === 0x74 && data[6] === 0x79 && data[7] === 0x70) return '.m4a'
  // ATW 私有格式
  if (b0 === 0x5B && b1 === 0x50) return '.atw'
  return null
}

AIRECBleManager.prototype._cleanupDownload = function() {
  clearTimeout(this._downloadTimer)
  this._downloadingFile = null
  this._downloadChunks = []
  this._downloadedBytes = 0
}

// --- 工具方法 --------------------------------------------------------------

AIRECBleManager.prototype._strToBytes = function(str) {
  var arr = []
  for (var i = 0; i < str.length; i++) arr.push(str.charCodeAt(i))
  return new Uint8Array(arr)
}

AIRECBleManager.prototype._bytesToStr = function(bytes) {
  var s = ''
  for (var i = 0; i < bytes.length; i++) {
    if (bytes[i] === 0) break
    s += String.fromCharCode(bytes[i])
  }
  return s
}

AIRECBleManager.prototype._readInt16BE = function(data, offset) {
  return ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF)
}

AIRECBleManager.prototype._readInt32BE = function(data, offset) {
  return ((data[offset] & 0xFF) << 24) | ((data[offset+1] & 0xFF) << 16)
    | ((data[offset+2] & 0xFF) << 8) | (data[offset+3] & 0xFF)
}

AIRECBleManager.prototype._parseFileTime = function(fileName) {
  if (!fileName || fileName.length < 14) return ''
  var raw = fileName.replace(/[._]/g, '')
  if (raw.length < 14) return ''
  var ts = raw.substring(0, 14)
  return ts.substring(0,4) + '-' + ts.substring(4,6) + '-' + ts.substring(6,8)
    + ' ' + ts.substring(8,10) + ':' + ts.substring(10,12) + ':' + ts.substring(12,14)
}

AIRECBleManager.prototype._formatFileSize = function(size) {
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  return (size / (1024 * 1024)).toFixed(1) + ' MB'
}

AIRECBleManager.prototype.formatStorageStr = function() {
  if (!this._device || this._device.storageTotal === 0) return '--'
  return (this._device.storageUsed / 1024).toFixed(1) + ' MB / '
    + (this._device.storageTotal / 1024).toFixed(1) + ' MB'
}

module.exports = AIRECBleManager
