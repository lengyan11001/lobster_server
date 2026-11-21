Page({
  data: {
    env: "",
    videos: [],
    audio: "",
    logs: [],
    busy: false
  },

  onLoad() { this.probe(); },

  log(line) {
    const logs = this.data.logs.concat([line]);
    this.setData({ logs: logs.slice(-60) });
  },

  probe() {
    const lines = [];
    const app = wx.getAppBaseInfo ? wx.getAppBaseInfo() : {};
    const dev = wx.getDeviceInfo ? wx.getDeviceInfo() : {};
    lines.push("SDK " + (app.SDKVersion || "?") + " / " + (dev.platform || "?") + " " + (dev.system || ""));
    lines.push("wx.createMediaContainer: " + typeof wx.createMediaContainer);
    if (typeof wx.createMediaContainer === "function") {
      try {
        const c = wx.createMediaContainer();
        const names = new Set();
        Object.keys(c).forEach((k) => names.add(k));
        let p = Object.getPrototypeOf(c);
        while (p && p !== Object.prototype) {
          Object.getOwnPropertyNames(p).forEach((n) => { if (n !== "constructor") names.add(n); });
          p = Object.getPrototypeOf(p);
        }
        lines.push("container 方法: " + Array.from(names).join(", "));
      } catch (e) {
        lines.push("createMediaContainer 失败: " + e.message);
      }
    }
    this.setData({ env: lines.join("\n") });
  },

  pickVideos() {
    wx.chooseMedia({
      count: 3,
      mediaType: ["video"],
      sourceType: ["album", "camera"],
      success: (res) => {
        const videos = (res.tempFiles || []).map((f) => ({ path: f.tempFilePath, size: f.size, duration: f.duration || 0 }));
        this.setData({ videos });
        this.log("已选视频 " + videos.length + " 段: " + videos.map((v) => (v.duration ? v.duration.toFixed(1) + "s" : "?")).join(" / "));
      },
      fail: (e) => this.log("选视频失败: " + e.errMsg)
    });
  },

  pickAudio() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["mp3", "m4a", "wav", "aac"],
      success: (res) => {
        const f = (res.tempFiles || [])[0];
        this.setData({ audio: f ? f.path : "" });
        this.log("已选音频: " + (f ? f.name + " " + Math.round(f.size / 1024) + "KB" : "无"));
      },
      fail: (e) => this.log("选音频失败: " + e.errMsg)
    });
  },

  compose() {
    if (this.data.busy) return;
    if (!this.data.videos.length) return wx.showToast({ title: "先选视频", icon: "none" });
    if (typeof wx.createMediaContainer !== "function") return wx.showToast({ title: "本机不支持媒体容器", icon: "none" });
    this.setData({ busy: true, logs: [] });
    const started = Date.now();
    let container;
    try {
      container = wx.createMediaContainer();
    } catch (e) {
      this.log("createMediaContainer 失败: " + e.message);
      this.setData({ busy: false });
      return;
    }

    const methodNames = [];
    let p = Object.getPrototypeOf(container);
    while (p && p !== Object.prototype) {
      Object.getOwnPropertyNames(p).forEach((n) => { if (n !== "constructor") methodNames.push(n); });
      p = Object.getPrototypeOf(p);
    }

    // 自动找"创建轨道"的方法
    const videoMaker = methodNames.find((n) => /video/i.test(n) && /track|clip/i.test(n));
    const audioMaker = methodNames.find((n) => /audio/i.test(n) && /track/i.test(n));
    this.log("尝试方法: video=" + videoMaker + " audio=" + audioMaker);

    const attach = (track, path, index) => {
      const names = [];
      let q = Object.getPrototypeOf(track);
      while (q && q !== Object.prototype) {
        Object.getOwnPropertyNames(q).forEach((n) => { if (n !== "constructor") names.push(n); });
        q = Object.getPrototypeOf(q);
      }
      const adder = names.find((n) => /^(add|append|insert|push)/i.test(n));
      this.log("轨道# " + index + " 方法: " + names.join(",") + " -> 用 " + adder);
      if (!adder) throw new Error("找不到添加素材的方法");
      const arg = { path: path, startTime: index, duration: 0 };
      try {
        track[adder](arg);
      } catch (e1) {
        try { track[adder](path); } catch (e2) { throw new Error("add 失败: " + e1.message + " / " + e2.message); }
      }
    };

    try {
      const vTrack = videoMaker ? container[videoMaker]() : null;
      if (!vTrack) throw new Error("没有视频轨方法");
      this.data.videos.forEach((v, i) => attach(vTrack, v.path, i));

      if (this.data.audio && audioMaker) {
        const aTrack = container[audioMaker]();
        attach(aTrack, this.data.audio, 9);
      }

      const exporter = methodNames.find((n) => /export/i.test(n));
      this.log("导出方法: " + exporter);
      if (!exporter) throw new Error("没有 export 方法");

      const result = container[exporter]();
      if (result && typeof result.then === "function") {
        result.then((res) => {
          const ms = Date.now() - started;
          this.log("导出成功 " + ms + "ms: " + JSON.stringify(res));
          if (res && (res.tempFilePath || res.path)) {
            wx.getFileInfo({
              filePath: res.tempFilePath || res.path,
              success: (info) => this.log("文件大小 " + Math.round(info.size / 1024 / 1024 * 10) / 10 + "MB")
            });
          }
          this.setData({ busy: false });
        }).catch((err) => {
          this.log("导出失败: " + (err && (err.errMsg || err.message || JSON.stringify(err))));
          this.setData({ busy: false });
        });
        return;
      }
      this.log("导出返回: " + JSON.stringify(result));
    } catch (e) {
      this.log("拼接失败: " + e.message);
    }
    this.setData({ busy: false });
  }
});
