Page({
  data: {
    fileName: '',
    fileSize: '',
    isPlaying: false,
    currentTimeStr: '00:00',
    durationStr: '--:--',
    sliderValue: 0,
    errMsg: '',
    ready: false,
    loading: false,
  },

  _audio: null,
  _duration: 0,
  _seeking: false,
  _isIOS: false,
  _playPath: '',

  onLoad: function(options) {
    var path = decodeURIComponent(options.path || '')
    var fileName = decodeURIComponent(options.fileName || '')
    var fileSize = decodeURIComponent(options.fileSize || '')
    this.setData({ fileName: fileName, fileSize: fileSize })

    try {
      var info = wx.getSystemInfoSync()
      this._isIOS = (info.platform === 'ios')
    } catch(e) { this._isIOS = false }

    if (path) {
      this.setData({ loading: true })
      this._initAudio(path)
    } else {
      this.setData({ errMsg: '文件路径无效' })
    }
  },

  onUnload: function() {
    this._destroyAudio()
  },

  _initAudio: function(path) {
    var self = this
    var fs = wx.getFileSystemManager()

    try {
      fs.statSync(path)
    } catch(e) {
      self.setData({ errMsg: '文件不存在，请重新下载', loading: false })
      return
    }

    var buf
    try {
      buf = fs.readFileSync(path)
    } catch(e) {
      self.setData({ errMsg: '读取文件失败，请重新下载', loading: false })
      return
    }

    if (!buf || buf.byteLength === 0) {
      self.setData({ errMsg: '文件为空，请重新下载', loading: false })
      return
    }

    var h = new Uint8Array(buf, 0, Math.min(8, buf.byteLength))

    if (h[0] === 0x5B && h[1] === 0x50) {
      self.setData({ errMsg: 'ATW 格式无法播放，请删除后重新下载', loading: false })
      return
    }

    if (h[0] === 0x4F && h[1] === 0x67 && h[2] === 0x67 && h[3] === 0x53 && self._isIOS) {
      // iOS 不支持 OGG，尝试播放，失败时 onError 会给出提示
      self._playPath = path
      self._createPlayer(path)
      return
    }

    self._playPath = path
    self._createPlayer(path)
  },

  _createPlayer: function(src) {
    var self = this
    var audio = wx.createInnerAudioContext()

    audio.onCanplay(function() {
      self._duration = audio.duration || 0
      self.setData({ durationStr: self._fmtTime(self._duration) })
    })

    audio.onPlay(function() { self.setData({ isPlaying: true }) })
    audio.onPause(function() { self.setData({ isPlaying: false }) })
    audio.onStop(function() { self.setData({ isPlaying: false }) })

    audio.onTimeUpdate(function() {
      if (self._seeking) return
      var cur = audio.currentTime || 0
      var dur = audio.duration || self._duration || 1
      self.setData({
        currentTimeStr: self._fmtTime(cur),
        sliderValue: Math.floor((cur / dur) * 100),
      })
    })

    audio.onEnded(function() {
      self.setData({ isPlaying: false, sliderValue: 100 })
    })

    audio.onError(function(err) {
      var code = String(err.errCode || '')
      var rawMsg = err.errMsg || ''
      var msg
      if (rawMsg.indexOf('-11828') >= 0 || rawMsg.indexOf('无法打开') >= 0) {
        msg = self._isIOS ? 'iOS 不支持此音频格式，请在 Android 设备上播放' : '格式不支持，请重新下载'
      } else if (code === '1182' || rawMsg.indexOf('1182') >= 0) {
        msg = '文件路径无效，请重新下载'
      } else if (code === '1183' || rawMsg.indexOf('1183') >= 0) {
        msg = self._isIOS ? 'iOS 不支持此音频格式，请在 Android 设备上播放' : '不支持的音频格式'
      } else {
        msg = '播放失败 (' + (code || rawMsg) + ')'
      }
      self.setData({ isPlaying: false, errMsg: msg, loading: false })
    })

    audio.src = src
    self._audio = audio
    self.setData({ errMsg: '', loading: false })
  },

  _destroyAudio: function() {
    if (this._audio) {
      try { this._audio.stop() } catch(e) {}
      try { this._audio.destroy() } catch(e) {}
      this._audio = null
    }
  },

  onPlayPause: function() {
    if (!this._audio) return
    if (this.data.isPlaying) { this._audio.pause() } else { this._audio.play() }
  },

  onSeekBack: function() {
    if (!this._audio) return
    this._audio.seek(Math.max(0, (this._audio.currentTime || 0) - 10))
  },

  onSeekForward: function() {
    if (!this._audio) return
    var dur = this._audio.duration || this._duration || 0
    this._audio.seek(Math.min(dur, (this._audio.currentTime || 0) + 10))
  },

  onSliderChanging: function(e) {
    this._seeking = true
    var dur = this._audio ? (this._audio.duration || this._duration || 0) : 0
    this.setData({ currentTimeStr: this._fmtTime((e.detail.value / 100) * dur) })
  },

  onSliderChange: function(e) {
    this._seeking = false
    if (!this._audio) return
    var dur = this._audio.duration || this._duration || 0
    this._audio.seek((e.detail.value / 100) * dur)
  },

  onSaveToLocal: function() {
    var src = this._playPath
    if (!src) { wx.showToast({ title: '无可保存的文件', icon: 'none' }); return }
    if (wx.saveFileToDisk) {
      wx.saveFileToDisk({
        filePath: src,
        success: function() { wx.showToast({ title: '已保存到本地', icon: 'success' }) },
        fail: function() { wx.showToast({ title: '保存失败', icon: 'none' }) },
      })
    } else {
      wx.showToast({ title: '当前版本不支持保存', icon: 'none' })
    }
  },

  _fmtTime: function(sec) {
    if (!sec || isNaN(sec) || sec < 0) return '00:00'
    var s = Math.floor(sec), m = Math.floor(s / 60)
    return (m < 10 ? '0' : '') + m + ':' + (s % 60 < 10 ? '0' : '') + (s % 60)
  },
})
